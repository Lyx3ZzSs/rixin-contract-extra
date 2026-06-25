# Phase 2 / Plan 3: 前端功能闭环（溯源高亮 + 规则告警 + classify + 条款）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** 前端消费 Plan 1 的页面图端点与 Plan 2 的 violations/clauses API，实现「点字段/条款 → 跳原文页 + bbox 框选零误差高亮」「规则违规告警 + 忽略」「classify 类型徽章」「条款最小版列表/复核」。

**Architecture:** 用 `<img src=/pages/{n}/image>` 取代 pdfjs 预览（Tier 2：bbox 与显示面同源）→ 新 `PageImagePreview` 组件做图像 + bbox overlay → 字段/条款卡片点击驱动高亮 → 新增 Violation UI + classify 徽章。bbox 对齐后端 `{x1,y1,x2,y2}` 原始像素，overlay 用 `显示宽/图像原始宽` 缩放（构造性零误差）。

**Tech Stack:** React 19 + Vite 6 + TypeScript 5.8 + lucide-react。**无测试框架**——门禁 = `npm run build`（tsc 类型检查）+ 手动 DoD；可选 vitest 覆盖 overlay 纯函数。

**对应 spec：** `docs/superpowers/specs/2026-06-24-phase2-feature-completion-design.md` §7。

## Global Constraints
- bbox 坐标契约对齐后端：`{x1,y1,x2,y2}`（Tier 2 原始像素，OCR 图像空间）。overlay = `bbox × (显示宽 / 图像 naturalWidth)`，**零误差**（同一张图）。
- `reviewed_value` 永不覆盖 `value`（既有契约）。
- 不引入新运行时依赖（lucide-react 已有）；vitest 仅作 devDependency（可选任务）。
- 遵循既有样式 token（`--red`/`--amber`/`--teal`）与 `.extract-review-badge`/`.extract-card-item.low-confidence` 范式。
- `npm run build` 必须通过（tsc 严格）。
- 提交规范：conventional commits，每任务一次。

## File Structure

| 文件 | 职责 | 任务 |
|---|---|---|
| `frontend/src/types.ts` | BBox→{x1,y1,x2,y2}；FieldDetail/ClauseDetail 加 bbox；Violation 类型；ContractDetail.violations；修 mapper | T1 |
| `frontend/src/lib/api.ts` | getPageImageUrl / reviewClause / reviewViolation | T1 |
| `frontend/src/lib/bboxOverlay.ts` | **新建**：bboxToImageRect 纯函数（图像空间 overlay 数学） | T2 |
| `frontend/src/components/PageImagePreview.tsx` | **新建**：`<img>` 分页预览 + bbox overlay + 跳转 | T2 |
| `frontend/src/components/ErrorBoundary.tsx` | **新建**：顶层错误兜底 | T6 |
| `frontend/src/pages/ExtractionPage.tsx` | 接 PageImagePreview；字段/条款点击高亮；classify 徽章；违规角标 | T3/T4/T5 |
| `frontend/src/pages/ExtractionRecordsPage.tsx` | 违规汇总 + 条款列表（详情区） | T5 |
| `frontend/src/styles.css` | 违规/徽章/overlay 样式类 | T4/T5 |
| `frontend/src/lib/pdfCoordinates.ts` | 删除（被 bboxOverlay 取代） | T2 |
| `frontend/package.json` | vitest（可选） | T7 |

---

## Task 1: 类型与 API 对齐

**Files:**
- Modify: `frontend/src/types.ts`（BBox :49-54；FieldDetail :97-114；ClauseDetail :116-126；ContractDetail :128-138；mapper :219-237）
- Modify: `frontend/src/lib/api.ts`（追加 3 个函数）
- Verify: `cd frontend && npm run build`（tsc 通过）

**Interfaces:**
- Produces: `BBox {x1,y1,x2,y2}`；`FieldDetail.bbox?: BBox`；`ClauseDetail.bbox?: BBox` + `page_end?`；`Violation` 类型；`ContractDetail.violations?: Violation[]` + `contract_type?` + `contract_type_confidence?`；api `getPageImageUrl(contractId, pageNo): string`、`reviewClause(contractId, clauseId, body): Promise<ClauseDetail>`、`reviewViolation(contractId, violationId, body): Promise<Violation>`。

- [ ] **Step 1: types.ts 改动**

`BBox`（:49-54）改为对齐后端原始像素契约：
```ts
export interface BBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}
```
`FieldDetail`（:97-114）加字段（`page_no` 已有，补 bbox + page_end）：
```ts
  bbox?: BBox | null;
  page_end?: number | null;
```
`ClauseDetail`（:116-126）加：
```ts
  bbox?: BBox | null;
  page_end?: number | null;
```
新增 `Violation` 类型（紧挨 `ClauseDetail` 之后）：
```ts
export interface Violation {
  id: string;
  field_key: string | null;
  rule_key: string;
  severity: "error" | "warning" | "info" | string;
  message: string;
  status: "active" | "ignored" | string;
  detail?: Record<string, unknown> | null;
  created_at: string;
  ignored_at?: string | null;
  ignored_by?: string | null;
}
```
`ContractDetail`（:128-138）补：
```ts
  contract_type?: string | null;
  contract_type_confidence?: number | null;
  violations?: Violation[];
```

> 修复 `fieldDetailToExtractionFieldValue`（:167-191）：bbox 不进 UI 的 `ExtractionFieldValue`（UI 类型保持精简），但**保留** FieldDetail.bbox 供 PageImagePreview 用——所以不要把 bbox 塞进 ExtractionFieldValue。无需改 mapper 逻辑，除非 tsc 报错。

- [ ] **Step 2: api.ts 加 3 个函数**

在 `downloadContractFileUrl`（:165）之后追加（沿用其 `?api_key=` 拼接 + `getApiKey`）：
```ts
export function getPageImageUrl(contractId: string, pageNo: number): string {
  let url = `${getApiBaseUrl()}/api/v1/contracts/${contractId}/pages/${pageNo}/image`;
  const key = getApiKey();
  if (key) url += `?api_key=${encodeURIComponent(key)}`;
  return url;
}
```
在 `reviewField`（:274）之后追加 `reviewClause`（同模式，路径 `/contracts/{cid}/clauses/{clid}/review`，返回 `ClauseDetail`）与 `reviewViolation`（路径 `/contracts/{cid}/violations/{vid}`，方法 PATCH，body `{action, new_value?, comment?}`，query `reviewer_id`，返回 `Violation`）。复用既有 `parseJsonResponse` 与 `authHeaders()`；reviewer_id 用 `"web"`（与 `batchReviewFields` 一致）。

- [ ] **Step 3: 验证 tsc**

Run: `cd frontend && npm run build`
Expected: build 成功（无 type error）。若 `ExtractionFieldValue` 的 `extraction_method` 等报错，按既有类型修。

- [ ] **Step 4: 提交**
```bash
git add frontend/src/types.ts frontend/src/lib/api.ts
git commit -m "feat(fe): align BBox to backend pixel space + add violation/clause API"
```

---

## Task 2: bbox overlay 数学 + PageImagePreview 组件

**Files:**
- Create: `frontend/src/lib/bboxOverlay.ts`
- Create: `frontend/src/components/PageImagePreview.tsx`
- Delete: `frontend/src/lib/pdfCoordinates.ts`
- Verify: `npm run build`

**Interfaces:**
- Produces: `bboxToImageRect(bbox, displayedWidth, naturalWidth) -> {left,top,width,height}`；`PageImagePreview` props `{ contractId: string; pageCount: number; target: { pageNo: number; bbox: BBox } | null; onPageActive?: (n:number)=>void }`。

- [ ] **Step 1: bboxOverlay.ts（纯函数，含可选单测对象）**

```ts
import type { BBox } from "../types";

export interface ImageRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Map a bbox (raw pixels in the OCR page-image's space) to a CSS overlay rect
 * on a displayed <img> of that same image. Tier 2: same image => exact mapping,
 * no axis flip, no coordinate-space conversion. scale = displayedWidth/naturalWidth.
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
```

- [ ] **Step 2: PageImagePreview.tsx（核心组件）**

要点：
- props: `contractId`, `pageCount`, `target`（当前要高亮的 {pageNo, bbox} 或 null）, 可选 `onPageActive`。
- 渲染 1..pageCount 个 `.pip-page-frame`（`position:relative`），每个内含 `<img src={getPageImageUrl(contractId, n)} loading="lazy" onLoad=>记录 naturalWidth 到 state`，以及一个绝对定位的 overlay div。
- `pageRefs = useRef<Map<number, HTMLDivElement>>`；当 `target` 变化 → `scrollIntoView` 到 `target.pageNo` 的 frame。
- overlay：仅当某页 `pageNo === target?.pageNo` 且该页 naturalWidth 已知 → 用 `bboxToImageRect(target.bbox, frameDisplayWidth, naturalWidth)` 渲染一个 `.pip-highlight` div。
- frame 显示宽度：跟随容器（`width:100%`），记录 `clientWidth` 作为 displayedWidth（与 naturalWidth 同比例缩放，故 overlay 精确）。
- 兜底：naturalWidth 未知时不画框；pageNo 越界不报错。

骨架（实现者补全 JSX 细节，遵循既有 `.extract-pdf-page-frame` 风格）：
```tsx
import { useEffect, useRef, useState } from "react";
import type { BBox } from "../types";
import { getPageImageUrl } from "../lib/api";
import { bboxToImageRect } from "../lib/bboxOverlay";

export interface HighlightTarget { pageNo: number; bbox: BBox; }
export interface PageImagePreviewProps {
  contractId: string;
  pageCount: number;
  target: HighlightTarget | null;
  onPageActive?: (pageNo: number) => void;
}

export function PageImagePreview({ contractId, pageCount, target, onPageActive }: PageImagePreviewProps) {
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const [naturalSizes, setNaturalSizes] = useState<Record<number, { w: number; cw: number }>>({});

  useEffect(() => {
    if (!target) return;
    const el = pageRefs.current.get(target.pageNo);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [target]);

  // ...render pages 1..pageCount; on img onLoad record naturalWidth=img.naturalWidth + clientWidth;
  // overlay rect via bboxToImageRect(target.bbox, clientWidth, naturalWidth) when pageNo===target?.pageNo.
  // Call onPageActive via an IntersectionObserver or onScroll (optional; onPageActive may be unused initially).
}
```

- [ ] **Step 3: 删除 pdfCoordinates.ts**（被 bboxOverlay 取代；确认无引用——Explore 确认它未被任何 .tsx 使用）。

- [ ] **Step 4: 样式（styles.css 追加）**
```css
.pip-page-frame { position: relative; width: 100%; margin: 0 auto 12px; }
.pip-page-frame img { display: block; width: 100%; height: auto; border-radius: 6px; box-shadow: var(--shadow); }
.pip-highlight {
  position: absolute; border: 2px solid var(--red); background: rgba(201,54,44,0.18);
  border-radius: 2px; pointer-events: none; animation: pip-pulse 1.2s ease-out 2;
}
@keyframes pip-pulse { 0%,100%{ box-shadow:0 0 0 0 rgba(201,54,44,0); } 50%{ box-shadow:0 0 0 6px rgba(201,54,44,0.25); } }
```

- [ ] **Step 5: 验证 build**
Run: `cd frontend && npm run build` → 通过。

- [ ] **Step 6: 提交**
```bash
git add frontend/src/lib/bboxOverlay.ts frontend/src/components/PageImagePreview.tsx frontend/src/styles.css
git rm frontend/src/lib/pdfCoordinates.ts
git commit -m "feat(fe): add PageImagePreview with zero-error bbox overlay"
```

---

## Task 3: 接入 ExtractionPage（字段/条款点击高亮）

**Files:**
- Modify: `frontend/src/pages/ExtractionPage.tsx`

**目标**：在结果视图（step3）用 `PageImagePreview` 取代 `FlatPdfPreview`（:830）；字段卡片点击 → 设 `highlightTarget = {pageNo: r.page_no, bbox: r.bbox}`；条款同样。

- [ ] **Step 1: state + 替换预览**
- 在 `ExtractionFieldSetup` state（:158-215）加：`const [highlightTarget, setHighlightTarget] = useState<HighlightTarget | null>(null);`
- 替换 `<FlatPdfPreview src={activeItem.documentUrl} file={activeFile} />`（:830）为：
```tsx
<PageImagePreview
  contractId={activeItem.upload.contract_id}
  pageCount={activeContract?.page_count ?? 1}
  target={highlightTarget}
/>
```
（`activeContract` = 当前选中文件的 ContractDetail；若 ExtractionFieldSetup 尚未持有，从 `activeItem` 的 getContractDetail 结果取——`results` 来自 `buildFieldValues(detail)`，需同时保留 `detail` 供 page_count/clauses/violations。若改造量大，最小做法：在 `doExport`/`runBatchReview` 已调 `getContractDetail` 的地方把 detail 存到 `activeItem.contractDetail`。）

- [ ] **Step 2: 字段卡片点击高亮**
- 字段卡片（:894 `results.map`）的卡片根 div 加 `onClick={() => r.page_no && r.bbox && setHighlightTarget({ pageNo: r.page_no, bbox: r.bbox })}`（仅当有坐标时；加 `role=button` + cursor pointer）。
- 排除「修正编辑态」点击触发（编辑态不触发高亮）。

- [ ] **Step 3: 验证 build + 手动**
Run: `cd frontend && npm run build` → 通过。手动（需后端 + 真实合同）：上传 → 提取 → 点字段 → 预览跳页 + 红框对齐原文。

- [ ] **Step 4: 提交**
```bash
git add frontend/src/pages/ExtractionPage.tsx
git commit -m "feat(fe): wire PageImagePreview + field-click highlight in ExtractionPage"
```

> **Note:** pdfjs 相关代码（imports :3,:28；`FlatPdfPreview`/`ExtractPdfPageCanvas` :1638-1860）此时若不再被结果视图引用，可在 T6 一并清理（保留不碍事，但 build 不应残留死导入——若 tsc 报 unused，移除该文件的 pdfjs import 与未用组件）。

---

## Task 4: 规则违规 UI + classify 徽章

**Files:**
- Modify: `frontend/src/pages/ExtractionPage.tsx`（字段面板 :837 区）
- Modify: `frontend/src/styles.css`（违规/徽章类）

**目标**：
- 字段级违规：字段卡片右上角 severity 角标（error 红 / warning 琥珀）；点击展开 message + 「忽略」按钮（`reviewViolation(action="approve")` → 置灰）。
- 合同级违规（field_key=null）：字段面板顶部 `.extract-violation-summary` 汇总。
- classify 徽章：结果区头部显示 `contract_type` + 置信度（复用 `.extract-review-badge` 范式）。

- [ ] **Step 1: 违规查找 helper**
在 ExtractionPage 内（或 types.ts）加：`violationsByField(violations: Violation[])` → `Map<field_key, Violation[]>`；`contractViolations(violations)` → field_key==null 的列表。

- [ ] **Step 2: 字段卡片角标**
- 字段卡片（:894 区）计算 `const fviolations = violationsByField(detail.violations).get(r.field_key) ?? []`。
- 有 error/warning → 卡片加类 `has-violation`（左侧 inset 红条，仿 `.low-confidence`）+ 右上角小徽章（图标 + severity）。点击徽章 → 展开 message + 「忽略」按钮。
- 「忽略」→ `await reviewViolation(contractId, vid, {action:"approve"})` → 从本地 violations 移除/置 ignored + 重渲染。

- [ ] **Step 3: 合同级汇总 + classify 徽章**
- 字段面板顶部（:866 批量工具栏附近）加 `<ViolationSummary violations={contractViolations(detail.violations)} />`（列出 field_key=null 的违规）。
- 结果区头部加 classify 徽章：`detail.contract_type` + `${Math.round((detail.contract_type_confidence??0)*100)}%`；低置信（<0.7）→「类型待确认」浅提示。

- [ ] **Step 4: 样式**
```css
.extract-card-item.result.has-violation { box-shadow: inset 3px 0 var(--red); }
.extract-violation-badge { /* 仿 .extract-review-badge，error 用 --red，warning 用 --amber */ }
.extract-violation-summary { background: var(--surface-soft); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
.extract-classify-badge { /* 仿 .extract-review-badge，用 --teal */ }
```

- [ ] **Step 5: 验证 build + 提交**
```bash
cd frontend && npm run build
git add frontend/src/pages/ExtractionPage.tsx frontend/src/styles.css
git commit -m "feat(fe): rule-violation badges + summary + classify badge"
```

---

## Task 5: 条款最小版（列表 + 复核 + 高亮）

**Files:**
- Modify: `frontend/src/pages/ExtractionRecordsPage.tsx`（详情区 :296 附近）或 ExtractionPage 字段面板下方
- Modify: `frontend/src/lib/api.ts`（reviewClause 已在 T1 加）
- Modify: `frontend/src/styles.css`

**目标**：扁平条款列表（按 sort_order），每条显 title/content/页码 + 复核状态徽章 + 复核操作（approve/reject）+ 点击高亮（setHighlightTarget）。不做 parent_id 层级树。

- [ ] **Step 1: 条款区 UI**
在记录页详情区（ExtractionRecordsPage `ExtractionTaskDetail` :296 字段配置区附近）或 ExtractionPage 字段面板底部，加 `.extract-clause-list`：遍历 `detail.clauses`（按 sort_order，types.ts 已有 clauses；若 mapper 丢弃则用原始 detail）。
每条：`clause_title ?? clause_type ?? "条款"`、`content`（截断/可展开）、`第 N 页`、复核徽章（复用 `.extract-review-badge`）、「通过/驳回」按钮（`reviewClause`）+ 点击卡片 → 高亮（需该页有 PageImagePreview；记录页若已展示预览则联动，否则条款高亮放 ExtractionPage）。

- [ ] **Step 2: mapper 携带 clauses**
`contractDetailToExtractionTaskResponse`（types.ts:219）目前丢 clauses——若条款 UI 走 ExtractionTaskResponse.results 通道则需扩展；更简单：条款区直接消费 `ContractDetail`（不经过 mapper）。**推荐**：条款/违规/预览统一消费 `ContractDetail`，mapper 只服务旧字段卡片。

- [ ] **Step 3: 样式 + 验证 + 提交**
```css
.extract-clause-item { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; cursor: pointer; }
.extract-clause-item:hover { border-color: var(--teal); }
```
```bash
cd frontend && npm run build
git add frontend/src/pages/ExtractionRecordsPage.tsx frontend/src/pages/ExtractionPage.tsx frontend/src/styles.css
git commit -m "feat(fe): clause minimal list + review + highlight"
```

---

## Task 6: ErrorBoundary + pdfjs 清理 + 手动 DoD

**Files:**
- Create: `frontend/src/components/ErrorBoundary.tsx`
- Modify: `frontend/src/App.tsx`（包顶层）
- Modify: `frontend/src/pages/ExtractionPage.tsx`（移除已无用的 pdfjs import/组件，若 tsc 报 unused）
- Modify: `frontend/package.json`（可选：移除 pdfjs-dist 若完全不再用——仅当确认无其他引用）

- [ ] **Step 1: ErrorBoundary**（class 组件 `componentDidCatch`，fallback 友好提示 + 「刷新」按钮）。
- [ ] **Step 2: App.tsx 包裹**：`<ErrorBoundary><.oa-frame.../></ErrorBoundary>`。
- [ ] **Step 3: pdfjs 清理**：若 `FlatPdfPreview`/`ExtractPdfPageCanvas`/pdfjs import 不再被引用 → 移除（保留则确保无 unused import 报错）。仅在确认全仓无 pdfjs 引用后才从 package.json 移除依赖（grep `pdfjs`/`getDocument`）。
- [ ] **Step 4: build + 提交**
```bash
cd frontend && npm run build
git add frontend/src/components/ErrorBoundary.tsx frontend/src/App.tsx frontend/src/pages/ExtractionPage.tsx
git commit -m "feat(fe): ErrorBoundary + pdfjs cleanup"
```

---

## Task 7（可选）: vitest 最小安全网

**Files:** `frontend/package.json`（devDeps + script）、`frontend/src/lib/__tests__/bboxOverlay.test.ts`

- [ ] 引入 vitest（`npm i -D vitest`）+ `package.json` 加 `"test": "vitest run"`。
- [ ] 测 `bboxToImageRect`：给定 bbox + displayedWidth/naturalWidth → 精确 rect；naturalWidth=0 → 零矩形；含 bbox 缩放正确（displayedWidth=2×naturalWidth → rect 翻倍）。
- [ ] Run: `cd frontend && npm test` → 通过。提交 `test(fe): vitest + bboxOverlay math test`。

> 若不纳入，T2 的 bboxOverlay 仍是纯函数，未来易补测。

---

## Spec coverage（Plan 3 对照 spec §7）

| Spec 条目 | 任务 |
|---|---|
| §7.1 `<img>` 预览 + 归一化 bbox 叠加（零误差） | T1（BBox 契约）+ T2（PageImagePreview + bboxOverlay）+ T3（接入） |
| §7.1 点字段/条款 → 跳页 + 框选 | T3（字段）+ T5（条款） |
| §7.2 规则违规展示（角标 + 汇总 + 忽略） | T4 |
| §7.3 classify 徽章 | T4 |
| §7.4 条款最小版（扁平列表 + 复核 + 高亮） | T5 |
| §7.5 ErrorBoundary + 抽组件 + 去 pdfjs | T2（抽组件）+ T6 |
| §7.6 vitest 最小安全网（可选） | T7 |

**门禁说明**：前端无既有测试框架，自动化门禁 = `npm run build`（tsc）；行为正确性靠手动 DoD（上传→提取→点字段高亮对齐、违规角标、忽略、条款复核、classify 徽章）。
