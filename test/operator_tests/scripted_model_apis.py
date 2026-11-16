"""Stand-in model APIs, so the real Claude and OpenAI runtimes run without a model.

The Claude runtime starts the real Claude CLI and the OpenAI runtime uses the
real OpenAI Agents SDK; both find their model API through an environment
variable. This service answers both APIs from one script: the "model" calls
the script's tools one after the other, then answers with every tool result
it was given, as text. What the scenario reads back is therefore what really
travelled from the tool to the model, successes and failures alike.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import JsonValue

# One scripted call: the tool's name as Avalanche offers it, and its arguments.
ScriptedCall = tuple[str, dict[str, JsonValue]]


class ScriptedModelApis:
    """Serve the Anthropic Messages API and the OpenAI Responses API from ``script``.

    The answer has one output field, ``answer_field``: a list with the text of
    each tool result, in call order.
    """

    def __init__(self, script: list[ScriptedCall], *, answer_field: str) -> None:
        self.script = script
        self.answer_field = answer_field
        # The tool names each API was offered, as the model saw them.
        self.offered: dict[str, list[str]] = {"anthropic": [], "openai": []}
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_HEAD(self) -> None:  # noqa: N802
                self._send(200, "application/json", b"")

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.startswith("/v1/messages"):
                    self._send(200, "text/event-stream", service._anthropic(body))
                elif self.path.startswith("/v1/responses"):
                    self._send(200, "text/event-stream", service._openai(body))
                else:
                    self._send(404, "application/json", b"{}")

            def _send(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
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

    def _next(self, offered: list[str], results: list[str]) -> ScriptedCall | None:
        """The next scripted call under the name the model was offered, or None at the end."""
        if len(results) >= len(self.script):
            return None
        name, arguments = self.script[len(results)]
        # The Claude CLI offers an in-process tool as ``mcp__<server>__<tool>``.
        return next(tool for tool in offered if tool.endswith(name)), arguments

    def _anthropic(self, body: dict) -> bytes:
        offered = [tool["name"] for tool in body.get("tools", [])]
        if "StructuredOutput" not in offered:
            # A side request of the CLI, such as naming the session.
            time.sleep(1.0)
            return _anthropic_stream(body["model"], {"type": "text", "text": '{"title": "t"}'})
        self.offered["anthropic"] = offered
        results = []
        for message in body["messages"]:
            if message["role"] != "user" or isinstance(message["content"], str):
                continue
            for block in message["content"]:
                if block["type"] == "tool_result":
                    results.append(_text(block["content"]))
        call = self._next(offered, results)
        if call is not None:
            name, arguments = call
        elif len(results) == len(self.script):
            name, arguments = "StructuredOutput", {self.answer_field: results}
        else:
            # The answer was accepted; the model has nothing more to say.
            return _anthropic_stream(body["model"], {"type": "text", "text": "Done."})
        block = {"type": "tool_use", "id": f"toolu_{len(results)}", "name": name, "input": {}}
        return _anthropic_stream(body["model"], block, json.dumps(arguments))

    def _openai(self, body: dict) -> bytes:
        offered = [tool["name"] for tool in body.get("tools", [])]
        self.offered["openai"] = offered
        results = [
            item["output"] if isinstance(item["output"], str) else _text(item["output"])
            for item in body["input"]
            if item.get("type") == "function_call_output"
        ]
        call = self._next(offered, results)
        if call is not None:
            name, arguments = call
            item = {
                "type": "function_call",
                "id": f"fc_{len(results)}",
                "call_id": f"call_{len(results)}",
                "name": name,
                "arguments": json.dumps(arguments),
                "status": "completed",
            }
        else:
            item = {
                "type": "message",
                "id": "msg_answer",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({self.answer_field: results}),
                        "annotations": [],
                    }
                ],
            }
        response = {
            "id": f"resp_{len(results)}",
            "object": "response",
            "created_at": 0,
            "model": body["model"],
            "status": "completed",
            "output": [item],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": 5,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 15,
            },
        }
        return _events(
            ("response.created", {"response": {**response, "status": "in_progress"}}),
            ("response.completed", {"response": response}),
            numbered=True,
        )


def _text(content: object) -> str:
    """A tool result's text, whether the API carries it as a string or as blocks."""
    if isinstance(content, str):
        return content
    assert isinstance(content, list), content
    return "\n".join(block["text"] for block in content if block["type"].endswith("text"))


def _anthropic_stream(model: str, block: dict, tool_input: str | None = None) -> bytes:
    usage = {"input_tokens": 10, "output_tokens": 5}
    message = {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage,
    }
    if tool_input is None:
        start = {**block, "text": ""}
        delta = {"type": "text_delta", "text": block["text"]}
    else:
        start = block
        delta = {"type": "input_json_delta", "partial_json": tool_input}
    stop_reason = "end_turn" if tool_input is None else "tool_use"
    return _events(
        ("message_start", {"message": message}),
        ("content_block_start", {"index": 0, "content_block": start}),
        ("content_block_delta", {"index": 0, "delta": delta}),
        ("content_block_stop", {"index": 0}),
        (
            "message_delta",
            {"delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": usage},
        ),
        ("message_stop", {}),
    )


def _events(*events: tuple[str, dict], numbered: bool = False) -> bytes:
    lines = []
    for number, (name, data) in enumerate(events):
        payload = {"type": name, **data, **({"sequence_number": number} if numbered else {})}
        lines.append(f"event: {name}\ndata: {json.dumps(payload)}\n\n")
    return "".join(lines).encode()
