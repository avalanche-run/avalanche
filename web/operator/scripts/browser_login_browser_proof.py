"""Browser login from the operator UI, end to end in a real browser.

A real ``ava operator`` process serves its packaged UI for the fixture
``test/fixtures/browser_login_flow.py``. The login service, the MCP server and
the Connector's API are the separate process ``test/fixtures/login_test_server.py``,
which speaks OAuth with the MCP library's own routes. The browser connects a
Connector with its app's client ID and secret and an MCP server with the login
the server describes, each through a tab the UI opens on the consent page, then
runs both workflows. No Delta and no fake host.

Run from ``pkgs/avalanche`` after ``make web-build``:
  uv run --frozen --package avalanche-ai --with playwright==1.55.0 python \
    web/operator/scripts/browser_login_browser_proof.py --evidence /path/to/evidence
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from integrations_browser_proof import (
    PACKAGE,
    Shown,
    free_port,
    node_card,
    open_integrations,
    open_workflow,
    shoot,
    start_run,
    stored_accounts,
    stored_text_outside_accounts,
)
from playwright.sync_api import Page, expect, sync_playwright

FIXTURE = PACKAGE / "test" / "fixtures" / "browser_login_flow.py"
LOGIN_SERVER = PACKAGE / "test" / "fixtures" / "login_test_server.py"
APP_CLIENT_ID = "acme-calendar-app"
APP_CLIENT_SECRET = "app-secret-4d1f8a02c7b94e63"


class LoginService:
    """The login test server as a separate process."""

    def __init__(self, journal: Path, redirect_uri: str, *options: str) -> None:
        self.journal = journal
        port = free_port()
        self.url = f"http://127.0.0.1:{port}"
        self._process = subprocess.Popen(
            [
                sys.executable,
                str(LOGIN_SERVER),
                str(port),
                str(journal),
                redirect_uri,
                *options,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert self._process.poll() is None, "the login test server stopped"
                assert time.monotonic() < deadline, "the login test server did not start"
                time.sleep(0.1)

    def entries(self, event: str) -> list[dict]:
        if not self.journal.exists():
            return []
        entries = [json.loads(line) for line in self.journal.read_text().splitlines()]
        return [entry for entry in entries if entry["event"] == event]

    def secrets(self) -> set[str]:
        """Every one-time code and token the service handed out."""
        return {
            entry[field]
            for event in ("consent", "tokens")
            for entry in self.entries(event)
            for field in ("code", "access_token", "refresh_token")
            if field in entry
        }

    def close(self) -> None:
        self._process.terminate()
        try:
            self._process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)


@contextmanager
def operator(
    state_db: Path,
    grpc_port: int,
    web_port: int,
    login_port: int,
    service: LoginService,
    locked: LoginService,
    log: Path,
) -> Iterator[str]:
    """Run ``ava operator`` with its browser UI until the block ends."""
    command = [
        sys.executable, "-m", "ava_cli", "operator", str(FIXTURE),
        "--port", str(grpc_port), "--web-port", str(web_port), "--webhook-port", "0",
        "--login-port", str(login_port), "--state-db", str(state_db),
    ]  # fmt: skip
    environment = {
        **os.environ,
        "LOGIN_SERVICE_URL": service.url,
        "LOCKED_LOGIN_SERVICE_URL": locked.url,
    }
    url = f"http://127.0.0.1:{web_port}/"
    with log.open("a") as output:
        # The operator caches workflow definitions in its working directory. The
        # fixture reads the services' addresses from the environment, so each
        # proof run gets a directory of its own.
        process = subprocess.Popen(
            command, stdout=output, stderr=output, env=environment, cwd=state_db.parent
        )
        try:
            deadline = time.monotonic() + 90
            while True:
                assert process.poll() is None, log.read_text()
                assert time.monotonic() < deadline, log.read_text()
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def log_in(page: Page, form, button: str, callback: str) -> str:
    """Press the form's login button and follow the tab it opens to the end."""
    with page.expect_popup() as opened:
        form.get_by_role("button", name=button, exact=True).click()
    tab = opened.value
    # The consent page approves at once and sends the tab back to the operator.
    tab.wait_for_url(f"{callback}**", timeout=60_000)
    expect(tab.get_by_role("heading")).to_contain_text("connected")
    said = tab.content()
    tab.close()
    return said


def scenario(
    page: Page,
    url: str,
    evidence: Path,
    service: LoginService,
    locked: LoginService,
    grpc_port: int,
    callback: str,
    journal: Path,
) -> Shown:
    shown = Shown(page)
    page.goto(url)
    panel = open_integrations(page)
    rows = {
        name: panel.locator(f'.integration-row[data-integration="{name}"]')
        for name in ("acme-calendar", "archive", "notes")
    }
    for row in rows.values():
        expect(row).to_have_attribute("data-account", "missing")
    assert not service.journal.exists() and not locked.journal.exists()

    # 1. A Connector that declares a browser login asks for its app's client ID
    # and secret, and offers no key field.
    rows["acme-calendar"].get_by_role("button", name="Connect Acme Calendar").click()
    form = rows["acme-calendar"].get_by_role("form", name="Connect Acme Calendar")
    expect(form).to_have_attribute("data-login", "browser")
    expect(form.get_by_label("API key", exact=True)).to_have_count(0)
    form.get_by_label("Client ID", exact=True).fill(APP_CLIENT_ID)
    form.get_by_label("Client secret", exact=False).fill(APP_CLIENT_SECRET)
    shoot(page, shown, evidence, "01-connector-login-form")
    shown.captures.append(log_in(page, form, "Log in with browser", callback))
    expect(rows["acme-calendar"]).to_have_attribute("data-account", "connected")
    expect(form).to_have_count(0)

    # 2. An MCP server takes a browser login or a key. The login is the default
    # and needs nothing typed: it is discovered from the server.
    rows["notes"].get_by_role("button", name="Connect notes").click()
    form = rows["notes"].get_by_role("form", name="Connect notes")
    form.get_by_label("Paste a key").check()
    expect(form.get_by_label("API key", exact=True)).to_be_visible()
    form.get_by_label("Browser login").check()
    expect(form.get_by_label("API key", exact=True)).to_have_count(0)
    expect(form.get_by_label("Client ID", exact=True)).to_have_count(0)
    shoot(page, shown, evidence, "02-mcp-login-form")
    shown.captures.append(log_in(page, form, "Log in with browser", callback))
    expect(rows["notes"]).to_have_attribute("data-account", "connected")
    assert len(service.entries("registered")) == 1

    # 3. A server that registers no client uses the one its declaration names.
    rows["archive"].get_by_role("button", name="Connect archive").click()
    form = rows["archive"].get_by_role("form", name="Connect archive")
    shown.captures.append(log_in(page, form, "Log in with browser", callback))
    expect(rows["archive"]).to_have_attribute("data-account", "connected")
    assert locked.entries("registered") == []
    shoot(page, shown, evidence, "03-all-connected")
    assert stored_accounts(grpc_port) == {"acme-calendar": True, "archive": True, "notes": True}

    # 4. Both workflows run on the accounts the browser connected.
    for name, node_id in (("calendar_region", "lookup_1"), ("notes_search", "find_1")):
        open_workflow(page, name)
        start_run(page, journal)
        expect(node_card(page, node_id)).to_have_attribute(
            "data-node-state", "success", timeout=120_000
        )
    assert len(service.entries("call")) == 1 and len(locked.entries("call")) == 1
    shoot(page, shown, evidence, "04-run-on-browser-logins")
    return shown


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence: Path = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    state_db = evidence / "state.db"
    log = evidence / "operator.log"
    journal = evidence / "journal.txt"
    service_journal = evidence / "service.jsonl"
    locked_journal = evidence / "locked.jsonl"
    for stale in (state_db, log, journal, service_journal, locked_journal):
        stale.unlink(missing_ok=True)
    shutil.rmtree(evidence / ".avalanche", ignore_errors=True)
    grpc_port, web_port, login_port = free_port(), free_port(), free_port()
    callback = f"http://127.0.0.1:{login_port}/callback"
    service = LoginService(service_journal, callback)
    locked = LoginService(locked_journal, callback, "--no-registration")
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            with operator(
                state_db, grpc_port, web_port, login_port, service, locked, log
            ) as url:
                shown = scenario(
                    page, url, evidence, service, locked, grpc_port, callback, journal
                )
            browser.close()
        assert errors == [], errors
        # The tokens, the one-time codes and the client secret are in the account
        # table and nowhere else: not in anything the browser was shown after the
        # secret was sent, the operator printed, or the operator stored.
        secrets = service.secrets() | locked.secrets()
        assert len(secrets) >= 9
        for text in (*shown.captures, log.read_text(), stored_text_outside_accounts(state_db)):
            for secret in secrets:
                assert secret not in text
        for text in (
            *shown.captures[2:],
            log.read_text(),
            stored_text_outside_accounts(state_db),
        ):
            assert APP_CLIENT_SECRET not in text
        result = {
            "status": "passed",
            "logins": len(service.entries("consent")) + len(locked.entries("consent")),
            "screenshots": sorted(path.name for path in evidence.glob("[0-9][0-9]-*.png")),
        }
    finally:
        service.close()
        locked.close()
    # The journals hold the test service's tokens; they are not evidence to keep.
    service_journal.unlink(missing_ok=True)
    locked_journal.unlink(missing_ok=True)
    (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
