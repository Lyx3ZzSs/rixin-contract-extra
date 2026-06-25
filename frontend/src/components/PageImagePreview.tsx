import { useCallback, useEffect, useRef, useState } from "react";
import type { BBox } from "../types";
import { getPageImageUrl } from "../lib/api";
import { bboxToImageRect } from "../lib/bboxOverlay";

export interface HighlightTarget {
  pageNo: number;
  bbox: BBox;
}

export interface PageImagePreviewProps {
  contractId: string;
  pageCount: number;
  target: HighlightTarget | null;
  onPageActive?: (pageNo: number) => void;
}

interface PageSize {
  /** img.naturalWidth — the page image's true pixel width (bbox lives in this space). */
  naturalWidth: number;
  /** img.clientWidth — the rendered <img> width; overlay scales by naturalWidth/clientWidth. */
  clientWidth: number;
}

/**
 * PageImagePreview — Tier 2 OCR traceability preview.
 *
 * Replaces the pdfjs-based preview for contracts that have per-page OCR images.
 * Each page is rendered as a plain <img> of the OCR page image, with a
 * zero-error bbox highlight: because the bbox is in the same pixel space as the
 * image, overlay math is `left=x1*scale, top=y1*scale, w=(x2-x1)*scale,
 * h=(y2-y1)*scale` where `scale = clientWidth / naturalWidth`.
 *
 * On `target` change the target page is scrolled into view; the highlight is
 * drawn only on the page whose pageNo === target.pageNo once its naturalWidth
 * is known (img has fired onLoad).
 */
export function PageImagePreview({
  contractId,
  pageCount,
  target,
  onPageActive,
}: PageImagePreviewProps) {
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const [sizes, setSizes] = useState<Record<number, PageSize>>({});

  const pageNumbers = Array.from({ length: pageCount }, (_, i) => i + 1);

  // Scroll the target page into view whenever `target` changes.
  useEffect(() => {
    if (!target) return;
    const el = pageRefs.current.get(target.pageNo);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [target]);

  // Report the most-visible page via IntersectionObserver (drives onPageActive).
  useEffect(() => {
    if (!onPageActive) return;
    const root = scrollContainerRef.current;
    const observer = new IntersectionObserver(
      (entries) => {
        let best: { pageNo: number; ratio: number } | null = null;
        for (const entry of entries) {
          const pageNo = Number(
            (entry.target as HTMLElement).dataset.pageNo ?? "0",
          );
          if (!pageNo) continue;
          if (!best || entry.intersectionRatio > best.ratio) {
            best = { pageNo, ratio: entry.intersectionRatio };
          }
        }
        if (best && best.ratio > 0) onPageActive(best.pageNo);
      },
      { root, threshold: [0.25, 0.5, 0.75] },
    );
    pageRefs.current.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [onPageActive, pageCount]);

  const handleImgLoad = useCallback(
    (pageNo: number, img: HTMLImageElement) => {
      const naturalWidth = img.naturalWidth;
      const clientWidth = img.clientWidth;
      setSizes((prev) => {
        const existing = prev[pageNo];
        if (
          existing &&
          existing.naturalWidth === naturalWidth &&
          existing.clientWidth === clientWidth
        ) {
          return prev;
        }
        return { ...prev, [pageNo]: { naturalWidth, clientWidth } };
      });
    },
    [],
  );

  const setRef = useCallback(
    (pageNo: number) => (node: HTMLDivElement | null) => {
      if (node) pageRefs.current.set(pageNo, node);
      else pageRefs.current.delete(pageNo);
    },
    [],
  );

  return (
    <div className="pip-scroll" ref={scrollContainerRef}>
      {pageNumbers.map((pageNo) => {
        const isTarget = target !== null && target.pageNo === pageNo;
        const size = sizes[pageNo];
        const showHighlight = isTarget && size && size.naturalWidth > 0;
        const rect = showHighlight
          ? bboxToImageRect(
              (target as HighlightTarget).bbox,
              size!.clientWidth,
              size!.naturalWidth,
            )
          : null;

        return (
          <div
            key={pageNo}
            className="pip-page-frame"
            data-page-no={pageNo}
            ref={setRef(pageNo)}
          >
            <img
              src={getPageImageUrl(contractId, pageNo)}
              alt={`合同第 ${pageNo} 页`}
              loading="lazy"
              onLoad={(e) => handleImgLoad(pageNo, e.currentTarget)}
            />
            {rect && (
              <div
                className="pip-highlight"
                style={{
                  left: `${rect.left}px`,
                  top: `${rect.top}px`,
                  width: `${rect.width}px`,
                  height: `${rect.height}px`,
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
