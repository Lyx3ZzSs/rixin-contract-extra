import type { BBox } from "../types";

export interface ViewportRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function bboxToViewportRect(bbox: BBox, zoom: number): ViewportRect {
  const x0 = Math.min(bbox.x1, bbox.x2) * zoom;
  const y0 = Math.min(bbox.y1, bbox.y2) * zoom;
  const x1 = Math.max(bbox.x1, bbox.x2) * zoom;
  const y1 = Math.max(bbox.y1, bbox.y2) * zoom;
  return {
    x: x0,
    y: y0,
    width: Math.max(1, x1 - x0),
    height: Math.max(1, y1 - y0),
  };
}
