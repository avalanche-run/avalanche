import { capText } from "./capText";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { type ReactNode, useState } from "react";

import type { NodeSnapshotMsg, RunSnapshotMsg } from "./model";
import { RunStatus, statusTone } from "./RunStatusBadge";

const NAV_BUTTON =
  "inline-flex cursor-pointer items-center gap-1 rounded border border-line bg-panel px-1.5 py-1 font-mono text-[10px] text-secondary hover:border-secondary hover:text-ink focus-visible:outline-2 focus-visible:outline-acid disabled:cursor-not-allowed disabled:opacity-45";

/** The item runs of a map parent, by index; uncreated indices are absent. */
export function mapItemRuns(run: RunSnapshotMsg | undefined, parentId: string | undefined) {
  const items = new Map<number, NodeSnapshotMsg>();
  if (!run || !parentId) return items;
  for (const node of run.nodes) {
    if (node.mapParent === parentId && node.mapIndex !== undefined) {
      items.set(node.mapIndex, node);
    }
  }
  return items;
}

/**
 * Run I/O of a mapped step: the parent node runs nothing itself, so this shows
 * a summary of its item runs and steps through each item's own inputs and output.
 */
export function MapRunIo({
  run,
  parentId,
  renderItem,
}: {
  run: RunSnapshotMsg;
  parentId: string;
  renderItem: (item: NodeSnapshotMsg) => ReactNode;
}) {
  const items = mapItemRuns(run, parentId);
  const count = Math.max(
    run.expansions[parentId] ?? 0,
    ...[...items.keys()].map((index) => index + 1),
  );
  const [selected, setSelected] = useState<{ parentId: string; index: number }>();
  const index = selected?.parentId === parentId ? Math.min(selected.index, count - 1) : 0;
  const tones = Array.from({ length: count }, (_, i) =>
    items.has(i) ? statusTone(items.get(i)?.status) : undefined,
  );
  const failed = tones.flatMap((tone, i) => (tone === "danger" ? [i] : []));
  const succeeded = tones.filter((tone) => tone === "success" || tone === "reused").length;
  const running = tones.filter((tone) => tone === "active").length;
  const skipped = tones.filter((tone) => tone === "skipped").length;
  const notStarted = tones.filter((tone) => tone === undefined).length;
  const select = (next: number) => setSelected({ parentId, index: next });
  const nextFailed = failed.find((i) => i > index) ?? failed[0];
  const item = items.get(index);

  if (count === 0) {
    return (
      <p className="m-0 text-[11px] text-muted" role="status">
        {run.expansions[parentId] === 0
          ? "This mapped step received an empty list, so no item ran."
          : "This mapped step has not started any item yet."}
      </p>
    );
  }
  return (
    <div className="grid min-w-0 gap-5">
      <section aria-label="Map summary" className="min-w-0">
        <p className="m-0 text-[11px] text-secondary">
          Ran via <code className="font-mono">map</code> over{" "}
          <strong className="text-ink">
            {count} {count === 1 ? "item" : "items"}
          </strong>
          : {succeeded} succeeded
          {failed.length > 0 && (
            <span className="text-run-status-danger"> · {failed.length} failed</span>
          )}
          {running > 0 && <> · {running} running</>}
          {skipped > 0 && <span className="text-run-status-skipped"> · {skipped} skipped</span>}
          {notStarted > 0 && <> · {notStarted} not started</>}
        </p>
      </section>
      <nav
        aria-label="Map items"
        className="flex min-w-0 flex-wrap items-center gap-1.5 border-b border-line pb-3"
      >
        <button
          type="button"
          className={NAV_BUTTON}
          aria-label="Previous item"
          disabled={index === 0}
          onClick={() => select(index - 1)}
        >
          <ChevronLeft aria-hidden="true" className="size-3" />
        </button>
        <label className="inline-flex items-center gap-1.5 font-mono text-[10px] text-secondary">
          <span className="sr-only">Item</span>
          <select
            className="cursor-pointer rounded border border-line bg-panel px-1.5 py-1 font-mono text-[10px] text-ink focus-visible:outline-2 focus-visible:outline-acid"
            value={index}
            onChange={(event) => select(Number(event.target.value))}
          >
            {tones.map((tone, i) => (
              <option key={i} value={i}>
                {`Item ${i}${tone === "danger" ? " · failed" : tone === undefined ? " · not started" : tone === "active" ? " · running" : tone === "skipped" ? " · skipped" : ""}`}
              </option>
            ))}
          </select>
          <span>of {count}</span>
        </label>
        <button
          type="button"
          className={NAV_BUTTON}
          aria-label="Next item"
          disabled={index >= count - 1}
          onClick={() => select(index + 1)}
        >
          <ChevronRight aria-hidden="true" className="size-3" />
        </button>
        {failed.length > 0 && (
          <button
            type="button"
            className={`${NAV_BUTTON} ml-auto text-run-status-danger`}
            disabled={failed.length === 1 && failed[0] === index}
            onClick={() => select(nextFailed)}
          >
            Next failed
          </button>
        )}
      </nav>
      <section aria-label={`Item ${index}`} className="grid min-w-0 gap-4">
        <header className="map-item-heading flex min-w-0 items-center gap-2 font-mono text-[11px] text-ink">
          {capText(<><RunStatus status={item?.status ?? "pending"} glyphOnly />
          <strong className="font-semibold">Item {index}</strong>
          <span className="text-[10px] text-muted">{item?.nodeId}</span></>)}
        </header>
        {item?.error && (
          <p className="m-0 text-[10px] text-danger [overflow-wrap:anywhere]" role="alert">
            {item.error}
          </p>
        )}
        {item ? (
          renderItem(item)
        ) : (
          <p className="m-0 text-[11px] text-muted" role="status">
            This item has not started.
          </p>
        )}
      </section>
    </div>
  );
}
