import { useEffect, useState } from "react";

// When each running-step reading first reached this page, on the browser's
// monotonic clock. Shared by every timer, so one mounted later (an inspector
// opened mid-run) counts from the same instant as one already showing.
const firstSeenMs = new Map<string, number>();
const FIRST_SEEN_LIMIT = 1_000;

function readingFirstSeenMs(startedAt: number, elapsedSeconds: number): number {
  const key = `${startedAt}\0${elapsedSeconds}`;
  const seen = firstSeenMs.get(key);
  if (seen !== undefined) return seen;
  const now = performance.now();
  if (firstSeenMs.size >= FIRST_SEEN_LIMIT) {
    const oldest = firstSeenMs.keys().next().value;
    if (oldest !== undefined) firstSeenMs.delete(oldest);
  }
  firstSeenMs.set(key, now);
  return now;
}

/**
 * A step's elapsed seconds, the one rule every step timer in the run view uses.
 *
 * A finished step shows `endedAt - startedAt`. A running step starts from the
 * host's `runningElapsedSeconds` and advances on the browser's monotonic clock
 * from the moment that reading first arrived, so timers mounted at different
 * moments agree and host/browser wall-clock skew never enters the count.
 */
export function useStepElapsedSeconds({
  startedAt,
  endedAt,
  running,
  runningElapsedSeconds,
}: {
  startedAt: number;
  endedAt?: number;
  running: boolean;
  runningElapsedSeconds: number;
}): number {
  const [nowMs, setNowMs] = useState(() => performance.now());
  const isRunning = running && endedAt === undefined;
  const receivedAtMs = isRunning ? readingFirstSeenMs(startedAt, runningElapsedSeconds) : 0;
  useEffect(() => {
    if (!isRunning) return;
    setNowMs(performance.now());
    const interval = window.setInterval(() => setNowMs(performance.now()), 100);
    return () => window.clearInterval(interval);
  }, [isRunning, runningElapsedSeconds, startedAt]);
  if (endedAt !== undefined) return Math.max(0, endedAt - startedAt);
  if (!isRunning) return 0;
  return Math.max(0, runningElapsedSeconds + Math.max(0, nowMs - receivedAtMs) / 1_000);
}
