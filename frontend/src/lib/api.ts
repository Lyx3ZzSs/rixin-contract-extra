import type {
  ApiResponse,
  ContractBrief,
  ContractDetail,
  ContractList,
  ExtractionRecordSummary,
  ExtractionTaskResponse,
  TaskDetail,
  UploadResponse,
} from "../types";


export function getApiBaseUrl(): string {
  // In production, use the relative path (proxy handles it).
  // For absolute URLs, set VITE_API_BASE_URL.
  const configured = import.meta.env.VITE_API_BASE_URL;
  return configured ? (configured as string).replace(/\/+$/, "") : "";
}

export function toApiUrl(path: string): string {
  if (!path) return "";
  if (/^https?:\/\//i.test(path)) return path;
  const prefix = getApiBaseUrl();
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${prefix}${normalized}`;
}

// ────────────────────────────────────────────────────────────
// Generic fetch helpers
// ────────────────────────────────────────────────────────────

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // keep status-based message
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

async function parseApiResponse<T>(response: Response): Promise<T> {
  // Upload endpoint returns ApiResponse<T>; other endpoints return T directly.
  const json = await parseJsonResponse<ApiResponse<T> | T>(response);
  if (json && typeof json === "object" && "code" in json && "message" in json && "data" in json) {
    return (json as ApiResponse<T>).data;
  }
  return json as T;
}


// ────────────────────────────────────────────────────────────
// Contract upload
// ────────────────────────────────────────────────────────────

export async function uploadContract(file: File, customFields?: FieldDefinitionItem[]): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (customFields && customFields.length > 0) {
    const fields = customFields.map(({ field_key, field_name, description, value_type }) => ({
      field_key,
      field_name,
      description,
      value_type,
    }));
    formData.append("custom_fields", JSON.stringify(fields));
  }
  const response = await fetch(toApiUrl("/api/v1/contracts/upload"), {
    method: "POST",
    body: formData,
  });
  return parseApiResponse<UploadResponse>(response);
}

// ────────────────────────────────────────────────────────────
// Task polling
// ────────────────────────────────────────────────────────────

export async function getTask(taskId: string): Promise<TaskDetail> {
  const response = await fetch(toApiUrl(`/api/v1/tasks/${taskId}`));
  return parseJsonResponse<TaskDetail>(response);
}

export async function getContractDetail(contractId: string): Promise<ContractDetail> {
  const response = await fetch(toApiUrl(`/api/v1/contracts/${contractId}`));
  return parseJsonResponse<ContractDetail>(response);
}

// ────────────────────────────────────────────────────────────
// Contract list (extraction records)
// ────────────────────────────────────────────────────────────

export async function listContracts(
  status?: string,
  page = 1,
  pageSize = 100,
): Promise<ContractList> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  const response = await fetch(toApiUrl(`/api/v1/contracts?${params.toString()}`));
  return parseJsonResponse<ContractList>(response);
}

// ────────────────────────────────────────────────────────────
// Preview (Word → PDF)
// ────────────────────────────────────────────────────────────

export async function createExtractionPreview(file: File): Promise<Blob> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(toApiUrl("/api/v1/contracts/preview"), {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    let message = `预览失败 (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // keep status message
    }
    throw new Error(message);
  }
  return response.blob();
}

// ────────────────────────────────────────────────────────────
// File download URL builder
// ────────────────────────────────────────────────────────────

export function downloadContractFileUrl(contractId: string): string {
  return toApiUrl(`/api/v1/contracts/${contractId}/files/download`);
}

// ────────────────────────────────────────────────────────────
// Legacy compat wrappers (used by existing page components)
// ────────────────────────────────────────────────────────────

export async function getExtractionRecords(): Promise<ExtractionRecordSummary[]> {
  const { contractBriefToExtractionRecordSummary } = await import("../types");
  const contractList = await listContracts(undefined, 1, 100);
  const allItems: ContractBrief[] = [];
  for (let page = 1; page <= Math.ceil(contractList.total / contractList.page_size); page++) {
    if (page === 1) {
      allItems.push(...contractList.items);
    } else {
      const nextPage = await listContracts(undefined, page, contractList.page_size);
      allItems.push(...nextPage.items);
    }
  }
  return allItems.map(contractBriefToExtractionRecordSummary);
}

export async function getExtractionTask(taskId: string): Promise<ExtractionTaskResponse> {
  const { contractDetailToExtractionTaskResponse } = await import("../types");
  // In the new backend, taskId is actually a contract ID
  const detail = await getContractDetail(taskId);
  return contractDetailToExtractionTaskResponse(detail);
}

// Re-extract with fields — for backward compat with ExtractionPage
export async function extractFields(
  file: File,
  _fields: { id: string; name: string; type: string; description: string; semanticExtraction: boolean }[],
): Promise<ExtractionTaskResponse & { contract_id: string }> {
  const uploadResp = await uploadContract(file);
  // Return a shape that ExtractionPage can consume
  return {
    task_id: uploadResp.task_id,
    task_type: "full_pipeline",
    status: "PROCESSING",
    stage: "uploaded",
    filename: file.name,
    file_url: downloadContractFileUrl(uploadResp.contract_id),
    extractor_used: "",
    fields: [],
    results: [],
    errors: [],
    contract_id: uploadResp.contract_id,
  };
}

// ────────────────────────────────────────────────────────────
// Field Definitions (CRUD)
// ────────────────────────────────────────────────────────────

export interface FieldDefinitionItem {
  id: string;
  field_key: string;
  field_name: string;
  description: string;
  value_type: string;
  required: boolean;
  sort_order: number;
  is_active: boolean;
}

export async function listFieldDefinitions(): Promise<FieldDefinitionItem[]> {
  const response = await fetch(toApiUrl("/api/v1/field-definitions"));
  return parseJsonResponse<FieldDefinitionItem[]>(response);
}

export async function createFieldDefinition(
  field: Omit<FieldDefinitionItem, "id" | "is_active">,
): Promise<FieldDefinitionItem> {
  const response = await fetch(toApiUrl("/api/v1/field-definitions"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(field),
  });
  return parseJsonResponse<FieldDefinitionItem>(response);
}

export async function updateFieldDefinition(
  fieldKey: string,
  updates: Partial<Pick<FieldDefinitionItem, "field_name" | "description" | "value_type" | "required" | "sort_order" | "is_active">>,
): Promise<FieldDefinitionItem> {
  const response = await fetch(toApiUrl(`/api/v1/field-definitions/${fieldKey}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  return parseJsonResponse<FieldDefinitionItem>(response);
}

export async function deleteFieldDefinition(fieldKey: string): Promise<void> {
  const response = await fetch(toApiUrl(`/api/v1/field-definitions/${fieldKey}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`删除字段失败 (${response.status})`);
  }
}

export async function resetFieldDefinitions(): Promise<FieldDefinitionItem[]> {
  const response = await fetch(toApiUrl("/api/v1/field-definitions/reset"), {
    method: "POST",
  });
  return parseJsonResponse<FieldDefinitionItem[]>(response);
}
