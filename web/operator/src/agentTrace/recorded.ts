// An agent trace a host holds as recorded bodies (its live events, then its final
// body) rather than as an operator's event index. It applies the rules the local
// operator applies when it stores each event and seals the trace (turn, duration,
// failure, tool call count; status, completeness, header), so a trace reads the
// same on every host.
import type {
  AgentEventDescriptorMsg,
  TokenUsageMsg,
  TraceDescriptorMsg,
  TraceHeaderMsg,
} from "../model";
import { parseAgentLiveEvent, parseAgentTraceTerminal } from "./common";
import { turnViewer } from "./viewers";

export interface RecordedAgentTrace {
  trace: TraceDescriptorMsg;
  /** The events' descriptors, numbered 1..n in recorded order. */
  events: AgentEventDescriptorMsg[];
  /** Each body by its token: an event's (`<token>#<n>`) and the final body's (`<token>#final`). */
  bodies: Map<string, unknown>;
}

const byteSize = (body: unknown) => new TextEncoder().encode(JSON.stringify(body)).length;
const count = (value: number | null | undefined) =>
  value === null || value === undefined ? undefined : String(value);

function eventDescriptor(
  body: unknown,
  eventSequence: number,
  bodyToken: string,
): AgentEventDescriptorMsg {
  const live = parseAgentLiveEvent(body);
  const { common, native } = live;
  const finished = common?.kind === "turn.finished" ? common : undefined;
  const toolTurn =
    common?.kind === "tool.started" || common?.kind === "tool.finished" ? common.turn : null;
  const nativeTurn = native !== null && "iteration" in native ? native.iteration : null;
  const turn = finished?.turn.index ?? toolTurn ?? nativeTurn ?? undefined;
  const reported =
    (common?.kind === "tool.finished" ? common.error : null) ??
    (native !== null && "predict_error" in native ? native.predict_error : null) ??
    (native !== null && "execution_error" in native ? native.execution_error : null) ??
    (native !== null && "run_error" in native ? native.run_error : null);
  const failedTurn = finished ? turnViewer(live).turn(live).error : false;
  const durationMs = finished?.turn.duration_ms ?? undefined;
  return {
    eventSequence: String(eventSequence),
    sizeBytes: String(byteSize(body)),
    bodyToken,
    invocationId: live.invocation_id,
    nativeEventKind: native?.kind ?? "",
    agentEventKind: common?.kind ?? "native",
    ...(turn === undefined ? {} : { turn }),
    ...(durationMs === undefined ? {} : { durationMs: String(durationMs) }),
    error: reported !== null || failedTurn,
    toolCallCount: finished ? finished.omitted_tool_calls + finished.turn.tool_calls.length : 0,
  };
}

/**
 * The trace of one agent invocation from its recorded bodies. `terminal` is the
 * final body, or null when the invocation ended without one; `omittedEvents`
 * counts live events the host dropped, which leaves the trace incomplete.
 */
export function recordedAgentTrace(
  events: readonly unknown[],
  terminal: unknown,
  omittedEvents: number,
  token: string,
  revision: string,
): RecordedAgentTrace {
  const bodies = new Map<string, unknown>();
  const descriptors = events.map((body, index) => {
    const bodyToken = `${token}#${index + 1}`;
    bodies.set(bodyToken, body);
    return eventDescriptor(body, index + 1, bodyToken);
  });
  const eventCount = String(descriptors.length);
  const base = { revision, eventCount, latestEventSequence: eventCount };
  if (terminal === null || terminal === undefined) {
    // No final body: the invocation stopped before sealing its trace.
    const sizeBytes = String(descriptors.reduce((sum, e) => sum + Number(e.sizeBytes), 0));
    return {
      trace: { ...base, status: "error", available: true, complete: false, sizeBytes },
      events: descriptors,
      bodies,
    };
  }
  const final = parseAgentTraceTerminal(terminal);
  const bodyToken = `${token}#final`;
  bodies.set(bodyToken, terminal);
  const sealed = {
    ...base,
    available: true,
    complete: final.complete && omittedEvents === 0,
    sizeBytes: String(byteSize(terminal)),
    bodyToken,
  };
  if (final.kind === "trace_unavailable") {
    return { trace: { ...sealed, status: "unavailable" }, events: descriptors, bodies };
  }
  const { header } = final;
  const usage: TokenUsageMsg | undefined = header.usage
    ? {
        inputTokens: String(header.usage.input_tokens),
        outputTokens: String(header.usage.output_tokens),
        ...(count(header.usage.cache_read_input_tokens) === undefined
          ? {}
          : { cacheReadInputTokens: count(header.usage.cache_read_input_tokens) }),
        ...(count(header.usage.cache_creation_input_tokens) === undefined
          ? {}
          : { cacheCreationInputTokens: count(header.usage.cache_creation_input_tokens) }),
        ...(header.usage.cost_usd === null ? {} : { costUsd: header.usage.cost_usd }),
      }
    : undefined;
  const mapped: TraceHeaderMsg = {
    status: header.status,
    durationMs: String(header.duration_ms),
    turnCount: String(header.turn_count),
    toolCallCount: String(header.tool_call_count),
    native: header.native,
    ...(header.model === null ? {} : { model: header.model }),
    ...(usage ? { usage } : {}),
    ...(header.external_run_id === null ? {} : { externalRunId: header.external_run_id }),
    ...(header.stop_reason === null ? {} : { stopReason: header.stop_reason }),
  };
  return {
    trace: { ...sealed, status: header.status, header: mapped },
    events: descriptors,
    bodies,
  };
}

/**
 * The trace of an agent invocation still running, from the live events a host has
 * received so far: in progress (an error once an event failed), with no final body
 * yet, as the local operator describes a trace before it is sealed.
 */
export function liveAgentTrace(
  events: readonly unknown[],
  token: string,
  revision: string,
): RecordedAgentTrace {
  const bodies = new Map<string, unknown>();
  const descriptors = events.map((body, index) => {
    const bodyToken = `${token}#${index + 1}`;
    bodies.set(bodyToken, body);
    return eventDescriptor(body, index + 1, bodyToken);
  });
  const eventCount = String(descriptors.length);
  return {
    trace: {
      status: descriptors.some((event) => event.error) ? "error" : "in_progress",
      revision,
      available: false,
      complete: false,
      eventCount,
      sizeBytes: "0",
      latestEventSequence: eventCount,
    },
    events: descriptors,
    bodies,
  };
}
