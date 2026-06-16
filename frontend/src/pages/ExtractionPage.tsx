import { forwardRef, type ChangeEvent, type UIEvent, useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist/types/src/pdf";

import { getCurrentPageFromScroll } from "../components/pdfPageScroll";
import type { FieldDefinitionItem } from "../lib/api";
import { createExtractionPreview, getContractDetail, getTask, uploadContract } from "../lib/api";
import { fieldDetailToExtractionFieldValue } from "../types";
import { readExtractionFieldLibrary } from "../lib/extractionFieldLibrary";
import type { ExtractionFieldValue } from "../types";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

const DEFAULT_PREVIEW_ZOOM = 1;
const PREVIEW_RENDER_SCALE = 0.9;
const MIN_PREVIEW_ZOOM = 0.8;
const MAX_PREVIEW_ZOOM = 1.8;
const PREVIEW_ZOOM_STEP = 0.1;

export function ExtractionPage() {
  const [file, setFile] = useState<File | null>(null);
  const [documentUrl, setDocumentUrl] = useState("");

  useEffect(() => {
    if (!file || typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
      setDocumentUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setDocumentUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function handleFilesChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0] ?? null;
    setFile(selectedFile);
  }

  if (file) {
    return <ExtractionFieldSetup file={file} documentUrl={documentUrl} onBack={() => setFile(null)} />;
  }

  return (
    <section className="extract-workspace" aria-labelledby="extract-title">
      <header className="extract-hero">
        <div className="extract-title-row">
          <h1 id="extract-title">AI智能合同提取工具</h1>
        </div>
        <div className="extract-hero-art" aria-hidden="true">
          <div className="extract-source-doc">
            <strong>XXX 合同</strong>
            <span />
            <span />
            <span />
            <span />
            <span />
          </div>
          <div className="extract-result-card">
            <strong>
              <i>AI</i>
              智能数据提取
            </strong>
            <span />
            <span />
            <span />
          </div>
          <div className="extract-arrow" />
        </div>
      </header>

      <form className="extract-card">
        <h2>上传文件</h2>
        <label className="extract-upload-zone" htmlFor="extract-files">
          <input
            id="extract-files"
            type="file"
            accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.bmp,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg,image/bmp"
            aria-label="上传合同提取文件"
            onChange={handleFilesChange}
          />
          <span className="extract-upload-icon" aria-hidden="true">
            <i>PDF</i>
            <i>W</i>
            <i>IMG</i>
          </span>
          <strong>拖拽文件上传或点击上传本地文件</strong>
          <small>支持格式 pdf/word/png/jpg/jpeg/bmp，图片5MB以内，其他文件60MB以内</small>
        </label>
      </form>
    </section>
  );
}

interface ExtractionFieldSetupProps {
  file: File;
  documentUrl: string;
  onBack: () => void;
}

function ExtractionFieldSetup({ file, documentUrl, onBack }: ExtractionFieldSetupProps) {
  const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  const isWord = /\.(doc|docx)$/i.test(file.name);
  const [wordPreviewUrl, setWordPreviewUrl] = useState("");
  const [wordPreviewState, setWordPreviewState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [wordPreviewError, setWordPreviewError] = useState("");
 const [fields, setFields] = useState<FieldDefinitionItem[]>([]);
  const [semanticEnabled, setSemanticEnabled] = useState<Record<string, boolean>>({});

useEffect(() => {
  readExtractionFieldLibrary()
     .then((items) => setFields(items.filter(f => f.required)))
     .catch(() => {});
}, []);
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractionError, setExtractionError] = useState("");
  const [results, setResults] = useState<ExtractionFieldValue[] | null>(null);
  const [extractionStage, setExtractionStage] = useState("");
  const [expandedResultIds, setExpandedResultIds] = useState<Set<string>>(() => new Set());
  const [currentStep, setCurrentStep] = useState<1 | 2 | 3>(2);
 const [editingFieldKey, setEditingFieldKey] = useState("");
  const [isAddingField, setIsAddingField] = useState(false);
 const [fieldDraft, setFieldDraft] = useState<Pick<FieldDefinitionItem, "field_name" | "description">>({
    field_name: "",
    description: "",
  });

  useEffect(() => {
    if (!isWord) {
      setWordPreviewUrl("");
      setWordPreviewState("idle");
      setWordPreviewError("");
      return undefined;
    }

    let isCurrent = true;
    let objectUrl = "";
    setWordPreviewUrl("");
    setWordPreviewState("loading");
    setWordPreviewError("");

    createExtractionPreview(file)
      .then((blob) => {
        if (!isCurrent) {
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setWordPreviewUrl(objectUrl);
        setWordPreviewState("ready");
      })
      .catch((err) => {
        if (!isCurrent) {
          return;
        }
        setWordPreviewError(err instanceof Error ? err.message : "Word 预览失败。");
        setWordPreviewState("error");
      });

    return () => {
      isCurrent = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [file, isWord]);

  async function removeField(fieldKey: string) {
    setFields((currentFields) => currentFields.filter((field) => field.field_key !== fieldKey));
    if (editingFieldKey === fieldKey) {
      cancelFieldEdit();
    }
  }

 function startFieldEdit(field: FieldDefinitionItem) {
   setIsAddingField(false);
   setEditingFieldKey(field.field_key);
    setFieldDraft({
      field_name: field.field_name,
      description: field.description,
    });
  }

  function updateFieldDraft(key: keyof typeof fieldDraft, value: string) {
    setFieldDraft((currentDraft) => ({ ...currentDraft, [key]: value }));
  }

 function cancelFieldEdit() {
   setEditingFieldKey("");
   setIsAddingField(false);
   setFieldDraft({ field_name: "", description: "" });
 }

  function startAddField() {
    setIsAddingField(true);
    setEditingFieldKey("");
    setFieldDraft({ field_name: "", description: "" });
  }

  async function saveNewField() {
    const name = fieldDraft.field_name.trim();
    const description = fieldDraft.description.trim();
    if (!name) return;
    const field_key = `custom-${name.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-").replace(/^-+|-+$/g, "")}-${Date.now().toString(36)}`;
    setFields((prev) => [
      ...prev,
      {
        id: "",
        field_key,
        field_name: name,
        description,
        value_type: "string",
        required: false,
        sort_order: prev.length + 1,
        is_active: true,
      },
    ]);
    cancelFieldEdit();
  }

 async function saveFieldEdit() {
    if (!editingFieldKey) {
      return;
    }
    const nextName = fieldDraft.field_name.trim();
    const nextDescription = fieldDraft.description.trim();
    if (!nextName) {
      return;
    }
    try {
      const { updateFieldDefinition } = await import("../lib/api");
      await updateFieldDefinition(editingFieldKey, { field_name: nextName, description: nextDescription });
      const items = await readExtractionFieldLibrary();
      setFields(items);
    } catch { /* ignore */ }
    cancelFieldEdit();
  }

  const stageMessages: Record<string, string> = {
    pending: "等待处理...",
    uploaded: "已上传，等待开始...",
    file_detecting: "正在检测文件类型...",
    text_extracting: "正在提取文本...",
    ocr_processing: "正在识别文字...",
    layout_analyzing: "正在分析布局...",
    clause_splitting: "正在拆分条款...",
    field_extracting: "正在提取字段...",
    validating: "正在校验数据...",
    review_pending: "等待复核...",
    completed: "处理完成",
  };

  async function handleStartExtraction() {
    if (fields.length === 0) return;

    setIsExtracting(true);
    setExtractionError("");
    setExtractionStage("file_detecting");
    setExpandedResultIds(new Set());
    setCurrentStep(3);

    try {
      // Step 1: Upload file to backend
      const customFields = fields.filter(f => !f.id);
      const uploadResult = await uploadContract(file, customFields);
      const contractId = uploadResult.contract_id;
      const taskId = uploadResult.task_id;

      // Step 2: Poll task until completed or failed
      const maxAttempts = 180;
      let taskComplete = false;
      for (let attempt = 0; attempt < maxAttempts; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const poll = await getTask(taskId);
        setExtractionStage(poll.status);

        if (poll.status === "completed") {
          taskComplete = true;
          break;
        }
        if (poll.status.endsWith("_failed")) {
          throw new Error(poll.error_message || "提取失败");
        }
      }

      if (!taskComplete) {
        throw new Error("提取超时，请稍后查看结果");
      }

      // Step 3: Get extraction results from contract detail
      const detail = await getContractDetail(contractId);

      // Step 4: Build a lookup from field_key to extracted result, then map
      // the user's field list to extraction results. Fields not found in the
      // backend response are shown as "not_found".
      const backendByFieldKey = new Map<string, ReturnType<typeof fieldDetailToExtractionFieldValue>>();
      for (const bf of detail.fields) {
        const fv = fieldDetailToExtractionFieldValue(bf);
        if (fv.field_key) {
          backendByFieldKey.set(fv.field_key, fv);
        }
      }

      const allFieldValues = fields.map((fieldDef) => {
        const matched = backendByFieldKey.get(fieldDef.field_key);
        if (matched) {
          return matched;
        }
        // No backend result for this field — report as not found
        return {
          field_id: "",
          field_name: fieldDef.field_name,
          field_key: fieldDef.field_key,
          value: "",
          confidence: 0,
          source_snippet: "",
          status: "not_found" as const,
          extraction_method: null as null,
        };
      });

      setResults(allFieldValues);
    } catch (err) {
      setExtractionError(err instanceof Error ? err.message : "提取失败，请重试");
      setCurrentStep(2);
    } finally {
      setIsExtracting(false);
    }
  }

  function returnToFieldSetup() {
    setResults(null);
    setExpandedResultIds(new Set());
    setExtractionStage("");
    setCurrentStep(2);
  }

  function toggleResultExpansion(fieldId: string) {
    setExpandedResultIds((currentIds) => {
      const nextIds = new Set(currentIds);
      if (nextIds.has(fieldId)) {
        nextIds.delete(fieldId);
      } else {
        nextIds.add(fieldId);
      }
      return nextIds;
    });
  }

  function handleExport() {
    if (!results) return;
    const header = "字段名称,提取值,状态\n";
    const rows = results
      .map(
        (r) =>
          `"${r.field_name}","${r.value || ""}","${r.status === "found" ? "已找到" : r.status === "not_found" ? "未找到" : "错误"}"`,
      )
      .join("\n");
    const bom = "﻿";
    const blob = new Blob([bom + header + rows], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${file.name.replace(/\.[^.]+$/, "")}_提取结果.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const activeFieldCount = fields.length;

  return (
    <section className="extract-flow" aria-labelledby="extract-flow-title">
      <header className="extract-flow-header">
        <button className="extract-close-button" type="button" onClick={onBack} aria-label="返回上传文件">
          ×
        </button>
        <div className="extract-flow-center">
          <h1 id="extract-flow-title">合同提取</h1>
          <ol className="extract-steps" aria-label="合同提取步骤">
            <li className="done">
              <span>✓</span>
              选择合同
            </li>
            <li className={currentStep >= 2 ? (results ? "done" : "active") : ""}>
              <span>{currentStep >= 3 && results ? "✓" : "2"}</span>
              设置提取字段
            </li>
            <li className={currentStep >= 3 ? "active" : ""}>
              <span>3</span>
              数据提取
            </li>
          </ol>
        </div>
        <button
          className="extract-flow-submit"
          type="button"
          onClick={results ? handleExport : handleStartExtraction}
          disabled={isExtracting || (!results && activeFieldCount === 0)}
        >
          {isExtracting ? "正在提取..." : results ? "导出" : "开始提取"}
        </button>
      </header>

      <div className="extract-flow-body">
        <aside className="extract-file-list" aria-label="合同文件列表">
          <label className="extract-search">
            <span>请输入</span>
            <input aria-label="搜索合同文件" />
          </label>
          <button className="active" type="button" title={file.name}>
            <span>1.</span>
            <strong>{file.name}</strong>
          </button>
        </aside>

        <main className="extract-preview" aria-label="合同预览">
          <div className="extract-document-page">
            {isPdf ? (
              <FlatPdfPreview src={documentUrl} file={file} />
            ) : isWord && wordPreviewState === "ready" && wordPreviewUrl ? (
              <FlatPdfPreview src={wordPreviewUrl} file={file} />
            ) : isWord ? (
              <DocumentFallback
                file={file}
                message={
                  wordPreviewState === "error"
                    ? wordPreviewError || "Word 预览失败，请确认后端已安装 LibreOffice。"
                    : "正在将 Word 转为 PDF 预览..."
                }
                isError={wordPreviewState === "error"}
              />
            ) : (
              <DocumentFallback file={file} />
            )}
          </div>
        </main>

        <aside className="extract-field-panel" aria-label="字段列表">
          {isExtracting && !results ? (
            <div className="extract-card-view">
              <div className="extract-card-header">
                <h2>提取字段信息</h2>
              </div>
              <div className="extract-status-bar">
                <span className="extract-status-spinner" aria-hidden="true" />
                {stageMessages[extractionStage] || "正在提取合同的结构信息"}
              </div>
              <div className="extract-card-list">
                {fields.map((field) => (
                  <div className="extract-card-item" key={field.field_key}>
                    <span className="extract-card-name">{field.field_name}</span>
                    <span className="extract-card-value loading">提取中...</span>
                  </div>
                ))}
              </div>
            </div>
          ) : results ? (
            <div className="extract-card-view">
              <div className="extract-card-header">
                <h2>提取字段信息</h2>
                <button type="button" className="extract-back-link" onClick={returnToFieldSetup}>
                  返回字段设置
                </button>
              </div>
              {extractionError && (
                <div className="extract-error-banner" role="alert">{extractionError}</div>
              )}
              <div className="extract-card-list">
                {results.map((r) => {
                  const valueText =
                    r.status === "found" ? (r.value || "—") : r.status === "not_found" ? "未找到" : "提取失败";
                  const isExpanded = expandedResultIds.has(r.field_id);
                  const canExpand = r.status === "found" && isLongExtractionValue(valueText);
                  return (
                    <div className="extract-card-item result" key={r.field_id}>
                      <span className="extract-card-name">
                        {r.field_name}
                        <small className="extract-method-badge">{extractionMethodLabel(r.extraction_method)}</small>
                      </span>
                      <span
                        className={`extract-card-value${r.status === "not_found" ? " empty" : r.status === "error" ? " error" : ""}${isExpanded ? " expanded" : ""}`}
                        title={valueText}
                      >
                        <span className="extract-card-value-text">{valueText}</span>
                        {canExpand && (
                          <button
                            type="button"
                            className="extract-value-toggle"
                            aria-expanded={isExpanded}
                            onClick={() => toggleResultExpansion(r.field_id)}
                          >
                            {isExpanded ? "收起" : "展开"}
                          </button>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <>
              <div className="extract-field-panel-head">
                <h2>字段列表</h2>
               <div className="extract-field-actions" aria-label="字段操作">
                  <button type="button" onClick={startAddField}>+ 自定义添加</button>
                  <button type="button">从字段库/模板添加</button>
                  <button type="button">从Excel导入</button>
                </div>
              </div>
              <div className="extract-field-grid" role="table" aria-label="提取字段列表">
               <div className="extract-field-row header" role="row">
                 <strong role="columnheader">字段名称</strong>
                <strong role="columnheader">字段描述</strong>
                  <strong role="columnheader">语义提取</strong>
                  <strong role="columnheader">操作</strong>
              </div>
                {isAddingField && (
                  <div className="extract-field-row" role="row">
                    <span className="field-name editing" role="cell">
                      <input
                        className="field-edit-input"
                        aria-label="新字段名称"
                        placeholder="字段名称"
                        value={fieldDraft.field_name}
                        onChange={(e) => updateFieldDraft("field_name", e.target.value)}
                        autoFocus
                      />
                    </span>
                    <span className="field-description editing" role="cell">
                      <input
                        className="field-edit-input"
                        aria-label="新字段描述"
                        placeholder="字段描述"
                        value={fieldDraft.description}
                        onChange={(e) => updateFieldDraft("description", e.target.value)}
                      />
                    </span>
                    <span className="field-semantic" role="cell" />
                    <span role="cell" className="field-actions">
                      <button type="button" onClick={saveNewField} disabled={!fieldDraft.field_name.trim()}>
                        保存
                      </button>
                      <button type="button" onClick={cancelFieldEdit}>
                        取消
                      </button>
                    </span>
                  </div>
                )}
               {fields.map((field) => (
                  <div className="extract-field-row" role="row" key={field.field_key}>
                    {editingFieldKey === field.field_key ? (
                      <>
                        <span className="field-name editing" role="cell">
                          <input
                            className="field-edit-input"
                            aria-label="编辑字段名称"
                            value={fieldDraft.field_name}
                            onChange={(event) => updateFieldDraft("field_name", event.target.value)}
                          />
                        </span>
                        <span className="field-description editing" role="cell">
                          <input
                            className="field-edit-input"
                            aria-label="编辑字段描述"
                            value={fieldDraft.description}
                            onChange={(event) => updateFieldDraft("description", event.target.value)}
                          />
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="field-name" role="cell" title={field.field_name}>
                          {field.field_name}
                        </span>
                        <span className="field-description" role="cell" title={field.description}>
                          {field.description}
                        </span>
                      </>
                    )}
                  <span className="field-semantic" role="cell">
                    <button
                       className={semanticEnabled[field.field_key] !== false ? "field-switch on" : "field-switch"}
                       type="button"
                       aria-label={`${field.field_name}语义提取`}
                       aria-pressed={semanticEnabled[field.field_key] !== false}
                       title={semanticEnabled[field.field_key] !== false ? "语义提取" : "严格匹配"}
                       onClick={() => {
                         setSemanticEnabled((prev) => ({
                           ...prev,
                           [field.field_key]: prev[field.field_key] === false ? true : false,
                         }));
                       }}
                       />
                     <small>{semanticEnabled[field.field_key] !== false ? "语义" : "严格"}</small>
                   </span>
                    <span role="cell" className="field-actions">
                      {editingFieldKey === field.field_key ? (
                        <>
                          <button type="button" onClick={saveFieldEdit} disabled={!fieldDraft.field_name.trim()}>
                            保存
                          </button>
                          <button type="button" onClick={cancelFieldEdit}>
                            取消
                          </button>
                        </>
                      ) : (
                        <>
                          <button type="button" onClick={() => startFieldEdit(field)}>
                            编辑
                          </button>
                          <button type="button" onClick={() => removeField(field.field_key)}>
                            移除
                          </button>
                        </>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}

function extractionMethodLabel(method: ExtractionFieldValue["extraction_method"]): string {
  if (method === "explicit") {
    return "严格匹配";
  }
  if (method === "semantic") {
    return "语义提取";
  }
  return "提取";
}

function isLongExtractionValue(value: string): boolean {
  return value.length > 72 || value.includes("\n");
}


function FlatPdfPreview({ src, file }: { src: string; file: File }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [loadState, setLoadState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(DEFAULT_PREVIEW_ZOOM);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());

  useEffect(() => {
    if (!src) {
      setPdf(null);
      setLoadState("idle");
      setCurrentPage(1);
      setZoom(DEFAULT_PREVIEW_ZOOM);
      return undefined;
    }

    let isMounted = true;
    const loadingTask = pdfjsLib.getDocument(src);
    setLoadState("loading");
    setCurrentPage(1);
    setZoom(DEFAULT_PREVIEW_ZOOM);
    loadingTask.promise
      .then((document) => {
        if (!isMounted) {
          document.destroy();
          return;
        }
        setPdf(document);
        setLoadState("ready");
      })
      .catch(() => {
        if (isMounted) {
          setPdf(null);
          setLoadState("error");
        }
      });

    return () => {
      isMounted = false;
      loadingTask.destroy();
    };
  }, [src]);

  const updateCurrentPageFromScroll = useCallback(() => {
    const scrollNode = scrollRef.current;
    if (!scrollNode) {
      return;
    }
    const pages = Array.from(pageRefs.current.entries())
      .map(([pageNumber, node]) => ({
        pageNumber,
        offsetTop: node.offsetTop,
        offsetHeight: node.offsetHeight,
      }))
      .sort((left, right) => left.pageNumber - right.pageNumber);
    setCurrentPage(getCurrentPageFromScroll(scrollNode.scrollTop, scrollNode.clientHeight, pages));
  }, []);

  useEffect(() => {
    const frameId = window.requestAnimationFrame(updateCurrentPageFromScroll);
    return () => window.cancelAnimationFrame(frameId);
  }, [pdf, updateCurrentPageFromScroll, zoom]);

  function scrollToPage(pageNumber: number) {
    const totalPages = pdf?.numPages ?? 1;
    const targetPage = Math.min(totalPages, Math.max(1, pageNumber));
    const scrollNode = scrollRef.current;
    const pageNode = pageRefs.current.get(targetPage);
    if (scrollNode && pageNode) {
      scrollNode.scrollTo({ top: Math.max(0, pageNode.offsetTop - 16), behavior: "smooth" });
    }
    setCurrentPage(targetPage);
  }

  function goToPreviousPage() {
    scrollToPage(currentPage - 1);
  }

  function goToNextPage() {
    scrollToPage(currentPage + 1);
  }

  function handlePageChange(event: ChangeEvent<HTMLInputElement>) {
    const nextPage = Number(event.target.value);
    if (!Number.isFinite(nextPage)) {
      return;
    }
    scrollToPage(nextPage);
  }

  function zoomOut() {
    setZoom((value) => Math.max(MIN_PREVIEW_ZOOM, Number((value - PREVIEW_ZOOM_STEP).toFixed(2))));
  }

  function zoomIn() {
    setZoom((value) => Math.min(MAX_PREVIEW_ZOOM, Number((value + PREVIEW_ZOOM_STEP).toFixed(2))));
  }

  function handleScroll(_event: UIEvent<HTMLDivElement>) {
    updateCurrentPageFromScroll();
  }

  if (!src || loadState === "idle") {
    return <DocumentFallback file={file} />;
  }

  const totalPages = pdf?.numPages ?? 0;
  const renderedZoom = PREVIEW_RENDER_SCALE * zoom;

  return (
    <div className="extract-flat-pdf" aria-label="合同 PDF 预览">
      {loadState === "loading" && <div className="extract-document-loading">正在载入 PDF...</div>}
      {loadState === "error" && <DocumentFallback file={file} />}
      {pdf && (
        <>
          <div ref={scrollRef} className="extract-pdf-scroll-shell" onScroll={handleScroll}>
            {Array.from({ length: pdf.numPages }, (_, index) => (
              <ExtractPdfPageCanvas
                key={`${src}-${index + 1}-${renderedZoom}`}
                ref={(node) => {
                  if (node) {
                    pageRefs.current.set(index + 1, node);
                  } else {
                    pageRefs.current.delete(index + 1);
                  }
                }}
                pdf={pdf}
                pageNumber={index + 1}
                zoom={renderedZoom}
              />
            ))}
          </div>
          <div className="extract-pdf-toolbar" aria-label="PDF预览工具栏">
            <button type="button" onClick={goToPreviousPage} disabled={currentPage <= 1} aria-label="上一页" title="上一页">
              <ChevronLeft aria-hidden="true" />
            </button>
            <label className="extract-page-control">
              <input
                aria-label="当前页码"
                type="number"
                min={1}
                max={totalPages}
                value={currentPage}
                onChange={handlePageChange}
              />
              <span>/ {totalPages}</span>
            </label>
            <button type="button" onClick={goToNextPage} disabled={currentPage >= totalPages} aria-label="下一页" title="下一页">
              <ChevronRight aria-hidden="true" />
            </button>
            <button type="button" onClick={zoomOut} disabled={zoom <= MIN_PREVIEW_ZOOM} aria-label="缩小PDF" title="缩小">
              <ZoomOut aria-hidden="true" />
            </button>
            <output className="extract-zoom-value" aria-label="当前缩放比例">
              {Math.round(zoom * 100)}%
            </output>
            <button type="button" onClick={zoomIn} disabled={zoom >= MAX_PREVIEW_ZOOM} aria-label="放大PDF" title="放大">
              <ZoomIn aria-hidden="true" />
            </button>
          </div>
        </>
      )}
    </div>
  );
}

const ExtractPdfPageCanvas = forwardRef<
  HTMLDivElement,
  { pdf: PDFDocumentProxy; pageNumber: number; zoom: number }
>(function ExtractPdfPageCanvas({ pdf, pageNumber, zoom }, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [pageSize, setPageSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    let isMounted = true;
    let renderTask: RenderTask | null = null;

    pdf.getPage(pageNumber).then((page) => {
      if (!isMounted) {
        return;
      }
      const viewport = page.getViewport({ scale: zoom });
      const canvas = canvasRef.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context) {
        return;
      }
      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      setPageSize({ width: viewport.width, height: viewport.height });
      const task = page.render({
        canvas,
        canvasContext: context,
        viewport,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
      });
      renderTask = task;
      task.promise.catch(() => {
        // Rendering can be cancelled while replacing uploaded files.
      });
    });

    return () => {
      isMounted = false;
      renderTask?.cancel();
    };
  }, [pageNumber, pdf, zoom]);

  return (
    <div
      ref={ref}
      className="extract-pdf-page-frame"
      style={{ width: pageSize.width || undefined, height: pageSize.height || undefined }}
      data-page-number={pageNumber}
    >
      <canvas ref={canvasRef} aria-label={`第 ${pageNumber} 页`} />
    </div>
  );
});

function DocumentFallback({
  file,
  message = "合同文件已上传，正在准备预览。",
  isError = false,
}: {
  file: File;
  message?: string;
  isError?: boolean;
}) {
  return (
    <div className={isError ? "extract-document-fallback error" : "extract-document-fallback"}>
      <span aria-hidden="true" />
      <strong>{file.name}</strong>
      <p>{message}</p>
    </div>
  );
}
