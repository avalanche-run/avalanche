import type * as ReactFlowModule from "@xyflow/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentType, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// jsdom has no graph viewport. Keep the real cards; replace only React Flow's layout host.
vi.mock("@xyflow/react", async (importOriginal) => ({
  ...(await importOriginal<typeof ReactFlowModule>()),
  Background: () => null,
  Controls: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Handle: () => null,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  useViewport: () => ({ x: 0, y: 0, zoom: 1 }),
  useStore: (selector: (state: { width: number }) => unknown) => selector({ width: 0 }),
  useUpdateNodeInternals: () => () => undefined,
  useReactFlow: () => ({
    viewportInitialized: false,
    screenToFlowPosition: () => ({ x: 0, y: 0 }),
    fitView: () => undefined,
    setCenter: () => undefined,
  }),
  ReactFlow: ({
    nodes,
    nodeTypes,
    children,
  }: {
    nodes: { id: string; type: string; data: unknown; selected?: boolean }[];
    nodeTypes: Record<string, ComponentType<{ data: unknown; selected?: boolean }>>;
    children: ReactNode;
  }) => (
    <div>
      {nodes.map((node) => {
        const Card = nodeTypes[node.type];
        return <Card key={node.id} data={node.data} selected={node.selected} />;
      })}
      {children}
    </div>
  ),
}));

import type { RerunRequest } from "./rerun";
import { NodeSnapshotMsg, type RunSnapshotMsg } from "./model";
import { RerunRefusedError } from "./rerun";
import { createApi, baseline, snapshotFor, summary, workflow } from "./test/fixtures";
import { WorkflowWorkspace, type WorkspaceDeployments } from "./WorkflowWorkspace";

const flow = {
  ...workflow,
  nodeIds: ["a_1", "c_1"],
  graph: { a_1: { children: ["c_1"] }, c_1: { children: [] } },
  nodeTypes: { a_1: "step", c_1: "step" },
  displayNames: { a_1: "A", c_1: "C" },
  // c_1 is the step's ID; its slug is what a rerun names.
  nodeSlugs: { a_1: "a", c_1: "c_renamed" },
};

function runSnapshot(
  runId: string,
  status: string,
  nodes: Partial<NodeSnapshotMsg>[],
  rerunOfRunId?: string,
): RunSnapshotMsg {
  const base = snapshotFor({ ...summary, runId, status, rerunOfRunId });
  return {
    ...base,
    topology: base.topology && {
      ...base.topology,
      nodeIds: flow.nodeIds,
      graph: flow.graph,
      nodeTypes: flow.nodeTypes,
      displayNames: flow.displayNames,
    },
    nodes: nodes.map((node) => NodeSnapshotMsg.create(node)),
  };
}

const failedRun = runSnapshot("run-1", "failed", [
  { nodeId: "a_1", name: "A", nodeType: "step", status: "success" },
  { nodeId: "c_1", name: "C", nodeType: "step", status: "failed" },
]);

function renderWorkspace(
  startRerun: (request: RerunRequest) => Promise<string>,
  {
    snapshots = { "run-1": failedRun },
    deployments,
    runs = [{ ...summary, runId: "run-1", status: "failed" }],
    onSelectRun = vi.fn<(runId: string | undefined, nodeId?: string) => void>(),
    selectedRunId = "run-1",
    nodeSlugs = flow.nodeSlugs,
  }: {
    snapshots?: Record<string, RunSnapshotMsg>;
    deployments?: WorkspaceDeployments;
    runs?: (typeof summary)[];
    onSelectRun?: ReturnType<
      typeof vi.fn<(runId: string | undefined, nodeId?: string) => void>
    >;
    selectedRunId?: string;
    nodeSlugs?: Record<string, string>;
  } = {},
) {
  const catalog = { ...baseline.catalog, workflows: [{ ...flow, nodeSlugs }] };
  const api = createApi({
    getCatalog: async () => catalog,
    loadBaseline: async () => ({ ...baseline, catalog, runs }),
    getLatestRunSnapshot: async (runId) => snapshots[runId],
    startRerun,
  });
  render(
    <WorkflowWorkspace
      api={api}
      workflowId={flow.workflowId}
      deployments={deployments}
      navigation={{ selectedRunId, onSelectRun }}
    />,
  );
  return onSelectRun;
}

async function openStep(name: string) {
  fireEvent.click(await screen.findByRole("button", { name: `Inspect ${name}` }));
}

async function openDialog(name = "C") {
  await openStep(name);
  fireEvent.click(await screen.findByRole("button", { name: "Rerun from here" }));
  return screen.findByRole("dialog");
}

describe("rerun from a step", () => {
  it("names the step's slug, defaults to everything after, and opens the new run", async () => {
    const startRerun = vi.fn(async (_: RerunRequest) => "run-2");
    const onSelectRun = renderWorkspace(startRerun);
    const dialog = await openDialog();

    expect(within(dialog).getByRole("radio", { name: /everything after/ })).toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "Start rerun" }));

    await waitFor(() => expect(onSelectRun).toHaveBeenCalledWith("run-2"));
    expect(startRerun.mock.calls[0][0]).toMatchObject({
      sourceRunId: "run-1",
      start: ["c_renamed"],
      mode: "autorun",
      workflowSelector: flow.workflowId,
    });
    expect(startRerun.mock.calls[0][0].deploymentId).toBeUndefined();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps the source run it opened on when the view moves to a newer run", async () => {
    // Never settles, so the workspace does not go on to follow a run it cannot load.
    const startRerun = vi.fn((_: RerunRequest) => new Promise<string>(() => undefined));
    const catalog = { ...baseline.catalog, workflows: [flow] };
    const newer = runSnapshot("run-2", "running", [
      { nodeId: "a_1", name: "A", nodeType: "step", status: "running" },
    ]);
    const api = createApi({
      getCatalog: async () => catalog,
      loadBaseline: async () => ({
        ...baseline,
        catalog,
        runs: [
          { ...summary, runId: "run-1", status: "failed", createdSequence: "1" },
          { ...summary, runId: "run-2", status: "running", createdSequence: "2" },
        ],
      }),
      getLatestRunSnapshot: async (runId) => (runId === "run-2" ? newer : failedRun),
      startRerun,
    });
    const workspace = (selectedRunId: string) => (
      <WorkflowWorkspace
        api={api}
        workflowId={flow.workflowId}
        navigation={{ selectedRunId, onSelectRun: () => undefined }}
      />
    );
    const { rerender } = render(workspace("run-1"));
    const dialog = await openDialog();

    rerender(workspace("run-2"));
    await screen.findAllByText("run-2");
    fireEvent.click(within(dialog).getByRole("button", { name: "Start rerun" }));

    await waitFor(() => expect(startRerun).toHaveBeenCalled());
    expect(startRerun.mock.calls[0][0].sourceRunId).toBe("run-1");
  });

  it("can run only this step", async () => {
    const startRerun = vi.fn(async (_: RerunRequest) => "run-2");
    renderWorkspace(startRerun);
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole("radio", { name: /only this step/i }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Start rerun" }));

    await waitFor(() => expect(startRerun).toHaveBeenCalled());
    expect(startRerun.mock.calls[0][0].mode).toBe("lazy");
  });

  it("shows every problem of a refusal and lets the person take the earliest start", async () => {
    const startRerun = vi
      .fn<(request: RerunRequest) => Promise<string>>()
      .mockRejectedValueOnce(
        new RerunRefusedError(
          [
            { step: "a", reason: "missing_from_source", detail: "a is gone" },
            { step: "b", reason: "not_reusable", detail: "a data frame" },
            { reason: "source_run_active", detail: "still running" },
          ],
          ["a"],
        ),
      )
      .mockResolvedValue("run-2");
    const onSelectRun = renderWorkspace(startRerun);
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole("button", { name: "Start rerun" }));

    const refusal = await within(dialog).findByRole("alert");
    expect(within(refusal).getAllByRole("listitem")).toHaveLength(3);
    expect(refusal).toHaveTextContent("a is gone");
    expect(refusal).toHaveTextContent("a data frame");
    expect(onSelectRun).not.toHaveBeenCalled();

    fireEvent.click(within(refusal).getByRole("button", { name: "Use this start" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Start rerun" }));

    await waitFor(() => expect(onSelectRun).toHaveBeenCalledWith("run-2"));
    const [first, second] = startRerun.mock.calls.map(([request]) => request);
    expect(second.start).toEqual(["a"]);
    expect(second.mode).toBe("autorun");
    // A refusal started nothing, so the next attempt is a new request.
    expect(second.requestId).not.toBe(first.requestId);
  });

  it("retries an unknown outcome as the same request and locks the choices", async () => {
    const startRerun = vi
      .fn<(request: RerunRequest) => Promise<string>>()
      .mockRejectedValueOnce(new Error("connection lost"))
      .mockResolvedValue("run-2");
    const onSelectRun = renderWorkspace(startRerun);
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole("button", { name: "Start rerun" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("connection lost");
    expect(within(dialog).getByRole("radio", { name: /only this step/i })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(onSelectRun).toHaveBeenCalledWith("run-2"));
    const [first, second] = startRerun.mock.calls.map(([request]) => request);
    expect(second.requestId).toBe(first.requestId);
  });

  it("picks a deployment, preselecting the current one", async () => {
    const startRerun = vi.fn(async (_: RerunRequest) => "run-2");
    renderWorkspace(startRerun, {
      deployments: {
        items: [
          { id: "dpl_new", label: "dpl_new", createdAt: 2, current: true },
          { id: "dpl_old", label: "dpl_old", createdAt: 1, current: false },
        ],
        hasMore: false,
        loading: false,
        onSelect: () => undefined,
      },
    });
    const dialog = await openDialog();

    const picker = within(dialog).getByRole("combobox", { name: "Deployment" });
    expect(picker).toHaveValue("dpl_new");
    fireEvent.change(picker, { target: { value: "dpl_old" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Start rerun" }));

    await waitFor(() => expect(startRerun).toHaveBeenCalled());
    expect(startRerun.mock.calls[0][0].deploymentId).toBe("dpl_old");
  });

  it("is not offered on a step the code has no slug for", async () => {
    renderWorkspace(vi.fn(), { nodeSlugs: { a_1: "a" } });
    await openStep("C");
    await screen.findByRole("heading", { level: 2, name: "C" });
    expect(screen.queryByRole("button", { name: "Rerun from here" })).toBeNull();
  });

  it("is not offered for a run still going", async () => {
    const startRerun = vi.fn(async (_: RerunRequest) => "run-2");
    const running = runSnapshot("run-1", "running", [
      { nodeId: "a_1", name: "A", nodeType: "step", status: "running" },
    ]);
    renderWorkspace(startRerun, {
      snapshots: { "run-1": running },
      runs: [{ ...summary, runId: "run-1", status: "running" }],
    });
    await openStep("A");
    await screen.findByRole("heading", { level: 2, name: "A" });
    expect(screen.queryByRole("button", { name: "Rerun from here" })).toBeNull();
  });

  it("offers nothing on a host that cannot rerun", async () => {
    const catalog = { ...baseline.catalog, workflows: [flow] };
    const api = createApi({
      getCatalog: async () => catalog,
      loadBaseline: async () => ({
        ...baseline,
        catalog,
        runs: [{ ...summary, runId: "run-1", status: "failed" }],
      }),
      getLatestRunSnapshot: async () => failedRun,
    });
    render(
      <WorkflowWorkspace
        api={api}
        workflowId={flow.workflowId}
        navigation={{ selectedRunId: "run-1", onSelectRun: () => undefined }}
      />,
    );
    await openStep("C");
    await screen.findByRole("heading", { level: 2, name: "C" });
    expect(screen.queryByRole("button", { name: "Rerun from here" })).toBeNull();
  });
});

describe("a rerun's reused steps", () => {
  const rerun = runSnapshot(
    "run-2",
    "success",
    [
      {
        nodeId: "a_1",
        name: "A",
        nodeType: "step",
        status: "reused",
        reusedFromRunId: "run-1",
      },
      { nodeId: "c_1", name: "C", nodeType: "step", status: "success" },
    ],
    "run-1",
  );
  const runs = [
    { ...summary, runId: "run-1", status: "failed", createdSequence: "1" },
    {
      ...summary,
      runId: "run-2",
      status: "success",
      createdSequence: "2",
      rerunOfRunId: "run-1",
    },
  ];

  it("have their own state, not success, and link to the source run's step", async () => {
    const onSelectRun = renderWorkspace(vi.fn(), {
      snapshots: { "run-1": failedRun, "run-2": rerun },
      runs,
      selectedRunId: "run-2",
    });
    const inspect = await screen.findByRole("button", { name: "Inspect A" });
    await waitFor(() =>
      expect(inspect.closest("article")).toHaveAttribute("data-node-state", "reused"),
    );
    const card = inspect.closest("article") as HTMLElement;

    fireEvent.click(within(card).getByRole("button", { name: /reused from run run-1/i }));
    expect(onSelectRun).toHaveBeenCalledWith("run-1", "a_1");
  });

  it("show the rerun's source in the run header and the run list", async () => {
    const onSelectRun = renderWorkspace(vi.fn(), {
      snapshots: { "run-1": failedRun, "run-2": rerun },
      runs,
      selectedRunId: "run-2",
    });
    fireEvent.click(await screen.findByRole("button", { name: /Rerun of run run-1/ }));
    expect(onSelectRun).toHaveBeenLastCalledWith("run-1");

    onSelectRun.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Open run run-1, which run-2 reruns" }));
    expect(onSelectRun).toHaveBeenLastCalledWith("run-1");
  });
});
