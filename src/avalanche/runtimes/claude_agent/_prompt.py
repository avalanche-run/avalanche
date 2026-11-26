"""The prompt Claude was given, read back from the Claude CLI's session transcript.

The CLI builds every model request itself: Claude Code's own system prompt, the
context it adds to the first message, and the tool definitions. It records each
request in the session's transcript file as an ``api-request`` entry, which names
an ``api-request-shape`` (the system prompt and tools) and the
``api-request-blob`` entries holding its messages. A message the transcript already
holds as a ``user`` or ``assistant`` entry gets no blob: the request names it by the
hash of that entry's content. That format belongs to the CLI, so it is validated here,
once, and read nowhere else.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    NonNegativeInt,
    Tag,
    TypeAdapter,
)

from ...agent.live import (
    PromptFilePart,
    PromptMessage,
    PromptPart,
    PromptSent,
    PromptText,
    PromptTextPart,
    PromptTool,
    prompt_schema,
)

# The CLI's entries carry fields Avalanche does not read, such as cache settings.
_ENTRY = ConfigDict(extra="ignore", frozen=True)
# The request the SDK's session sends; the CLI's side requests have other sources.
_SDK_SOURCE = "sdk"


class _TextBlock(BaseModel):
    model_config = _ENTRY

    type: Literal["text"]
    text: str


class _MediaSource(BaseModel):
    model_config = _ENTRY

    media_type: str | None = None


class _MediaBlock(BaseModel):
    model_config = _ENTRY

    type: Literal["image", "document"]
    source: _MediaSource
    title: str | None = None


_Block = Annotated[_TextBlock | _MediaBlock, Field(discriminator="type")]


class _CustomTool(BaseModel):
    """A tool the model calls with input matching its schema."""

    model_config = _ENTRY

    name: str
    description: str | None = None
    input_schema: dict[str, JsonValue]


class _ServerTool(BaseModel):
    """A tool the API defines, such as web search; its settings are the API's own."""

    model_config = ConfigDict(extra="allow", frozen=True)

    type: str
    name: str

    @property
    def settings(self) -> dict[str, JsonValue]:
        return _SETTINGS.validate_python({"type": self.type, **(self.model_extra or {})})


_SETTINGS: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


class _Shape(BaseModel):
    model_config = _ENTRY

    system: list[_TextBlock]
    # A custom tool has an input schema; a server tool has a type and no input schema.
    tools: list[_CustomTool | _ServerTool]


class _Message(BaseModel):
    model_config = _ENTRY

    role: str
    content: str | list[_Block]


class _JsonSchemaFormat(BaseModel):
    """An answer format the CLI sends apart from the tools."""

    model_config = _ENTRY

    type: Literal["json_schema"]
    json_schema: dict[str, JsonValue] = Field(alias="schema")


class _OutputConfig(BaseModel):
    model_config = _ENTRY

    format: _JsonSchemaFormat | None = None


class _Params(BaseModel):
    model_config = _ENTRY

    output_config: _OutputConfig | None = None


# Each transcript line is parsed once into one of these. The request, shape and
# message payloads stay raw JSON: only those of the first request are read, so later
# requests, which hold tool calls, results and thinking, are never validated.
class _RequestLine(BaseModel):
    model_config = _ENTRY

    type: Literal["api-request"]
    querySource: str  # noqa: N815 - the CLI's field name
    shapeHash: str  # noqa: N815
    # Messages carried over from the previous request; the first request has none.
    keep: NonNegativeInt
    tail: list[str]
    params: JsonValue


class _ShapeLine(BaseModel):
    model_config = _ENTRY

    type: Literal["api-request-shape"]
    shapeHash: str  # noqa: N815 - the CLI's field name
    shape: JsonValue


class _BlobLine(BaseModel):
    model_config = _ENTRY

    type: Literal["api-request-blob"]
    hash: str
    message: JsonValue


class _MessageLine(BaseModel):
    """A ``user`` or ``assistant`` entry: one message, or one more part of it."""

    model_config = _ENTRY

    type: Literal["user", "assistant"]
    message: JsonValue = None


class _OtherLine(BaseModel):
    """An entry of any other kind, such as a user or assistant message."""

    model_config = _ENTRY

    type: str


def _line_kind(line: object) -> str:
    kind = line.get("type") if isinstance(line, dict) else None
    if kind in _REQUEST_KINDS:
        return kind
    return "message" if kind in ("user", "assistant") else "other"


_REQUEST_KINDS = frozenset({"api-request", "api-request-shape", "api-request-blob"})
_Line = _RequestLine | _ShapeLine | _BlobLine | _MessageLine | _OtherLine
_LINE: TypeAdapter[_Line] = TypeAdapter(
    Annotated[
        Annotated[_RequestLine, Tag("api-request")]
        | Annotated[_ShapeLine, Tag("api-request-shape")]
        | Annotated[_BlobLine, Tag("api-request-blob")]
        | Annotated[_MessageLine, Tag("message")]
        | Annotated[_OtherLine, Tag("other")],
        Discriminator(_line_kind),
    ]
)


class TranscriptFormatError(ValueError):
    """The transcript records the first request in a form Avalanche cannot read."""


def first_prompt(transcript: Path) -> PromptSent | None:
    """The prompt of the session's first model request, or ``None`` when the CLI
    recorded no request, as CLI versions before 2.1 do."""
    # The CLI writes what a request names before the request, so the entries after
    # the first request, which hold the whole session, are never parsed.
    entries: list[_Line] = []
    request: _RequestLine | None = None
    with transcript.open(encoding="utf-8") as lines:
        for line in lines:
            if not line.strip():
                continue
            entry = _LINE.validate_json(line)
            if isinstance(entry, _RequestLine) and entry.querySource == _SDK_SOURCE:
                request = entry
                break
            entries.append(entry)
    if request is None:
        return None
    if request.keep:
        raise TranscriptFormatError(
            f"the first request keeps {request.keep} earlier messages; it should have none"
        )
    shape = next(
        (
            _Shape.model_validate(entry.shape)
            for entry in entries
            if isinstance(entry, _ShapeLine) and entry.shapeHash == request.shapeHash
        ),
        None,
    )
    blobs = {
        entry.hash: entry.message
        for entry in entries
        if isinstance(entry, _BlobLine) and entry.hash in request.tail
    }
    if any(key not in blobs for key in request.tail):
        held = _held_messages(entries)
        blobs.update({key: held[key] for key in request.tail if key in held})
    missing = [key for key in request.tail if key not in blobs]
    if shape is None or missing:
        raise TranscriptFormatError(
            "the first request names a shape or messages the transcript does not hold"
        )
    output = _Params.model_validate(request.params).output_config
    return PromptSent(
        system=tuple(PromptText.of(block.text) for block in shape.system),
        messages=tuple(_message(_Message.model_validate(blobs[key])) for key in request.tail),
        tools=tuple(_tool(tool) for tool in shape.tools),
        output_schema=None
        if output is None or output.format is None
        else prompt_schema(output.format.json_schema),
    )


def _held_messages(entries: list[_Line]) -> dict[str, JsonValue]:
    """The messages the transcript's own entries hold, by the hash a request names them by.

    The CLI joins consecutive ``user`` entries, and ``assistant`` entries of one API
    message, into one message, and hashes each prefix of that message's content
    blocks as canonical JSON without ``cache_control``.
    """
    held: dict[str, JsonValue] = {}
    run: tuple[str, str | None] | None = None
    blocks: list[JsonValue] = []
    digest = hashlib.sha256()
    for entry in entries:
        if not isinstance(entry, _MessageLine):
            continue
        message = entry.message
        role = message.get("role") if isinstance(message, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(role, str) or content is None:
            run = None
            continue
        message_id = message.get("id")
        message_id = message_id if isinstance(message_id, str) else None
        if run is None or not _same_message(run, role, message_id):
            run, blocks, digest = (role, message_id), [], hashlib.sha256(b'{"content":[')
        parts = (
            [{"type": "text", "text": content}]
            if isinstance(content, str)
            else content
            if isinstance(content, list)
            else [content]
        )
        for part in parts:
            digest.update(("," if blocks else "").encode() + _canonical(part).encode())
            blocks.append(part)
        key = digest.copy()
        key.update(f'],"role":{_canonical(role)}}}'.encode())
        held[key.hexdigest()] = {"role": role, "content": list(blocks)}
    return held


def _same_message(run: tuple[str, str | None], role: str, message_id: str | None) -> bool:
    if run[0] != role:
        return False
    return role == "user" or (message_id is not None and run[1] == message_id)


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _canonical(value: JsonValue) -> str:
    """``value`` as the CLI hashes it: JavaScript's JSON with sorted keys and no
    ``cache_control``."""
    if isinstance(value, dict):
        fields = (
            f"{_canonical(key)}:{_canonical(item)}"
            for key, item in sorted(value.items())
            if key != "cache_control"
        )
        return "{" + ",".join(fields) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    text = json.dumps(value, ensure_ascii=False)
    # JavaScript escapes a lone surrogate; Python would write it raw.
    return _LONE_SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", text)


def _message(message: _Message) -> PromptMessage:
    blocks = (
        [_TextBlock(type="text", text=message.content)]
        if isinstance(message.content, str)
        else message.content
    )
    return PromptMessage(role=message.role, parts=tuple(_part(block) for block in blocks))


def _part(block: _TextBlock | _MediaBlock) -> PromptPart:
    match block:
        case _TextBlock(text=text):
            return PromptTextPart(text=PromptText.of(text))
        case _MediaBlock(title=title, source=source):
            return PromptFilePart(name=title, content_type=source.media_type)


def _tool(tool: _CustomTool | _ServerTool) -> PromptTool:
    match tool:
        case _CustomTool(name=name, description=description, input_schema=schema):
            return PromptTool(
                name=name,
                description=None if description is None else PromptText.of(description),
                input_schema=prompt_schema(schema),
            )
        case _ServerTool(name=name):
            # The API defines a server tool; its settings are all the step can show.
            return PromptTool(
                name=name, description=None, input_schema=prompt_schema(tool.settings)
            )
