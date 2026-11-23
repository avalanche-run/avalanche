import { useMemo } from "react";

import { GraphCanvas } from "./GraphCanvas";
import { FlowInfoMsg } from "./model";

// A fit never zooms in past the compact cards a typical flow fits at (below the
// zoom where cards add descriptions and details): a one-step flow shows its step
// at the size steps have in other flows, centred, not blown up to fill the canvas.
const FIT_ZOOM_CAP = 0.6;

/** A flow's steps and edges, as a host that holds only its definition has them. */
export interface FlowGraphDefinition {
  /** The flow's name; the canvas's accessible label. */
  name: string;
  /** Step IDs in definition order. */
  nodeIds: readonly string[];
  /** Each step's children. */
  graph: Readonly<Record<string, readonly string[]>>;
  displayNames?: Readonly<Record<string, string>>;
  nodeTypes?: Readonly<Record<string, string>>;
}

export interface FlowGraphProps {
  flow: FlowGraphDefinition;
  /** Sizes the canvas; it fills its box, so give it a height. */
  className?: string;
}

/**
 * A flow's DAG drawn as the workspace draws it, read-only: no inspector, no
 * selection, and the wheel scrolls the host page (drag pans, the controls zoom).
 * For pages that show a definition without an operator behind it.
 */
export function FlowGraph({ flow, className = "" }: FlowGraphProps) {
  const workflow = useMemo(
    () =>
      FlowInfoMsg.create({
        workflowId: flow.name,
        name: flow.name,
        displayName: flow.name,
        nodeIds: [...flow.nodeIds],
        graph: Object.fromEntries(
          Object.entries(flow.graph).map(([id, children]) => [id, { children: [...children] }]),
        ),
        displayNames: { ...flow.displayNames },
        nodeTypes: { ...flow.nodeTypes },
      }),
    [flow],
  );
  return (
    // The host sizes the outer box; the operator scope fills it (its root takes 100% height).
    <div role="group" aria-label={`${flow.name} steps graph`} className={className}>
      <div className="avalanche-operator-ui avalanche-flow-graph blueprint-canvas relative bg-panel">
        <GraphCanvas workflow={workflow} maxFitZoom={FIT_ZOOM_CAP} />
      </div>
    </div>
  );
}
