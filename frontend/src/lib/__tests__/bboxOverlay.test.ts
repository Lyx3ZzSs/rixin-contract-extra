import { describe, expect, test } from "vitest";
import { bboxToImageRect } from "../bboxOverlay";

describe("bboxToImageRect", () => {
  test("scales bbox from image space to displayed rect", () => {
    // image 100px wide, displayed at 200px → scale = 2
    const rect = bboxToImageRect(
      { x1: 10, y1: 20, x2: 60, y2: 70 },
      200,
      100,
    );
    expect(rect.left).toBe(20);
    expect(rect.top).toBe(40);
    expect(rect.width).toBe(100);
    expect(rect.height).toBe(100);
  });

  test("returns zero rect when naturalWidth is 0", () => {
    const rect = bboxToImageRect(
      { x1: 10, y1: 20, x2: 60, y2: 70 },
      200,
      0,
    );
    expect(rect).toEqual({ left: 0, top: 0, width: 0, height: 0 });
  });

  test("handles degenerate bbox (zero area)", () => {
    // x1==x2, y1==y2 → width/height clamped to 1px min
    const rect = bboxToImageRect(
      { x1: 50, y1: 50, x2: 50, y2: 50 },
      100,
      100,
    );
    expect(rect.left).toBe(50);
    expect(rect.top).toBe(50);
    expect(rect.width).toBe(1);  // clamped to visible minimum
    expect(rect.height).toBe(1);
  });
});
