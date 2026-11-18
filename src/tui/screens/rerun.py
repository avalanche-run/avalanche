"""The rerun dialog: pick how much to run again, start it, and show any refusal."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from avalanche.reruns import RerunRefusal

from ..extensions import ExtensionSession
from ..state import (
    ActionDescriptor,
    ActionResult,
    TuiContext,
    TuiExtensionError,
    TuiRerunRefusedError,
)


def refusal_text(refusal: RerunRefusal) -> str:
    """Every problem, then the earliest step that would work when there is one."""
    lines = ["Rerun refused, nothing was started:"]
    lines += [
        f"  {problem.step or 'source run'}: {problem.reason.value}: {problem.detail}"
        for problem in refusal.problems
    ]
    if refusal.earliest_start:
        lines.append(f"Earliest step to rerun from: {', '.join(refusal.earliest_start)}")
    return "\n".join(lines)


class RerunScreen(ModalScreen[None]):
    """Starts one rerun of ``context``'s run from its selected step.

    The operator answers after its checks, which can take many seconds, so Escape
    stays available: the rerun carries on, and its outcome becomes a notification.
    """

    DEFAULT_CSS = """
    RerunScreen { align: center middle; }
    RerunScreen > Vertical {
        width: 80%; max-width: 100; height: auto; max-height: 90%;
        background: $surface; border: solid $accent; padding: 1 2;
    }
    RerunScreen OptionList { height: auto; margin: 1 0; }
    RerunScreen #rerun-outcome { height: auto; max-height: 16; }
    """
    AUTO_FOCUS = "#rerun-mode"
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        session: ExtensionSession,
        action: ActionDescriptor,
        context: TuiContext,
        step_name: str,
        open_run: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._extensions = session
        self._rerun_action = action
        self._rerun_context = context
        self._step_name = step_name
        self._open_run = open_run
        # Kept while an attempt's outcome is unknown: the same intent cannot start twice.
        self._attempt: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                f"Rerun from {self._step_name} · {self._rerun_context.run}", markup=False
            )
            yield OptionList(
                Option("This step and everything after", id="autorun"),
                Option("Only this step", id="lazy"),
                id="rerun-mode",
            )
            yield Static("Enter starts · Esc closes", id="rerun-hint", markup=False)
            with VerticalScroll(id="rerun-outcome"):
                yield Static("", id="rerun-outcome-text", markup=False)

    def _show_outcome(self, text: str) -> None:
        self.query_one("#rerun-outcome-text", Static).update(text)

    def _set_choosing(self, enabled: bool) -> None:
        options = self.query_one("#rerun-mode", OptionList)
        options.disabled = not enabled
        if enabled:
            options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        mode = event.option.id
        assert mode is not None
        if self._attempt is not None and self._attempt[0] != mode:
            self._show_outcome(
                "The last attempt's outcome is unknown and it may have started. "
                "Retry the same option, or close this and check the run list."
            )
            return
        retrying = self._attempt is not None
        attempt = self._attempt or (mode, str(uuid4()))
        self._attempt = attempt
        self._set_choosing(False)
        self._show_outcome("Checking the saved outputs…")
        if not self._extensions.invoke_pinned(
            self._rerun_action,
            self._rerun_context,
            {"mode": mode},
            attempt[1],
            self._rerun_started,
            self._rerun_failed,
        ):
            # Nothing was sent. An earlier attempt may still have started, so a retry
            # keeps its intent; only an attempt that never left this call is forgotten.
            if not retrying:
                self._attempt = None
            self._set_choosing(True)
            self._show_outcome("The operator is busy with other requests. Try again.")

    def _rerun_started(self, result: ActionResult) -> None:
        run_id = result.value["run_id"]
        assert isinstance(run_id, str)
        if self.is_attached:
            self.dismiss()
            self._open_run(run_id)
        else:
            self.app.notify(f"Rerun started: {run_id}")

    def _rerun_failed(self, error: TuiExtensionError) -> None:
        if isinstance(error, TuiRerunRefusedError):
            text = refusal_text(error.refusal)
        elif error.retryable:
            text = f"{error} The rerun may have started. Retrying cannot start it twice."
        else:
            text = str(error)
        if not error.retryable:
            self._attempt = None
        if self.is_attached:
            self._set_choosing(True)
            self._show_outcome(text)
        else:
            self.app.notify(text, severity="error")
