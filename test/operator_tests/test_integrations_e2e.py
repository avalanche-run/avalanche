"""Integrations end to end on a real local operator.

Three scenarios, each with a separate operator process, real spawned run
workers, the real gRPC API and ``ava connect`` CLI. The first follows a
Connector's accounts against an HTTP service that checks the API key. The
second follows MCP servers, one remote and one local process, into agent steps
on PredictRLM, on the Claude CLI and on the OpenAI Agents SDK. The third
connects a Connector and two MCP servers through the browser against a login
service that speaks OAuth, then follows the tokens through expiry, refresh and
revocation.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import parse_qs, urlsplit

import grpc
import pytest

from avalanche.integrations import (
    ConnectorDeclaration,
    IntegrationNotConnectedError,
    MCPDeclaration,
)
from runtime.operator.client import GrpcStateProvider, OperatorCallError
from runtime.operator.models import NodeStatus, RunState, RunStatus, WorkflowInfo
from runtime.operator.proto import operator_pb2 as pb
from runtime.operator.proto import operator_pb2_grpc as pb_grpc

from .scripted_model_apis import ScriptedModelApis

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_FIXTURE = _FIXTURES / "integrations_flow.py"
_MCP_FIXTURE = _FIXTURES / "mcp_tools_flow.py"
_MCP_SERVER = _FIXTURES / "mcp_test_server.py"
_LOGIN_FIXTURE = _FIXTURES / "browser_login_flow.py"
_LOGIN_SERVER = _FIXTURES / "login_test_server.py"
# The app a Connector's owner registered with the login service, and its secret.
_APP_CLIENT_ID = "acme-calendar-app"
_APP_CLIENT_SECRET = "app-secret-4d1f8a02c7b94e63"
_US_KEY = "us-key-7f3c9a51d2e84b06"
_EU_KEY = "eu-key-b81d04c6a97f4e23"
_KB_KEY = "kb-key-52c0e7a3f91d4b68"
_REGIONS = {_US_KEY: "us", _EU_KEY: "eu"}
_TERMINAL = {"succeeded", "failed", "cancelled"}

# Workflow files with one integration mistake each; discovery must refuse them.
_CONNECTOR = """import avalanche as ava


class AcmeCrm(ava.Connector):
    name = "acme-crm"
    auth = ava.ApiKey(header="X-Api-Key")
    base_url = "https://crm.invalid"

    @ava.tool
    def find_customer(self, email: str) -> str:
        return self.client.get("/customers", params={"email": email}).text


class Ask(ava.Signature):
    question: str = ava.InputField()
    answer: str = ava.OutputField()

"""
_MISTAKES = {
    "own_tool_clash.py": """
def acme_crm__find_customer(email: str) -> str:
    return email


@ava.agent_step(Ask, tools=[acme_crm__find_customer], integrations=[AcmeCrm()])
async def ask(*, agent: ava.Agent) -> str:
    return (await agent(question="who?")).outputs.answer


@ava.workflow
def own_tool_clash():
    return ask()
""",
    "integration_clash.py": """
@ava.agent_step(Ask, integrations=[AcmeCrm("billing-eu"), AcmeCrm("billing_eu")])
async def ask(*, agent: ava.Agent) -> str:
    return (await agent(question="who?")).outputs.answer


@ava.workflow
def integration_clash():
    return ask()
""",
    "client_outside_step.py": """
customers = AcmeCrm().client.get("/customers")


@ava.workflow
def client_outside_step():
    pass
""",
}


class _CrmService:
    """A stand-in CRM that answers only requests carrying a known API key."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        # Every request that reached the service, and those that carried a key.
        self.hits = 0
        self.keyed_redirects = 0
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                url = urlsplit(self.path)
                service.hits += 1
                region = _REGIONS.get(self.headers.get("X-Api-Key", ""))
                if url.path == "/moved":
                    service.keyed_redirects += region is not None
                    self.send_response(302)
                    self.send_header("Location", parse_qs(url.query)["to"][0])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                email = parse_qs(url.query).get("email", [""])[0]
                service.requests.append((region or "refused", email))
                body = json.dumps({"email": email, "region": region}).encode()
                self.send_response(200 if region is not None else 401)
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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def _operator(
    state_db: Path,
    address: str,
    environment: dict[str, str],
    log: Path,
    fixture: Path = _FIXTURE,
    *options: str,
) -> Iterator[GrpcStateProvider]:
    """Run ``ava operator`` on a fixture until the block ends."""
    port = address.rsplit(":", 1)[1]
    command = [
        sys.executable, "-m", "ava_cli", "operator", str(fixture),
        "--port", port, "--webhook-port", "0", "--no-web", "--state-db", str(state_db),
        *options,
    ]  # fmt: skip
    with log.open("a") as output:
        # The operator keeps a discovery cache in its working directory. The
        # fixtures read test addresses from the environment, so each test's
        # operators get a directory of their own, next to the state database.
        process = subprocess.Popen(
            command,
            stdout=output,
            stderr=output,
            env={**os.environ, **environment},
            cwd=state_db.parent,
        )
        provider = GrpcStateProvider(address, unary_timeout=30)
        try:
            deadline = time.monotonic() + 60
            while True:
                try:
                    provider.list_integrations()
                    break
                except OperatorCallError:
                    assert process.poll() is None, log.read_text()
                    assert time.monotonic() < deadline, log.read_text()
                    time.sleep(0.1)
            yield provider
        finally:
            provider.close()
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


class _McpServer:
    """The MCP test server as a separate process that serves HTTP and wants a key."""

    def __init__(
        self,
        journal: Path,
        key: str,
        expired: Path | None = None,
        *,
        server: Path = _MCP_SERVER,
        marker: str = "",
    ) -> None:
        self.journal = journal
        port = _free_port()
        self.url = f"http://127.0.0.1:{port}/mcp"
        self._process = subprocess.Popen(
            [sys.executable, str(server), "--http", str(port), key],
            env={
                **os.environ,
                "MCP_TEST_SERVER_JOURNAL": str(journal),
                "CATALOG_ACCOUNT_MARKER": marker,
                **({"MCP_TEST_EXPIRED_PASS": str(expired)} if expired is not None else {}),
            },
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

    def entries(self, event: str) -> list[dict]:
        """What the test servers, remote and local, wrote to the journal for ``event``."""
        if not self.journal.exists():
            return []
        entries = [json.loads(line) for line in self.journal.read_text().splitlines()]
        return [entry for entry in entries if entry["event"] == event]

    def close(self) -> None:
        self._process.terminate()
        try:
            self._process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)


def _connect(
    address: str, *arguments: str, key: str | None = None, browser: Path | None = None
):
    """Run ``ava connect``; ``key`` is what a person would type, one answer per line."""
    return subprocess.run(
        [sys.executable, "-m", "ava_cli", "connect", *arguments, "--connect", address],
        input=None if key is None else key + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **({} if browser is None else {"BROWSER": str(browser)})},
    )


def _accounts(address: str) -> dict[str, bool]:
    listed = _connect(address, "--json")
    assert listed.returncode == 0, listed.stderr
    return {row["name"]: row["connected"] for row in json.loads(listed.stdout)}


def _finished(
    provider: GrpcStateProvider,
    flow: str,
    journal: Path,
    input: dict[str, str] | None = None,
) -> RunState:
    run_id = provider.start_run(
        flow, input=input or {"email": "ada@example.com", "journal": str(journal)}
    )
    return _ended(provider, run_id)


def _ended(provider: GrpcStateProvider, run_id: str) -> RunState:
    # Poll the bounded snapshot; the full run is read once, after it has ended.
    with grpc.insecure_channel(provider.connection_label) as channel:
        stub = pb_grpc.OperatorServiceV2Stub(channel)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            request = pb.GetRunSnapshotRequestV2(run_id=run_id)
            snapshot = stub.GetRunSnapshot(request, timeout=30)
            if snapshot.summary.status in _TERMINAL:
                run = provider.get_run(run_id)
                assert run is not None
                return run
            time.sleep(0.05)
    pytest.fail(f"Run {run_id} did not finish")


def _stored_text_outside_accounts(state_db: Path) -> str:
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


def test_connector_accounts_from_connect_to_agent_and_plain_steps(tmp_path):
    crm = _CrmService()
    other = _CrmService()
    kb = _McpServer(tmp_path / "kb.jsonl", _KB_KEY)
    environment = {"ACME_CRM_URL": crm.url, "ACME_KB_URL": kb.url}
    state_db = tmp_path / "state.db"
    log = tmp_path / "operator.log"
    journal = tmp_path / "journal.txt"
    address = f"127.0.0.1:{_free_port()}"
    mistakes = tmp_path / "mistakes"
    mistakes.mkdir()
    for file_name, body in _MISTAKES.items():
        (mistakes / file_name).write_text(_CONNECTOR + body)
    shown: list[str] = []
    try:
        # A tool name used twice, and a client read outside a running step, are
        # refused when the workflow is defined: the operator reports each file
        # and does not start.
        refused_start = subprocess.run(
            [
                sys.executable,
                "-m",
                "ava_cli",
                "operator",
                str(mistakes),
                "--port",
                address.rsplit(":", 1)[1],
                "--webhook-port",
                "0",
                "--no-web",
                "--state-db",
                str(tmp_path / "mistakes.db"),
            ],  # fmt: skip
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert refused_start.returncode != 0
        refusals = {
            file_name: next(
                line for line in refused_start.stderr.splitlines() if file_name in line
            )
            for file_name in _MISTAKES
        }
        assert "acme_crm__find_customer" in refusals["own_tool_clash.py"]
        assert "billing-eu" in refusals["integration_clash.py"]
        assert "billing_eu" in refusals["integration_clash.py"]
        assert "running step" in refusals["client_outside_step.py"]

        with _operator(state_db, address, environment, log) as provider:
            # The definition says which integrations each step declares, read
            # without running a step, calling the service or holding an account.
            flow = next(
                item for item in provider.list_workflows() if item.name == "customer_regions"
            )
            declared = {
                node_id: [item.name for item in step.integrations]
                for node_id, step in flow.integration_declarations.items()
            }
            assert declared == {
                "lookup_1": ["acme-crm", "acme-crm-eu"],
                "triage_1": ["acme-crm", "kb"],
            }
            connector, server = flow.integration_declarations["triage_1"].integrations
            assert isinstance(connector, ConnectorDeclaration)
            assert (connector.display_name, connector.icon) == ("Acme CRM", "assets/acme.svg")
            assert [tool.name for tool in connector.tools] == [
                "acme_crm__fetch",
                "acme_crm__find_customer",
            ]
            assert isinstance(server, MCPDeclaration)
            assert (server.url, server.allow) == (kb.url, ("search",))
            assert _accounts(address) == {"acme-crm": False, "acme-crm-eu": False, "kb": False}
            assert crm.requests == []
            # Declaring and listing an MCP server sent it nothing.
            assert kb.entries("request") == []

            # No account: each kind of step fails before its own code runs, and the
            # error names the command that connects the account.
            for name, node_id in (("customer_regions", "lookup_1"), ("agent_only", "triage_1")):
                refused = _finished(provider, name, journal)
                assert refused.status is RunStatus.FAILED
                node = refused.nodes[node_id]
                assert node.status is NodeStatus.FAILED
                assert "ava connect acme-crm" in node.error
                shown.append(node.error)
            assert not journal.exists()
            assert crm.requests == []

            # Only a declared integration can be connected, and a Connector's key
            # goes where its class says.
            unknown = _connect(address, "acme-crn", key=_US_KEY)
            assert unknown.returncode == 1
            assert "acme-crm" in unknown.stderr
            misplaced = _connect(address, "acme-crm", "--header", "X-Other", key=_US_KEY)
            assert misplaced.returncode == 1
            for name, key in (("acme-crm", _US_KEY), ("acme-crm-eu", _EU_KEY)):
                connected = _connect(address, name, key=key)
                assert connected.returncode == 0, connected.stderr
                shown += [connected.stdout, connected.stderr]
            shown += [unknown.stdout, unknown.stderr, misplaced.stdout, misplaced.stderr]
            assert _accounts(address) == {"acme-crm": True, "acme-crm-eu": True, "kb": False}

            # The MCP server says it wants a login when the step connects to it.
            # With no account for it, the step fails before its own code runs
            # and names the command, like a Connector without an account.
            refused = _finished(provider, "agent_only", journal)
            assert refused.status is RunStatus.FAILED
            assert "ava connect kb" in refused.nodes["triage_1"].error
            assert not journal.exists()
            assert kb.entries("request") and not any(
                request["authorized"] for request in kb.entries("request")
            )
            shown.append(refused.nodes["triage_1"].error)
            connected = _connect(address, "kb", "--api-key", key=_KB_KEY)
            assert connected.returncode == 0, connected.stderr
            shown += [connected.stdout, connected.stderr]
            assert _accounts(address) == {"acme-crm": True, "acme-crm-eu": True, "kb": True}

        # The accounts were connected before this operator process existed.
        with _operator(state_db, address, environment, log) as provider:
            assert _accounts(address) == {"acme-crm": True, "acme-crm-eu": True, "kb": True}
            run = _finished(provider, "customer_regions", journal)
            assert run.status is RunStatus.SUCCESS, [node.error for node in run.nodes.values()]
            # One Connector class, two names, two accounts in the same step; the
            # agent reached the same Connector through its tool.
            result = provider.get_run_result(run.run_id)
            assert result == {"us": "us", "eu": "eu", "agent": "us"}
            assert journal.read_text().splitlines() == ["lookup started", "triage started"]
            assert crm.requests == [
                ("us", "ada@example.com"),
                ("eu", "ada@example.com"),
                # The agent's own tool tidied the address before the Connector's.
                ("us", "ada@example.com"),
            ]
            # The agent also searched the MCP server, with the region it had found.
            assert [(call["tool"], call["query"]) for call in kb.entries("call")] == [
                ("search", "us")
            ]
            shown.append(repr(run))

            # The client calls its own service only. Another origin is refused
            # before anything is sent, as an absolute URL and as a redirect the
            # real service answers with, so the key never leaves for it.
            other_url = other.url.replace("127.0.0.1", "localhost")
            strayed = _finished(provider, "elsewhere", journal, {"other_url": other_url})
            assert strayed.status is RunStatus.SUCCESS
            outcomes = provider.get_run_result(strayed.run_id)
            assert set(outcomes) == {"absolute", "redirect"}
            assert all(outcome.startswith("refused") for outcome in outcomes.values())
            assert crm.keyed_redirects == 1
            assert other.hits == 0
            shown.append(repr(strayed))
            # The CLI exposes the existing disconnect operation for both older kinds.
            for name in ("acme-crm", "kb"):
                disconnected = subprocess.run(
                    [sys.executable, "-m", "ava_cli", "disconnect", name, "--connect", address],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert disconnected.returncode == 0, disconnected.stderr
                assert not next(
                    row for row in provider.list_integrations() if row.name == name
                ).connected

        # The agent's call is in the stored trace under its tool name. The keys
        # are in the account table and nowhere else the operator wrote or printed.
        stored = _stored_text_outside_accounts(state_db)
        assert '"kind":"tool.started"' in stored
        assert '"name":"acme_crm__find_customer"' in stored
        assert '"name":"kb__search"' in stored
        for text in (stored, log.read_text(), *shown):
            assert _US_KEY not in text
            assert _EU_KEY not in text
            assert _KB_KEY not in text
    finally:
        crm.close()
        other.close()
        kb.close()


def _running(pid: int) -> bool:
    """Whether a process is still running; one that ended and awaits its parent is not."""
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _run_process(pid: int) -> int:
    """The run's process that a local server descends from: the operator's child."""

    def ps(field: str, of: int) -> str:
        return subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(of)], capture_output=True, text=True, check=True
        ).stdout.strip()

    while True:
        parent = int(ps("ppid", pid))
        if " ava_cli operator " in ps("args", parent):
            return pid
        pid = parent


def _local_servers_stopped(servers: _McpServer, *, started: int) -> None:
    """Every local server process the steps started so far is gone."""
    pids = [
        entry["pid"] for entry in servers.entries("started") if entry["transport"] != ["--http"]
    ]
    assert len(pids) == started, pids
    deadline = time.monotonic() + 20
    while any(_running(pid) for pid in pids):
        assert time.monotonic() < deadline, f"local MCP servers still running among {pids}"
        time.sleep(0.1)


def _mcp_calls() -> list[tuple[str, dict]]:
    """The calls every agent of the fixture makes, read from the fixture itself."""
    spec = importlib.util.spec_from_file_location("mcp_tools_flow_calls", _MCP_FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CALLS


def test_mcp_tools_reach_agents_on_every_runtime_and_end_with_the_step(tmp_path):
    # The Claude and OpenAI runtimes are optional extras.
    for package in ("claude_agent_sdk", "agents"):
        pytest.importorskip(package)
    servers = _McpServer(tmp_path / "servers.jsonl", _KB_KEY)
    models = ScriptedModelApis(_mcp_calls(), answer_field="findings")
    state_db = tmp_path / "state.db"
    log = tmp_path / "operator.log"
    journal = tmp_path / "journal.txt"
    address = f"127.0.0.1:{_free_port()}"
    environment = {
        "MCP_KB_URL": servers.url,
        # Nothing listens here.
        "MCP_DOWN_URL": f"http://127.0.0.1:{_free_port()}/mcp",
        # The local server processes write to the same journal as the remote one.
        "MCP_TEST_SERVER_JOURNAL": str(servers.journal),
        # The Claude CLI and the OpenAI SDK reach the scripted model through these.
        "ANTHROPIC_BASE_URL": models.url,
        "ANTHROPIC_API_KEY": "scripted-model",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "OPENAI_BASE_URL": f"{models.url}/v1",
        "OPENAI_API_KEY": "scripted-model",
    }
    topic = {"journal": str(journal)}
    shown: list[str] = []
    try:
        with _operator(state_db, address, environment, log, _MCP_FIXTURE) as provider:
            # Declaring and listing start no process and call no server.
            provider.list_workflows()
            assert _accounts(address) == dict.fromkeys(
                ("crashing", "down", "ghost", "kb", "lingering-kb", "local-kb", "strict-kb"),
                False,
            )
            assert servers.entries("request") == []
            assert [entry["transport"] for entry in servers.entries("started")] == [["--http"]]

            # A local process has no request header to carry a key.
            refused = _connect(address, "local-kb", key=_KB_KEY)
            assert refused.returncode == 1
            assert "local process" in refused.stderr
            connected = _connect(address, "kb", "--api-key", key=_KB_KEY)
            assert connected.returncode == 0, connected.stderr
            shown += [refused.stdout, refused.stderr, connected.stdout, connected.stderr]

            # The same step on each runtime: a call to the remote server, a call
            # to the local process, a tool that fails and arguments the tool's
            # schema refuses. Every result and every failure reached the model.
            for started, runtime in enumerate(("predict_rlm", "claude", "openai"), start=1):
                run = _finished(provider, f"on_{runtime}", journal, topic)
                errors = [node.error for node in run.nodes.values()]
                assert run.status is RunStatus.SUCCESS, (runtime, errors, log.read_text())
                remote, local, failed, invalid, period, pong = provider.get_run_result(
                    run.run_id
                )
                assert "refunds-0" in remote and "refunds-1" in remote, runtime
                assert "returns-0" in local, runtime
                assert "the archive is offline (maintenance)" in failed, runtime
                assert "'query' is a required property" in invalid, runtime
                # An argument named ``from``, and a tool whose schema names no argument.
                assert "monday..now" in period, runtime
                assert "pong" in pong, runtime
                # The connection and the process ended with the step.
                _local_servers_stopped(servers, started=started)
                shown.append(repr(run))
            assert journal.read_text().splitlines() == [
                "predict_rlm started",
                "claude started",
                "openai started",
            ]
            # Each runtime made the same calls; the refused one never left.
            calls = [
                (call["tool"], call.get("query") or call.get("reason") or call.get("period"))
                for call in servers.entries("call")
            ]
            assert calls == 3 * [
                ("search", "refunds"),
                ("search", "returns"),
                ("broken", "maintenance"),
                ("window", {"from": "monday", "until": "now"}),
                ("ping", None),
            ]
            assert all(request["authorized"] for request in servers.entries("request"))

            # Only allowed tools were offered, under one name on every runtime.
            allowed = {
                "kb__search",
                "kb__broken",
                "kb__window",
                "local_kb__search",
                "local_kb__wait",
                "local_kb__ping",
            }
            assert set(models.offered["openai"]) == allowed
            claude_tools = {
                name.removeprefix("mcp__avalanche__")
                for name in models.offered["anthropic"]
                if name.startswith("mcp__avalanche__")
            }
            assert claude_tools == allowed

            # A server that cannot be used fails the step when it starts, with the
            # reason, before the step's own code; the local process started
            # beside it is stopped. The unreachable one is tried on each retry.
            for flow, reasons, local_starts in (
                ("unreachable", ("'down'", environment["MCP_DOWN_URL"]), 3),
                ("missing_program", ("avalanche-no-such-mcp-server", "not found"), 1),
                ("stops", ("'crashing'", "the index file is missing"), 1),
                ("allow_unknown", ("'strict-kb'", "summarize"), 2),
            ):
                asked = time.monotonic()
                run = _finished(provider, flow, journal, topic)
                assert time.monotonic() - asked < 60, flow
                assert run.status is RunStatus.FAILED, flow
                (error,) = [node.error for node in run.nodes.values() if node.error]
                for reason in reasons:
                    assert reason in error, (flow, error)
                started += local_starts
                _local_servers_stopped(servers, started=started)
                shown.append(error)
            assert len(journal.read_text().splitlines()) == 3

            # A run cancelled while its agent waits on a tool: the operator stops
            # the run's process, which cannot close anything. This local server
            # ignores its closed input, and still does not outlive the run.
            run_id = provider.start_run("waits", input=topic)
            deadline = time.monotonic() + 60
            while not any(call["tool"] == "wait" for call in servers.entries("call")):
                assert time.monotonic() < deadline, log.read_text()
                time.sleep(0.05)
            provider.cancel_run(run_id)
            assert _ended(provider, run_id).status is RunStatus.CANCELLED
            _local_servers_stopped(servers, started=started + 1)

            # The same when the run's process is killed outright.
            run_id = provider.start_run("waits", input=topic)
            deadline = time.monotonic() + 60
            while len([call for call in servers.entries("call") if call["tool"] == "wait"]) < 2:
                assert time.monotonic() < deadline, log.read_text()
                time.sleep(0.05)
            os.kill(_run_process(servers.entries("started")[-1]["pid"]), signal.SIGKILL)
            assert _ended(provider, run_id).status is RunStatus.FAILED
            _local_servers_stopped(servers, started=started + 2)

        # Calls and failures are in the stored traces under the tools' names; a
        # tool outside allow= is nowhere, and neither is the key.
        stored = _stored_text_outside_accounts(state_db)
        for name in ("kb__search", "local_kb__search", "kb__broken"):
            assert f'"name":"{name}"' in stored
        assert "the archive is offline (maintenance)" in stored
        assert "delete_everything" not in stored
        assert not any(call["tool"] == "delete-everything" for call in servers.entries("call"))
        for text in (stored, log.read_text(), *shown):
            assert _KB_KEY not in text
    finally:
        servers.close()
        models.close()


class _LoginService:
    """The login test server as a separate process: a login service, an MCP server, an API."""

    def __init__(self, journal: Path, redirect_uri: str, *options: str) -> None:
        self.journal = journal
        port = _free_port()
        self.url = f"http://127.0.0.1:{port}"
        self._process = subprocess.Popen(
            [
                sys.executable,
                str(_LOGIN_SERVER),
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

    def entries(self, event: str, **fields: object) -> list[dict]:
        if not self.journal.exists():
            return []
        entries = [json.loads(line) for line in self.journal.read_text().splitlines()]
        return [
            entry
            for entry in entries
            if entry["event"] == event
            and all(entry.get(name) == value for name, value in fields.items())
        ]

    def secrets(self) -> set[str]:
        """Every one-time code and token the service handed out."""
        return {
            entry[field]
            for event in ("consent", "tokens")
            for entry in self.entries(event)
            for field in ("code", "access_token", "refresh_token")
            if field in entry
        }

    def admin(self, action: str) -> None:
        request = urllib.request.Request(f"{self.url}/admin/{action}", method="POST")
        urllib.request.urlopen(request, timeout=30).close()

    def api_status(self, access_token: str) -> int:
        """What the service's API answers to a token, asked directly."""
        request = urllib.request.Request(
            f"{self.url}/crm/customers?email=probe@example.com",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status
        except urllib.error.HTTPError as refused:
            return refused.code

    def close(self) -> None:
        self._process.terminate()
        try:
            self._process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)


def _status(url: str) -> int | None:
    """The status of a GET, redirects followed; ``None`` when nothing listens."""
    try:
        with urllib.request.urlopen(url, timeout=90) as response:
            return response.status
    except urllib.error.HTTPError as refused:
        return refused.code
    except urllib.error.URLError as closed:
        assert isinstance(closed.reason, ConnectionRefusedError), closed
        return None


def _other_address() -> str | None:
    """An address of this machine that is not the loopback one, if it has a route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # Connecting a datagram socket sends nothing; it only picks a route.
            probe.connect(("192.0.2.1", 9))
        except OSError:
            return None
        return probe.getsockname()[0]


def test_browser_logins_refresh_and_fail_when_revoked(tmp_path):
    login_port = _free_port()
    callback = f"http://127.0.0.1:{login_port}/callback"
    service = _LoginService(tmp_path / "service.jsonl", callback)
    # A second login service that does not let a client register itself.
    locked = _LoginService(tmp_path / "locked.jsonl", callback, "--no-registration")
    environment = {"LOGIN_SERVICE_URL": service.url, "LOCKED_LOGIN_SERVICE_URL": locked.url}
    state_db = tmp_path / "state.db"
    log = tmp_path / "operator.log"
    journal = tmp_path / "journal.txt"
    address = f"127.0.0.1:{_free_port()}"
    options = ("--login-port", str(login_port))
    # The person's browser: it opens the consent page and follows it back.
    browser = tmp_path / "browser"
    browser.write_text(
        f"#!{sys.executable}\n"
        "import sys, urllib.request\n"
        "urllib.request.urlopen(sys.argv[1], timeout=90).read()\n"
    )
    browser.chmod(0o755)
    app = f"{_APP_CLIENT_ID}\n{_APP_CLIENT_SECRET}"
    shown: list[str] = []

    def started(line: str) -> int:
        return journal.read_text().splitlines().count(line) if journal.exists() else 0

    def refreshes(source: _LoginService, client_id: str | None = None) -> int:
        fields = {} if client_id is None else {"client_id": client_id}
        return len(source.entries("tokens", grant="refresh_token", **fields))

    try:
        # The first access tokens live two seconds, so they expire during the test.
        service.admin("lifetime?seconds=2")
        locked.admin("lifetime?seconds=2")
        with _operator(
            state_db, address, environment, log, _LOGIN_FIXTURE, *options
        ) as provider:
            listed = _connect(address, "--json")
            assert listed.returncode == 0, listed.stderr
            rows = {row["name"]: row for row in json.loads(listed.stdout)}
            assert {name: row["connected"] for name, row in rows.items()} == {
                "acme-calendar": False,
                "archive": False,
                "notes": False,
            }
            # A Connector declares its login; an MCP server takes either kind.
            assert rows["acme-calendar"]["login"] == ["browser"]
            assert rows["notes"]["login"] == ["browser", "api_key"]
            flow = next(
                item for item in provider.list_workflows() if item.name == "notes_search"
            )
            declared = flow.integration_declarations["find_1"].integrations[1]
            assert isinstance(declared, MCPDeclaration)
            assert declared.client_id == "avalanche-at-notes"
            # Declaring and listing reached no login service.
            assert not service.journal.exists() and not locked.journal.exists()

            # No account yet: both kinds of step fail before their own code.
            for name, node_id, account in (
                ("calendar_region", "lookup_1", "acme-calendar"),
                ("notes_search", "find_1", "notes"),
            ):
                refused = _finished(provider, name, journal)
                assert refused.status is RunStatus.FAILED
                assert f"ava connect {account}" in refused.nodes[node_id].error
                shown.append(refused.nodes[node_id].error)
            assert not journal.exists()

            # A login waits for the person. Its consent page carries a state and
            # a PKCE challenge, and the listener takes only that login's return.
            waiting = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "ava_cli",
                    "connect",
                    "notes",
                    "--no-browser",
                    "--connect",
                    address,
                ],  # fmt: skip
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert waiting.stderr is not None
            consent_page = ""
            while not consent_page.startswith("http"):
                consent_page = waiting.stderr.readline().strip()
                assert consent_page or waiting.poll() is None, "ava connect ended early"
            asked = parse_qs(urlsplit(consent_page).query)
            assert asked["code_challenge_method"] == ["S256"] and asked["code_challenge"]
            assert asked["redirect_uri"] == [callback]
            (state,) = asked["state"]
            assert _status(f"{callback}?code=stolen&state=not-{state}") == 400
            assert _status(f"{callback}?code=stolen") == 400
            assert _status(f"http://127.0.0.1:{login_port}/?code=stolen&state={state}") == 400
            other_address = _other_address()
            if other_address is not None:
                # The listener is on the loopback address and on no other.
                assert _status(f"http://{other_address}:{login_port}/callback") is None
            assert waiting.poll() is None
            assert _accounts(address)["notes"] is False
            assert service.entries("tokens") == []

            # The person agrees. The service sends the browser back with the code,
            # and the operator exchanges it: the service checks the PKCE verifier.
            with urllib.request.urlopen(consent_page, timeout=90) as page:
                assert page.status == 200
                returned_to = page.url
            assert returned_to.startswith(callback)
            out, err = waiting.communicate(timeout=60)
            assert waiting.returncode == 0, err
            shown += [consent_page, out, err]
            assert _accounts(address)["notes"] is True
            assert len(service.entries("registered")) == 1
            assert len(service.entries("tokens", grant="authorization_code")) == 1
            # The listener is gone once no login waits: the same return a second
            # time finds nothing listening.
            assert _status(returned_to) is None
            assert len(service.entries("tokens")) == 1

            # A Connector's login is declared, and the app's client ID and secret
            # are asked for. A wrong secret connects nothing.
            mistaken = _connect(
                address,
                "acme-calendar",
                key=f"{_APP_CLIENT_ID}\nnot-the-secret",
                browser=browser,
            )
            assert mistaken.returncode == 1
            assert _accounts(address)["acme-calendar"] is False
            as_key = _connect(address, "acme-calendar", "--api-key", key="a-key")
            assert as_key.returncode == 1
            connected = _connect(address, "acme-calendar", key=app, browser=browser)
            assert connected.returncode == 0, connected.stderr
            # This server's login service registers nobody: the declared client is used.
            archived = _connect(address, "archive", browser=browser)
            assert archived.returncode == 0, archived.stderr
            assert locked.entries("registered") == []
            assert [entry["client_id"] for entry in locked.entries("tokens")] == [
                "avalanche-at-notes"
            ]
            for result in (mistaken, as_key, connected, archived):
                shown += [result.stdout, result.stderr]
            assert _accounts(address) == {
                "acme-calendar": True,
                "archive": True,
                "notes": True,
            }
            first_tokens = [
                entry["access_token"]
                for source in (service, locked)
                for entry in source.entries("tokens", grant="authorization_code")
            ]
            assert len(first_tokens) == 3

        # The access tokens from the logins have expired by now: the service
        # itself refuses them. New ones will live an hour.
        time.sleep(3)
        assert {service.api_status(token) for token in first_tokens} == {401}
        refused_calls = len(service.entries("api", authorized=False))
        service.admin("lifetime?seconds=3600")
        locked.admin("lifetime?seconds=3600")

        # A new operator process on the same database: the logins are still there,
        # and runs work through a refresh, with nothing for the person to do.
        with _operator(
            state_db, address, environment, log, _LOGIN_FIXTURE, *options
        ) as provider:
            assert _accounts(address) == {
                "acme-calendar": True,
                "archive": True,
                "notes": True,
            }
            looked_up = _finished(provider, "calendar_region", journal)
            assert looked_up.status is RunStatus.SUCCESS, looked_up.nodes["lookup_1"].error
            assert provider.get_run_result(looked_up.run_id)["region"] == "us"
            assert refreshes(service, _APP_CLIENT_ID) == 1
            (served,) = service.entries("api", authorized=True)
            assert served["access_token"] not in first_tokens
            found = _finished(provider, "notes_search", journal)
            assert found.status is RunStatus.SUCCESS, found.nodes["find_1"].error
            assert refreshes(service) == 2 and refreshes(locked) == 1
            assert len(service.entries("call")) == 1 and len(locked.entries("call")) == 1
            shown += [repr(looked_up), repr(found)]

            # A token that is still valid is used as it is.
            for name in ("calendar_region", "notes_search"):
                assert _finished(provider, name, journal).status is RunStatus.SUCCESS
            assert refreshes(service) == 2 and refreshes(locked) == 1
            assert (started("lookup started"), started("find started")) == (2, 2)

            # The person revokes the logins at the service. The MCP server refuses
            # the token when the step connects, before the step's own code.
            service.admin("revoke")
            revoked = _finished(provider, "notes_search", journal)
            assert revoked.status is RunStatus.FAILED
            error = revoked.nodes["find_1"].error
            assert "no longer valid" in error and "ava connect notes" in error
            assert started("find started") == 2
            # The Connector's service refuses its first call; the step is not retried.
            revoked = _finished(provider, "calendar_region", journal)
            assert revoked.status is RunStatus.FAILED
            error = revoked.nodes["lookup_1"].error
            assert "no longer valid" in error and "ava connect acme-calendar" in error
            assert len(service.entries("api", authorized=False)) == refused_calls + 1
            assert started("lookup started") == 3
            shown.append(error)

            # Connecting again replaces the account. This login's token expires at
            # once and its refresh token is revoked, so the refresh fails: the step
            # fails before its own code, and nothing is sent to the service's API.
            service.admin("lifetime?seconds=1")
            again = _connect(address, "acme-calendar", key=app, browser=browser)
            assert again.returncode == 0, again.stderr
            service.admin("revoke-refresh")
            time.sleep(2)
            api_calls = len(service.entries("api"))
            refresh_refusals = len(service.entries("refresh refused"))
            expired = _finished(provider, "calendar_region", journal)
            assert expired.status is RunStatus.FAILED
            error = expired.nodes["lookup_1"].error
            assert "no longer valid" in error and "ava connect acme-calendar" in error
            assert started("lookup started") == 3
            assert len(service.entries("api")) == api_calls
            assert len(service.entries("refresh refused")) == refresh_refusals + 1
            shown += [again.stdout, again.stderr, error]

            # Connecting once more is all it takes to work again.
            service.admin("lifetime?seconds=3600")
            again = _connect(address, "acme-calendar", key=app, browser=browser)
            assert again.returncode == 0, again.stderr
            recovered = _finished(provider, "calendar_region", journal)
            assert recovered.status is RunStatus.SUCCESS, recovered.nodes["lookup_1"].error
            shown += [again.stdout, again.stderr, repr(recovered)]

        # The agent's calls are in the stored trace. The tokens, the one-time codes
        # and the client secret are in the account table and nowhere else the
        # operator wrote or printed.
        stored = _stored_text_outside_accounts(state_db)
        assert '"name":"notes__search"' in stored
        assert '"name":"archive__search"' in stored
        secrets = service.secrets() | locked.secrets() | {_APP_CLIENT_SECRET}
        assert len(secrets) > 20
        for text in (stored, log.read_text(), *shown):
            for secret in secrets:
                assert secret not in text
    finally:
        service.close()
        locked.close()


class _CatalogService:
    """External login/connection service and real MCP relay for the plug-in proof."""

    def __init__(
        self,
        root: Path,
        *,
        integration: str = "asana",
        server: Path = _FIXTURES / "catalog_test_server.py",
    ) -> None:
        self.integration = integration
        self.expired_pass = root / "expired-pass"
        self.mcp = _McpServer(
            root / "catalog-calls.jsonl",
            _KB_KEY,
            self.expired_pass,
            server=server,
        )
        self.mode = "ready"
        self.user: str | None = "user-a"
        self.named: list[dict] = []
        self.named_relays: dict[str, _McpServer] = {}
        self.bound_sessions: list[dict] = []
        self.named_logins: dict[str, dict] = {}
        self.connected = False
        self.connected_at: float | None = None
        self.sessions: list[dict[str, str | float]] = []
        self.connection_checks: list[str] = []
        self.logins: dict[str, str] = {}
        self.journal = root / "provider.jsonl"
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                code = 200
                body = "null"
                if url.path == "/connections":
                    service.connection_checks.append(service.mode)
                if service.mode in {"signed_out", "unreachable"}:
                    code = 401 if service.mode == "signed_out" else 503
                elif url.path == "/entries":
                    body = json.dumps(
                        [
                            {
                                "name": service.integration,
                                "display_name": service.integration.title() + " proof",
                                "icon_url": service.url + "/icon.png",
                            }
                        ]
                    )
                elif url.path == "/user":
                    body = json.dumps(service.user)
                elif url.path == "/named-connections":
                    body = json.dumps(service.named)
                elif url.path == "/connections":
                    body = json.dumps(
                        [
                            {
                                "name": service.integration,
                                "connected_at": service.connected_at,
                                "state": "reconnect_needed"
                                if service.mode == "expired"
                                else "connected",
                            }
                        ]
                        if service.connected
                        else []
                    )
                elif url.path == "/named-login":
                    login_id = str(len(service.logins) + 1)
                    slug, kind = query["slug"][0], query["kind"][0]
                    user = service.user if kind == "per_user" else None
                    service.logins[login_id] = "waiting"
                    service.named_logins[login_id] = {
                        "name": query["name"][0],
                        "slug": slug,
                        "kind": kind,
                        "user": user,
                        "instance_id": f"{slug}:{user}",
                        "state": "connected",
                    }
                    body = service.login_view(login_id)
                elif url.path == "/bound-session":
                    bindings = json.loads(query["bindings"][0])
                    (instance,) = bindings.values()
                    current = next(
                        (
                            row
                            for row in service.named
                            if (row["instance_id"], row["slug"], row["user"], row["state"])
                            == (
                                instance["id"],
                                instance["connection_id"],
                                instance["user_id"],
                                "connected",
                            )
                        ),
                        None,
                    )
                    if current is None:
                        code = 409
                    else:
                        service.bound_sessions.append(bindings)
                        with service.journal.open("a") as journal:
                            journal.write(
                                json.dumps({"event": "bound-session", "bindings": bindings})
                                + "\n"
                            )
                        body = json.dumps(
                            {
                                "url": service.named_relays[instance["id"]].url,
                                "pass_value": _KB_KEY,
                                "expires_at": time.time() + 3600.0,
                            }
                        )
                elif url.path == "/login":
                    login_id = str(len(service.logins) + 1)
                    service.logins[login_id] = "waiting"
                    with service.journal.open("a") as journal:
                        journal.write(json.dumps({"event": "login", "id": login_id}) + "\n")
                    body = service.login_view(login_id)
                elif url.path == "/approve":
                    service.connected = True
                    service.connected_at = time.time()
                    service.logins[query["id"][0]] = "connected"
                    if query["id"][0] in service.named_logins:
                        row = service.named_logins[query["id"][0]]
                        service.named = [
                            existing
                            for existing in service.named
                            if (existing["slug"], existing["user"])
                            != (row["slug"], row["user"])
                        ]
                        service.named.append(row)
                elif url.path == "/status":
                    body = service.login_view(query["id"][0])
                elif url.path == "/disconnect":
                    service.named = [
                        row for row in service.named if row["slug"] != query["name"][0]
                    ]
                    service.connected = False
                    service.connected_at = None
                elif url.path == "/expire-pass":
                    service.expired_pass.touch()
                elif url.path == "/session":
                    entry = {
                        "step": query["step"][0],
                        "names": query["names"][0],
                        "expires_at": time.time() + 3600.0,
                    }
                    service.sessions.append(entry)
                    with service.journal.open("a") as journal:
                        journal.write(json.dumps({"event": "session", **entry}) + "\n")
                    body = json.dumps(
                        {
                            "url": service.mcp.url,
                            "pass_value": _KB_KEY,
                            "expires_at": entry["expires_at"],
                        }
                    )
                else:
                    code = 404
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def login_view(self, login_id: str) -> str:
        return json.dumps(
            {
                "login_id": login_id,
                "name": self.integration,
                "status": "failed" if self.mode == "not_connected" else self.logins[login_id],
                "authorization_url": f"{self.url}/approve?id={login_id}",
                "error": str(
                    IntegrationNotConnectedError(
                        "catalog login", [self.integration], "ava connect"
                    )
                )
                if self.mode == "not_connected"
                else None,
            }
        )

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self.mcp.close()


@pytest.fixture
def catalog_provider_path() -> str:
    """Install the external distribution with uv; never import its implementation."""
    package = Path(__file__).parents[1] / "catalog_provider"
    installed = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--python",
            sys.executable,
            "--with",
            str(package),
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
    return installed.stdout.strip()


def test_catalog_provider_connect_run_restart_and_failures(tmp_path, catalog_provider_path):
    # These are required proof runtimes: missing extras are failures, not skips.
    for package in ("claude_agent_sdk", "agents"):
        assert importlib.util.find_spec(package), f"Install the {package} proof extra"
    fixture = _FIXTURES / "catalog_tools_flow.py"
    calls = [
        (
            "asana__search_tasks_in_workspace",
            {"workspace_gid": "1200000000000001", "assignee": "me", "limit": 2},
        )
    ]
    models = ScriptedModelApis(calls, answer_field="findings")
    service = _CatalogService(tmp_path)
    state_db, log = tmp_path / "catalog.db", tmp_path / "operator.log"
    journal = tmp_path / "body.txt"
    address = f"127.0.0.1:{_free_port()}"
    environment = {
        "PYTHONPATH": os.pathsep.join(
            filter(None, [catalog_provider_path, os.environ.get("PYTHONPATH")])
        ),
        "CATALOG_PROOF_URL": service.url,
        "ANTHROPIC_BASE_URL": models.url,
        "ANTHROPIC_API_KEY": "scripted-model",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "OPENAI_BASE_URL": f"{models.url}/v1",
        "OPENAI_API_KEY": "scripted-model",
    }
    shown: list[str] = []
    try:
        with _operator(state_db, address, environment, log, fixture) as provider:
            (status,) = provider.list_integrations()
            assert status.declaration.kind == "catalog"
            assert status.account_state == "missing" and not status.connected
            assert status.display_name == "Asana proof"
            assert status.icon_url == service.url + "/icon.png"
            for flow in provider.list_workflows():
                for step in flow.integration_declarations.values():
                    assert [(entry.kind, entry.name) for entry in step.integrations] == [
                        ("catalog", "asana")
                    ]
            assert not service.sessions and not service.mcp.entries("request")
            missing = _connect(address)
            assert missing.returncode == 0
            assert "asana" in missing.stdout and "missing" in missing.stdout
            checks = len(service.connection_checks)
            refused = _finished(provider, "on_predict_rlm", journal, {"journal": str(journal)})
            assert refused.status is RunStatus.FAILED
            (node,) = refused.nodes.values()
            assert node.error is not None
            assert node.error.count("no connected account") == 1
            assert "ava connect asana" in node.error
            assert len(service.connection_checks) == checks + 1
            assert not journal.exists()
            shown.append(node.error)
            # A provider can refuse to complete a login when the service account
            # is missing. The CLI preserves the same actionable domain message.
            service.mode = "not_connected"
            refused_login = _connect(address, "asana", "--no-browser")
            assert refused_login.returncode == 1
            assert refused_login.stderr.count("no connected account") == 1
            assert "ava connect asana" in refused_login.stderr
            shown.append(refused_login.stderr)
            service.mode = "ready"
            unsupported = _connect(address, "asana", "--slug", "office", "--no-browser")
            assert unsupported.returncode == 1
            assert unsupported.stderr.splitlines()[-1] == (
                "INVALID_ARGUMENT: This provider does not support named connections."
            )

            # --no-browser prints the address and waits until the real consent URL is opened.
            waiting = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "ava_cli",
                    "connect",
                    "asana",
                    "--no-browser",
                    "--connect",
                    address,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert waiting.stderr is not None
            consent = ""
            while not consent.startswith("http"):
                consent = waiting.stderr.readline().strip()
                assert consent or waiting.poll() is None, "ava connect ended early"
            assert consent.startswith(service.url + "/approve?")
            with urllib.request.urlopen(consent, timeout=30) as response:
                assert response.status == 200
            stdout, stderr = waiting.communicate(timeout=60)
            assert waiting.returncode == 0, stderr
            assert stdout.startswith("Connected asana")
            shown += [stdout, stderr]
            assert provider.list_integrations()[0].account_state == "connected"
            listed = _connect(address, "--json")
            assert listed.returncode == 0 and json.loads(listed.stdout)[0]["connected"]

            # All three agents use the same example unchanged, without an operator restart.
            for count, runtime in enumerate(("predict_rlm", "claude", "openai"), 1):
                run = _finished(provider, f"on_{runtime}", journal, {"journal": str(journal)})
                assert run.status is RunStatus.SUCCESS, (
                    runtime,
                    [node.error for node in run.nodes.values()],
                    log.read_text(),
                )
                (answer,) = provider.get_run_result(run.run_id)
                assert "refunds-0" in answer and "refunds-1" in answer
                assert len(service.sessions) == count
                shown.append(repr(run))
            assert (
                service.mcp.entries("call")
                == [
                    {
                        "event": "call",
                        "tool": "search-tasks-in-workspace",
                        "workspace_gid": "1200000000000001",
                        "assignee": "me",
                        "limit": 2,
                    }
                ]
                * 3
            )
            assert journal.read_text().splitlines() == ["body started"] * 3

            # Definition-time names are sound, but remote tool names are checked at step start.
            clash = _finished(provider, "clash", journal, {"journal": str(journal)})
            assert clash.status is RunStatus.FAILED
            (node,) = clash.nodes.values()
            assert (
                "asana__search_tasks_in_workspace" in node.error
                and "several tools" in node.error
            )
            assert len(journal.read_text().splitlines()) == 3
            assert len(service.sessions) == 4

            # Expired login and removed account both fail before the body, with existing errors.
            for mode, reason in (
                ("expired", "no longer valid"),
                ("removed", "no connected account"),
            ):
                service.mode = mode
                service.connected = mode != "removed"
                checks = len(service.connection_checks)
                failed = _finished(
                    provider, "on_predict_rlm", journal, {"journal": str(journal)}
                )
                assert failed.status is RunStatus.FAILED
                (node,) = failed.nodes.values()
                assert node.error is not None
                assert node.error.count(reason) == 1 and "ava connect asana" in node.error
                assert len(service.connection_checks) == checks + 1
                assert len(journal.read_text().splitlines()) == 3
                assert len(service.sessions) == 4
                shown.append(node.error)
            service.connected = True
            for mode, message in (
                ("signed_out", "The proof provider is not signed in."),
                ("unreachable", "The integration provider is unreachable."),
            ):
                service.mode = mode
                refused = _connect(address, "asana", "--no-browser")
                assert refused.returncode == 1
                assert refused.stderr.count(message) == 1, refused.stderr
                checks = len(service.connection_checks)
                failed = _finished(
                    provider, "on_predict_rlm", journal, {"journal": str(journal)}
                )
                assert failed.status is RunStatus.FAILED
                (node,) = failed.nodes.values()
                assert node.error is not None
                assert node.error.count(message) == 1
                # Login/setup errors are final; outages use the configured two retries.
                attempts = 3 if mode == "unreachable" else 1
                assert len(service.connection_checks) == checks + attempts
                assert len(journal.read_text().splitlines()) == 3
                shown += [refused.stderr, node.error]
            service.mode = "ready"
            expired = _finished(provider, "expired_pass", journal, {"journal": str(journal)})
            assert expired.status is RunStatus.FAILED
            (node,) = expired.nodes.values()
            assert "session expired" in node.error, node.error
            assert len(service.sessions) == 5  # no renewal
            assert service.expired_pass.exists()
            assert len(journal.read_text().splitlines()) == 4
            assert len(service.mcp.entries("call")) == 3
            assert any(not hit["authorized"] for hit in service.mcp.entries("request"))

        # Connections live at the provider, not in operator state, and survive its restart.
        with _operator(state_db, address, environment, log, fixture) as provider:
            assert provider.list_integrations()[0].account_state == "connected"
            disconnected = subprocess.run(
                [sys.executable, "-m", "ava_cli", "disconnect", "asana", "--connect", address],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert disconnected.returncode == 0, disconnected.stderr
            assert provider.list_integrations()[0].account_state == "missing"
            assert not service.connected
        with sqlite3.connect(state_db) as database:
            assert database.execute("SELECT COUNT(*) FROM integration_accounts").fetchone() == (
                0,
            )
        stored = _stored_text_outside_accounts(state_db)
        assert '"name":"asana__search_tasks_in_workspace"' in stored
        for text in (stored, log.read_text(), service.journal.read_text(), *shown):
            assert _KB_KEY not in text
    finally:
        service.close()
        models.close()
    _prove_named_catalog_accounts(tmp_path, catalog_provider_path)


def _prove_named_catalog_accounts(tmp_path: Path, catalog_provider_path: str) -> None:
    """The same installed provider opts in; all calls still cross real gRPC/MCP."""
    root = tmp_path / "named"
    root.mkdir()
    service = _CatalogService(root, integration="slack")
    relays: list[_McpServer] = []
    state_db, log = root / "state.db", root / "operator.log"
    journal = root / "body.txt"
    address = f"127.0.0.1:{_free_port()}"
    fixture = _FIXTURES / "named_catalog_tools_flow.py"
    environment = {
        "PYTHONPATH": os.pathsep.join(
            filter(None, [catalog_provider_path, os.environ.get("PYTHONPATH")])
        ),
        "AVALANCHE_INTEGRATION_PROVIDER": "proof",
        "CATALOG_PROOF_NAMED": "1",
        "CATALOG_PROOF_URL": service.url,
    }

    def slot(slug: str, kind: str = "shared", user: str | None = None) -> dict:
        return {
            "name": "slack",
            "slug": slug,
            "kind": kind,
            "user": user,
            "instance_id": f"{slug}:{user}",
            "state": "connected",
        }

    def refusal(provider: GrpcStateProvider, flow: str, reason: str, run_id: str) -> None:
        before = journal.read_text() if journal.exists() else ""
        sessions = len(service.bound_sessions)
        with pytest.raises(OperatorCallError, match=reason) as error:
            # Caller-controlled context is not a starting identity.
            provider.start_run(
                flow,
                run_id=run_id,
                input={"journal": str(journal)},
                context={"user": "user-a", "created_by": "user-a"},
            )
        if reason == "Connect your slack":
            assert error.value.details == (
                "Connect your slack account ('my-slack') to run flow 'personal', "
                "then start the run again."
            )
        assert error.value.status is grpc.StatusCode.FAILED_PRECONDITION
        assert provider.get_run(run_id) is None
        assert (journal.read_text() if journal.exists() else "") == before
        assert len(service.bound_sessions) == sessions

    completed: dict[str, list[str]] = {}

    def result(provider: GrpcStateProvider, flow: str) -> list[str]:
        run = _finished(provider, flow, journal, {"journal": str(journal)})
        assert run.status is RunStatus.SUCCESS, (
            [node.error for node in run.nodes.values()],
            log.read_text(),
        )
        answers = provider.get_run_result(run.run_id)
        completed[run.run_id] = answers
        return answers

    try:
        browser = root / "browser"
        browser.write_text(
            f"#!{sys.executable}\nimport sys, urllib.request\n"
            "urllib.request.urlopen(sys.argv[1], timeout=30).read()\n"
        )
        browser.chmod(0o755)
        service.named = [
            slot("schedule-slot"),
            {**slot("my-slack", "per_user"), "state": "disconnected"},
        ]
        for marker, instance in (
            ("acme-hq-", "acme-hq:None"),
            ("partner-ws-", "partner-ws:None"),
            ("alice-", "my-slack:user-a"),
            ("bob-", "my-slack:user-b"),
        ):
            relay = _McpServer(
                root / f"{marker}calls.jsonl",
                _KB_KEY,
                server=_FIXTURES / "catalog_test_server.py",
                marker=marker,
            )
            relays.append(relay)
            service.named_relays[instance] = relay
        service.named_relays["schedule-slot:None"] = relays[0]
        with _operator(state_db, address, environment, log, fixture) as provider:
            for slug in ("acme-hq", "partner-ws"):
                connected = _connect(address, "slack", "--slug", slug, browser=browser)
                assert connected.returncode == 0, connected.stderr
            listed = _connect(address, "--json")
            assert listed.returncode == 0, listed.stderr
            text = _connect(address)
            assert text.returncode == 0 and "assigned to operator" in text.stdout
            rows = json.loads(listed.stdout)[0]["connections"]
            assert {(row["slug"], row["kind"], tuple(row["assignments"])) for row in rows} == {
                ("acme-hq", "shared", ("operator",)),
                ("partner-ws", "shared", ("operator",)),
                ("schedule-slot", "shared", ("operator",)),
                ("my-slack", "per_user", ("operator",)),
            }
            # Missing per-user instance is rejected at admission: no run record,
            # no body and no temporary tool session.
            refusal(provider, "personal", "Connect your slack", "missing-a")
            assert not journal.exists()
            refusal(provider, "ambiguous", "connections are assigned", "ambiguous")
            answers = result(provider, "shared_accounts")
            assert "acme-hq-refunds-0" in answers[0] and "partner-ws-refunds-0" in answers[1]
            assert all(len(relay.entries("call")) == 1 for relay in relays[:2])

            connected = _connect(
                address, "slack", "--slug", "my-slack", "--per-user", browser=browser
            )
            assert connected.returncode == 0, connected.stderr
            immutable = _connect(address, "slack", "--slug", "acme-hq", "--per-user")
            assert immutable.returncode == 1
            assert "already exists; its integration and kind are immutable" in immutable.stderr
            assert "alice-refunds-0" in result(provider, "personal")[0]
            service.user = "user-b"
            refusal(provider, "personal", "Connect your slack", "missing-b")
            service.user = None
            refusal(provider, "personal", "Connect your slack", "no-human")
            no_human = _connect(address, "slack", "--slug", "my-slack", "--per-user")
            assert no_human.returncode == 1
            assert no_human.stderr.splitlines()[-1] == (
                "INVALID_ARGUMENT: A per-user connection requires a starting human. "
                "Sign in, then connect again."
            )
            service.user = "user-b"

            # Registration was shared; a later provider change to per-user must
            # also be refused when enabling, before changing stored schedule state.
            schedule_slot = next(row for row in service.named if row["slug"] == "schedule-slot")
            schedule_slot["kind"] = "per_user"
            refusal(provider, "scheduled", "Assign a shared connection", "scheduled-manual")
            with pytest.raises(OperatorCallError, match="Assign a shared connection") as error:
                provider.set_schedule_enabled("scheduled", True, {"journal": str(journal)})
            assert error.value.status is grpc.StatusCode.FAILED_PRECONDITION
            assert error.value.details == (
                "Flow 'scheduled' has a schedule or webhook, and its slack connection "
                "'schedule-slot' is per user. A triggered run has no user. "
                "Assign a shared connection."
            )
            scheduled = next(
                flow for flow in provider.list_workflows() if flow.name == "scheduled"
            )
            assert not scheduled.schedule_enabled
            schedule_slot["kind"] = "shared"

        # The second process reads the same records and the same provider state:
        # B is still missing, A still works and earlier successful runs survive.
        with _operator(state_db, address, environment, log, fixture) as provider:
            assert {item.slug for item in provider.list_integrations()[0].connections} == {
                "acme-hq",
                "partner-ws",
                "my-slack",
                "schedule-slot",
            }
            for run_id, answers in completed.items():
                assert provider.get_run(run_id).status is RunStatus.SUCCESS
                assert provider.get_run_result(run_id) == answers
            refusal(provider, "personal", "Connect your slack", "missing-b-restart")
            service.user = "user-a"
            assert "alice-refunds-0" in result(provider, "personal")[0]
            assert "acme-hq-refunds-0" in result(provider, "shared_accounts")[0]
            service.user = "user-b"
            service.named.append(slot("my-slack", "per_user", "user-b"))
            assert "bob-refunds-0" in result(provider, "personal")[0]
            # Even a connected user cannot register a per-user schedule.
            schedule_slot["kind"] = "per_user"
            with pytest.raises(OperatorCallError, match="triggered run has no user"):
                provider.set_schedule_enabled("scheduled", True, {"journal": str(journal)})

        # Initial registration must refuse too, not just the enable RPC. The
        # state lock is released, so fixing the provider makes startup work again.
        refused = subprocess.run(
            [
                sys.executable,
                "-m",
                "ava_cli",
                "operator",
                str(fixture),
                "--no-web",
                "--port",
                address.rsplit(":", 1)[1],
                "--webhook-port",
                "0",
                "--state-db",
                str(state_db),
            ],
            cwd=root,
            env={**os.environ, **environment},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert refused.returncode != 0
        assert "Assign a shared connection" in refused.stderr
        schedule_slot["kind"] = "shared"
        with _operator(state_db, address, environment, log, fixture) as provider:
            assert {item.slug for item in provider.list_integrations()[0].connections} == {
                "acme-hq",
                "partner-ws",
                "my-slack",
                "schedule-slot",
            }
            for run_id, answers in completed.items():
                assert provider.get_run(run_id).status is RunStatus.SUCCESS
                assert provider.get_run_result(run_id) == answers
            assert "bob-refunds-0" in result(provider, "personal")[0]
            assert "partner-ws-refunds-0" in result(provider, "shared_accounts")[1]
            default = _connect(address, "slack", browser=browser)
            assert default.returncode == 0, default.stderr
            assert "slack" in {
                item.slug for item in provider.list_integrations()[0].connections
            }
            disconnected = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ava_cli",
                    "disconnect",
                    "slack",
                    "--slug",
                    "partner-ws",
                    "--connect",
                    address,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert disconnected.returncode == 0, disconnected.stderr
            assert {item.slug for item in provider.list_integrations()[0].connections} == {
                "acme-hq",
                "my-slack",
                "schedule-slot",
                "slack",
            }
        assert len(journal.read_text().splitlines()) == 7
        stored = _stored_text_outside_accounts(state_db)
        assert _KB_KEY not in stored and _KB_KEY not in log.read_text()
        with sqlite3.connect(state_db) as database:
            assert database.execute("SELECT COUNT(*) FROM integration_accounts").fetchone() == (
                0,
            )
    finally:
        for relay in relays:
            relay.close()
        service.close()


def _prove_connection_resolution(named: WorkflowInfo, bare: WorkflowInfo) -> None:
    from avalanche.integrations.connections import (
        Connection,
        ConnectionAmbiguousError,
        ConnectionInstance,
        ConnectionNotAssignedError,
        ConnectionNotFoundError,
        ConnectionNotResolvedError,
        ConnectionPerUserTriggeredError,
        ConnectionUserNotConnectedError,
        FlowAssignment,
        OrgAssignment,
        ProjectAssignment,
        resolve,
        resolve_connections,
    )
    from avalanche.integrations.core import IntegrationReconnectError
    from avalanche.policies import NonRetryableError

    org = Connection("org", "org-slack", "slack", "shared")
    project = Connection("project", "acme-hq", "slack", "shared")
    flow = Connection("flow", "partner-ws", "slack", "shared")
    connections = [org, project, flow]
    instances = [
        ConnectionInstance(item.id, item.id, None, "connected") for item in connections
    ]
    assignments = [
        OrgAssignment(org.id),
        ProjectAssignment(project.id, "project-a"),
        FlowAssignment(flow.id, "project-a", bare.name),
    ]

    class Context(TypedDict):
        flow: str
        project: str
        starter: str | None
        triggered: Literal["schedule", "webhook"] | None

    context: Context = {
        "flow": bare.name,
        "project": "project-a",
        "starter": "ada",
        "triggered": None,
    }
    # The first occupied level wins, even with ambiguous lower levels. A flow
    # slug in another project is not the same flow, and duplicate assignments
    # do not turn one connection into two candidates.
    assert resolve(
        bare.integration_declarations, connections, assignments, instances, **context
    ) == {"unbound_tools_1": {"slack": instances[2]}}
    project_only = [*assignments[:2], FlowAssignment(flow.id, "project-b", bare.name)]
    assert resolve(
        bare.integration_declarations, connections, project_only, instances, **context
    ) == {"unbound_tools_1": {"slack": instances[1]}}
    org_only = [assignments[0], assignments[0], ProjectAssignment(project.id, "project-b")]
    assert resolve(
        bare.integration_declarations, connections, org_only, instances, **context
    ) == {"unbound_tools_1": {"slack": instances[0]}}
    ambiguous = [*assignments[:2], ProjectAssignment(flow.id, "project-a")]
    # More-specific flow assignment settles otherwise ambiguous project accounts.
    assert resolve(
        bare.integration_declarations,
        connections,
        [*ambiguous, assignments[2]],
        instances,
        **context,
    ) == {"unbound_tools_1": {"slack": instances[2]}}
    with pytest.raises(ConnectionAmbiguousError) as refused:
        resolve(bare.integration_declarations, connections, ambiguous, instances, **context)
    assert refused.value.code == "connection_ambiguous"
    assert isinstance(refused.value, NonRetryableError)
    assert str(refused.value) == (
        f"Flow {bare.name!r} uses slack, and two slack connections are assigned "
        "to its project: 'acme-hq' and 'partner-ws'. Remove one, or name one with "
        "ava.catalog.slack(connection=...)."
    )
    with pytest.raises(ConnectionNotResolvedError, match="no slack connection is assigned"):
        resolve(bare.integration_declarations, connections, [], instances, **context)

    context["flow"] = named.name
    # Naming both slugs bypasses same-level ambiguity and preserves TWO instances.
    assert resolve(
        named.integration_declarations, connections, ambiguous, instances, **context
    ) == {"named_tools_1": {"acme-hq": instances[1], "partner-ws": instances[2]}}
    with pytest.raises(ConnectionNotAssignedError) as refused:
        resolve(
            named.integration_declarations, connections, [assignments[0]], instances, **context
        )
    assert str(refused.value) == (
        f"Flow {named.name!r} names connection 'acme-hq', which is not assigned to the flow, "
        "its project or the organization."
    )
    for unavailable in (connections[:1], [org, replace(project, integration="gmail"), flow]):
        with pytest.raises(ConnectionNotFoundError) as refused:
            resolve(
                named.integration_declarations, unavailable, ambiguous, instances, **context
            )
        assert str(refused.value) == (
            f"Flow {named.name!r} names connection 'acme-hq', which does not exist "
            "(or is not a slack connection)."
        )
    per_user = [org, replace(project, kind="per_user"), flow]
    # Deploy/schedule admission selects connections, not a starting human's
    # accounts: no invented connected instances or login-state validation.
    assert resolve_connections(
        named.integration_declarations,
        per_user,
        ambiguous,
        flow=named.name,
        project="project-a",
        triggered=None,
    ) == {"named_tools_1": {"acme-hq": per_user[1], "partner-ws": flow}}
    for trigger in ("schedule", "webhook"):
        with pytest.raises(ConnectionPerUserTriggeredError):
            resolve_connections(
                named.integration_declarations,
                per_user,
                ambiguous,
                flow=named.name,
                project="project-a",
                triggered=trigger,
            )
    ada = ConnectionInstance("ada-account", project.id, "ada", "connected")
    grace = ConnectionInstance("grace-account", project.id, "grace", "connected")
    accounts = [*instances, ada, grace]
    assert resolve(
        named.integration_declarations, per_user, ambiguous, accounts, **context
    ) == {"named_tools_1": {"acme-hq": ada, "partner-ws": instances[2]}}
    for trigger in ("schedule", "webhook"):
        with pytest.raises(ConnectionPerUserTriggeredError) as refused:
            resolve(
                named.integration_declarations,
                per_user,
                ambiguous,
                accounts,
                **{**context, "triggered": trigger},
            )
        assert str(refused.value) == (
            f"Flow {named.name!r} has a schedule or webhook, and its slack connection "
            "'acme-hq' is per user. A triggered run has no user. Assign a shared connection."
        )
    # No anonymous/token-owner fallback, no other user's account, no shared
    # instance fallback, and no acceptance of an instance needing reconnect.
    for starter, current in (
        (None, accounts),
        ("new-user", accounts),
        ("ada", instances),
        ("ada", [*instances, replace(ada, state="reconnect_needed")]),
    ):
        with pytest.raises(ConnectionUserNotConnectedError) as refused:
            resolve(
                named.integration_declarations,
                per_user,
                ambiguous,
                current,
                **{**context, "starter": starter},
            )
        assert str(refused.value) == (
            f"Connect your slack account ('acme-hq') to run flow {named.name!r}, "
            "then start the run again."
        )
    reconnect = [instances[0], replace(instances[1], state="reconnect_needed"), instances[2]]
    with pytest.raises(
        IntegrationReconnectError, match="connection 'acme-hq' requires a new login"
    ):
        resolve(named.integration_declarations, connections, ambiguous, reconnect, **context)
    with pytest.raises(IntegrationNotConnectedError, match="acme-hq"):
        resolve(
            named.integration_declarations, connections, ambiguous, instances[:1], **context
        )


def test_catalog_connection_slugs_prefix_tools_and_refuse_conflicts(
    tmp_path, catalog_provider_path
):
    from avalanche.catalog import slack
    from avalanche.integrations.declaration import CatalogDeclaration

    # Bare declarations keep their exact wire shape and the generated constant
    # is not mutated by binding a connection or narrowing its tools.
    assert slack.declaration().model_dump_json() == (
        '{"kind":"catalog","name":"slack","allow":null}'
    )
    header = """import avalanche as ava
from avalanche.catalog import slack
from avalanche.integrations.core import Catalog

class Research(ava.Signature):
    topic: str = ava.InputField()
    findings: list[str] = ava.OutputField()

"""
    mistakes = tmp_path / "mistakes"
    mistakes.mkdir()
    declarations = {
        "mixed_bare_first": ("slack, slack(connection='acme-hq')", "set connection="),
        "mixed_bound_first": ("slack(connection='acme-hq'), slack", "set connection="),
        "duplicate_slug": (
            "slack(connection='acme-hq'), slack(connection='acme-hq')",
            "same connection",
        ),
        "shared_slug": (
            "slack(connection='acme-hq'), Catalog('notion', connection='acme-hq')",
            "acme_hq__",
        ),
        "prefix_clash": ("slack(connection='acme-hq'), Catalog('acme-hq')", "acme_hq__"),
        "normalized_clash": (
            "slack(connection='acme-hq'), slack(connection='acme_hq')",
            "acme_hq__",
        ),
        "invalid_slug": ("slack(connection='Acme--HQ')", "lowercase"),
    }
    for name, (members, _) in declarations.items():
        (mistakes / f"{name}.py").write_text(
            header
            + f"@ava.agent_step(Research, integrations=[{members}])\n"
            + "async def ask(*, agent: ava.Agent): pass\n"
            + "@ava.workflow\ndef refused(): return ask()\n"
        )
    refused = subprocess.run(
        [sys.executable, "-m", "ava_cli", "operator", str(mistakes), "--no-web"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert refused.returncode != 0
    for name, (_, reason) in declarations.items():
        error = next(line for line in refused.stderr.splitlines() if f"{name}.py" in line)
        assert reason in error, error

    fixture = tmp_path / "named_catalog.py"
    fixture.write_text(
        header
        + """@ava.agent_step(
    Research,
    runtime=ava.runtimes.OpenAIAgent(model="gpt-5.4-mini", narrate=False),
    integrations=[
        slack(allow=["search"])(connection="acme-hq"),
        Catalog("slack", connection="partner-ws", allow=["ping"]),
    ],
    retries=0,
)
async def named_tools(*, agent: ava.Agent) -> list[str]:
    return (await agent(topic="refunds")).outputs.findings

@ava.agent_step(Research, integrations=[slack], retries=0)
async def unbound_tools(*, agent: ava.Agent):
    pass

@ava.workflow
def unbound_catalog():
    return unbound_tools()

@ava.workflow
def named_catalog():
    return named_tools()
"""
    )
    calls = [("acme_hq__search", {"query": "refunds"}), ("partner_ws__ping", {})]
    models = ScriptedModelApis(calls, answer_field="findings")
    # Reuse the prefix scenario with resolved named accounts after activation.
    service = _CatalogService(tmp_path, integration="slack", server=_MCP_SERVER)
    service.named = [
        {
            "name": "slack",
            "slug": slug,
            "kind": "shared",
            "user": None,
            "instance_id": slug,
            "state": "connected",
        }
        for slug in ("acme-hq", "partner-ws")
    ]
    service.named_relays = {slug: service.mcp for slug in ("acme-hq", "partner-ws")}
    service.connected = True
    service.connected_at = time.time()
    environment = {
        "PYTHONPATH": os.pathsep.join(
            filter(None, [catalog_provider_path, os.environ.get("PYTHONPATH")])
        ),
        "AVALANCHE_INTEGRATION_PROVIDER": "proof",
        "CATALOG_PROOF_URL": service.url,
        "OPENAI_BASE_URL": f"{models.url}/v1",
        "OPENAI_API_KEY": "scripted-model",
    }
    state_db, log = tmp_path / "catalog.db", tmp_path / "operator.log"
    address = f"127.0.0.1:{_free_port()}"
    try:
        # Legacy providers must never silently map explicit slugs onto one account.
        with _operator(state_db, address, environment, log, fixture) as provider:
            with pytest.raises(OperatorCallError, match="does not support named connections"):
                provider.start_run("named_catalog", run_id="legacy-named")
            assert provider.get_run("legacy-named") is None
            assert not service.sessions and not service.bound_sessions
        environment["CATALOG_PROOF_NAMED"] = "1"
        # A second operator reads the persisted definition after a restart.
        for _ in range(2):
            with _operator(state_db, address, environment, log, fixture) as provider:
                flows = {item.name: item for item in provider.list_workflows()}
                flow = flows["named_catalog"]
                # Resolve the operator's real discovered/stored declarations, not
                # handwritten declarations; hosts use this same resolver.
                _prove_connection_resolution(flow, flows["unbound_catalog"])
                declared = flow.integration_declarations["named_tools_1"].integrations
                for entry, expected in zip(
                    declared,
                    [("slack", "acme-hq", ("search",)), ("slack", "partner-ws", ("ping",))],
                    strict=True,
                ):
                    assert isinstance(entry, CatalogDeclaration)
                    assert (entry.name, entry.connection, entry.allow) == expected
                run = _ended(provider, provider.start_run("named_catalog"))
                assert run.status is RunStatus.SUCCESS, (
                    [node.error for node in run.nodes.values()],
                    log.read_text(),
                )
                answers = provider.get_run_result(run.run_id)
                assert "refunds-0" in answers[0] and "pong" in answers[1]
                assert set(models.offered["openai"]) == {name for name, _ in calls}
        assert [(call["tool"], call.get("query")) for call in service.mcp.entries("call")] == [
            ("search", "refunds"),
            ("ping", None),
            ("search", "refunds"),
            ("ping", None),
        ]
        stored = _stored_text_outside_accounts(state_db)
        assert '"name":"acme_hq__search"' in stored
        assert '"name":"partner_ws__ping"' in stored
        assert slack.declaration().model_dump_json() == (
            '{"kind":"catalog","name":"slack","allow":null}'
        )
        for text in (stored, log.read_text(), service.journal.read_text()):
            assert _KB_KEY not in text
    finally:
        service.close()
        models.close()


def test_catalog_without_provider_imports_lists_and_reports_setup_once(tmp_path):
    fixture = _FIXTURES / "catalog_tools_flow.py"
    address = f"127.0.0.1:{_free_port()}"
    journal = tmp_path / "body.txt"
    log = tmp_path / "operator.log"
    with _operator(tmp_path / "catalog.db", address, {}, log, fixture) as provider:
        (status,) = provider.list_integrations()
        message = status.provider_error
        assert message == (
            "Built-in integrations need a provider, for example Avalanche Cloud "
            "(Trampoline Delta): uv tool install trampoline-delta. Then sign in and "
            "use `ava connect <name>`."
        )
        listed = _connect(address, "--json")
        assert listed.returncode == 0
        assert json.loads(listed.stdout)[0]["kind"] == "catalog"
        refused = _connect(address, "asana", "--no-browser")
        assert refused.returncode == 1 and refused.stderr.count(message) == 1
        failed = _finished(provider, "on_predict_rlm", journal, {"journal": str(journal)})
        assert failed.status is RunStatus.FAILED
        (node,) = failed.nodes.values()
        assert node.error is not None
        assert node.error.count(message) == 1
        assert not journal.exists()

    # A plain step cannot use catalog tools, even with no plug-in installed.
    plain = tmp_path / "plain.py"
    plain.write_text(
        "import avalanche as ava\n"
        "@ava.step(integrations=[ava.catalog.asana])\n"
        "def plain(): return 1\n"
    )
    refused = subprocess.run(
        [sys.executable, str(plain)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert refused.returncode != 0 and "agent steps only" in refused.stderr


@pytest.fixture
def alternate_catalog_provider_path(tmp_path) -> str:
    package = tmp_path / "alternate-provider"
    shutil.copytree(Path(__file__).parents[1] / "catalog_provider", package)
    manifest = package / "pyproject.toml"
    manifest.write_text(
        manifest.read_text()
        .replace(
            'name = "avalanche-test-catalog-provider"',
            'name = "avalanche-test-alternate-provider"',
        )
        .replace("proof = ", "alternate = ")
    )
    installed = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--python",
            sys.executable,
            "--with",
            str(package),
            "python",
            "-c",
            "from importlib.metadata import distribution; "
            "print(distribution('avalanche-test-alternate-provider').locate_file(''))",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return installed.stdout.strip()


def test_catalog_provider_selection_is_explicit(
    tmp_path,
    catalog_provider_path,
    alternate_catalog_provider_path,
):
    service = _CatalogService(tmp_path)
    address = f"127.0.0.1:{_free_port()}"
    environment = {
        "PYTHONPATH": os.pathsep.join([catalog_provider_path, alternate_catalog_provider_path]),
        "CATALOG_PROOF_URL": service.url,
    }
    journal = tmp_path / "body.txt"
    fixture = _FIXTURES / "catalog_tools_flow.py"
    state_db, log = tmp_path / "catalog.db", tmp_path / "operator.log"
    try:
        with _operator(state_db, address, environment, log, fixture) as provider:
            (status,) = provider.list_integrations()
            assert status.provider_error
            assert "alternate, proof" in status.provider_error
            assert "AVALANCHE_INTEGRATION_PROVIDER" in status.provider_error
            failed = _finished(provider, "on_predict_rlm", journal, {"journal": str(journal)})
            assert failed.status is RunStatus.FAILED and not journal.exists()
            refused = _connect(address, "asana", "--no-browser")
            assert refused.returncode == 1
            assert refused.stderr.count(status.provider_error) == 1
            assert not service.sessions
        environment["AVALANCHE_INTEGRATION_PROVIDER"] = "proof"
        with _operator(state_db, address, environment, log, fixture) as provider:
            (status,) = provider.list_integrations()
            assert status.provider_error is None
            assert status.display_name == "Asana proof"
    finally:
        service.close()


def test_catalog_release_artifact_and_stale_gate(tmp_path):
    """Exercise the real generator and wheel boundary, not a mocked build."""
    package = Path(__file__).parents[2]
    copy = tmp_path / "catalog-source"
    (copy / "scripts").mkdir(parents=True)
    (copy / "src/avalanche").mkdir(parents=True)
    for relative in (
        "catalog_entries.json",
        "scripts/generate_catalog.py",
        "src/avalanche/catalog.py",
    ):
        shutil.copyfile(package / relative, copy / relative)
    command = [sys.executable, str(copy / "scripts/generate_catalog.py")]
    generated = copy / "src/avalanche/catalog.py"
    original = generated.read_bytes()
    subprocess.run(command, check=True, timeout=30)
    assert generated.read_bytes() == original
    subprocess.run([*command, "--check"], check=True, timeout=30)
    generated.write_text(generated.read_text() + "# stale\n")
    stale = subprocess.run([*command, "--check"], capture_output=True, text=True, timeout=30)
    assert stale.returncode == 1 and "stale" in stale.stderr
    wheel_dir = tmp_path / "wheel"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir), str(package)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    import zipfile

    (wheel,) = wheel_dir.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("avalanche/catalog.py") == original
        assert not any("test_catalog_provider" in name for name in archive.namelist())
