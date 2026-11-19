import type { NodeSnapshotMsg } from "./model";

/** A flow's wait spec as both hosts send it: `kind` and, for a call, its start node. */
interface WaitSpecJson {
  kind?: string;
  run_start_node_id?: string | null;
}

/** Each `ava.call`'s wait node ID to the start node ID the same call declared. */
export function callStarts(waits: Record<string, WaitSpecJson | undefined>) {
  const starts: Record<string, string> = {};
  for (const [nodeId, spec] of Object.entries(waits))
    if (spec?.kind === "run" && typeof spec.run_start_node_id === "string")
      starts[nodeId] = spec.run_start_node_id;
  return starts;
}

interface CallTopology {
  nodeIds: string[];
  graph: Record<string, { children: string[] }>;
}

/**
 * Draws each `ava.call` as one node: its start node is folded into its wait node,
 * which the start's parents now enter. A pair the shown graph does not hold as
 * start → wait (a run of an older definition) stays as it is. Returns the folded
 * topology and each folded call's start node ID by wait node ID.
 */
export function foldCalls<T extends CallTopology>(topology: T, starts: Record<string, string>) {
  const folded: Record<string, string> = {};
  const waitOf = new Map<string, string>();
  for (const [wait, start] of Object.entries(starts))
    if (topology.nodeIds.includes(wait) && topology.graph[start]?.children.includes(wait)) {
      folded[wait] = start;
      waitOf.set(start, wait);
    }
  if (!waitOf.size) return { topology, folded };
  const unique = (ids: string[]) => [...new Set(ids)];
  const graph: Record<string, { children: string[] }> = {};
  for (const [nodeId, edges] of Object.entries(topology.graph))
    if (!waitOf.has(nodeId))
      graph[nodeId] = {
        ...edges,
        children: unique(edges.children.map((id) => waitOf.get(id) ?? id)),
      };
  for (const [start, wait] of waitOf)
    graph[wait] = {
      ...graph[wait],
      children: unique([
        ...(graph[wait]?.children ?? []),
        ...topology.graph[start].children.filter((id) => id !== wait),
      ]),
    };
  return {
    topology: { ...topology, nodeIds: topology.nodeIds.filter((id) => !waitOf.has(id)), graph },
    folded,
  };
}

/**
 * The one status of a folded call: its start's until the child has started
 * (starting, skipped or failed to start), then its wait's (waiting on the child,
 * done or failed).
 */
export function callNodeState(
  start: NodeSnapshotMsg | undefined,
  wait: NodeSnapshotMsg | undefined,
): NodeSnapshotMsg | undefined {
  if (!start || start.status === "success" || start.status === "reused") return wait;
  return { ...start, nodeId: wait?.nodeId ?? start.nodeId };
}
