# Phase 1 · Product Review Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn read-only extraction results into a human-in-the-loop review surface: inline-editable field values with audit trail, review-status badges, batch review, source/page/confidence display, and multi-format export.

**Architecture:** Frontend-only. The backend review API already exists (`api/review.py`: `PATCH /contracts/{id}/fields/{fid}/review`, `POST /contracts/{id}/review/batch`, `GET /contracts/{id}/review/records`, writing `reviewed_value` without overwriting `value`). This plan wires those endpoints into the React UI, aligns the `FieldDetail` type with the backend schema, and extends export. No backend changes.

**Tech Stack:** React 19 + TypeScript 5.8 + Vite 6. No UI library (custom CSS in `styles.css`). No state/router libs. **No frontend test framework** (spec D7 — do not introduce one); verification = `tsc -b` type-check + manual UI checklist per task.

**Spec:** `docs/superpowers/specs/2026-06-23-phase1-review-loop-and-accuracy-foundation-design.md` items ④⑤⑥⑦. Branches from `feat/phase1-accuracy-engine`.

## Global Constraints

- **Frontend-only** — no backend changes. All review endpoints already exist and return resources directly (use `parseJsonResponse`, not `parseApiResponse`).
- **Edit writes `reviewed_value`, never overwrites `value`** — the original extracted value is the accuracy-measurement / few-shot truth source (spec D4). UI shows "original / corrected" when a correction exists.
- **No new test framework** (YAGNI, spec D7). Each task's verification = `cd frontend && npx tsc -b` (type-check) + `npm run build` succeeds + the manual checklist.
- **`low_confidence_threshold` is a frontend constant (0.7)** — confidence is already in the response; no backend config (spec D6/§4.3).
- **Follow existing CSS conventions** — kebab-case, feature prefixes (`.extract-*`, `.extraction-*`), reuse `--teal-strong/soft`, `--red`, `--amber` custom properties. Reuse `.field-edit-input` for edit inputs.
- **Commit style:** conventional commits. One commit per task (or logical step).

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `frontend/src/types.ts` | Align `FieldDetail` with backend; extend `ExtractionFieldValue`; fix mapper | 1 |
| `frontend/src/lib/api.ts` | Add 3 review-endpoint calls | 2 |
| `frontend/src/pages/ExtractionPage.tsx` | Inline review edit + status badge + save | 3 |
| `frontend/src/pages/ExtractionPage.tsx` | Batch review + source/page/confidence display + low-conf flag | 4 |
| `frontend/src/pages/ExtractionRecordsPage.tsx` | Review badge + inline edit + history drawer + export button | 5 |
| `frontend/src/lib/excelExport.ts` | CSV/JSON builders + extra columns | 6 |
| `frontend/src/pages/ExtractionPage.tsx` + `ExtractionRecordsPage.tsx` | Export-format dropdown UI | 6 |
| `frontend/src/styles.css` | `.extract-review-badge`, edit modifiers, export menu | 3,4,5,6 |

---

## Task 1: Align `FieldDetail` type and fix the mapper

**Files:**
- Modify: `frontend/src/types.ts` (`FieldDetail` ~:93-106, `ExtractionFieldValue` ~:5-14, `fieldDetailToExtractionFieldValue` ~:159-186)

**Interfaces:**
- Consumes: backend `FieldDetail` (`schemas/contract.py:56-74`) which returns `value_type`, `reviewed_value`, `reviewer_id`, `reviewed_at` (already present server-side).
- Produces: `FieldDetail` with `value_type`/`reviewed_value`/`reviewer_id`/`reviewed_at` added; `ExtractionFieldValue` with `review_status`/`reviewed_value`/`page_no`/`source_text` added; mapper propagates them. Downstream tasks rely on these fields.

- [ ] **Step 1: Extend `FieldDetail`**

In `frontend/src/types.ts`, replace the `FieldDetail` interface with (adds `value_type`, `reviewed_value`, `reviewer_id`, `reviewed_at`; keeps `extract_method` as optional since backend doesn't send it — see note):

```ts
export interface FieldDetail {
  id: string;
  field_name: string;
  field_key: string;
  value: string | null;
  value_type: string;
  source_text: string | null;
  page_no: number | null;
  confidence: number | null;
  source_paragraph_id?: number | null;
  source_block_start?: number | null;
  source_block_end?: number | null;
  extract_method?: string;
  review_status: string;
  reviewed_value: string | null;
  reviewer_id: string | null;
  reviewed_at: string | null;
}
```

- [ ] **Step 2: Extend `ExtractionFieldValue`**

Replace the `ExtractionFieldValue` interface (~:5-14) with:

```ts
export interface ExtractionFieldValue {
  field_id: string;
  field_name: string;
  field_key: string;
  value: string;
  confidence: number;
  source_snippet: string;
  source_text: string | null;
  page_no: number | null;
  status: ExtractionFieldStatus;
  extraction_method?: "explicit" | "semantic" | null;
  review_status: string;
  reviewed_value: string | null;
}
```

- [ ] **Step 3: Rewrite the mapper**

Replace `fieldDetailToExtractionFieldValue` (~:159-186) with (removes the dead confidence branch, propagates review + source fields):

```ts
export function fieldDetailToExtractionFieldValue(f: FieldDetail): ExtractionFieldValue {
  const status: ExtractionFieldStatus = f.value ? "found" : "not_found";

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
    source_text: f.source_text,
    page_no: f.page_no,
    status,
    extraction_method,
    review_status: f.review_status,
    reviewed_value: f.reviewed_value,
  };
}
```

- [ ] **Step 4: Type-check + build**

Run: `cd frontend && npx tsc -b`
Expected: no errors. If `extract_method` is referenced elsewhere as required, make call-sites tolerate `undefined`. Then `npm run build` → succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts
git commit -m "refactor(frontend): align FieldDetail with backend schema, propagate review fields"
```

---

## Task 2: Add review-endpoint calls to the API client

**Files:**
- Modify: `frontend/src/lib/api.ts` (append after the field-definition CRUD, ~line 212)

**Interfaces:**
- Consumes: `FieldDetail`, `ContractDetail` from `types.ts`; the fetch patterns `parseJsonResponse` + `toApiUrl` (existing).
- Produces: `reviewField`, `batchReviewFields`, `listReviewRecords`. Downstream tasks call these.

- [ ] **Step 1: Add the three calls**

Append to `frontend/src/lib/api.ts` (mirror the `updateFieldDefinition` JSON-body pattern; backend returns the resource directly so use `parseJsonResponse`):

```ts
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
    headers: { "Content-Type": "application/json" },
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
    headers: { "Content-Type": "application/json" },
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
  const response = await fetch(toApiUrl(`/api/v1/contracts/${contractId}/review/records`));
  const data = await parseJsonResponse<{ items: ReviewRecord[]; total: number }>(response);
  return data.items;
}
```

- [ ] **Step 2: Type-check + build**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(frontend): add review field/batch/records API client calls"
```

---

## Task 3: Inline review edit + status badge on ExtractionPage

**Files:**
- Modify: `frontend/src/pages/ExtractionPage.tsx` (result card JSX ~:773-794; add review state near ~:176)
- Modify: `frontend/src/styles.css` (add `.extract-review-badge` + edit modifiers)

**Interfaces:**
- Consumes: `reviewField` from Task 2; `ExtractionFieldValue.review_status`/`reviewed_value` from Task 1; `fieldDetailToExtractionFieldValue` to re-map the PATCH response.
- Produces: per-field inline edit that saves via `PATCH /fields/{fid}/review` (action `modify`), a review-status badge, and local result refresh.

- [ ] **Step 1: Add review-edit state**

In `ExtractionFieldSetup` (the component containing the result card), near the existing `editingFieldKey`/`fieldDraft` state (~:176), add:

```tsx
const [reviewingFieldKey, setReviewingFieldKey] = useState<string | null>(null);
const [reviewDraft, setReviewDraft] = useState<string>("");
const [reviewSavingKey, setReviewSavingKey] = useState<string | null>(null);
```

(Import `useState` is already present.) These mirror the existing field-name edit pattern at lines 208-281.

- [ ] **Step 2: Add the save handler**

Add inside the same component (mirror `saveFieldEdit`'s shape; `updateItem` refreshes the batch item's `results`):

```tsx
async function saveFieldReview(fieldKey: string, fieldId: string) {
  if (!fieldId || !activeItem) return;
  setReviewSavingKey(fieldKey);
  try {
    const updated = await reviewField(activeItem.upload.contract_id, fieldId, {
      action: "modify",
      new_value: reviewDraft,
    });
    const remapped = fieldDetailToExtractionFieldValue(updated);
    // Replace the matching result in this item's results.
    const nextResults = (activeItem.results ?? []).map((r) =>
      r.field_key === fieldKey ? { ...r, ...remapped } : r,
    );
    updateItem(activeItem.item.item_id, { results: nextResults });
    setReviewingFieldKey(null);
  } catch (err) {
    console.error("review save failed", err);
    alert("复核保存失败，请重试");
  } finally {
    setReviewSavingKey(null);
  }
}
```

Note: `activeItem.results` and `updateItem(itemId, patch)` already exist (used at ~:183-187, :466-471). `activeItem.upload.contract_id` is the contract id. If the exact `activeItem` shape differs in the file, adapt the field paths but keep the logic (PATCH → re-map → replace in results → clear editing state).

- [ ] **Step 3: Render the review badge + inline edit affordance**

In the result-card JSX (~:773-794), inside `<div className="extract-card-item result">`, add a review badge next to the method badge, and replace the value-text span with an editable control when reviewing. The block becomes:

```tsx
<div className="extract-card-item result" key={resultKey}>
  <span className="extract-card-name">
    {r.field_name}
    <small className="extract-method-badge">{extractionMethodLabel(r.extraction_method)}</small>
    {r.review_status && r.review_status !== "extracted" && (
      <small className={`extract-review-badge ${r.review_status}`}>
        {reviewStatusLabel(r.review_status)}
      </small>
    )}
  </span>
  {reviewingFieldKey === r.field_key ? (
    <span className="extract-card-value editing">
      <input
        className="field-edit-input"
        value={reviewDraft}
        onChange={(e) => setReviewDraft(e.target.value)}
        autoFocus
      />
      <button
        type="button"
        className="extract-value-toggle"
        disabled={reviewSavingKey === r.field_key}
        onClick={() => saveFieldReview(r.field_key, r.field_id)}
      >
        {reviewSavingKey === r.field_key ? "保存中" : "保存"}
      </button>
      <button
        type="button"
        className="extract-value-toggle"
        onClick={() => setReviewingFieldKey(null)}
      >
        取消
      </button>
    </span>
  ) : (
    <span className={`extract-card-value${r.status === "not_found" ? " empty" : r.status === "error" ? " error" : ""}${isExpanded ? " expanded" : ""}`} title={valueText}>
      <span className="extract-card-value-text">
        {r.reviewed_value ? (
          <>
            <span className="extract-card-corrected">{r.reviewed_value}</span>
            <small className="extract-card-original">原值：{valueText}</small>
          </>
        ) : (
          valueText
        )}
      </span>
      {r.status === "found" && r.field_id && (
        <button type="button" className="extract-value-toggle" onClick={() => { setReviewingFieldKey(r.field_key); setReviewDraft(r.reviewed_value || r.value); }}>
          修正
        </button>
      )}
      {canExpand && (
        <button type="button" className="extract-value-toggle" aria-expanded={isExpanded} onClick={() => toggleResultExpansion(resultKey)}>
          {isExpanded ? "收起" : "展开"}
        </button>
      )}
    </span>
  )}
</div>
```

Add the label helper near `extractionMethodLabel` (~:1174):

```tsx
function reviewStatusLabel(status: string): string {
  switch (status) {
    case "corrected": return "已修正";
    case "approved": return "已通过";
    case "rejected": return "已驳回";
    case "reviewed": return "已复核";
    default: return "";
  }
}
```

- [ ] **Step 4: Add CSS**

In `frontend/src/styles.css`, near the `.extract-method-badge` rule (~:1527-1601), add:

```css
.extract-review-badge {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  background: var(--teal-soft);
  color: var(--teal-strong);
}
.extract-review-badge.corrected { background: var(--amber); color: #fff; }
.extract-review-badge.approved { background: #2e7d32; color: #fff; }
.extract-review-badge.rejected { background: var(--red); color: #fff; }
.extract-card-value.editing { display: flex; gap: 6px; align-items: center; }
.extract-card-value.editing .field-edit-input { flex: 1; }
.extract-card-corrected { font-weight: 600; }
.extract-card-original { display: block; font-size: 11px; color: #888; }
```

- [ ] **Step 5: Type-check + build**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: succeeds.

- [ ] **Step 6: Manual verification checklist**

`npm run dev`, upload a contract, extract, then verify:
- Each "found" field shows a 修正 button.
- Click 修正 → input appears with current value → edit → 保存 → badge shows "已修正", corrected value displays with "原值：…" beneath.
- Refresh the page / reopen the contract → the correction persists (proves it round-tripped through the backend).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ExtractionPage.tsx frontend/src/styles.css
git commit -m "feat(frontend): inline review edit + status badge on extraction results"
```

---

## Task 4: Batch review + source/page/confidence display + low-confidence flag

**Files:**
- Modify: `frontend/src/pages/ExtractionPage.tsx` (batch toolbar near results; result card meta line)

**Interfaces:**
- Consumes: `batchReviewFields` (Task 2); `reviewed_value`/`source_text`/`page_no`/`confidence` on `ExtractionFieldValue` (Task 1).
- Produces: a "select + batch 通过/驳回" control; per-card meta line showing page/confidence; low-confidence (< 0.7) highlight.

- [ ] **Step 1: Add selection + batch state**

Near the review state from Task 3, add:

```tsx
const LOW_CONFIDENCE_THRESHOLD = 0.7;
const [selectedReviewKeys, setSelectedReviewKeys] = useState<Set<string>>(new Set());
const [batchBusy, setBatchBusy] = useState(false);
```

- [ ] **Step 2: Add a per-card checkbox + meta line**

In the result card (Task 3's JSX), inside `.extract-card-item result`, add a checkbox at the start and a meta line under the value. Wrap the existing content so the row has: `[checkbox] name+badges … value+actions` and beneath the value a small meta:

```tsx
<input
  type="checkbox"
  className="extract-review-check"
  checked={selectedReviewKeys.has(r.field_key)}
  onChange={(e) => {
    const next = new Set(selectedReviewKeys);
    if (e.target.checked) next.add(r.field_key); else next.delete(r.field_key);
    setSelectedReviewKeys(next);
  }}
  aria-label={`选择 ${r.field_name}`}
/>
```

And after the `.extract-card-value` span, a meta line:

```tsx
{(r.page_no != null || r.confidence != null) && (
  <small className="extract-card-meta">
    {r.page_no != null && `第 ${r.page_no} 页`}
    {r.page_no != null && r.confidence != null && " · "}
    {r.confidence != null && `${Math.round(r.confidence * 100)}%`}
    {r.confidence != null && r.confidence < LOW_CONFIDENCE_THRESHOLD && " · 低置信"}
    {r.source_text && ` · 来源：${r.source_text.slice(0, 40)}${r.source_text.length > 40 ? "…" : ""}`}
  </small>
)}
```

Add a `low-confidence` modifier to the card when applicable: change the outer `<div className="extract-card-item result" ...>` className to include `${r.confidence != null && r.confidence < LOW_CONFIDENCE_THRESHOLD ? " low-confidence" : ""}`.

- [ ] **Step 3: Add a batch toolbar**

Above the `.extract-card-list` (where `results` is rendered, ~:764), add a toolbar:

```tsx
{results && results.some((r) => r.field_id) && (
  <div className="extract-review-toolbar">
    <button
      type="button"
      className="extract-value-toggle"
      disabled={selectedReviewKeys.size === 0 || batchBusy}
      onClick={() => runBatchReview("approve")}
    >
      批量通过 ({selectedReviewKeys.size})
    </button>
    <button
      type="button"
      className="extract-value-toggle"
      disabled={selectedReviewKeys.size === 0 || batchBusy}
      onClick={() => setSelectedReviewKeys(new Set(
        results.filter((r) => r.confidence < LOW_CONFIDENCE_THRESHOLD && r.field_id).map((r) => r.field_key),
      ))}
    >
      仅选低置信
    </button>
  </div>
)}
```

- [ ] **Step 4: Add the batch handler**

```tsx
async function runBatchReview(action: "approve" | "reject") {
  if (!activeItem) return;
  const chosen = (activeItem.results ?? []).filter((r) => selectedReviewKeys.has(r.field_key) && r.field_id);
  if (chosen.length === 0) return;
  setBatchBusy(true);
  try {
    await batchReviewFields(
      activeItem.upload.contract_id,
      chosen.map((r) => ({ field_id: r.field_id, action })),
    );
    // Refresh detail to pick up updated review_status.
    const detail = await getContractDetail(activeItem.upload.contract_id);
    const nextResults = buildFieldValues(selectedFields, detail.fields);
    updateItem(activeItem.item.item_id, { results: nextResults });
    setSelectedReviewKeys(new Set());
  } catch (err) {
    console.error("batch review failed", err);
    alert("批量复核失败，请重试");
  } finally {
    setBatchBusy(false);
  }
}
```

(`getContractDetail`, `buildFieldValues`, `selectedFields`, `updateItem` all exist in this file — verify exact names against the file and adapt if needed.)

- [ ] **Step 5: Add CSS**

```css
.extract-card-item.result.low-confidence { box-shadow: inset 3px 0 var(--amber); }
.extract-card-meta { display: block; width: 100%; font-size: 11px; color: #888; margin-top: 2px; }
.extract-review-check { margin-right: 8px; }
.extract-review-toolbar { display: flex; gap: 8px; margin: 6px 0; }
```

- [ ] **Step 6: Type-check + build + manual verification**

Run: `cd frontend && npx tsc -b && npm run build` (succeeds). Then `npm run dev`: select 2+ fields → 批量通过 → badges update to 已通过; click 仅选低置信 → only <0.7 fields selected; low-confidence cards show an amber left edge; meta line shows page/confidence/source.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ExtractionPage.tsx frontend/src/styles.css
git commit -m "feat(frontend): batch review, source/page/confidence display, low-confidence flag"
```

---

## Task 5: Review UI on ExtractionRecordsPage (badge + edit + history drawer)

**Files:**
- Modify: `frontend/src/pages/ExtractionRecordsPage.tsx` (detail panel ~:179-236; `openTask` ~:55-72)

**Interfaces:**
- Consumes: `reviewField`, `listReviewRecords` (Task 2); review fields on the result type.
- Produces: review badge + inline edit in the detail result cards; a history drawer showing the audit trail.

- [ ] **Step 1: Fetch review records when opening a task**

In `openTask` (~:55-72), after loading the task, also fetch records and store them. Add state:

```tsx
const [reviewRecords, setReviewRecords] = useState<ReviewRecord[]>([]);
const [editingResultId, setEditingResultId] = useState<string | null>(null);
const [editDraft, setEditDraft] = useState("");
```

Inside `openTask` (after the existing detail load), add:

```tsx
try {
  const recs = await listReviewRecords(taskId);
  setReviewRecords(recs);
} catch {
  setReviewRecords([]);
}
```

(Import `listReviewRecords`, `ReviewRecord`, `reviewField` from `lib/api`.)

- [ ] **Step 2: Add badge + inline edit to the result card**

In `ExtractionTaskDetail` (~:219-236), modify the `<article className="extraction-result-card ...">` to add a badge in the header and an editable value. Replace the `<p>…</p>` value line with:

```tsx
{editingResultId === result.field_id ? (
  <p className="extraction-result-edit">
    <input className="field-edit-input" value={editDraft} onChange={(e) => setEditDraft(e.target.value)} autoFocus />
    <button type="button" className="extract-value-toggle" onClick={() => saveRecordReview(task.task_id, result.field_id)}>保存</button>
    <button type="button" className="extract-value-toggle" onClick={() => setEditingResultId(null)}>取消</button>
  </p>
) : (
  <p>
    {result.reviewed_value ? (
      <>
        <span className="extract-card-corrected">{result.reviewed_value}</span>
        <small className="extract-card-original">原值：{result.status === "found" ? result.value || "-" : statusText(result.status)}</small>
      </>
    ) : (result.status === "found" ? result.value || "-" : statusText(result.status))}
    {result.status === "found" && (
      <button type="button" className="extract-value-toggle" onClick={() => { setEditingResultId(result.field_id); setEditDraft(result.reviewed_value || result.value); }}>修正</button>
    )}
  </p>
)}
```

In the `<header>` `<span>`, append a review badge (same `reviewStatusLabel` helper — extract it to a shared util or duplicate locally):

```tsx
{result.review_status && result.review_status !== "extracted" && (
  <small className={`extract-review-badge ${result.review_status}`}>{reviewStatusLabel(result.review_status)}</small>
)}
```

`task` and `result` must be in scope where this JSX lives; pass `task` (or `task.task_id`) into `ExtractionTaskDetail` if not already.

- [ ] **Step 3: Add the save handler**

```tsx
async function saveRecordReview(contractId: string, fieldId: string) {
  try {
    await reviewField(contractId, fieldId, { action: "modify", new_value: editDraft });
    setEditingResultId(null);
    const recs = await listReviewRecords(contractId);
    setReviewRecords(recs);
    // Re-fetch the task detail to refresh values (reuse the existing openTask/refresh path).
  } catch (err) {
    console.error(err);
    alert("复核保存失败");
  }
}
```

- [ ] **Step 4: Add a history drawer toggle**

In `.extraction-detail-head` (~:179-183), add a button that toggles a records panel:

```tsx
<button type="button" className="extract-value-toggle" onClick={() => setHistoryOpen((v) => !v)}>
  复核历史 ({reviewRecords.length})
</button>
```

Add `const [historyOpen, setHistoryOpen] = useState(false);`. Below the results section, render the drawer when open:

```tsx
{historyOpen && (
  <section className="extraction-detail-section">
    <h3>复核历史</h3>
    <ul className="review-history-list">
      {reviewRecords.map((rec) => (
        <li key={rec.id}>
          <strong>{rec.action}</strong> · {rec.reviewer_id ?? "-"} · {rec.created_at}
          {rec.old_value != null && <div>原值：{rec.old_value || "—"}</div>}
          {rec.new_value != null && <div>新值：{rec.new_value || "—"}</div>}
          {rec.comment && <div>备注：{rec.comment}</div>}
        </li>
      ))}
      {reviewRecords.length === 0 && <li>暂无复核记录</li>}
    </ul>
  </section>
)}
```

- [ ] **Step 5: Add CSS**

```css
.review-history-list { list-style: none; padding: 0; margin: 0; }
.review-history-list li { padding: 6px 0; border-bottom: 1px solid #eee; font-size: 13px; }
.extraction-result-edit { display: flex; gap: 6px; align-items: center; }
.extraction-result-edit .field-edit-input { flex: 1; }
```

- [ ] **Step 6: Type-check + build + manual verification**

Run: `cd frontend && npx tsc -b && npm run build`. Then `npm run dev`: open a contract in 提取记录 → 修正 a field → badge shows; click 复核历史 → audit trail lists the change with old/new values.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ExtractionRecordsPage.tsx frontend/src/styles.css
git commit -m "feat(frontend): review badge, inline edit, and history drawer on records page"
```

---

## Task 6: Export enhancement (CSV/JSON + extra columns + format dropdown)

**Files:**
- Modify: `frontend/src/lib/excelExport.ts` (add CSV/JSON builders + columns)
- Modify: `frontend/src/pages/ExtractionPage.tsx` (export dropdown UI ~:640-697)
- Modify: `frontend/src/pages/ExtractionRecordsPage.tsx` (single-result export button)

**Interfaces:**
- Consumes: `ExtractionFieldValue` (with `confidence`/`page_no`/`reviewed_value` from Task 1); existing `buildBatchExtractionResultsWorkbook` pattern.
- Produces: `buildExtractionResultsCsv`, `buildExtractionResultsJson`, downloaders; columns include confidence/page/reviewed_value; a format dropdown (xlsx/csv/json) for batch + single.

- [ ] **Step 1: Add CSV + JSON builders + downloaders**

Append to `frontend/src/lib/excelExport.ts`. CSV uses simple RFC4180-style quoting:

```ts
function csvEscape(value: string | number): string {
  const s = String(value ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function buildExtractionResultsCsv(
  fields: { field_key: string; field_name: string }[],
  rows: BatchExtractionWorkbookRow[],
): string {
  const headers = ["序号", "文件名", ...fields.map((f) => f.field_name), "处理状态", "错误信息"];
  const lines: string[] = [headers.map(csvEscape).join(",")];
  rows.forEach((row, index) => {
    const byKey = new Map((row.results ?? []).map((r) => [r.field_key, r]));
    const values = [
      index + 1,
      row.fileName,
      ...fields.map((f) => {
        const r = byKey.get(f.field_key);
        return r?.reviewed_value || (r?.status === "found" ? r.value : "") || "";
      }),
      workbookStatusLabel(row.status),
      row.error || "",
    ];
    lines.push(values.map(csvEscape).join(","));
  });
  return "﻿" + lines.join("\n"); // BOM for Excel CJK
}

export function buildExtractionResultsJson(
  fields: { field_key: string; field_name: string }[],
  rows: BatchExtractionWorkbookRow[],
): string {
  const byKey = (results: BatchExtractionWorkbookRow["results"]) =>
    new Map((results ?? []).map((r) => [r.field_key, r]));
  return JSON.stringify(
    rows.map((row, index) => {
      const m = byKey(row.results);
      return {
        index: index + 1,
        fileName: row.fileName,
        status: row.status,
        error: row.error || null,
        fields: Object.fromEntries(
          fields.map((f) => {
            const r = m.get(f.field_key);
            return [f.field_key, r ? {
              value: r.value, reviewed_value: r.reviewed_value ?? null,
              confidence: r.confidence ?? null, page_no: r.page_no ?? null,
              review_status: r.review_status ?? null,
            } : null];
          }),
        ),
      };
    }),
    null,
    2,
  );
}

function downloadText(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function downloadExtractionResultsCsv(fields: { field_key: string; field_name: string }[], rows: BatchExtractionWorkbookRow[]): void {
  downloadText("extraction-results.csv", buildExtractionResultsCsv(fields, rows), "text/csv;charset=utf-8");
}
export function downloadExtractionResultsJson(fields: { field_key: string; field_name: string }[], rows: BatchExtractionWorkbookRow[]): void {
  downloadText("extraction-results.json", buildExtractionResultsJson(fields, rows), "application/json");
}
```

- [ ] **Step 2: Add confidence/page/reviewed columns to the xlsx batch builder**

In `buildBatchExtractionResultsWorkbook` (~:59-72), after the `处理状态`/`错误信息` columns are defined, you may keep xlsx focused on values (CSV/JSON carry the richer data). If you want them in xlsx too, append `置信度`/`页码`/`校正值` headers and matching per-row values derived from `resultByKey`. Keep the change minimal — the primary richness lives in CSV/JSON.

- [ ] **Step 3: Add a format dropdown on the batch export**

In `ExtractionPage.tsx`, replace the single export button (~:685-697) with a dropdown that calls the right builder. Use a simple toggle + menu (mirror the `.extract-value-toggle` style):

```tsx
const [exportMenuOpen, setExportMenuOpen] = useState(false);
// ...
<div className="extract-export-menu">
  <button type="button" className="extract-value-toggle" onClick={() => setExportMenuOpen((v) => !v)}>
    导出 ▾
  </button>
  {exportMenuOpen && (
    <div className="extract-export-menu-items">
      <button type="button" onClick={() => { doExport("xlsx"); setExportMenuOpen(false); }}>Excel (.xlsx)</button>
      <button type="button" onClick={() => { doExport("csv"); setExportMenuOpen(false); }}>CSV</button>
      <button type="button" onClick={() => { doExport("json"); setExportMenuOpen(false); }}>JSON</button>
    </div>
  )}
</div>
```

Add a dispatcher (builds `rows` the same way `handleExport` does at ~:640-648):

```tsx
function doExport(fmt: "xlsx" | "csv" | "json") {
  const fields = selectedFields.map((f) => ({ field_key: f.field_key, field_name: f.field_name }));
  const rows = batchItemsWithResults.map((it) => ({
    fileName: it.file.name,
    status: it.status,
    error: it.error ?? "",
    results: it.results ?? null,
  }));
  if (fmt === "xlsx") downloadBatchExtractionResultsWorkbook(fields, rows);
  else if (fmt === "csv") downloadExtractionResultsCsv(fields, rows);
  else downloadExtractionResultsJson(fields, rows);
}
```

(Adapt `batchItemsWithResults` to the actual variable holding the items with results in this component.)

- [ ] **Step 4: Add single-result export on records page**

In `ExtractionRecordsPage.tsx` detail meta (~:184-202), add a small export using the open task's results:

```tsx
<button type="button" className="extract-value-toggle" onClick={() => {
  const fields = task.results.map((r) => ({ field_key: r.field_key, field_name: r.field_name }));
  const rows = [{ fileName: task.filename, status: "found" as const, error: "", results: task.results }];
  downloadExtractionResultsJson(fields, rows);
}}>导出 JSON</button>
```

- [ ] **Step 5: Add CSS**

```css
.extract-export-menu { position: relative; display: inline-block; }
.extract-export-menu-items {
  position: absolute; right: 0; z-index: 10;
  display: flex; flex-direction: column; gap: 2px;
  background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 4px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.12);
}
.extract-export-menu-items button { text-align: left; padding: 4px 10px; background: none; border: none; cursor: pointer; border-radius: 4px; }
.extract-export-menu-items button:hover { background: var(--teal-soft); }
```

- [ ] **Step 6: Type-check + build + manual verification**

Run: `cd frontend && npx tsc -b && npm run build`. Then `npm run dev`: batch export → dropdown offers xlsx/csv/json; CSV opens in Excel with correct CJK; JSON includes confidence/page/reviewed_value; records page single-result JSON export works.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/excelExport.ts frontend/src/pages/ExtractionPage.tsx frontend/src/pages/ExtractionRecordsPage.tsx frontend/src/styles.css
git commit -m "feat(frontend): CSV/JSON export with review/confidence/page columns + format dropdown"
```

---

## Self-Review

**1. Spec coverage (items ④⑤⑥⑦):**
- ④ 复核闭环: Tasks 1 (types), 2 (API), 3 (inline edit+badge), 4 (batch), 5 (records edit+history). ✓
- ⑤ 溯源(片段+页码): Task 4 meta line (source_text + page_no). ✓
- ⑥ 置信度: Task 4 meta + low-confidence flag. ✓
- ⑦ 导出: Task 6 (CSV/JSON + columns + dropdown, batch + single). ✓
- D4 (reviewed_value not overwrite value): Task 1 mapper propagates both; Task 3/5 UI shows original+corrected. ✓
- D7 (no frontend test framework): verification = tsc + manual checklist throughout. ✓

**2. Placeholder scan:** Code blocks are complete. Where a task says "adapt to the actual variable name" (e.g. `activeItem` shape, `batchItemsWithResults`), that's an honest pointer for the implementer to match the file's real identifiers — the logic is fully specified.

**3. Type consistency:** `FieldReviewRequest`, `BatchReviewItem`, `ReviewRecord` defined Task 2, consumed Tasks 3/4/5. `ExtractionFieldValue` extensions (Task 1) consumed Tasks 3/4/5/6. `reviewStatusLabel` used Tasks 3 & 5 (extract to shared or duplicate — noted).

No gaps for ④⑤⑥⑦.

---

## Execution Handoff

Plan complete. Execute via superpowers:subagent-driven-development (fresh subagent per task, review between). Frontend caveat: no test framework, so reviewers verify code quality + `tsc -b` + spec compliance; behavior is manually verified per task checklist. Then Plan 3 (auth).
