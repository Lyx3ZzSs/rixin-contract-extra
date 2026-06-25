import type { BBox } from "../types";

export interface ImageRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Map a bbox (raw pixels in the OCR page-image's space) to a CSS overlay rect
 * on a displayed <img> of that same image.
 *
 * Tier 2 traceability: the bbox comes from PP-StructureV3 in the same pixel
 * space as the page image we render via <img>. (x1,y1) is top-left,
 * (x2,y2) is bottom-right — no axis flip, no coordinate-space conversion.
 *
 * scale = displayedWidth / naturalWidth. Because bbox and image share the same
 * pixel space, scaling by the rendered <img> width ratio is exact.
 *
 * Guards:
 *  - naturalWidth === 0 (image not yet loaded / broken) → zero rect (drawn nowhere).
 *  - degenerate bbox (x2<=x1 or y2<=y1) → width/height clamped to ≥1 so a 1px
 *    marker still renders instead of collapsing to invisible.
 */
export function bboxToImageRect(
  bbox: BBox,
  displayedWidth: number,
  naturalWidth: number,
): ImageRect {
  if (!naturalWidth) return { left: 0, top: 0, width: 0, height: 0 };
  const scale = displayedWidth / naturalWidth;
  return {
    left: bbox.x1 * scale,
    top: bbox.y1 * scale,
    width: Math.max(1, (bbox.x2 - bbox.x1) * scale),
    height: Math.max(1, (bbox.y2 - bbox.y1) * scale),
  };
}
