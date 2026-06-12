// UI types (used by page components — kept unchanged)
export type TaskStatus = "PROCESSING" | "COMPLETED" | "FAILED";
export type ExtractionFieldStatus = "found" | "not_found" | "error";

export interface ExtractionFieldValue {
  field_id: string;
  field_name: string;
  field_key: string;
  value: string;
  confidence: number;
  source_snippet: string;
  status: ExtractionFieldStatus;
  extraction_method?: "explicit" | "semantic" | null;
}

export interface ExtractionTaskResponse {
  task_id: string;
  task_type: string;
  status: TaskStatus;
  stage: string;
  filename: string;
  file_url: string;
  extractor_used: string;
  fields: { id: string; name: string; type: string; description: string; semantic_extraction: boolean }[];
  results: ExtractionFieldValue[];
  errors: string[];
}

export interface ExtractionRecordSummary {
  task_id: string;
  task_type: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  filename: string;
  file_url: string;
  extractor_used: string;
  field_count: number;
  found_count: number;
  not_found_count: number;
  error_count: number;
}


export interface BBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

// ────────────────────────────────────────────────────────────
// Backend-matching types (aligned with Pydantic schemas)

export interface BBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

// ────────────────────────────────────────────────────────────

export interface ApiResponse<T = unknown> {
  code: number;
  message: string;
  data: T;
}

export interface UploadResponse {
  contract_id: string;
  file_id: string;
  task_id: string;
  status: string;
}

export interface ContractBrief {
  id: string;
  title: string | null;
  file_name: string;
  file_type: string;
  contract_type: string | null;
  status: string;
  created_at: string;
}

export interface ContractList {
  items: ContractBrief[];
  total: number;
  page: number;
  page_size: number;
}

export interface FileBrief {
  id: string;
  file_name: string;
  file_type: string;
  file_size: number;
}

export interface FieldDetail {
  id: string;
  field_name: string;
  field_key: string;
  field_category: string;
  value: string | null;
  source_text: string | null;
  page_no: number | null;
  confidence: number | null;
  extract_method: string;
  review_status: string;
}

export interface ClauseDetail {
  id: string;
  clause_type: string | null;
  clause_title: string | null;
  content: string;
  page_no: number | null;
  confidence: number | null;
  review_status: string;
}

export interface RiskDetail {
  id: string;
  risk_level: string;
  risk_type: string;
  description: string;
  evidence: string | null;
  suggestion: string | null;
  source_text: string | null;
  review_status: string;
}

export interface ContractDetail {
  id: string;
  title: string | null;
  status: string;
  page_count: number | null;
  files: FileBrief[];
  fields: FieldDetail[];
  clauses: ClauseDetail[];
  risks: RiskDetail[];
  created_at: string;
  updated_at: string;
}

export interface TaskDetail {
  id: string;
  contract_id: string;
  task_type: string;
  status: string;
  progress: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
}


export interface BBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

// ────────────────────────────────────────────────────────────
// Mapper functions: backend types → UI types

export interface BBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

// ────────────────────────────────────────────────────────────

export function fieldDetailToExtractionFieldValue(f: FieldDetail): ExtractionFieldValue {
  let status: ExtractionFieldStatus;
  if (f.value && (f.confidence ?? 0) >= 0.5) {
    status = "found";
  } else if (f.value) {
    status = "found";
  } else {
    status = "not_found";
  }

  let extraction_method: "explicit" | "semantic" | null = null;
  if (f.extract_method === "rule" || f.extract_method === "regex") {
    extraction_method = "explicit";
  } else if (f.extract_method === "llm" || f.extract_method === "semantic") {
    extraction_method = "semantic";
  }

    return {
    field_id: f.id,
    field_name: f.field_name,
    field_key: f.field_key,
    value: f.value ?? "",
    confidence: f.confidence ?? 0,
    source_snippet: f.source_text ?? "",
    status,
    extraction_method,
  };
}

export function contractBriefToExtractionRecordSummary(
  c: ContractBrief,
): ExtractionRecordSummary {
  return {
    task_id: c.id,
    task_type: "full_pipeline",
    status: mapContractStatusToTaskStatus(c.status),
    created_at: c.created_at,
    updated_at: c.created_at,
    filename: c.file_name || "",
    file_url: "",
    extractor_used: c.contract_type ?? "-",
    field_count: 0,
    found_count: 0,
    not_found_count: 0,
    error_count: 0,
  };
}

function mapContractStatusToTaskStatus(s: string): TaskStatus {
  if (s === "processing" || s === "uploaded") return "PROCESSING";
  if (s === "reviewing" || s === "approved") return "COMPLETED";
  if (s === "failed") return "FAILED";
  return "PROCESSING";
}

export function contractDetailToExtractionTaskResponse(
  detail: ContractDetail,
  fileName?: string,
): ExtractionTaskResponse {
  const fields = detail.fields.map((f) => fieldDetailToExtractionFieldValue(f));

  return {
    task_id: detail.id,
    task_type: "full_pipeline",
    status: "COMPLETED",
    stage: "completed",
    filename: fileName ?? detail.files?.[0]?.file_name ?? "",
    file_url: "",
    extractor_used: "",
    fields: [],
    results: fields,
    errors: [],
  };
}
