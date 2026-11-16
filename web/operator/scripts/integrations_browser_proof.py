"""Integrations in the operator UI, end to end in a real browser.

A real ``ava operator`` process serves its packaged UI for the fixture
``test/fixtures/integrations_flow.py``; the Connector's service is a local HTTP
server that answers only requests carrying the connected API key, and the MCP
server is a separate process that wants its own key. The browser sees the
declared integrations, connects them, runs the workflow and reads the
attributed tool calls, then exercises an installed catalog provider. No remote host.

Run from ``pkgs/avalanche`` after ``make web-build``:
  uv run --frozen --package avalanche-ai --with playwright==1.55.0 python \
    web/operator/scripts/integrations_browser_proof.py --evidence /path/to/evidence
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Locator, Page, expect, sync_playwright

PACKAGE = Path(__file__).resolve().parents[3]
FIXTURE = PACKAGE / "test" / "fixtures" / "integrations_flow.py"
MCP_SERVER = PACKAGE / "test" / "fixtures" / "mcp_test_server.py"
US_KEY = "us-key-7f3c9a51d2e84b06"
EU_KEY = "eu-key-b81d04c6a97f4e23"
KB_KEY = "kb-key-52c0e7a3f91d4b68"
CATALOG_PASS = "catalog-pass-83b6a947d15f420c"
EMAIL = "ada@example.com"


class CrmService:
    """A stand-in CRM that answers only requests carrying the connected key."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                url = urlsplit(self.path)
                accepted = self.headers.get("X-Api-Key", "") == US_KEY
                email = parse_qs(url.query).get("email", [""])[0]
                service.requests.append(("accepted" if accepted else "refused", email))
                body = json.dumps({"email": email, "region": "us"}).encode()
                self.send_response(200 if accepted else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class KbServer:
    """The MCP test server as a separate process that serves HTTP and wants a key."""

    def __init__(self, journal: Path, *, key: str = KB_KEY) -> None:
        self._journal = journal
        port = free_port()
        self.url = f"http://127.0.0.1:{port}/mcp"
        self._process = subprocess.Popen(
            [sys.executable, str(MCP_SERVER), "--http", str(port), key],
            env={**os.environ, "MCP_TEST_SERVER_JOURNAL": str(journal)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert self._process.poll() is None, "the MCP test server stopped"
                assert time.monotonic() < deadline, "the MCP test server did not start"
                time.sleep(0.1)

    def entries(self, event: str) -> list[dict[str, object]]:
        """What the server wrote to its journal for ``event``."""
        if not self._journal.exists():
            return []
        entries = [json.loads(line) for line in self._journal.read_text().splitlines()]
        return [entry for entry in entries if entry["event"] == event]

    def close(self) -> None:
        self._process.terminate()
        try:
            self._process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)


class CatalogService:
    """Loopback-only service used by the separately installed proof provider."""

    def __init__(self, kb: KbServer, journal: Path) -> None:
        self.mode = "ready"
        self.connected = False
        self.connected_at: float | None = None
        self.logins: dict[str, str] = {}
        self.sessions: list[str] = []
        self.journal = journal
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                code, media, body = 200, "application/json", "null"
                with journal.open("a") as output:
                    output.write(json.dumps({"path": url.path, "mode": service.mode}) + "\n")
                if url.path == "/icon.svg":
                    media = "image/svg+xml"
                    body = (
                        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
                        '<rect width="32" height="32" rx="7" fill="#f06a6a"/>'
                        '<circle cx="16" cy="16" r="6" fill="white"/>'
                        "<script>window.catalogIconScript=true</script></svg>"
                    )
                elif service.mode in {"signed_out", "unreachable"}:
                    code = 401 if service.mode == "signed_out" else 503
                elif url.path == "/entries":
                    body = json.dumps(
                        [
                            {
                                "name": "asana",
                                "display_name": "Asana proof",
                                "icon_url": service.url + "/icon.svg",
                            }
                        ]
                    )
                elif url.path == "/connections":
                    body = json.dumps(
                        [
                            {
                                "name": "asana",
                                "connected_at": service.connected_at,
                                "state": "reconnect_needed"
                                if service.mode == "expired"
                                else "connected",
                            }
                        ]
                        if service.connected
                        else []
                    )
                elif url.path == "/login":
                    login_id = str(len(service.logins) + 1)
                    service.logins[login_id] = "waiting"
                    body = service.login_view(login_id)
                elif url.path == "/approve":
                    media = "text/html"
                    body = (
                        "<title>Proof provider login</title><h1>Connect Asana proof</h1>"
                        "<p>This local provider keeps the account, not the operator.</p>"
                        f'<a href="/finish?id={query["id"][0]}">Approve connection</a>'
                    )
                elif url.path == "/finish":
                    service.connected = True
                    service.connected_at = time.time()
                    service.mode = "ready"
                    service.logins[query["id"][0]] = "connected"
                    media, body = "text/html", "<h1>Connected</h1>You can close this tab."
                elif url.path == "/status":
                    body = service.login_view(query["id"][0])
                elif url.path == "/disconnect":
                    service.connected = False
                    service.connected_at = None
                elif url.path == "/session":
                    service.sessions.append(query["step"][0])
                    body = json.dumps(
                        {
                            "url": kb.url,
                            "pass_value": CATALOG_PASS,
                            "expires_at": time.time() + 3600.0,
                        }
                    )
                else:
                    code = 404
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", media)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def login_view(self, login_id: str) -> str:
        from avalanche.integrations.core import IntegrationNotConnectedError

        return json.dumps(
            {
                "login_id": login_id,
                "name": "asana",
                "authorization_url": f"{self.url}/approve?id={login_id}",
                "status": "failed" if self.mode == "not_connected" else self.logins[login_id],
                "error": str(
                    IntegrationNotConnectedError("catalog login", ["asana"], "ava connect")
                )
                if self.mode == "not_connected"
                else None,
            }
        )

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def installed_catalog_provider() -> str:
    """Install distribution metadata without importing the plug-in in this process."""
    done = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--python",
            sys.executable,
            "--with",
            str(PACKAGE / "test/catalog_provider"),
            "python",
            "-c",
            "from importlib.metadata import distribution; "
            "print(distribution('avalanche-test-catalog-provider').locate_file(''))",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return done.stdout.strip()


@contextmanager
def operator(
    state_db: Path,
    grpc_port: int,
    web_port: int,
    crm_url: str,
    kb_url: str,
    log: Path,
    *,
    fixture: Path = FIXTURE,
    environment: dict[str, str] | None = None,
) -> Iterator[str]:
    """Run ``ava operator`` with its browser UI until the block ends."""
    command = [
        sys.executable, "-m", "ava_cli", "operator", str(fixture),
        "--port", str(grpc_port), "--web-port", str(web_port), "--webhook-port", "0",
        "--state-db", str(state_db),
    ]  # fmt: skip
    # The agent runs in PredictRLM's sandbox, which records each turn's tool calls.
    environment = {
        **os.environ,
        **(environment or {}),
        "ACME_CRM_URL": crm_url,
        "ACME_KB_URL": kb_url,
        "INTEGRATIONS_FLOW_SANDBOX": "1",
    }
    url = f"http://127.0.0.1:{web_port}/"
    with log.open("a") as output:
        process = subprocess.Popen(command, stdout=output, stderr=output, env=environment)
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


def ava_connect(grpc_port: int, *arguments: str, key: str | None = None) -> str:
    """``ava connect`` against the running operator, as a person would in a terminal."""
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "ava_cli",
            "connect",
            *arguments,
            "--connect",
            f"127.0.0.1:{grpc_port}",
        ],  # fmt: skip
        input=None if key is None else key + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout + done.stderr


def stored_accounts(grpc_port: int) -> dict[str, bool]:
    listed = json.loads(ava_connect(grpc_port, "--json"))
    return {row["name"]: row["connected"] for row in listed}


def stored_text_outside_accounts(state_db: Path) -> str:
    """Everything the operator stored except the account table, as one text."""
    connection = sqlite3.connect(state_db)
    try:
        tables = [
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if name != "integration_accounts"
        ]
        return "\n".join(
            repr(row)
            for table in tables
            for row in connection.execute(f"SELECT * FROM {table}")
        )
    finally:
        connection.close()


class Shown:
    """Everything the browser was given: page text, markup and field values."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self.captures: list[str] = []

    def capture(self) -> None:
        self.captures.append(self._page.content())
        self.captures.append(
            self._page.evaluate(
                """() => JSON.stringify([
                    [...document.querySelectorAll('input, textarea')].map(field => field.value),
                    Object.entries(localStorage), Object.entries(sessionStorage),
                ])"""
            )
        )


def shoot(page: Page, shown: Shown, evidence: Path, name: str) -> None:
    """One screenshot per theme; the page is captured for the secret check."""
    shown.capture()
    page.screenshot(path=str(evidence / f"{name}-light.png"))
    page.evaluate("document.documentElement.classList.add('dark')")
    page.screenshot(path=str(evidence / f"{name}-dark.png"))
    page.evaluate("document.documentElement.classList.remove('dark')")


def open_workflow(page: Page, name: str) -> None:
    page.get_by_role("complementary", name="Explorer").get_by_role(
        "button", name=name
    ).first.click()
    expect(page.locator(".react-flow__node-workflow").first).to_be_visible()


def open_integrations(page: Page):
    page.get_by_role("complementary", name="Explorer").get_by_role(
        "region", name="Integrations", exact=True
    ).get_by_role("button").click()
    return page.locator(".integrations-panel")


def start_run(page: Page, journal: Path) -> None:
    """Fill the run form from the graph's input dot, then press Run."""
    page.get_by_role("button", name="Inspect workflow input Lookup").click()
    inputs = page.get_by_role("complementary", name="Workflow input declaration")
    inputs.get_by_label("email value", exact=True).fill(EMAIL)
    inputs.get_by_label("journal value", exact=True).fill(str(journal))
    inputs.get_by_role("button", name="Close", exact=True).click()
    page.get_by_role("button", name="Run", exact=True).click()


def node_card(page: Page, node_id: str):
    return page.locator(f'.react-flow__node-workflow[data-id="{node_id}"] .node-card')


def scenario(
    page: Page,
    url: str,
    evidence: Path,
    crm: CrmService,
    kb: KbServer,
    grpc_port: int,
    journal: Path,
) -> Shown:
    shown = Shown(page)
    outside: list[str] = []
    page.context.on(
        "request",
        lambda request: (
            outside.append(request.url)
            if urlsplit(request.url).hostname not in {"127.0.0.1", "localhost", "::1"}
            and not request.url.startswith(("data:", "blob:"))
            else None
        ),
    )
    page.goto(url)
    open_workflow(page, "agent_only")

    # 1. Node badges: one per declared integration, on the step's own card. The
    # Connector draws its declared icon file; the MCP server declares none.
    triage = node_card(page, "triage_1")
    badges = triage.get_by_role("list", name="Integrations").get_by_role("listitem")
    expect(badges).to_have_count(2)
    crm_badge = triage.locator('[data-integration="acme-crm"]')
    kb_badge = triage.locator('[data-integration="kb"]')
    expect(crm_badge).to_contain_text("Acme CRM")
    expect(crm_badge).to_have_attribute("data-account", "missing")
    expect(kb_badge).to_have_attribute("data-account", "missing")
    declared_icon = crm_badge.locator('img[data-integration-icon="declared"]')
    expect(declared_icon).to_be_visible()
    expect(kb_badge.locator('svg[data-integration-icon="generic"]')).to_be_visible()
    expect(page.locator(".react-flow__node")).to_have_count(3)  # input dot + two steps
    # The icon file holds a script. It is decoded as an image and never becomes
    # markup of the page, so the script has not run.
    page.wait_for_function(
        "() => document.querySelector('img[data-integration-icon]').naturalWidth > 0"
    )
    assert declared_icon.get_attribute("src").startswith("data:image/svg+xml;base64,")
    assert page.evaluate("window.acmeIconScript") is None
    assert page.locator("svg script, svg [onload]").count() == 0
    shoot(page, shown, evidence, "01-node-badges")

    # 2. Node details: each integration's kind and, for the Connector, its tools.
    triage.get_by_role("button", name="Inspect triage").click()
    details = page.get_by_role("complementary", name="Node declaration").get_by_role(
        "region", name="Integrations"
    )
    crm_detail = details.locator('[data-integration="acme-crm"]')
    expect(crm_detail).to_contain_text("Connector")
    expect(details.locator('[data-integration="kb"]')).to_contain_text("MCP")
    crm_detail.locator("summary").click()
    tools = crm_detail.get_by_role("list", name="Acme CRM tools").get_by_role("listitem")
    expect(tools).to_have_count(2)
    expect(tools.nth(1)).to_contain_text("acme_crm__find_customer")
    shoot(page, shown, evidence, "02-node-details")

    # 3. The integrations screen: every declared integration, none connected.
    # Showing it reached no service and asked nothing outside the operator.
    panel = open_integrations(page)
    rows = panel.locator(".integration-row")
    expect(rows).to_have_count(3)
    for name in ("acme-crm", "acme-crm-eu", "kb"):
        expect(panel.locator(f'.integration-row[data-integration="{name}"]')).to_have_attribute(
            "data-account", "missing"
        )
    assert stored_accounts(grpc_port) == {"acme-crm": False, "acme-crm-eu": False, "kb": False}
    assert crm.requests == []
    assert kb.entries("request") == []
    assert outside == [], outside
    shoot(page, shown, evidence, "03-integrations-missing")

    # 4. A run without the account: the step fails before its code runs, says
    # why, and links to the place to connect it.
    panel.get_by_role("button", name="Close integrations").click()
    start_run(page, journal)
    expect(node_card(page, "triage_1")).to_have_attribute(
        "data-node-state", "failed", timeout=60_000
    )
    node_card(page, "triage_1").get_by_role("button", name="Inspect triage").click()
    reason = page.get_by_role("complementary", name="Run inspector").locator(
        ".integration-missing-account"
    )
    expect(reason).to_contain_text("Acme CRM")
    assert not journal.exists()
    assert crm.requests == []
    shoot(page, shown, evidence, "04-missing-account-failure")
    reason.get_by_role("button", name="Connect Acme CRM").click()

    # 5. Connect with a key. The link opened the screen on that integration with
    # its form ready; the row turns connected without a reload or a restart.
    crm_row = panel.locator('.integration-row[data-integration="acme-crm"]')
    form = crm_row.get_by_role("form", name="Connect Acme CRM")
    key_field = form.get_by_label("API key", exact=True)
    expect(key_field).to_be_focused()
    expect(key_field).to_have_attribute("type", "password")
    key_field.fill(US_KEY)
    form.get_by_role("button", name="Connect", exact=True).click()
    expect(crm_row).to_have_attribute("data-account", "connected")
    expect(form).to_have_count(0)
    assert stored_accounts(grpc_port) == {"acme-crm": True, "acme-crm-eu": False, "kb": False}

    # An account connected from a terminal reaches the open page by itself.
    eu_row = panel.locator('.integration-row[data-integration="acme-crm-eu"]')
    ava_connect(grpc_port, "acme-crm-eu", key=EU_KEY)
    expect(eu_row).to_have_attribute("data-account", "connected")
    shoot(page, shown, evidence, "05-integrations-connected")

    # Disconnect removes the stored account, after a confirmation.
    eu_row.get_by_role("button", name="Disconnect Acme CRM (EU)").click()
    eu_row.get_by_role("group", name="Disconnect Acme CRM (EU)").get_by_role(
        "button", name="Remove account"
    ).click()
    expect(eu_row).to_have_attribute("data-account", "missing")
    assert stored_accounts(grpc_port) == {"acme-crm": True, "acme-crm-eu": False, "kb": False}

    # 6. Run again. The MCP server asks for a login and has no account: the step
    # fails the same way, and its link opens the form for that server.
    open_workflow(page, "agent_only")
    expect(
        node_card(page, "triage_1").locator('[data-integration="acme-crm"]')
    ).to_have_attribute("data-account", "connected")
    start_run(page, journal)
    expect(node_card(page, "triage_1")).to_have_attribute(
        "data-node-state", "failed", timeout=120_000
    )
    node_card(page, "triage_1").get_by_role("button", name="Inspect triage").click()
    expect(reason).to_contain_text("kb")
    assert not journal.exists()
    assert crm.requests == []
    assert kb.entries("request") and not any(
        request["authorized"] for request in kb.entries("request")
    )
    reason.get_by_role("button", name="Connect kb").click()
    kb_row = panel.locator('.integration-row[data-integration="kb"]')
    kb_form = kb_row.get_by_role("form", name="Connect kb")
    # An MCP server takes a browser login or a key; this one wants a key.
    kb_form.get_by_label("Paste a key").check()
    kb_form.get_by_label("API key", exact=True).fill(KB_KEY)
    kb_form.get_by_role("button", name="Connect", exact=True).click()
    expect(kb_row).to_have_attribute("data-account", "connected")
    assert stored_accounts(grpc_port) == {"acme-crm": True, "acme-crm-eu": False, "kb": True}

    # 7. Run again. The agent's calls to the Connector and to the MCP server are
    # in the trace, each named as its integration's; the agent's own tool is not.
    open_workflow(page, "agent_only")
    start_run(page, journal)
    expect(node_card(page, "triage_1")).to_have_attribute(
        "data-node-state", "success", timeout=120_000
    )
    assert crm.requests == [("accepted", EMAIL)]
    node_card(page, "triage_1").get_by_role("button", name="Inspect triage").click()
    trace = page.get_by_role("region", name="Agent trace")
    trace.locator("details.trace-section > summary", has_text="Tools").first.click()
    connector_call = trace.locator("details", has_text="acme_crm__find_customer").last
    expect(connector_call.locator(".tool-integration")).to_have_attribute(
        "data-integration", "acme-crm"
    )
    expect(connector_call.locator(".tool-integration")).to_contain_text("Acme CRM")
    assert [(call["tool"], call["query"]) for call in kb.entries("call")] == [("search", "us")]
    mcp_call = trace.locator("details", has_text="kb__search").last
    expect(mcp_call.locator(".tool-integration")).to_have_attribute("data-integration", "kb")
    own_call = trace.locator("details", has_text="tidy_email").last
    expect(own_call).to_be_visible()
    expect(own_call.locator(".tool-integration")).to_have_count(0)
    shoot(page, shown, evidence, "06-trace-attribution")
    assert outside == [], outside
    assert page.evaluate("window.acmeIconScript") is None
    return shown


def catalog_login(page: Page, row: Locator, shown: Shown, evidence: Path, name: str) -> None:
    """The real UI starts a login, opens a tab and polls until approval."""
    row.get_by_role("button", name="Connect Asana proof", exact=True).click()
    form = row.get_by_role("form", name="Connect Asana proof")
    expect(form.locator("input[type=password], input[type=text]")).to_have_count(0)
    with page.context.expect_page() as opened:
        form.get_by_role("button", name="Log in", exact=False).click()
    consent = opened.value
    expect(consent.get_by_role("heading", name="Connect Asana proof")).to_be_visible()
    assert consent.evaluate("window.opener") is None
    expect(form.get_by_role("status")).to_contain_text("Waiting")
    shoot(page, shown, evidence, name + "-waiting")
    consent.screenshot(path=str(evidence / f"{name}-provider.png"))
    consent.get_by_role("link", name="Approve connection").click()
    expect(row).to_have_attribute("data-account", "connected", timeout=30_000)
    expect(form).to_have_count(0)
    consent.close()


def catalog_scenario(
    page: Page,
    url: str,
    shown: Shown,
    evidence: Path,
    service: CatalogService,
    grpc_port: int,
) -> None:
    page.goto(url)
    open_workflow(page, "on_predict_rlm")
    node = node_card(page, "research_on_predict_rlm_1")
    badge = node.locator('[data-integration="asana"]')
    expect(badge).to_contain_text("Asana proof")
    expect(badge).to_have_attribute("data-account", "missing")
    icon = badge.locator("img")
    expect(icon).to_have_attribute("src", service.url + "/icon.svg")
    page.wait_for_function(
        "() => [...document.querySelectorAll('img')].some(i => "
        "i.src.endsWith('/icon.svg') && i.naturalWidth > 0)"
    )
    assert page.evaluate("window.catalogIconScript") is None
    expect(page.locator(".react-flow__node")).to_have_count(2)
    shoot(page, shown, evidence, "08-catalog-badge")
    node.get_by_role("button", name="Inspect research_on_predict_rlm").click()
    detail = page.get_by_role("complementary", name="Node declaration").get_by_role(
        "region", name="Integrations"
    )
    expect(detail).to_contain_text("Asana proof")
    expect(detail).to_contain_text("Built-in")
    expect(detail.locator("img")).to_have_attribute("src", service.url + "/icon.svg")
    shoot(page, shown, evidence, "09-catalog-details")
    panel = open_integrations(page)
    row = panel.locator('.integration-row[data-integration="asana"]')
    expect(row).to_have_attribute("data-account", "missing")
    assert not service.sessions
    shoot(page, shown, evidence, "10-catalog-missing")

    # Provider login failure is returned verbatim, without a credential form.
    service.mode = "not_connected"
    row.get_by_role("button", name="Connect Asana proof", exact=True).click()
    with page.context.expect_page() as opened:
        row.get_by_role("button", name="Log in with browser").click()
    refused = opened.value
    message = json.loads(service.login_view("1"))["error"]
    expect(row.get_by_role("alert")).to_have_text(message)
    expect(row).to_have_attribute("data-account", "missing")
    refused.close()
    shoot(page, shown, evidence, "11-catalog-not-connected-error")
    row.get_by_role("button", name="Cancel", exact=True).click()
    service.mode = "ready"
    catalog_login(page, row, shown, evidence, "12-catalog-connect")
    assert stored_accounts(grpc_port) == {"asana": True}
    shoot(page, shown, evidence, "13-catalog-connected")

    # State comes from the provider, without a failed run or error-text inference.
    service.mode = "expired"
    page.reload()
    panel = open_integrations(page)
    row = panel.locator('.integration-row[data-integration="asana"]')
    expect(row).to_have_attribute("data-account", "reconnect_needed")
    expect(row).to_contain_text("Reconnect needed")
    expect(row.get_by_role("button", name="Disconnect Asana proof")).to_be_visible()
    assert stored_accounts(grpc_port) == {"asana": False}
    shoot(page, shown, evidence, "14-catalog-reconnect-needed")
    open_workflow(page, "on_predict_rlm")
    expect(
        node_card(page, "research_on_predict_rlm_1").locator('[data-integration="asana"]')
    ).to_have_attribute("data-account", "reconnect_needed")
    panel = open_integrations(page)
    row = panel.locator('.integration-row[data-integration="asana"]')
    catalog_login(page, row, shown, evidence, "15-catalog-reconnect")
    row.get_by_role("button", name="Disconnect Asana proof").click()
    row.get_by_role("button", name="Remove account").click()
    expect(row).to_have_attribute("data-account", "missing")
    assert not service.connected and stored_accounts(grpc_port) == {"asana": False}
    shoot(page, shown, evidence, "16-catalog-disconnected")

    # Both provider failures stay distinct and preserve their exact guidance.
    for mode, name in (
        ("signed_out", "17-catalog-signed-out"),
        ("unreachable", "18-catalog-unreachable"),
    ):
        service.mode = mode
        page.reload()
        panel = open_integrations(page)
        row = panel.locator('.integration-row[data-integration="asana"]')
        status = json.loads(ava_connect(grpc_port, "--json"))[0]
        expect(row.get_by_role("alert")).to_have_text(status["provider_error"])
        shoot(page, shown, evidence, name)
    service.mode = "ready"
    page.reload()
    panel = open_integrations(page)
    row = panel.locator('.integration-row[data-integration="asana"]')
    expect(row.get_by_role("alert")).to_have_count(0)
    catalog_login(page, row, shown, evidence, "19-catalog-connect-before-restart")


def after_restart(
    page: Page,
    url: str,
    shown: Shown,
    evidence: Path,
    *,
    catalog: bool = False,
) -> None:
    """The account connected in the browser was stored: a new operator has it."""
    page.goto(url)
    panel = open_integrations(page)
    if catalog:
        row = panel.locator('.integration-row[data-integration="asana"]')
        expect(row).to_have_attribute("data-account", "connected")
        expect(row).to_contain_text("Asana proof")
        shoot(page, shown, evidence, "20-catalog-after-restart")
        return
    for name in ("acme-crm", "kb"):
        expect(panel.locator(f'.integration-row[data-integration="{name}"]')).to_have_attribute(
            "data-account", "connected"
        )
    expect(panel.locator('.integration-row[data-integration="acme-crm-eu"]')).to_have_attribute(
        "data-account", "missing"
    )
    shoot(page, shown, evidence, "07-after-restart")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence: Path = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    state_db = evidence / "state.db"
    log = evidence / "operator.log"
    journal = evidence / "journal.txt"
    kb_journal = evidence / "kb.jsonl"
    for stale in (
        state_db,
        log,
        journal,
        kb_journal,
        evidence / "catalog.db",
        evidence / "provider.jsonl",
    ):
        stale.unlink(missing_ok=True)
    grpc_port, web_port = free_port(), free_port()
    crm = CrmService()
    kb = KbServer(kb_journal)
    catalog_kb = KbServer(evidence / "catalog-kb.jsonl", key=CATALOG_PASS)
    catalog = CatalogService(catalog_kb, evidence / "provider.jsonl")
    provider_path = installed_catalog_provider()
    catalog_fixture = PACKAGE / "test/fixtures/catalog_tools_flow.py"
    catalog_db = evidence / "catalog.db"
    catalog_environment = {
        "PYTHONPATH": os.pathsep.join(
            filter(None, [provider_path, os.environ.get("PYTHONPATH")])
        ),
        "CATALOG_PROOF_URL": catalog.url,
        "AVALANCHE_INTEGRATION_PROVIDER": "proof",
    }
    errors: list[str] = []
    outside: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.context.on(
                "request",
                lambda request: (
                    outside.append(request.url)
                    if urlsplit(request.url).hostname not in {"127.0.0.1", "localhost", "::1"}
                    and not request.url.startswith(("data:", "blob:"))
                    else None
                ),
            )
            with operator(state_db, grpc_port, web_port, crm.url, kb.url, log) as url:
                shown = scenario(page, url, evidence, crm, kb, grpc_port, journal)
            with operator(state_db, grpc_port, web_port, crm.url, kb.url, log) as url:
                after_restart(page, url, shown, evidence)
            # No plug-in in this operator's import path: display the SDK's setup
            # message verbatim. Installing the proof package does not alter it.
            with operator(
                catalog_db, grpc_port, web_port, crm.url, kb.url, log, fixture=catalog_fixture
            ) as url:
                page.goto(url)
                panel = open_integrations(page)
                row = panel.locator('.integration-row[data-integration="asana"]')
                message = json.loads(ava_connect(grpc_port, "--json"))[0]["provider_error"]
                assert "No integration provider is installed" in message
                expect(row.get_by_role("alert")).to_have_text(message)
                shoot(page, shown, evidence, "21-catalog-no-provider")
            with operator(
                catalog_db,
                grpc_port,
                web_port,
                crm.url,
                kb.url,
                log,
                fixture=catalog_fixture,
                environment=catalog_environment,
            ) as url:
                catalog_scenario(page, url, shown, evidence, catalog, grpc_port)
            with operator(
                catalog_db,
                grpc_port,
                web_port,
                crm.url,
                kb.url,
                log,
                fixture=catalog_fixture,
                environment=catalog_environment,
            ) as url:
                after_restart(page, url, shown, evidence, catalog=True)
                assert stored_accounts(grpc_port) == {"asana": True}
            browser.close()
        assert errors == [], errors
        assert outside == [], outside
        with sqlite3.connect(catalog_db) as database:
            assert database.execute("SELECT COUNT(*) FROM integration_accounts").fetchone() == (
                0,
            )
        # The keys are in the account table and nowhere else: not in anything the
        # browser was given, the operator printed, or the operator stored.
        for text in (
            *shown.captures,
            log.read_text(),
            stored_text_outside_accounts(state_db),
            stored_text_outside_accounts(catalog_db),
            catalog.journal.read_text(),
        ):
            assert US_KEY not in text
            assert EU_KEY not in text
            assert KB_KEY not in text
            assert CATALOG_PASS not in text
        # Unlike the custom MCP account key, the catalog pass belongs nowhere
        # in the evidence, including either SQLite database.
        for artifact in evidence.rglob("*"):
            if artifact.is_file():
                assert CATALOG_PASS.encode() not in artifact.read_bytes(), artifact
        result = {
            "status": "passed",
            "non_localhost_requests": outside,
            "catalog_logins": len(catalog.logins),
            "catalog_connected_after_restart": catalog.connected,
            "catalog_credentials_stored": 0,
            "crm_requests": crm.requests,
            "kb_calls": kb.entries("call"),
            "screenshots": sorted(path.name for path in evidence.glob("[0-9][0-9]-*.png")),
        }
    finally:
        crm.close()
        catalog.close()
        catalog_kb.close()
        kb.close()
    (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
