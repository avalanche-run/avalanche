import { useEffect, useId, useRef, useState } from "react";

import type { NodeSnapshotMsg } from "./model";
import {
  type RerunMode,
  type RerunProblem,
  type RerunRequest,
  RerunRefusedError,
} from "./rerun";
import type { WorkspaceDeployments } from "./WorkflowWorkspace";

const REASON_TEXT: Record<string, string> = {
  unknown_start_step: "is not in the code that will run",
  missing_from_source: "is not in the source run",
  not_reusable: "has a saved output that cannot be reused",
  output_unavailable: "has a saved output that is no longer available",
  output_does_not_fit: "has a saved output that does not fit its return type",
  stream_not_replayable: "wrote stream rows that cannot be replayed",
  source_run_active: "is still going",
};

function newRequestId() {
  return crypto.randomUUID().replaceAll("-", "");
}

function ProblemRow({ problem }: { problem: RerunProblem }) {
  return (
    <li className="rerun-problem" data-reason={problem.reason}>
      <span className="font-medium">
        {problem.step ? <code className="font-mono">{problem.step}</code> : "The source run"}{" "}
        {REASON_TEXT[problem.reason] ?? problem.reason}
      </span>
      {problem.detail && (
        <span className="block text-[11px] [overflow-wrap:anywhere] opacity-80">
          {problem.detail}
        </span>
      )}
    </li>
  );
}

export interface RerunDialogProps {
  workflowSelector: string;
  sourceRunId: string;
  /** The step this dialog opened on. */
  stepName: string;
  stepSlug: string;
  /** Present on hosts with deployments: adds a picker, preselected on the current one. */
  deployments?: WorkspaceDeployments;
  /** Starts the rerun. Rejects with RerunRefusedError when it is refused. */
  onStart: (request: RerunRequest) => Promise<string>;
  onClose: () => void;
}

/** "Rerun from here": choose what runs again, see why it was refused, and pick another start. */
export function RerunDialog({
  workflowSelector,
  sourceRunId,
  stepName,
  stepSlug,
  deployments,
  onStart,
  onClose,
}: RerunDialogProps) {
  const [start, setStart] = useState([stepSlug]);
  const [mode, setMode] = useState<RerunMode>("autorun");
  const [pickedDeploymentId, setPickedDeploymentId] = useState<string>();
  const [pending, setPending] = useState(false);
  const [refusal, setRefusal] = useState<RerunRefusedError>();
  const [error, setError] = useState<string>();
  // A failure other than a refusal may still have started the run, so a retry repeats the
  // same request and the choices stay locked until the dialog closes.
  const [retrying, setRetrying] = useState(false);
  const requestId = useRef(newRequestId());
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const modeName = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const currentDeployment = deployments?.items.find((item) => item.current);
  const deploymentId = pickedDeploymentId ?? currentDeployment?.id;
  const fromThisStep = start.length === 1 && start[0] === stepSlug;
  const startLabel = fromThisStep ? stepName : start.join(", ");
  const earliest = refusal?.earliestStart ?? [];
  const canUseEarliest =
    earliest.length > 0 &&
    (earliest.length !== start.length || earliest.some((slug) => !start.includes(slug)));
  const locked = pending || retrying;

  const clearRefusal = () => {
    setRefusal(undefined);
    setError(undefined);
  };

  const submit = async () => {
    clearRefusal();
    setPending(true);
    try {
      await onStart({
        requestId: requestId.current,
        workflowSelector,
        sourceRunId,
        start,
        mode,
        ...(deploymentId ? { deploymentId } : {}),
      });
      onClose();
    } catch (reason) {
      if (reason instanceof RerunRefusedError) {
        // Nothing started, so any choice may be tried at once.
        requestId.current = newRequestId();
        setRefusal(reason);
      } else {
        setRetrying(true);
        setError(reason instanceof Error ? reason.message : "The rerun could not be started");
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className="rerun-dialog m-auto max-h-[85dvh] w-[min(34rem,calc(100vw-2rem))] overflow-auto overscroll-contain rounded border border-line bg-panel p-0 text-ink shadow-xl backdrop:bg-backdrop/40"
      onCancel={(event) => {
        // The answer decides whether a run started, so wait for it.
        if (pending) event.preventDefault();
      }}
      onClose={onClose}
    >
      <form
        className="grid min-w-0 gap-4 p-6"
        onSubmit={(event) => {
          event.preventDefault();
          if (!pending) void submit();
        }}
      >
        <div>
          <h2 id={titleId} className="m-0 text-base [overflow-wrap:anywhere]">
            Rerun from {startLabel}
          </h2>
          <p className="m-0 mt-1 text-[11px] text-muted">
            Earlier steps are not run again: their saved outputs from run{" "}
            <code className="font-mono [overflow-wrap:anywhere]">{sourceRunId}</code> are
            reused.
          </p>
        </div>
        <fieldset className="m-0 grid gap-2 border-0 p-0 text-xs" disabled={locked}>
          <legend className="sr-only">What runs again</legend>
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="radio"
              name={modeName}
              checked={mode === "autorun"}
              onChange={() => {
                setMode("autorun");
                clearRefusal();
              }}
            />
            {start.length > 1
              ? "These steps and everything after"
              : "This step and everything after"}
          </label>
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="radio"
              name={modeName}
              checked={mode === "lazy"}
              onChange={() => {
                setMode("lazy");
                clearRefusal();
              }}
            />
            {start.length > 1 ? "Only these steps" : "Only this step"}
          </label>
        </fieldset>
        {deployments && (
          <div className="grid gap-1 text-xs">
            <label className="grid gap-1">
              <span>Deployment</span>
              <select
                className="min-w-0 rounded border border-line bg-panel px-2 py-1.5 font-mono text-[11px] text-ink disabled:opacity-50"
                value={deploymentId ?? ""}
                disabled={locked}
                onChange={(event) => {
                  setPickedDeploymentId(event.target.value);
                  clearRefusal();
                }}
              >
                {!currentDeployment && <option value="">Current deployment</option>}
                {deployments.items.map((item) => (
                  <option key={item.id} value={item.id} title={item.id}>
                    {item.label}
                    {item.current ? " (current)" : ""}
                    {" · "}
                    {new Date(item.createdAt * 1000).toLocaleString()}
                  </option>
                ))}
              </select>
            </label>
            {deployments.hasMore && deployments.loadMore && (
              <button
                type="button"
                className="cursor-pointer justify-self-start border-0 bg-transparent p-0 text-[11px] text-secondary underline underline-offset-2 hover:text-ink disabled:cursor-wait disabled:opacity-50"
                disabled={deployments.loading || locked}
                onClick={deployments.loadMore}
              >
                Load older deployments
              </button>
            )}
            {deployments.error && (
              <p role="alert" className="m-0 text-[11px] text-danger">
                {deployments.error}
              </p>
            )}
          </div>
        )}
        {refusal && (
          <div
            role="alert"
            className="rerun-refusal grid gap-2 rounded border border-error-line bg-error-surface px-3 py-2.5 text-xs text-error-ink"
          >
            <strong>This rerun was refused. Nothing started.</strong>
            <ul className="m-0 grid list-disc gap-1.5 pl-4">
              {refusal.problems.map((problem, index) => (
                <ProblemRow key={index} problem={problem} />
              ))}
            </ul>
            {earliest.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <span>
                  Earliest step to rerun from:{" "}
                  <code className="font-mono [overflow-wrap:anywhere]">
                    {earliest.join(", ")}
                  </code>
                </span>
                {canUseEarliest && (
                  <button
                    type="button"
                    className="cursor-pointer rounded border border-error-line bg-panel px-2 py-1 text-[11px] text-error-ink hover:text-ink"
                    onClick={() => {
                      setStart(earliest);
                      setMode("autorun");
                      clearRefusal();
                    }}
                  >
                    Use this start
                  </button>
                )}
              </div>
            )}
          </div>
        )}
        {error && (
          <p
            role="alert"
            className="m-0 rounded border border-error-line bg-error-surface px-3 py-2.5 text-xs text-error-ink [overflow-wrap:anywhere]"
          >
            {error}
          </p>
        )}
        <div className="flex items-center justify-end gap-2">
          {pending && (
            <span role="status" className="mr-auto text-[11px] text-secondary">
              Checking the rerun. This can take a while.
            </span>
          )}
          <button
            type="button"
            className="cursor-pointer rounded border border-line bg-panel px-3 py-1.5 text-[11px] text-secondary hover:text-ink disabled:cursor-wait disabled:opacity-50"
            disabled={pending}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="cursor-pointer rounded border border-acid bg-acid px-3 py-1.5 text-[11px] font-bold text-on-accent hover:border-acid-hover hover:bg-acid-hover disabled:cursor-wait disabled:opacity-50"
            disabled={pending}
          >
            {retrying ? "Try again" : "Start rerun"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

/** A run step's rerun button and, on a reused step, the link to the run holding its output. */
export function StepRerunActions({
  node,
  onRerunFromStep,
  onOpenReusedSource,
}: {
  node?: NodeSnapshotMsg;
  onRerunFromStep?: () => void;
  onOpenReusedSource?: (runId: string, nodeId: string) => void;
}) {
  const reusedFrom = node?.reusedFromRunId;
  if (!onRerunFromStep && !(reusedFrom && onOpenReusedSource)) return null;
  return (
    <div className="mt-2 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
      {node && reusedFrom && onOpenReusedSource && (
        <ReusedFromLink
          runId={reusedFrom}
          onOpen={() => onOpenReusedSource(reusedFrom, node.reusedFromNodeId ?? node.nodeId)}
        />
      )}
      {onRerunFromStep && (
        <button
          type="button"
          aria-haspopup="dialog"
          className="rerun-from-here cursor-pointer rounded border border-line bg-panel px-2 py-[5px] font-mono text-[9px] text-secondary uppercase hover:border-secondary hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
          onClick={onRerunFromStep}
        >
          Rerun from here
        </button>
      )}
    </div>
  );
}

/** "Reused from run X", opening that run on the step that holds the output. */
export function ReusedFromLink({
  runId,
  onOpen,
  className = "",
}: {
  runId: string;
  onOpen: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={`reused-from-link min-w-0 cursor-pointer truncate border-0 bg-transparent p-0 text-left font-mono text-[9px] text-run-status-reused underline underline-offset-2 hover:text-ink ${className}`}
      title={`Reused from run ${runId}`}
      onClick={(event) => {
        // Graph cards open their own step on click.
        event.stopPropagation();
        onOpen();
      }}
    >
      Reused from run {runId}
    </button>
  );
}
