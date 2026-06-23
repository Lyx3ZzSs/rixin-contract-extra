import type {
  ApiResponse,
  ContractBrief,
  ContractDetail,
  ContractList,
  ExtractionRecordSummary,
  FieldDetail,
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
// API-Key auth
// ────────────────────────────────────────────────────────────

const API_KEY_STORAGE = "rixin_contract_api_key";

export function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE);
}

export function setApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE, key);
}

export function clearApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE);
}

function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { "X-API-Key": key } : {};
}

function handle401(response: Response): void {
  if (response.status === 401) {
    clearApiKey();
    if (window.location.pathname !== "/") {
      window.location.assign("/");
    } else {
      window.location.reload();
    }
    throw new Error("未授权，请重新登录");
  }
}

// ────────────────────────────────────────────────────────────
// Generic fetch helpers
// ────────────────────────────────────────────────────────────

async function parseJsonResponse<T>(response: Response): Promise<T> {
  handle401(response);
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
  handle401(response);
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

export async function prepareContract(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(toApiUrl("/api/v1/contracts/prepare"), {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  return parseApiResponse<UploadResponse>(response);
}

export async function startContractExtraction(
  contractId: string,
  fields?: FieldDefinitionItem[],
): Promise<UploadResponse> {
  const body = fields && fields.length > 0
    ? JSON.stringify({
        fields: fields.map(({ field_key, field_name, description, value_type }) => ({
          field_key,
          field_name,
          description,
          value_type,
        })),
      })
    : undefined;
  const response = await fetch(toApiUrl(`/api/v1/contracts/${contractId}/extract`), {
    method: "POST",
    headers: body ? { "Content-Type": "application/json", ...authHeaders() } : authHeaders(),
    body,
  });
  return parseApiResponse<UploadResponse>(response);
}

// ────────────────────────────────────────────────────────────
// Task polling
// ────────────────────────────────────────────────────────────

export async function getTask(taskId: string): Promise<TaskDetail> {
  const response = await fetch(toApiUrl(`/api/v1/tasks/${taskId}`), { headers: authHeaders() });
  return parseJsonResponse<TaskDetail>(response);
}

export async function getContractDetail(contractId: string): Promise<ContractDetail> {
  const response = await fetch(toApiUrl(`/api/v1/contracts/${contractId}`), { headers: authHeaders() });
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
  const response = await fetch(toApiUrl(`/api/v1/contracts?${params.toString()}`), { headers: authHeaders() });
  return parseJsonResponse<ContractList>(response);
}

// ────────────────────────────────────────────────────────────
// File download URL builder
// ────────────────────────────────────────────────────────────

export function downloadContractFileUrl(contractId: string): string {
  let url = toApiUrl(`/api/v1/contracts/${contractId}/files/download`);
  const key = getApiKey();
  if (key) url += `?api_key=${encodeURIComponent(key)}`;
  return url;
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

export async function getExtractionTask(taskId: string) {
  const { contractDetailToExtractionTaskResponse } = await import("../types");
  const detail = await getContractDetail(taskId);
  return contractDetailToExtractionTaskResponse(detail);
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
  const response = await fetch(toApiUrl("/api/v1/field-definitions"), { headers: authHeaders() });
  return parseJsonResponse<FieldDefinitionItem[]>(response);
}

export async function createFieldDefinition(
  field: Omit<FieldDefinitionItem, "id" | "is_active">,
): Promise<FieldDefinitionItem> {
  const response = await fetch(toApiUrl("/api/v1/field-definitions"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
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
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(updates),
  });
  return parseJsonResponse<FieldDefinitionItem>(response);
}

export async function deleteFieldDefinition(fieldKey: string): Promise<void> {
  const response = await fetch(toApiUrl(`/api/v1/field-definitions/${fieldKey}`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!response.ok) {
    handle401(response);
    throw new Error(`删除字段失败 (${response.status})`);
  }
}

export async function resetFieldDefinitions(): Promise<FieldDefinitionItem[]> {
  const response = await fetch(toApiUrl("/api/v1/field-definitions/reset"), {
    method: "POST",
    headers: authHeaders(),
  });
  return parseJsonResponse<FieldDefinitionItem[]>(response);
}

// --- Review (human-in-the-loop) ---

export interface FieldReviewRequest {
  action: "modify" | "approve" | "reject";
  new_value?: string;
  comment?: string;
}

export interface BatchReviewItem {
  field_id: string;
  action: "modify" | "approve" | "reject";
  new_value?: string;
  comment?: string;
}

export async function reviewField(
  contractId: string,
  fieldId: string,
  body: FieldReviewRequest,
): Promise<FieldDetail> {
  const response = await fetch(toApiUrl(`/api/v1/contracts/${contractId}/fields/${fieldId}/review`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  return parseJsonResponse<FieldDetail>(response);
}

export async function batchReviewFields(
  contractId: string,
  items: BatchReviewItem[],
): Promise<unknown[]> {
  const response = await fetch(toApiUrl(`/api/v1/contracts/${contractId}/review/batch`), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      reviewer_id: "web",
      items: items.map((it) => ({
        target_type: "field",
        target_id: it.field_id,
        action: it.action,
        new_value: it.new_value,
        comment: it.comment,
      })),
    }),
  });
  return parseJsonResponse<unknown[]>(response);
}

export interface ReviewRecord {
  id: string;
  target_type: string;
  target_id: string;
  action: string;
  old_value: string | null;
  new_value: string | null;
  comment: string | null;
  reviewer_id: string | null;
  created_at: string;
}

export async function listReviewRecords(contractId: string): Promise<ReviewRecord[]> {
  const response = await fetch(toApiUrl(`/api/v1/contracts/${contractId}/review/records`), { headers: authHeaders() });
  const data = await parseJsonResponse<{ items: ReviewRecord[]; total: number }>(response);
  return data.items;
}
