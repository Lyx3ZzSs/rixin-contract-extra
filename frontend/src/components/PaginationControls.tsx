import { ChevronLeft, ChevronRight } from "lucide-react";

interface PaginationControlsProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

export function PaginationControls({
  page,
  pageSize,
  total,
  onPageChange,
}: PaginationControlsProps) {
  if (total <= pageSize) return null;

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(Math.max(page, 1), totalPages);
  const start = (safePage - 1) * pageSize + 1;
  const end = Math.min(total, safePage * pageSize);

  return (
    <nav className="pagination-controls" aria-label="分页">
      <span className="pagination-summary">
        {start}-{end} / {total}
      </span>
      <div className="pagination-buttons">
        <button
          type="button"
          onClick={() => onPageChange(safePage - 1)}
          disabled={safePage <= 1}
          aria-label="上一页"
          title="上一页"
        >
          <ChevronLeft aria-hidden="true" />
        </button>
        <span className="pagination-page">
          第 {safePage} / {totalPages} 页
        </span>
        <button
          type="button"
          onClick={() => onPageChange(safePage + 1)}
          disabled={safePage >= totalPages}
          aria-label="下一页"
          title="下一页"
        >
          <ChevronRight aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}
