"""Reruns select work without losing producer identity or replaying unrelated rows."""


import pytest

import avalanche as ava


def test_lazy_rerun_prunes_returns_while_autorun_cascades():
    events = []

    @ava.source
    def load():
        raise AssertionError("rerun must not execute skipped upstream")

    @ava.step(slug="middle")
    def middle(ctx: ava.RunContext):
        events.append("middle")
        return ctx.rerun.run_id

    @ava.dest
    def sink(value):
        events.append("sink")
        return value + "-saved"

    @ava.workflow
    def flow():
        selected = load() >> middle()
        final = selected >> sink()
        return selected, final

    workflow = flow()
    executor = ava.LocalExecutor()
    assert workflow.run(
        executor=executor,
        rerun=ava.Rerun(run_id="source-run", start=["middle"], mode="lazy"),
    ).result(timeout=5) == ("source-run", None)
    assert events == ["middle"]

    events.clear()
    assert workflow.run(
        executor=executor,
        rerun=ava.Rerun(run_id="source-run", start=["middle"], mode="autorun"),
    ).result(timeout=5) == ("source-run", "source-run-saved")
    assert events == ["middle", "sink"]
    with pytest.raises(ValueError, match="Unknown rerun start slug"):
        workflow.run(
            executor=executor,
            rerun=ava.Rerun(run_id="source-run", start=["missing"]),
        ).result(timeout=5)










def test_skipped_python_input_is_not_silently_replaced_with_none():
    @ava.source
    def load():
        return "data"

    @ava.step
    def consume(value):
        raise AssertionError("invalid rerun must fail before invoking user code")

    @ava.workflow
    def flow():
        return consume(load())

    with pytest.raises(ValueError, match="requires saved outputs"):
        flow().run(
            executor=ava.LocalExecutor(),
            rerun=ava.Rerun(run_id="source-run", start=["consume"], mode="lazy"),
        ).result()


