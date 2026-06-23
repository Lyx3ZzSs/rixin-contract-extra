import { useEffect, useMemo, useState } from "react";

import { downloadContractFileUrl, getContractDetail, listContracts, listReviewRecords, reviewField } from "../lib/api";
import type { ReviewRecord } from "../lib/api";
import { reviewStatusLabel } from "../lib/reviewStatus";
import { contractBriefToExtractionRecordSummary, contractDetailToExtractionTaskResponse } from "../types";
import type { ExtractionFieldValue, ExtractionRecordSummary, ExtractionTaskResponse, TaskStatus } from "../types";

interface ExtractionRecordsPageProps {
  onCreateExtraction: () => void;
}

const statusLabels: Record<TaskStatus, string> = {
  PROCESSING: "处理中",
  COMPLETED: "已完成",
  FAILED: "失败",
};

export function ExtractionRecordsPage({ onCreateExtraction }: ExtractionRecordsPageProps) {
  const [records, setRecords] = useState<ExtractionRecordSummary[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [selectedTask, setSelectedTask] = useState<ExtractionTaskResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [reviewRecords, setReviewRecords] = useState<ReviewRecord[]>([]);
  const [editingResultId, setEditingResultId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);

  useEffect(() => {
    let isCurrent = true;

    async function loadRecords() {
      setIsLoading(true);
      setError("");
      try {
        const contractList = await listContracts(undefined, 1, 100);
        const payload = contractList.items.map(contractBriefToExtractionRecordSummary);
        if (isCurrent) {
          setRecords(payload);
        }
      } catch (err) {
        if (isCurrent) {
          setError(err instanceof Error ? err.message : "提取记录加载失败。");
        }
      } finally {
        if (isCurrent) {
          setIsLoading(false);
        }
      }
    }

    void loadRecords();
    return () => {
      isCurrent = false;
    };
  }, []);

  async function openTask(taskId: string) {
    setSelectedTaskId(taskId);
    setSelectedTask(null);
    setDetailError("");
    setEditingResultId(null);
    setHistoryOpen(false);
    setIsDetailLoading(true);
    try {
      const detail = await getContractDetail(taskId);
      const taskResponse = contractDetailToExtractionTaskResponse(
        detail,
        records.find((r) => r.task_id === taskId)?.filename,
      );
      setSelectedTask(taskResponse);
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "提取详情加载失败。");
    } finally {
      setIsDetailLoading(false);
    }
    try {
      const recs = await listReviewRecords(taskId);
      setReviewRecords(recs);
    } catch {
      setReviewRecords([]);
    }
  }

  async function saveRecordReview(contractId: string, fieldId: string) {
    try {
      await reviewField(contractId, fieldId, { action: "modify", new_value: editDraft });
      setEditingResultId(null);
      const recs = await listReviewRecords(contractId);
      setReviewRecords(recs);
      // Re-fetch the task detail so reviewed_value / review_status refresh.
      const detail = await getContractDetail(contractId);
      const taskResponse = contractDetailToExtractionTaskResponse(
        detail,
        records.find((r) => r.task_id === contractId)?.filename,
      );
      setSelectedTask(taskResponse);
    } catch (err) {
      console.error(err);
      alert("复核保存失败");
    }
  }

  const selectedRecord = useMemo(
    () => records.find((record) => record.task_id === selectedTaskId) ?? null,
    [records, selectedTaskId],
  );

  return (
    <section className="records-workspace extraction-records-workspace" aria-labelledby="extract-records-title">
      <header className="records-header">
        <div>
          <span>合同智能提取</span>
          <h1 id="extract-records-title">提取记录</h1>
        </div>
        <button type="button" onClick={onCreateExtraction}>
          新建合同提取
        </button>
      </header>

      <div className="extraction-records-layout">
        <div className="records-panel extraction-records-panel">
          {isLoading ? (
            <div className="records-state" role="status">
              正在加载提取记录...
            </div>
          ) : error ? (
            <div className="records-state error" role="alert">
              <strong>记录加载失败</strong>
              <span>{error}</span>
            </div>
          ) : records.length === 0 ? (
            <div className="records-state">
              <strong>暂无提取记录</strong>
              <span>完成一次合同提取后，记录会显示在这里。</span>
            </div>
          ) : (
            <div className="extraction-record-table" aria-label="合同提取记录列表">
              <div className="extraction-record-row header">
                <strong>文件名</strong>
                <strong>更新时间</strong>
                <strong>识别器</strong>
                <strong>字段</strong>
                <strong>结果</strong>
                <strong>操作</strong>
              </div>
              {records.map((record) => (
                <article
                  className={selectedTaskId === record.task_id ? "extraction-record-row active" : "extraction-record-row"}
                  key={record.task_id}
                >
                  <div className="extract-record-file">
                    <strong title={record.filename}>{record.filename || "未命名文件"}</strong>
                    <span>{record.task_id}</span>
                  </div>
                  <span>{formatDateTime(record.updated_at || record.created_at)}</span>
                  <span>{record.extractor_used || "-"}</span>
                  <span>{record.field_count}</span>
                  <div className="extract-result-counts" aria-label="提取结果统计">
                    <b>{record.found_count}</b>
                    <span>{record.not_found_count}</span>
                    <i>{record.error_count}</i>
                  </div>
                  <div className="record-actions">
                    <span className={`record-status ${record.status.toLowerCase()}`}>{statusLabels[record.status]}</span>
                    <button type="button" onClick={() => openTask(record.task_id)}>
                      查看详情
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>

        <aside className="extraction-detail-panel" aria-label="提取详情">
          {!selectedTaskId ? (
            <div className="records-state compact">
              <strong>选择一条记录</strong>
              <span>详情会显示在这里。</span>
            </div>
          ) : isDetailLoading ? (
            <div className="records-state compact" role="status">
              正在加载提取详情...
            </div>
          ) : detailError ? (
            <div className="records-state compact error" role="alert">
              <strong>详情加载失败</strong>
              <span>{detailError}</span>
            </div>
          ) : selectedTask ? (
            <ExtractionTaskDetail
              task={selectedTask}
              fallbackRecord={selectedRecord}
              reviewRecords={reviewRecords}
              editingResultId={editingResultId}
              editDraft={editDraft}
              historyOpen={historyOpen}
              onEditDraftChange={setEditDraft}
              onStartEdit={(fieldId, draft) => {
                setEditingResultId(fieldId);
                setEditDraft(draft);
              }}
              onCancelEdit={() => setEditingResultId(null)}
              onSaveReview={saveRecordReview}
              onToggleHistory={() => setHistoryOpen((v) => !v)}
            />
          ) : null}
        </aside>
      </div>
    </section>
  );
}

function ExtractionTaskDetail({
  task,
  fallbackRecord,
  reviewRecords,
  editingResultId,
  editDraft,
  historyOpen,
  onEditDraftChange,
  onStartEdit,
  onCancelEdit,
  onSaveReview,
  onToggleHistory,
}: {
  task: ExtractionTaskResponse;
  fallbackRecord: ExtractionRecordSummary | null;
  reviewRecords: ReviewRecord[];
  editingResultId: string | null;
  editDraft: string;
  historyOpen: boolean;
  onEditDraftChange: (value: string) => void;
  onStartEdit: (fieldId: string, draft: string) => void;
  onCancelEdit: () => void;
  onSaveReview: (contractId: string, fieldId: string) => void;
  onToggleHistory: () => void;
}) {
  return (
    <div className="extraction-detail">
      <div className="extraction-detail-head">
        <span className={`record-status ${task.status.toLowerCase()}`}>{statusLabels[task.status]}</span>
        <h2 title={task.filename}>{task.filename || fallbackRecord?.filename || "提取文件"}</h2>
        <p>{task.task_id}</p>
        <button type="button" className="extract-value-toggle" onClick={onToggleHistory}>
          复核历史 ({reviewRecords.length})
        </button>
      </div>
      <div className="extraction-detail-meta">
        <span>
          <b>识别器</b>
          {task.extractor_used || "-"}
        </span>
        <span>
          <b>字段数</b>
          {task.fields.length}
        </span>
        <span>
          <b>结果数</b>
          {task.results.length}
        </span>
      </div>
      {task.file_url && (
        <a className="extraction-file-link" href={downloadContractFileUrl(task.task_id)} target="_blank" rel="noreferrer">
          打开原文件
        </a>
      )}
      {task.errors.length > 0 && (
        <div className="extract-error-banner" role="alert">
          {task.errors.join("; ")}
        </div>
      )}
      <section className="extraction-detail-section" aria-labelledby="extraction-detail-fields">
        <h3 id="extraction-detail-fields">字段配置</h3>
        <div className="extraction-detail-list">
          {task.fields.map((field) => (
            <div key={field.id}>
              <strong>{field.name}</strong>
              <span>{field.description || "-"}</span>
            </div>
          ))}
        </div>
      </section>
      <section className="extraction-detail-section" aria-labelledby="extraction-detail-results">
        <h3 id="extraction-detail-results">提取结果</h3>
        <div className="extraction-result-list">
          {task.results.map((result) => (
            <ResultCard
              key={result.field_id}
              result={result}
              editing={editingResultId === result.field_id}
              editDraft={editDraft}
              onEditDraftChange={onEditDraftChange}
              onStartEdit={() => onStartEdit(result.field_id, result.reviewed_value || result.value)}
              onCancelEdit={onCancelEdit}
              onSaveReview={() => onSaveReview(task.task_id, result.field_id)}
            />
          ))}
        </div>
      </section>
      {historyOpen && (
        <section className="extraction-detail-section extraction-review-history" aria-labelledby="extraction-detail-history">
          <h3 id="extraction-detail-history">复核历史</h3>
          <ul className="review-history-list">
            {reviewRecords.map((rec) => (
              <li key={rec.id}>
                <strong>{rec.action}</strong> · {rec.reviewer_id ?? "-"} · {formatDateTime(rec.created_at)}
                {rec.old_value != null && <div>原值：{rec.old_value || "—"}</div>}
                {rec.new_value != null && <div>新值：{rec.new_value || "—"}</div>}
                {rec.comment && <div>备注：{rec.comment}</div>}
              </li>
            ))}
            {reviewRecords.length === 0 && <li>暂无复核记录</li>}
          </ul>
        </section>
      )}
    </div>
  );
}

function ResultCard({
  result,
  editing,
  editDraft,
  onEditDraftChange,
  onStartEdit,
  onCancelEdit,
  onSaveReview,
}: {
  result: ExtractionFieldValue;
  editing: boolean;
  editDraft: string;
  onEditDraftChange: (value: string) => void;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSaveReview: () => void;
}) {
  return (
    <article className={`extraction-result-card ${result.status}`}>
      <header>
        <strong>{result.field_name}</strong>
        <span>
          {extractionMethodLabel(result.extraction_method)}
          {result.status === "found" ? ` · ${Math.round(result.confidence * 100)}%` : ` · ${statusText(result.status)}`}
          {result.review_status && result.review_status !== "extracted" && (
            <small className={`extract-review-badge ${result.review_status}`}>{reviewStatusLabel(result.review_status)}</small>
          )}
        </span>
      </header>
      {editing ? (
        <p className="extraction-result-edit">
          <input
            className="field-edit-input"
            value={editDraft}
            onChange={(e) => onEditDraftChange(e.target.value)}
            autoFocus
          />
          <button type="button" className="extract-value-toggle" onClick={onSaveReview}>
            保存
          </button>
          <button type="button" className="extract-value-toggle" onClick={onCancelEdit}>
            取消
          </button>
        </p>
      ) : (
        <p>
          {result.reviewed_value ? (
            <>
              <span className="extract-card-corrected">{result.reviewed_value}</span>
              <small className="extract-card-original">
                原值：{result.status === "found" ? result.value || "-" : statusText(result.status)}
              </small>
            </>
          ) : (
            result.status === "found" ? result.value || "-" : statusText(result.status)
          )}
          {result.status === "found" && (
            <button type="button" className="extract-value-toggle" onClick={onStartEdit}>
              修正
            </button>
          )}
        </p>
      )}
      {result.source_snippet && <small>{result.source_snippet}</small>}
    </article>
  );
}

function statusText(status: string): string {
  if (status === "not_found") {
    return "未找到";
  }
  if (status === "error") {
    return "提取失败";
  }
  return "已找到";
}

function extractionMethodLabel(method: string | null | undefined): string {
  if (method === "explicit") {
    return "严格匹配";
  }
  if (method === "semantic") {
    return "语义提取";
  }
  return "提取";
}

function formatDateTime(value: string): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
