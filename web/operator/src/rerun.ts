/** What a rerun asks for, in both runtimes. Names follow `ava.Rerun` and Delta's rerun spec. */
export type RerunMode = "autorun" | "lazy";

export interface RerunRequest {
  /** Stays the same across retries of one attempt, so a retry cannot start a second run. */
  requestId: string;
  workflowSelector: string;
  sourceRunId: string;
  /** Slugs of the steps to run again. */
  start: string[];
  mode: RerunMode;
  /** The deployment that runs; omitted means the flow's current one. Hosted runtimes only. */
  deploymentId?: string;
}

export interface RerunProblem {
  /** The step's slug; absent when the problem is with the whole source run. */
  step?: string;
  reason: string;
  detail: string;
}

/** The rerun was refused before anything started. */
export class RerunRefusedError extends Error {
  readonly problems: RerunProblem[];
  /** The nearest start steps that would pass the checks; may be empty. */
  readonly earliestStart: string[];

  constructor(problems: RerunProblem[], earliestStart: string[]) {
    super("The rerun was refused");
    this.name = "RerunRefusedError";
    this.problems = problems;
    this.earliestStart = earliestStart;
  }
}

// Avalanche and Delta run statuses that have stopped. A run still going cannot be rerun.
const STOPPED_RUN_STATUSES = new Set([
  "success",
  "succeeded",
  "skipped",
  "failed",
  "cancelled",
  "rejected",
  "expired",
  "integrity",
]);

export function canRerunRun(status: string | undefined): boolean {
  return status !== undefined && STOPPED_RUN_STATUSES.has(status);
}
