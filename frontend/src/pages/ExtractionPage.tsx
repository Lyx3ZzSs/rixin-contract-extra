import { forwardRef, type ChangeEvent, type UIEvent, useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist/types/src/pdf";

import { getCurrentPageFromScroll } from "../components/pdfPageScroll";
import type { FieldDefinitionItem } from "../lib/api";
import {
  createFieldDefinition,
  getContractDetail,
  getTask,
  prepareContract,
  startContractExtraction,
} from "../lib/api";
import { downloadBatchExtractionResultsWorkbook } from "../lib/excelExport";
import { fieldDetailToExtractionFieldValue } from "../types";
import { readExtractionFieldLibrary } from "../lib/extractionFieldLibrary";
import type { ExtractionFieldValue, FieldDetail, UploadResponse } from "../types";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

const DEFAULT_PREVIEW_ZOOM = 1;
const PREVIEW_RENDER_SCALE = 0.9;
const MIN_PREVIEW_ZOOM = 0.8;
const MAX_PREVIEW_ZOOM = 1.8;
const PREVIEW_ZOOM_STEP = 0.1;
const MAX_BATCH_FILES = 5;
const IMAGE_MAX_SIZE = 5 * 1024 * 1024;
const DOCUMENT_MAX_SIZE = 60 * 1024 * 1024;
const ALLOWED_EXTRACT_EXTENSIONS = new Set(["pdf", "png", "jpg", "jpeg", "bmp"]);
const prepareContractCache = new WeakMap<File, Promise<UploadResponse>>();

type BatchStatus = "pending" | "processing" | "completed" | "failed" | "skipped";

interface BatchFileItem {
  id: string;
  file: File;
  documentUrl: string;
  upload: UploadResponse | null;
  ocrStatus: BatchStatus;
  ocrStage: string;
  extractionStatus: BatchStatus;
  extractionStage: string;
  results: ExtractionFieldValue[] | null;
  error: string;
}

export function ExtractionPage() {
  const [batchItems, setBatchItems] = useState<BatchFileItem[]>([]);
  const [uploadError, setUploadError] = useState("");

  function handleFilesChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFiles = Array.from(event.target.files ?? []);
    event.target.value = "";
    const validationError = validateBatchFiles(selectedFiles);
    if (validationError) {
      setUploadError(validationError);
      return;
    }

    const nextItems = selectedFiles.map(createBatchFileItem);
    setUploadError("");
    setBatchItems(nextItems);
  }

  function returnToUpload() {
    for (const item of batchItems) {
      URL.revokeObjectURL(item.documentUrl);
    }
    setBatchItems([]);
  }

  if (batchItems.length > 0) {
    return <ExtractionFieldSetup initialItems={batchItems} onBack={returnToUpload} />;
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
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.bmp,application/pdf,image/png,image/jpeg,image/bmp"
            aria-label="上传合同提取文件"
            onChange={handleFilesChange}
          />
          <span className="extract-upload-icon" aria-hidden="true">
            <i>PDF</i>
            <i>IMG</i>
          </span>
          <strong>拖拽文件上传或点击上传本地文件</strong>
          <small>支持格式 pdf/png/jpg/jpeg/bmp，数量不超过5份，图片5MB以内，其他文件60MB以内</small>
        </label>
        {uploadError && (
          <div className="extract-error-banner extract-upload-error" role="alert">
            {uploadError}
          </div>
        )}
      </form>
    </section>
  );
}

interface ExtractionFieldSetupProps {
  initialItems: BatchFileItem[];
  onBack: () => void;
}

function ExtractionFieldSetup({ initialItems, onBack }: ExtractionFieldSetupProps) {
  const [items, setItems] = useState<BatchFileItem[]>(initialItems);
  const [activeItemId, setActiveItemId] = useState(initialItems[0]?.id ?? "");
  const activeItem = items.find((item) => item.id === activeItemId) ?? items[0];
  const activeFile = activeItem?.file ?? initialItems[0]?.file;
  const isPdf = Boolean(activeFile && (activeFile.type === "application/pdf" || activeFile.name.toLowerCase().endsWith(".pdf")));
  const [fields, setFields] = useState<FieldDefinitionItem[]>([]);
  const [libraryFields, setLibraryFields] = useState<FieldDefinitionItem[]>([]);
  const [fieldLibraryState, setFieldLibraryState] = useState<"loading" | "ready" | "error">("loading");
  const [fieldLibraryError, setFieldLibraryError] = useState("");
  const [isLibraryPickerOpen, setIsLibraryPickerOpen] = useState(false);
  const [selectedLibraryKeys, setSelectedLibraryKeys] = useState<Set<string>>(() => new Set());
  const [fieldActionMessage, setFieldActionMessage] = useState("");
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [semanticEnabled, setSemanticEnabled] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setFieldLibraryState("loading");
    setFieldLibraryError("");
    readExtractionFieldLibrary()
      .then((items) => {
        setLibraryFields(items);
        setFields(items.filter((f) => f.required));
        setFieldLibraryState("ready");
      })
      .catch((err) => {
        const message = err instanceof Error ? err.message : "字段库加载失败";
        setFieldLibraryError(message);
        setFieldLibraryState("error");
      });
  }, []);
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractionError, setExtractionError] = useState("");
  const [expandedResultKeys, setExpandedResultKeys] = useState<Set<string>>(() => new Set());
  const [currentStep, setCurrentStep] = useState<1 | 2 | 3>(2);
  const batchRunIdRef = useRef(0);
  const ocrSyncPromiseByItemRef = useRef(new Map<string, Promise<void>>());
  const [editingFieldKey, setEditingFieldKey] = useState("");
  const [isAddingField, setIsAddingField] = useState(false);
  const [fieldDraft, setFieldDraft] = useState<Pick<FieldDefinitionItem, "field_name" | "description">>({
    field_name: "",
    description: "",
  });

  const updateItem = useCallback((itemId: string, patch: Partial<BatchFileItem>) => {
    setItems((currentItems) =>
      currentItems.map((item) => (item.id === itemId ? { ...item, ...patch } : item)),
    );
  }, []);

  useEffect(() => {
    const runId = batchRunIdRef.current + 1;
    batchRunIdRef.current = runId;
    ocrSyncPromiseByItemRef.current = new Map();
    setExtractionError("");

    initialItems.forEach((item) => {
      const readyPromise = syncPreparedOcrItem(item, runId);
      ocrSyncPromiseByItemRef.current.set(item.id, readyPromise);
    });
  }, [initialItems, updateItem]);

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
    setFieldActionMessage("");
  }

  async function saveNewField() {
    const name = fieldDraft.field_name.trim();
    const description = fieldDraft.description.trim();
    if (!name) return;
    const field_key = createCustomFieldKey(name);
    try {
      const created = await createFieldDefinition({
        field_key,
        field_name: name,
        description,
        value_type: "string",
        required: false,
        sort_order: Math.floor(Date.now() / 1000),
      });
      setLibraryFields((currentFields) => [...currentFields, created]);
      setFields((prev) => [...prev, created]);
      setFieldActionMessage(`已添加字段：${name}`);
      cancelFieldEdit();
    } catch (err) {
      setFieldActionMessage(err instanceof Error ? err.message : "字段保存失败");
    }
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
      setLibraryFields(items);
      setFields((currentFields) =>
        currentFields.map((field) =>
          field.field_key === editingFieldKey
            ? { ...field, field_name: nextName, description: nextDescription }
            : field,
        ),
      );
      setFieldActionMessage(`已更新字段：${nextName}`);
    } catch { /* ignore */ }
    cancelFieldEdit();
  }

  async function openFieldLibraryPicker() {
    setFieldActionMessage("");
    setSelectedLibraryKeys(new Set());
    setIsLibraryPickerOpen(true);
    if (fieldLibraryState === "ready" && libraryFields.length > 0) {
      return;
    }
    setFieldLibraryState("loading");
    setFieldLibraryError("");
    try {
      const items = await readExtractionFieldLibrary();
      setLibraryFields(items);
      setFieldLibraryState("ready");
    } catch (err) {
      setFieldLibraryError(err instanceof Error ? err.message : "字段库加载失败");
      setFieldLibraryState("error");
    }
  }

  function toggleLibrarySelection(fieldKey: string) {
    setSelectedLibraryKeys((currentKeys) => {
      const nextKeys = new Set(currentKeys);
      if (nextKeys.has(fieldKey)) {
        nextKeys.delete(fieldKey);
      } else {
        nextKeys.add(fieldKey);
      }
      return nextKeys;
    });
  }

  function confirmLibrarySelection() {
    const existingKeys = new Set(fields.map((field) => field.field_key));
    const selectedFields = libraryFields.filter((field) => selectedLibraryKeys.has(field.field_key) && !existingKeys.has(field.field_key));
    if (selectedFields.length === 0) {
      setFieldActionMessage("请选择尚未添加的字段");
      return;
    }
    setFields((currentFields) => [...currentFields, ...selectedFields]);
    setFieldActionMessage(`已从字段库添加 ${selectedFields.length} 个字段`);
    setIsLibraryPickerOpen(false);
    setSelectedLibraryKeys(new Set());
  }

  function startExcelImport() {
    setFieldActionMessage("");
    importInputRef.current?.click();
  }

  async function handleImportFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0] ?? null;
    event.target.value = "";
    if (!selectedFile) {
      return;
    }
    try {
      const importedRows = await parseFieldImportFile(selectedFile);
      const existingKeys = new Set([...libraryFields, ...fields].map((field) => field.field_key));
      const importedFields: FieldDefinitionItem[] = [];
      for (const row of importedRows) {
        const name = row.field_name.trim();
        if (!name) continue;
        let fieldKey = row.field_key?.trim() || createCustomFieldKey(name);
        while (existingKeys.has(fieldKey)) {
          fieldKey = createCustomFieldKey(name);
        }
        existingKeys.add(fieldKey);
        const created = await createFieldDefinition({
          field_key: fieldKey,
          field_name: name,
          description: row.description.trim(),
          value_type: row.value_type || "string",
          required: false,
          sort_order: fields.length + importedFields.length + 1,
        });
        importedFields.push(created);
      }
      if (importedFields.length === 0) {
        setFieldActionMessage("未识别到可导入字段，请检查表头或第一列字段名称");
        return;
      }
      setLibraryFields((currentFields) => [...currentFields, ...importedFields]);
      setFields((currentFields) => [...currentFields, ...importedFields]);
      setFieldActionMessage(`已导入 ${importedFields.length} 个字段`);
    } catch (err) {
      setFieldActionMessage(err instanceof Error ? err.message : "字段导入失败");
    }
  }

  const stageMessages: Record<string, string> = {
    pending: "等待处理...",
    queued: "等待任务调度...",
    uploaded: "已上传，等待开始...",
    file_detecting: "正在检测文件类型...",
    text_extracting: "正在提取文本...",
    field_extracting: "正在提取字段...",
    completed: "处理完成",
    running: "正在处理...",
    retrying: "等待重试...",
    failed: "处理失败",
    cancelled: "已取消",
    timed_out: "处理超时",
  };

  async function waitForTaskCompletion(
    taskId: string,
    onStatus: (status: string) => void,
    failureMessage: string,
  ) {
    const maxAttempts = 180;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      const poll = await getTask(taskId);
      onStatus(poll.stage || poll.status);

      if (poll.status === "completed") {
        return;
      }
      if (poll.status === "failed" || poll.status === "cancelled" || poll.status === "timed_out" || poll.status.endsWith("_failed")) {
        throw new Error(poll.error_message || failureMessage);
      }
      await new Promise((resolve) => setTimeout(resolve, attempt < 10 ? 1000 : 2000));
    }
    throw new Error("处理超时，请稍后查看结果");
  }

  function applyCurrentBatchPatch(itemId: string, runId: number, patch: Partial<BatchFileItem>) {
    if (batchRunIdRef.current !== runId) {
      return;
    }
    updateItem(itemId, patch);
  }

  function ensurePrepareStarted(file: File): Promise<UploadResponse> {
    let uploadPromise = prepareContractCache.get(file);
    if (!uploadPromise) {
      uploadPromise = prepareContract(file);
      prepareContractCache.set(file, uploadPromise);
    }
    return uploadPromise;
  }

  async function syncPreparedOcrItem(item: BatchFileItem, runId: number) {
    try {
      applyCurrentBatchPatch(item.id, runId, { ocrStatus: "processing", ocrStage: "uploaded", error: "" });
      const uploadResult = await ensurePrepareStarted(item.file);
      applyCurrentBatchPatch(item.id, runId, { upload: uploadResult, ocrStage: "uploaded" });
      await waitForTaskCompletion(
        uploadResult.task_id,
        (status) => applyCurrentBatchPatch(item.id, runId, { ocrStage: status }),
        "OCR预处理失败",
      );
      applyCurrentBatchPatch(item.id, runId, { ocrStatus: "completed", ocrStage: "completed", error: "" });
    } catch (err) {
      applyCurrentBatchPatch(item.id, runId, {
        ocrStatus: "failed",
        ocrStage: "failed",
        error: err instanceof Error ? err.message : "OCR预处理失败",
      });
    }
  }

  async function extractSingleBatchItem(
    item: BatchFileItem,
    selectedFields: FieldDefinitionItem[],
    onPatch: (itemId: string, patch: Partial<BatchFileItem>) => void,
  ) {
    if (!item.upload) {
      onPatch(item.id, {
        extractionStatus: "failed",
        error: "文件预处理尚未开始，请重新选择文件",
      });
      return;
    }

    try {
      onPatch(item.id, { extractionStatus: "processing", extractionStage: "field_extracting", error: "" });
      const extractionTask = await startContractExtraction(item.upload.contract_id, selectedFields);
      await waitForTaskCompletion(
        extractionTask.task_id,
        (status) => onPatch(item.id, { extractionStage: status }),
        "提取失败",
      );
      const detail = await getContractDetail(item.upload.contract_id);
      onPatch(item.id, {
        extractionStatus: "completed",
        extractionStage: "completed",
        results: buildFieldValues(selectedFields, detail.fields),
        error: "",
      });
    } catch (err) {
      onPatch(item.id, {
        extractionStatus: "failed",
        extractionStage: "failed",
        error: err instanceof Error ? err.message : "提取失败，请重试",
      });
    }
  }

  async function handleStartExtraction() {
    if (fields.length === 0) {
      setExtractionError("请先添加至少一个提取字段");
      setFieldActionMessage("请先添加至少一个提取字段");
      return;
    }

    setIsExtracting(true);
    setExtractionError("");
    setExpandedResultKeys(new Set());
    setCurrentStep(3);
    const runId = batchRunIdRef.current;
    const selectedFields = fields.map((field) => ({ ...field }));
    const itemsToExtract = await getLatestItems();
    setItems((currentItems) =>
      currentItems.map((item) => ({
        ...item,
        extractionStatus: item.ocrStatus === "failed" ? "skipped" : "pending",
        extractionStage: "",
        results: null,
        error: item.ocrStatus === "failed" ? item.error || "OCR预处理失败" : item.error,
      })),
    );

    try {
      await Promise.allSettled(
        itemsToExtract.map((item) => runExtractionWhenOcrReady(item, selectedFields, runId)),
      );
      const latestItems = await getLatestItems();
      const hasCompleted = latestItems.some((item) => item.extractionStatus === "completed");
      const hasTerminal = latestItems.some((item) =>
        item.extractionStatus === "completed" ||
        item.extractionStatus === "failed" ||
        item.extractionStatus === "skipped",
      );
      if (!hasCompleted && !hasTerminal) {
        setExtractionError("没有可提取的文件，请确认 OCR 预处理完成后重试");
      }
    } catch (err) {
      setExtractionError(err instanceof Error ? err.message : "提取失败，请重试");
    } finally {
      setIsExtracting(false);
    }
  }

  async function runExtractionWhenOcrReady(
    item: BatchFileItem,
    selectedFields: FieldDefinitionItem[],
    runId: number,
  ) {
    const readyPromise = ocrSyncPromiseByItemRef.current.get(item.id);
    if (readyPromise) {
      await readyPromise;
    }
    if (batchRunIdRef.current !== runId) {
      return;
    }

    let latestItem = (await getLatestItems()).find((candidate) => candidate.id === item.id) ?? item;
    if (!latestItem.upload) {
      try {
        const upload = await ensurePrepareStarted(item.file);
        latestItem = { ...latestItem, upload };
        updateItem(item.id, { upload });
      } catch (err) {
        updateItem(item.id, {
          ocrStatus: "failed",
          ocrStage: "failed",
          extractionStatus: "skipped",
          extractionStage: "failed",
          error: err instanceof Error ? err.message : "OCR预处理失败",
        });
        return;
      }
    }

    if (latestItem.ocrStatus !== "completed") {
      const [refreshedItem] = await refreshOcrTaskStatuses([latestItem]);
      latestItem = refreshedItem;
      updateItem(item.id, refreshedItem);
    }

    if (latestItem.ocrStatus === "completed" && latestItem.upload) {
      await extractSingleBatchItem(latestItem, selectedFields, updateItem);
      return;
    }

    updateItem(item.id, {
      extractionStatus: "skipped",
      extractionStage: latestItem.ocrStage || "failed",
      error: latestItem.error || "OCR预处理失败，已跳过提取",
    });
  }

  async function refreshOcrTaskStatuses(currentItems: BatchFileItem[]): Promise<BatchFileItem[]> {
    return Promise.all(currentItems.map(async (item) => {
      if (!item.upload || item.ocrStatus === "completed" || item.ocrStatus === "failed") {
        return item;
      }
      try {
        const task = await getTask(item.upload.task_id);
        if (task.status === "completed") {
          return { ...item, ocrStatus: "completed" as const, ocrStage: "completed", error: "" };
        }
        if (task.status === "failed" || task.status === "cancelled" || task.status === "timed_out" || task.status.endsWith("_failed")) {
          return {
            ...item,
            ocrStatus: "failed" as const,
            ocrStage: task.stage || task.status,
            error: task.error_message || "OCR预处理失败",
          };
        }
        return { ...item, ocrStage: task.stage || task.status };
      } catch (err) {
        return {
          ...item,
          ocrStatus: "failed" as const,
          ocrStage: "failed",
          error: err instanceof Error ? err.message : "OCR状态刷新失败",
        };
      }
    }));
  }

  async function getLatestItems(): Promise<BatchFileItem[]> {
    return new Promise((resolve) => {
      setItems((currentItems) => {
        resolve(currentItems);
        return currentItems;
      });
    });
  }

  function returnToFieldSetup() {
    setItems((currentItems) =>
      currentItems.map((item) => ({
        ...item,
        extractionStatus: item.ocrStatus === "failed" ? "skipped" : "pending",
        extractionStage: "",
        results: null,
      })),
    );
    setExpandedResultKeys(new Set());
    setExtractionError("");
    setCurrentStep(2);
  }

  function toggleResultExpansion(resultKey: string) {
    setExpandedResultKeys((currentKeys) => {
      const nextKeys = new Set(currentKeys);
      if (nextKeys.has(resultKey)) {
        nextKeys.delete(resultKey);
      } else {
        nextKeys.add(resultKey);
      }
      return nextKeys;
    });
  }

  function handleExport() {
    if (!hasBatchResults) return;
    downloadBatchExtractionResultsWorkbook("批量合同提取结果", fields, items.map((item) => ({
      fileName: item.file.name,
      status: item.extractionStatus,
      error: item.error,
      results: item.results,
    })));
  }

  const activeFieldCount = fields.length;
  const results = activeItem?.results ?? null;
  const hasBatchResults = items.some((item) =>
    item.extractionStatus === "completed" || item.extractionStatus === "failed" || item.extractionStatus === "skipped",
  );
  const preparingCount = items.filter((item) => item.ocrStatus === "processing" || item.ocrStatus === "pending").length;
  const ocrCompletedCount = items.filter((item) => item.ocrStatus === "completed").length;
  const ocrFailedCount = items.filter((item) => item.ocrStatus === "failed").length;
  const activeProgressMessage = getBatchProgressMessage(activeItem, isExtracting, stageMessages);
  const pendingFieldValueText = activeItem ? getPendingFieldValueText(activeItem) : "等待提取...";
  const showPendingFieldRows = Boolean(activeItem && !results && shouldShowPendingFieldRows(activeItem, isExtracting));

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
              <li className={currentStep >= 2 ? (hasBatchResults ? "done" : "active") : ""}>
                <span>{currentStep >= 3 && hasBatchResults ? "✓" : "2"}</span>
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
            onClick={hasBatchResults ? handleExport : handleStartExtraction}
            disabled={isExtracting || (!hasBatchResults && activeFieldCount === 0)}
            title={
              !hasBatchResults && activeFieldCount === 0
                  ? "请先添加至少一个提取字段"
                  : undefined
            }
          >
            {isExtracting ? "正在提取..." : hasBatchResults ? "导出" : "开始提取"}
          </button>
      </header>

      <div className="extract-flow-body">
          <aside className="extract-file-list" aria-label="合同文件列表">
            <label className="extract-search">
              <span>请输入</span>
              <input aria-label="搜索合同文件" />
            </label>
            <div className="extract-file-items">
              {items.map((item, index) => (
                <button
                  className={item.id === activeItem?.id ? "active" : ""}
                  type="button"
                  title={item.file.name}
                  key={item.id}
                  onClick={() => {
                    setActiveItemId(item.id);
                    setExpandedResultKeys(new Set());
                  }}
                >
                  <span>{index + 1}.</span>
                  <strong>{item.file.name}</strong>
                  <small className={`extract-file-status ${batchStatusTone(item)}`}>{batchStatusLabel(item)}</small>
                </button>
              ))}
            </div>
          </aside>

          <main className="extract-preview" aria-label="合同预览">
            <div className="extract-document-page">
              {!activeItem || !activeFile ? (
                <DocumentFallback file={initialItems[0].file} message="请选择合同文件" />
              ) : isPdf ? (
                <FlatPdfPreview src={activeItem.documentUrl} file={activeFile} />
              ) : (
                <DocumentFallback file={activeFile} />
              )}
            </div>
          </main>

          <aside className="extract-field-panel" aria-label="字段列表">
            {currentStep === 3 && (isExtracting || hasBatchResults) ? (
              <div className="extract-card-view">
                <div className="extract-card-header">
                  <div>
                    <h2>提取字段信息</h2>
                    <p>{activeItem?.file.name ?? "合同文件"}</p>
                  </div>
                  <button type="button" className="extract-back-link" onClick={returnToFieldSetup}>
                    返回字段设置
                  </button>
                </div>
                {extractionError && (
                  <div className="extract-error-banner" role="alert">{extractionError}</div>
                )}
                {activeProgressMessage && (
                  <div className="extract-status-bar">
                    <span className="extract-status-spinner" aria-hidden="true" />
                    {activeProgressMessage}
                  </div>
                )}
                {(activeItem?.extractionStatus === "failed" || activeItem?.extractionStatus === "skipped") && (
                  <div className="extract-error-banner" role="alert">
                    {activeItem.error || "该文件未完成提取"}
                  </div>
                )}
                {results ? (
                  <div className="extract-card-list">
                    {results.map((r) => {
                      const valueText =
                        r.status === "found" ? (r.value || "—") : r.status === "not_found" ? "未找到" : "提取失败";
                      const resultKey = getExtractionResultIdentity(r);
                      const isExpanded = expandedResultKeys.has(resultKey);
                      const canExpand = r.status === "found" && isLongExtractionValue(valueText);
                      return (
                        <div className="extract-card-item result" key={resultKey}>
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
                                onClick={() => toggleResultExpansion(resultKey)}
                              >
                                {isExpanded ? "收起" : "展开"}
                              </button>
                            )}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                ) : showPendingFieldRows ? (
                  <div className="extract-card-list">
                    {fields.map((field) => (
                      <div className="extract-card-item" key={field.field_key}>
                        <span className="extract-card-name">{field.field_name}</span>
                        <span className="extract-card-value loading">{pendingFieldValueText}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : (
            <>
              <div className="extract-field-panel-head">
                <h2>字段列表</h2>
               <div className="extract-field-actions" aria-label="字段操作">
                  <button type="button" onClick={startAddField}>+ 自定义添加</button>
                  <button type="button" onClick={openFieldLibraryPicker}>从字段库/模板添加</button>
                  <button type="button" onClick={startExcelImport}>从Excel导入</button>
                </div>
              </div>
              <input
                ref={importInputRef}
                type="file"
                accept=".csv,.tsv,.txt,.xlsx"
                className="extract-hidden-file-input"
                aria-label="导入字段文件"
                onChange={handleImportFileChange}
              />
              {fieldLibraryState === "loading" && fields.length === 0 && (
                <div className="extract-info-banner">正在加载字段库...</div>
              )}
              {fieldLibraryError && (
                <div className="extract-error-banner" role="alert">
                  字段库加载失败：{fieldLibraryError}
                </div>
              )}
              {fieldActionMessage && (
                <div className={fieldActionMessage.includes("失败") || fieldActionMessage.includes("请先") || fieldActionMessage.includes("未识别") ? "extract-error-banner" : "extract-info-banner"} role="status">
                  {fieldActionMessage}
                </div>
              )}
                {(preparingCount > 0 || ocrCompletedCount > 0 || ocrFailedCount > 0) && (
                  <div className={ocrFailedCount > 0 ? "extract-info-banner" : "extract-info-banner"} role="status">
                    {ocrCompletedCount === items.length
                      ? "全部文件 OCR 预处理完成，点击开始提取可直接进入字段提取。"
                      : `OCR预处理中：已完成 ${ocrCompletedCount}/${items.length}，处理中 ${preparingCount}，失败 ${ocrFailedCount}`}
                  </div>
                )}
              {extractionError && (
                <div className="extract-error-banner" role="alert">
                  {extractionError}
                </div>
              )}
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
              {fields.length === 0 && fieldLibraryState !== "loading" && (
                <div className="extract-field-empty">
                  <strong>暂无提取字段</strong>
                  <span>请自定义添加、从字段库选择，或导入字段清单后开始提取。</span>
                </div>
              )}
            </>
          )}
        </aside>
      </div>
      {isLibraryPickerOpen && (
        <div className="extract-modal-backdrop" role="presentation" onMouseDown={() => setIsLibraryPickerOpen(false)}>
          <div
            className="extract-field-picker"
            role="dialog"
            aria-modal="true"
            aria-labelledby="field-picker-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <h2 id="field-picker-title">从字段库/模板添加</h2>
                <p>选择需要加入当前合同提取任务的字段。</p>
              </div>
              <button type="button" aria-label="关闭字段库" onClick={() => setIsLibraryPickerOpen(false)}>
                ×
              </button>
            </header>
            {fieldLibraryState === "loading" ? (
              <div className="extract-picker-state">正在加载字段库...</div>
            ) : fieldLibraryState === "error" ? (
              <div className="extract-error-banner" role="alert">
                字段库加载失败：{fieldLibraryError}
              </div>
            ) : (
              <div className="extract-picker-list">
                {libraryFields.length === 0 ? (
                  <div className="extract-picker-state">字段库为空，请先在字段管理中维护字段。</div>
                ) : (
                  libraryFields.map((field) => {
                    const alreadyAdded = fields.some((currentField) => currentField.field_key === field.field_key);
                    return (
                      <label className={alreadyAdded ? "extract-picker-item disabled" : "extract-picker-item"} key={field.field_key}>
                        <input
                          type="checkbox"
                          checked={selectedLibraryKeys.has(field.field_key) || alreadyAdded}
                          disabled={alreadyAdded}
                          onChange={() => toggleLibrarySelection(field.field_key)}
                        />
                        <span>
                          <strong>{field.field_name}</strong>
                          <small>{field.description || "无字段描述"}</small>
                        </span>
                        {alreadyAdded && <em>已添加</em>}
                      </label>
                    );
                  })
                )}
              </div>
            )}
            <footer>
              <button type="button" onClick={() => setIsLibraryPickerOpen(false)}>
                取消
              </button>
              <button type="button" className="primary" onClick={confirmLibrarySelection} disabled={selectedLibraryKeys.size === 0}>
                添加选中字段
              </button>
            </footer>
          </div>
        </div>
      )}
    </section>
  );
}

function createBatchFileItem(file: File): BatchFileItem {
  return {
    id: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`,
    file,
    documentUrl: URL.createObjectURL(file),
    upload: null,
    ocrStatus: "pending",
    ocrStage: "pending",
    extractionStatus: "pending",
    extractionStage: "",
    results: null,
    error: "",
  };
}

function validateBatchFiles(files: File[]): string {
  if (files.length === 0) {
    return "";
  }
  if (files.length > MAX_BATCH_FILES) {
    return `一次最多上传 ${MAX_BATCH_FILES} 份合同，请减少文件数量后重试。`;
  }

  for (const file of files) {
    const extension = getFileExtension(file.name);
    if (!ALLOWED_EXTRACT_EXTENSIONS.has(extension)) {
      return `文件“${file.name}”格式不支持，请上传 pdf、png、jpg、jpeg 或 bmp。`;
    }
    const maxSize = isImageFile(file) ? IMAGE_MAX_SIZE : DOCUMENT_MAX_SIZE;
    if (file.size > maxSize) {
      return `文件“${file.name}”超过大小限制，图片需小于5MB，其他文件需小于60MB。`;
    }
  }

  return "";
}

function getFileExtension(fileName: string): string {
  return fileName.includes(".") ? fileName.split(".").pop()?.toLowerCase() ?? "" : "";
}

function isImageFile(file: File): boolean {
  const extension = getFileExtension(file.name);
  return ["png", "jpg", "jpeg", "bmp"].includes(extension) || file.type.startsWith("image/");
}

function batchStatusLabel(item: BatchFileItem): string {
  if (item.extractionStatus === "completed") return "已提取";
  if (item.extractionStatus === "failed") return "提取失败";
  if (item.extractionStatus === "skipped") return "OCR失败";
  if (item.extractionStatus === "processing") return "提取中";
  if (item.ocrStatus === "completed") return "OCR完成";
  if (item.ocrStatus === "failed") return "OCR失败";
  if (item.ocrStatus === "processing") return "OCR中";
  return "等待OCR";
}

function batchStatusTone(item: BatchFileItem): string {
  if (item.extractionStatus === "failed" || item.extractionStatus === "skipped" || item.ocrStatus === "failed") {
    return "error";
  }
  if (item.extractionStatus === "completed" || item.ocrStatus === "completed") {
    return "done";
  }
  return "active";
}

function shouldShowPendingFieldRows(item: BatchFileItem, isExtracting: boolean): boolean {
  if (item.extractionStatus === "failed" || item.extractionStatus === "skipped") {
    return false;
  }
  if (item.extractionStatus === "processing" || item.ocrStatus === "completed") {
    return true;
  }
  return isExtracting && (item.ocrStatus === "pending" || item.ocrStatus === "processing");
}

function getPendingFieldValueText(item: BatchFileItem): string {
  if (item.ocrStatus === "pending" || item.ocrStatus === "processing") {
    return "等待OCR...";
  }
  if (item.extractionStatus === "processing") {
    return "提取中...";
  }
  return "等待提取...";
}

function getBatchProgressMessage(
  item: BatchFileItem | undefined,
  isExtracting: boolean,
  stageMessages: Record<string, string>,
): string {
  if (!item || item.extractionStatus === "failed" || item.extractionStatus === "skipped") {
    return "";
  }
  if (item.extractionStatus === "processing") {
    return stageMessages[item.extractionStage] || "正在处理合同...";
  }
  if (!isExtracting) {
    return "";
  }
  if (item.ocrStatus === "pending" || item.ocrStatus === "processing") {
    return stageMessages[item.ocrStage] || "等待OCR预处理完成...";
  }
  if (item.ocrStatus === "completed" && item.extractionStatus === "pending") {
    return "等待开始字段提取...";
  }
  return "";
}

function buildFieldValues(fields: FieldDefinitionItem[], backendFields: FieldDetail[]): ExtractionFieldValue[] {
  const backendByFieldKey = new Map<string, ReturnType<typeof fieldDetailToExtractionFieldValue>>();
  for (const backendField of backendFields) {
    const fieldValue = fieldDetailToExtractionFieldValue(backendField);
    if (fieldValue.field_key) {
      backendByFieldKey.set(fieldValue.field_key, fieldValue);
    }
  }

  return fields.map((fieldDef) => {
    const matched = backendByFieldKey.get(fieldDef.field_key);
    if (matched) {
      return matched;
    }
    return {
      field_id: "",
      field_name: fieldDef.field_name,
      field_key: fieldDef.field_key,
      value: "",
      confidence: 0,
      source_snippet: "",
      status: "not_found" as const,
      extraction_method: null,
    };
  });
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

function getExtractionResultIdentity(result: ExtractionFieldValue): string {
  return result.field_key || result.field_id || result.field_name;
}

function isLongExtractionValue(value: string): boolean {
  return value.length > 72 || value.includes("\n");
}

interface ImportedFieldRow {
  field_key?: string;
  field_name: string;
  description: string;
  value_type?: string;
}

function createCustomFieldKey(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `custom-${slug || "field"}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

async function parseFieldImportFile(file: File): Promise<ImportedFieldRow[]> {
  const fileName = file.name.toLowerCase();
  if (fileName.endsWith(".xlsx")) {
    return parseXlsxFieldRows(file);
  }
  if (fileName.endsWith(".xls")) {
    throw new Error("暂不支持旧版 .xls，请另存为 .xlsx 或 CSV 后导入");
  }
  const text = await file.text();
  return parseDelimitedFieldRows(text);
}

function parseDelimitedFieldRows(text: string): ImportedFieldRow[] {
  const rows = parseDelimitedRows(text).filter((row) => row.some((cell) => cell.trim()));
  if (rows.length === 0) {
    return [];
  }
  return rowsToImportedFields(rows);
}

function parseDelimitedRows(text: string): string[][] {
  const normalized = text.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const delimiter = pickDelimiter(normalized);
  const rows: string[][] = [];
  let current = "";
  let row: string[] = [];
  let inQuotes = false;

  for (let index = 0; index < normalized.length; index++) {
    const char = normalized[index];
    const nextChar = normalized[index + 1];
    if (char === "\"") {
      if (inQuotes && nextChar === "\"") {
        current += "\"";
        index++;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (!inQuotes && char === delimiter) {
      row.push(current.trim());
      current = "";
      continue;
    }
    if (!inQuotes && char === "\n") {
      row.push(current.trim());
      rows.push(row);
      row = [];
      current = "";
      continue;
    }
    current += char;
  }
  if (current || row.length > 0) {
    row.push(current.trim());
    rows.push(row);
  }
  return rows;
}

function pickDelimiter(text: string): string {
  const firstLine = text.split("\n").find((line) => line.trim()) || "";
  const candidates = [",", "\t", ";"];
  return candidates.reduce((best, candidate) =>
    firstLine.split(candidate).length > firstLine.split(best).length ? candidate : best,
  );
}

function rowsToImportedFields(rows: string[][]): ImportedFieldRow[] {
  const header = rows[0].map(normalizeHeader);
  const nameIndex = findHeaderIndex(header, ["字段名称", "字段名", "field_name", "name"]);
  const descIndex = findHeaderIndex(header, ["字段描述", "描述", "description", "desc"]);
  const keyIndex = findHeaderIndex(header, ["字段标识", "字段key", "field_key", "key"]);
  const typeIndex = findHeaderIndex(header, ["值类型", "value_type", "type"]);
  const hasHeader = nameIndex >= 0 || descIndex >= 0 || keyIndex >= 0;
  const dataRows = hasHeader ? rows.slice(1) : rows;
  const effectiveNameIndex = hasHeader && nameIndex >= 0 ? nameIndex : 0;
  const effectiveDescIndex = hasHeader ? descIndex : 1;

  return dataRows
    .map((row) => ({
      field_key: keyIndex >= 0 ? row[keyIndex]?.trim() : undefined,
      field_name: row[effectiveNameIndex]?.trim() || "",
      description: effectiveDescIndex >= 0 ? row[effectiveDescIndex]?.trim() || "" : "",
      value_type: typeIndex >= 0 ? row[typeIndex]?.trim() || "string" : "string",
    }))
    .filter((row) => row.field_name);
}

function normalizeHeader(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, "_");
}

function findHeaderIndex(headers: string[], aliases: string[]): number {
  const normalizedAliases = aliases.map(normalizeHeader);
  return headers.findIndex((header) => normalizedAliases.includes(header));
}

async function parseXlsxFieldRows(file: File): Promise<ImportedFieldRow[]> {
  const entries = await unzipXlsxEntries(await file.arrayBuffer());
  const sheetName = Object.keys(entries)
    .filter((name) => /^xl\/worksheets\/sheet\d+\.xml$/i.test(name))
    .sort()[0];
  if (!sheetName) {
    throw new Error("未在 Excel 文件中找到工作表");
  }
  const sharedStrings = parseSharedStrings(entries["xl/sharedStrings.xml"]);
  const rows = parseWorksheetRows(entries[sheetName], sharedStrings);
  return rowsToImportedFields(rows);
}

async function unzipXlsxEntries(buffer: ArrayBuffer): Promise<Record<string, string>> {
  const view = new DataView(buffer);
  const decoder = new TextDecoder("utf-8");
  let eocdOffset = -1;
  for (let offset = view.byteLength - 22; offset >= 0; offset--) {
    if (view.getUint32(offset, true) === 0x06054b50) {
      eocdOffset = offset;
      break;
    }
  }
  if (eocdOffset < 0) {
    throw new Error("Excel 文件格式无效");
  }

  const entryCount = view.getUint16(eocdOffset + 10, true);
  let centralOffset = view.getUint32(eocdOffset + 16, true);
  const entries: Record<string, string> = {};

  for (let i = 0; i < entryCount; i++) {
    if (view.getUint32(centralOffset, true) !== 0x02014b50) {
      throw new Error("Excel 文件目录损坏");
    }
    const method = view.getUint16(centralOffset + 10, true);
    const compressedSize = view.getUint32(centralOffset + 20, true);
    const fileNameLength = view.getUint16(centralOffset + 28, true);
    const extraLength = view.getUint16(centralOffset + 30, true);
    const commentLength = view.getUint16(centralOffset + 32, true);
    const localHeaderOffset = view.getUint32(centralOffset + 42, true);
    const nameBytes = new Uint8Array(buffer, centralOffset + 46, fileNameLength);
    const entryName = decoder.decode(nameBytes);

    if (entryName.endsWith(".xml")) {
      const localNameLength = view.getUint16(localHeaderOffset + 26, true);
      const localExtraLength = view.getUint16(localHeaderOffset + 28, true);
      const dataOffset = localHeaderOffset + 30 + localNameLength + localExtraLength;
      const compressed = new Uint8Array(buffer, dataOffset, compressedSize);
      const data = await inflateZipEntry(compressed, method);
      entries[entryName] = decoder.decode(data);
    }

    centralOffset += 46 + fileNameLength + extraLength + commentLength;
  }

  return entries;
}

async function inflateZipEntry(data: Uint8Array, method: number): Promise<Uint8Array> {
  if (method === 0) {
    return data;
  }
  if (method !== 8) {
    throw new Error("Excel 压缩格式暂不支持");
  }
  if (typeof DecompressionStream === "undefined") {
    throw new Error("当前浏览器不支持直接解析 .xlsx，请另存为 CSV 后导入");
  }
  const dataBuffer = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
  const stream = new Blob([dataBuffer]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  const buffer = await new Response(stream).arrayBuffer();
  return new Uint8Array(buffer);
}

function parseSharedStrings(xml: string | undefined): string[] {
  if (!xml) {
    return [];
  }
  const documentNode = new DOMParser().parseFromString(xml, "application/xml");
  return Array.from(documentNode.getElementsByTagName("si")).map((item) =>
    Array.from(item.getElementsByTagName("t")).map((node) => node.textContent || "").join(""),
  );
}

function parseWorksheetRows(xml: string | undefined, sharedStrings: string[]): string[][] {
  if (!xml) {
    return [];
  }
  const documentNode = new DOMParser().parseFromString(xml, "application/xml");
  return Array.from(documentNode.getElementsByTagName("row")).map((rowNode) => {
    const cells: string[] = [];
    Array.from(rowNode.getElementsByTagName("c")).forEach((cellNode) => {
      const ref = cellNode.getAttribute("r") || "";
      const columnIndex = columnNameToIndex(ref.replace(/\d+/g, ""));
      const type = cellNode.getAttribute("t");
      const rawValue = cellNode.getElementsByTagName("v")[0]?.textContent || "";
      const inlineValue = Array.from(cellNode.getElementsByTagName("t")).map((node) => node.textContent || "").join("");
      let value = rawValue;
      if (type === "s") {
        value = sharedStrings[Number(rawValue)] || "";
      } else if (type === "inlineStr") {
        value = inlineValue;
      }
      cells[columnIndex] = value.trim();
    });
    return cells.map((cell) => cell || "");
  });
}

function columnNameToIndex(columnName: string): number {
  let index = 0;
  for (const char of columnName) {
    index = index * 26 + char.toUpperCase().charCodeAt(0) - 64;
  }
  return Math.max(index - 1, 0);
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
