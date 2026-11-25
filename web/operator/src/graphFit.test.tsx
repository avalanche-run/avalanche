import type { Node } from "@xyflow/react";
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  CONTROLS_CLEARANCE,
  firstFrameFor,
  reserveFit,
  reserveSide,
  useFitViewOptions,
  type CanvasReserve,
} from "./GraphCanvas";

// The runs panel floating over the canvas's top-left corner.
const PANEL: CanvasReserve = { width: 330, height: 300 };
const OPTIONS = { padding: 0.24 };
const MIN_ZOOM = 0.12;

const row = (count: number): Node[] =>
  Array.from({ length: count }, (_, index) => ({
    id: `n${index}`,
    position: { x: index * 400, y: 0 },
    data: {},
  }));
const column = (count: number): Node[] =>
  Array.from({ length: count }, (_, index) => ({
    id: `n${index}`,
    position: { x: 0, y: index * 200 },
    data: {},
  }));

const zoomOn = (
  nodes: Node[],
  canvas: { width: number; height: number },
  side: "beside" | "below",
) => firstFrameFor(nodes, canvas, reserveFit(OPTIONS, PANEL, side), MIN_ZOOM)!.viewport.zoom;

describe("fit beside or below the runs panel", () => {
  it("fits a wide, short graph below the panel and a tall one beside it", () => {
    const canvas = { width: 1200, height: 900 };
    expect(reserveSide(OPTIONS, PANEL, row(6), canvas, MIN_ZOOM)).toBe("below");
    expect(reserveSide(OPTIONS, PANEL, column(6), canvas, MIN_ZOOM)).toBe("beside");
  });

  it("keeps the graph clear of the panel on the side it picked", () => {
    const below = reserveFit(OPTIONS, PANEL, "below").padding as Record<string, string>;
    expect(parseFloat(below.top)).toBeGreaterThanOrEqual(PANEL.height! + 16);
    const beside = reserveFit(OPTIONS, PANEL, "beside").padding as Record<string, string>;
    expect(parseFloat(beside.left)).toBeGreaterThanOrEqual(PANEL.width + 16);
  });

  it("keeps a graph fitted below the panel clear of the bottom-left canvas controls", () => {
    // The flow's even 48 px margin would put the input dot's label under the controls.
    const below = reserveFit(OPTIONS, PANEL, "below").padding as Record<string, string>;
    expect(parseFloat(below.left)).toBe(CONTROLS_CLEARANCE);
    const machine = reserveFit(
      { padding: { top: "120px", bottom: "150px", left: "200px", right: "80px" } },
      PANEL,
      "below",
    ).padding as Record<string, string>;
    expect(machine.left).toBe("200px");
  });

  it("stays beside unless below is more than 5% larger, so small changes do not flip it", () => {
    const nodes = row(4);
    let near = 0;
    let clear = 0;
    for (let height = 300; height <= 1400; height += 5) {
      const canvas = { width: 1000, height };
      const ratio = zoomOn(nodes, canvas, "below") / zoomOn(nodes, canvas, "beside");
      const side = reserveSide(OPTIONS, PANEL, nodes, canvas, MIN_ZOOM);
      if (ratio > 1 && ratio <= 1.05) {
        near++;
        expect(side).toBe("beside");
      } else if (ratio > 1.05) {
        clear++;
        expect(side).toBe("below");
      } else expect(side).toBe("beside");
    }
    // Both bands were crossed.
    expect(near).toBeGreaterThan(0);
    expect(clear).toBeGreaterThan(0);
  });

  it("fits beside a panel whose height is unknown", () => {
    expect(
      reserveSide(OPTIONS, { width: 330 }, row(6), { width: 1200, height: 900 }, 0.12),
    ).toBe("beside");
  });
});

describe("fit options identity", () => {
  it("re-fits on a new label scale and keeps the options otherwise", () => {
    const reserve = { width: 330, height: 300 };
    const { result, rerender } = renderHook(
      ({ scale }: { scale: number }) => useFitViewOptions(OPTIONS, reserve, "below", scale),
      { initialProps: { scale: 1 } },
    );
    const first = result.current;
    rerender({ scale: 1 });
    expect(result.current).toBe(first);
    rerender({ scale: 1.4 });
    expect(result.current).not.toBe(first);
    expect(result.current).toEqual(first);
  });
});
