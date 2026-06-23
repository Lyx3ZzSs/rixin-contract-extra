# Phase 1 设计：可用的产品复核闭环 + 准确率地基

- **日期**：2026-06-23
- **状态**：待评审（Draft，待用户确认后进入 writing-plans）
- **范围**：rixin-contract-extract（AI 合同信息抽取系统）优化的 **Phase 1**
- **关联**：本 spec 是「合同抽取系统优化路线图」三阶段中的第一阶段

---

## 1. 背景与上下文

rixin-contract-extract 是一个 FastAPI(async) + React/TS + 自建 Qwen3-30B-A3B / PP-OCR 的合同信息抽取系统，管线为：
`上传 → PP-OCR → Qwen 结构化字段抽取 → 规则校验 → 人工复核`。

市面对标（e签宝 / 通义法睿 / 法大大）的核心能力是：高精度抽取、合同审查/风险预警、条款识别、**抽取结果可编辑复核**、原文溯源、多人协作。本项目当前最大的产品级短板是：**抽取结果只读、风险/条款能力写了又闲置、长合同会崩、不可量化、裸奔无鉴权**。

### 1.1 已确认的优化约束（来自头脑风暴）

| 维度 | 结论 |
|---|---|
| 首要目标 | 提升抽取准确率 + 补齐产品功能对标竞品 + 新增智能能力（工程化暂缓） |
| 使用场景 | **商业化产品**（需复核闭环、溯源、协作；多租户/权限可后置） |
| 模型设施 | 灵活，可换更强模型 / 云 API |
| 业务规模 | 小规模（<100 份/天）→ **SQLite 单 worker 队列够用，架构重构一律后置** |

### 1.2 推进策略：方案 B（产品闭环优先）

```
Phase 1（本 spec）：可用的产品复核闭环 + 准确率地基
Phase 2：准确率与智能提升（换模型/few-shot、合同摘要、风险审查）
Phase 3：条款管理与协作（接线 clause_service、协作留痕、多租户/部署）
```

**为什么闭环优先**：商业化产品里，人工复核闭环是**唯一能既立刻可用、又持续变准**的设计——它既交付了"可用产品"，又产出了 Phase 2 准确率提升最缺的**标注数据**（校正后的 `reviewed_value`）。同时 Phase 1 优先吃掉项目里最大的两类债（只读结果、死代码），ROI 最高。

---

## 2. 目标与非目标

### 2.1 Phase 1 目标
让系统从"抽取完只能看"变成"**抽取完能校、能溯源、能导出、能量化、不裸奔**"。

### 2.2 非目标（明确排除，留待后续阶段）
- ❌ 多租户、用户体系、细粒度权限、RBAC（→ Phase 3）
- ❌ 多人协作审查、修改留痕 UI、版本树（→ Phase 3）
- ❌ 合同摘要、风险/合规审查（→ Phase 2）
- ❌ 条款提取与管理（→ Phase 3，接线 `clause_service`）
- ❌ 队列重构（Redis/Celery）、多 worker 水平扩展（小规模不需要）
- ❌ 模型更换 / few-shot / 微调（→ Phase 2，依赖本阶段的标注与评测基线）
- ❌ Docker / CI/CD / 可观测性体系（→ Phase 3 工程化补齐）

---

## 3. 关键发现：后端骨架已就绪（重大去风险）

深入读码后确认，Phase 1 大量基础设施**已经存在**，真正的工作是接线 + 少数新模块。这显著降低工作量与风险：

| 能力 | 现状证据 | Phase 1 实际要做 |
|---|---|---|
| 复核数据模型 | `ExtractedField` 已有 `reviewed_value`/`review_status`/`reviewer_id`/`reviewed_at`/`confidence`/`bbox`/`page_no`/`source_paragraph_id`/`source_block_start/end`（`models/contract.py:90-121`） | **零数据库迁移** |
| 复核 API | `api/review.py` Phase9 已完整：`POST /review` 批量校正、`PATCH /fields/{fid}/review` 单字段复核、`POST /review/batch`、`GET /review/records` 审计流水；`reviewed_value` 不覆盖原 `value`（`review.py:43-358`） | **后端几乎不动，纯前端接线** |
| 置信度落库 | `save_fields` 已持久化 `confidence`/`bbox`/`page_no`/`source_text`（`extraction_service.py:218-229`） | 已落库，仅加阈值 |
| LLM 返回溯源 | prompt 已要求 `source_text`/`source_page`/`source_bbox`/`confidence`，`_convert_raw_fields` 已映射（`qwen.py:84-90, 219-250`） | 已产出，需后处理校正坐标 |
| 字段返回 schema | `FieldDetail` 已返回 bbox/confidence/reviewed_value/review_status 等全部字段（`schemas/contract.py:56-74`） | 前端类型需对齐 |
| 前端坐标工具 | `bboxToViewportRect` 已存在（`pdfCoordinates.ts:10`） | 接到 PDF 预览 overlay 层 |

**含义**：Phase 1 真正的新代码集中在三处——**长合同分块**、**OCR 块匹配做坐标溯源**、**评测集**——其余多为接线与类型对齐。

---

## 4. Phase 1 详细设计（7 项工作）

### 4.1 ① 人工复核闭环（纯前端接线 + 极小后端微调）

**现状**：后端复核 API 全部就绪并写审计流水（`review.py`）；前端 `ExtractionPage` 结果区只读，从不调用任何复核端点。

**改动**：
- 前端每个字段卡片增加：
  - **inline 编辑**：保存时调用 `PATCH /api/v1/contracts/{id}/fields/{fid}/review`（`body.action="modify", body.new_value=...`）。
  - **复核状态徽章**：extracted / corrected / approved / rejected（值已在 `FieldDetail.review_status`）。
  - **批量通过**：选中多字段 → `POST /api/v1/contracts/{id}/review/batch`。
  - **复核历史抽屉**：`GET /api/v1/contracts/{id}/review/records` 展示审计流水（原值→新值、操作人、时间、备注）。
- 合同级状态联动：复核完成可调既有 `POST /contracts/{id}/approve` / `/reject`（`api/contract.py:191,232`）。

**关键决策**：
- 编辑写 `reviewed_value`，**永不覆盖原始 `value`**——与后端既有设计一致。原始 `value` 是 Phase 2 准确率度量与 few-shot 的真值来源，必须保留。
- 前端展示"原始值 / 校正值"对比，校正值存在时优先展示并标注"已校正"。
- 顺手修：前端 `FieldDetail` 类型（`types.ts:93-106`）补齐 `bbox`/`reviewed_value`/`reviewer_id`/`reviewed_at`/`value_type`/`page_end`，删除幻影字段 `extract_method`（后端不返回）。

### 4.2 ② 原文溯源高亮（后端后处理 + 前端覆盖层）⚠️ 核心技术点

**问题**：LLM 自报的 `source_bbox` 不可信（大模型坐标能力差），直接用高亮会飘。但 `OCRBlock` 表存有**可靠**的 `bbox` + `page_no`（`models/ocr.py`）。

**方案（已确认采用 OCR 块匹配）**：
- 在 `extraction_service.extract_and_save` 抽取后、`save_fields` 之前，插入一步**溯源匹配** `_attach_source_geometry(fields, ocr_blocks)`：
  - 对每个有 `source_text` 的字段，在该合同全部 `OCRBlock.text` 中做**模糊匹配**（文本归一化后做子串匹配；无精确命中则退化为最长公共子串/Token 重叠率，阈值如 0.6）。
  - 命中则用该 block 的 `bbox`/`page_no` 回填 `ExtractedField.bbox`/`page_no`，并填 `source_block_start`/`source_block_end`（当前恒空）。
  - 无命中：保留 LLM 自报坐标作 fallback；仍无则 bbox 留空（前端该字段不可点高亮，不报错）。
- 该函数需读取已持久化的 `OCRBlock`（`OCRService.load_result` 已能重建），在 `pipeline._run_extraction_pipeline_inner` 内调用。

**前端**：
- `FlatPdfPreview`（`ExtractionPage.tsx:1425`）增加绝对定位 overlay 层，按当前页字段 bbox 用 `bboxToViewportRect` 画高亮框。
- 点字段 → 滚动到 `page_no` 并高亮对应 bbox；高亮框可 hover 显示 `source_text` 摘要。

**⚠️ 必须修：bbox 坐标契约不一致**
- 后端 `BBox` 形状 `{x1, y1, x2, y2}`（`base.py:10-21`，`save_fields` 用 `model_dump()` 存此形状，`extraction_service.py:216`）。
- 前端 `BBox` 形状 `{x0, y0, x1, y1}`（`types.ts:45-50`），`bboxToViewportRect` 按 x0/y0/x1/y1 取值（`pdfCoordinates.ts:11-14`）。
- **统一契约**：后端为权威源，bbox 一律以 `{x1, y1, x2, y2}`（左上 x1,y1；右下 x2,y2）对外。前端新增 `mapBackendBBox(b)` 适配到 `bboxToViewportRect` 所需形状，或统一将前端 `BBox` 改为 `{x1,y1,x2,y2}` 并同步修改 `bboxToViewportRect`。spec 推荐后者（一处定义，全局一致），需回归检查所有引用。

### 4.3 ③ 置信度展示与兜底

**现状**：`confidence` 已落库并随 `FieldDetail` 返回。

**改动**：
- 前端按阈值给低置信度字段打"低置信·建议复核"标，默认置顶/高亮；批量复核入口提供"仅复核低置信度字段"快捷过滤。
- **阈值判定 + 阈值本身都放前端**：`low_confidence_threshold` 作为前端常量（默认 `0.7`），因为 confidence 值已在响应里，无需后端改动或新增配置接口（YAGNI；后续若需可配置化再接 `/config` 端点）。

### 4.4 ④ 长合同分块（`qwen.py` 核心改动）⚠️ 核心技术点

**现状**：`extract_fields` 把全文塞进单个 prompt（`qwen.py:184`），长合同超模型上下文窗口与 `llm_max_tokens`(4096) 必崩。

**方案（按页分窗 + 合并仲裁）**：
- 抽取入口改为：先按 OCR **页**切分为窗口（默认每窗口 N 页，保留 1 页重叠，N 由 `llm_chunk_pages` 配置，默认 6），估算每窗口 token 数，超过安全水位则缩小窗口。
- 每窗口独立走现有 `_build_dynamic_prompt` + Instructor 调用，得到分段 `ExtractionResult`。
- **合并仲裁** `_merge_chunk_results(per_chunk_results)`：
  - 按 `field_key` 聚合。
  - 非空值优先于空值。
  - 同 key 多段有值且冲突 → 取 `confidence` 最高者；并列时取页码靠前（合同首部优先）。
  - `source_text`/`page_no`/`bbox` 跟随胜出值。
- **短合同（单窗口）退化为现有路径**，行为零变化（回归安全）。
- 单窗口失败：该窗口降级为空结果（其字段在合并阶段表现为"未抽取到"），不中断整体；最终若所有窗口均失败，沿用既有 `RuntimeError("LLM returned no requested fields")` 路径。

**关键决策**：分块粒度=**页**（OCR 已天然分页，避免硬按 token 切割破坏字段语境），而非按 token 硬切。

### 4.5 ⑤ 导出增强（前端为主）

**现状**：仅批量手写 xlsx（`lib/excelExport.ts`、`ExtractionPage.tsx:640`）。

**改动**：
- 前端在结果区与记录页增加"导出"下拉，支持 **单结果 / 批量 × CSV / JSON / XLSX**。
- 纯客户端用已拉取的字段数据生成：JSON/CSV 直接拼接（含 confidence、review_status、page_no），XLSX 复用既有手写逻辑并补"校正值/置信度/页码"列。
- **不新增后端导出端点**（YAGNI；数据前端已有）。

### 4.6 ⑥ 评测集 / 回归基线（新模块）⚠️ 度量地基

**新增**：
- 目录 `backend/tests/eval/samples/`：放脱敏样本合同（PDF/图片）+ 每份的 `golden.json`（期望字段值）。
- 脚本/fixture `backend/tests/eval/test_extraction_accuracy.py`：对每份样本跑真实抽取 → 与 golden 比对 → 输出**字段级 Precision/Recall/F1 + 整体 + null 率**。
- pytest 标记 `@pytest.mark.eval`，**默认不在常规 `pytest` 中跑**（依赖真实 provider，慢），CI 可通过 `pytest -m eval` 手动触发。
- 命中语义：抽取值规范化后 `==` golden 值（或 `==` 该字段的人工 `reviewed_value`）算命中。

**关键决策（已确认）**：若当前无脱敏真实样本，先用 **mock provider** 跑通评测流程并产出基线数字，`samples/` 预留真实样本接入位（含 README 说明标注格式），真实样本由用户后续补。这样度量框架先就位，不阻塞。

### 4.7 ⑦ 最小鉴权（商业化底线）

**现状**：前端硬编码 `admin/123456`（`App.tsx:19`），后端完全裸奔；CORS `["*"]` + credentials（`main.py:107`）。

**方案（已确认 API Key 路线）**：
- 后端：新增鉴权依赖 `verify_api_key`，校验请求头 `X-API-Key` ∈ 环境变量 `APP_API_KEYS`（逗号分隔多 key），挂到 `/api/v1` 全部路由（可放 router 级 `dependencies=`）。`/health` 放行。
- 前端：登录页改为"输入 API Key"，存 localStorage；`api.ts` 请求统一注入 `X-API-Key` 头；401 时回到登录页。
- **不做**：用户体系、JWT、多租户、细粒度权限、限流（→ Phase 3）。CORS 收紧到具体前端来源（从配置读 `allowed_origins`）。
- 安全债顺手清：`.env`（含 key）移出版控、修 `.env` vs `.env.example` 矛盾值（PPOCR 端口、LLM URL 路径）。

---

## 5. 统一契约与跨切关注点

### 5.1 bbox 坐标契约（权威定义）
- 全系统 bbox 一律为 **`{x1, y1, x2, y2}`**：`(x1,y1)` 左上角，`(x2,y2)` 右下角，单位为 PDF/图像像素点（OCR 坐标系）。
- 后端 `BBox`（`base.py`）已是此形状，作为唯一权威。
- 前端统一 `BBox` 为 `{x1,y1,x2,y2}`，`bboxToViewportRect` 同步改为读 `x1/y1/x2/y2`（当前读 x0/y0/x1/y1，需修正）。
- viewport 缩放：`zoom` 由 PDF 预览渲染倍数决定，overlay 层与 canvas 同坐标系。

### 5.2 前后端 `FieldDetail` 对齐
后端 `FieldDetail`（`schemas/contract.py:56-74`）为权威。前端 `FieldDetail`（`types.ts:93-106`）需：
- **补**：`value_type`、`bbox`、`page_end`、`reviewed_value`、`reviewer_id`、`reviewed_at`。
- **删**：幻影字段 `extract_method`（后端不返回；`fieldDetailToExtractionFieldValue` 中相关映射随之移除/简化）。
- 顺手修 `fieldDetailToExtractionFieldValue` 的冗余 status 逻辑（`types.ts:160-167` 两个分支同结果）。

### 5.3 错误处理
- 分块单窗口失败 → 该窗口降级为空，不中断整体。
- OCR 块匹配无命中 → bbox 留空，前端该字段不可点高亮（不报错、不阻塞）。
- 鉴权失败 → 401，前端回登录页。
- 导出生成失败 → 前端 toast，不影响已抽取数据。
- 复用既有 task 重试 / 租约 / 取消机制，**不动队列**。

### 5.4 测试策略
- **后端**（pytest，沿用 `backend/tests/`）：
  - 分块合并仲裁 `_merge_chunk_results`：覆盖空值优先、冲突取高 confidence、并列取靠前页、单窗口退化。
  - 溯源匹配 `_attach_source_geometry`：覆盖精确命中、模糊命中、无命中 fallback。
  - 鉴权中间件：无 key/错 key/正确 key 三例。
  - 评测集 `@pytest.mark.eval`（默认跳过）。
- **前端**：本期**不引入测试框架**（YAGNI，前端目前无测试基建），靠手动验收清单（见 §7 DoD）。

### 5.5 迁移与配置
- **零 Alembic 迁移**（所需列均已存在）。
- 新增配置项（`config.py` Settings + `.env.example`）：
  - `llm_chunk_pages: int = 6`（分块窗口页数）
  - `app_api_keys: str = ""`（鉴权 key 集合）
  - `allowed_origins: str`（CORS 收紧）
  - 注：`low_confidence_threshold` 为前端常量，不在后端配置（见 §4.3）
- 修 `.env` vs `.env.example` 矛盾；`.env` 移出版控（保留 `.env.example`）。

---

## 6. 受影响文件清单（预估）

**后端**
- `app/extraction/llm/qwen.py` —— 分块 + 合并（④，核心）
- `app/services/extraction_service.py` —— 溯源匹配 `_attach_source_geometry`（②）
- `app/services/pipeline.py` —— 在抽取流程中传入 OCR blocks 调溯源（②）
- `app/config.py` —— 新增配置项
- `app/main.py` —— 鉴权依赖挂载、CORS 收紧（⑦）
- `app/api/router.py` 或各 router —— 鉴权依赖（⑦）
- `backend/tests/` —— 分块/溯源/鉴权单测 + `eval/` 评测集（④②⑦⑥）

**前端**
- `src/types.ts` —— FieldDetail 对齐、BBox 契约、mapper 修正（①②⑤）
- `src/lib/api.ts` —— 复核端点调用、API Key 注入（①⑦）
- `src/lib/pdfCoordinates.ts` —— bbox 契约修正（②）
- `src/pages/ExtractionPage.tsx` —— inline 编辑、复核状态、高亮 overlay、导出（①②⑤）
- `src/pages/ExtractionRecordsPage.tsx` —— 导出、复核历史（①⑤）
- `src/pages/LoginPage.tsx` / `src/App.tsx` —— API Key 登录（⑦）
- `src/lib/excelExport.ts` —— 补列、CSV/JSON（⑤）

**无变化**：数据模型、Alembic、队列、worker、OCR provider、复核 API 后端逻辑。

---

## 7. 完成定义（Definition of Done）

**功能验收（手动）**：
1. 上传一份合同 → 看到带置信度标注的抽取结果。
2. 点击任一字段 → PDF **高亮跳转到原文对应位置**。
3. **修正**一个错误字段并保存 → 看到"原值/校正值"对比，复核历史留痕。
4. 选中多个字段 → **批量通过/复核**。
5. 导出单结果与批量，CSV/JSON/XLSX 均可用且含置信度/页码/校正值。
6. 上传一份**长合同**（多页）→ 不再崩溃，字段完整。
7. 不带 API Key 访问任意 `/api/v1` 端点 → 401。

**工程验收**：
8. 后端新增逻辑（分块合并、溯源匹配、鉴权）有 pytest 覆盖且通过。
9. `pytest -m eval` 能跑通评测流程并产出字段级 P/R/F1 基线数字（mock 或真实样本）。
10. `.env` 已移出版控，`.env.example` 与实际配置一致。

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| OCR 块匹配误命中（source_text 短/通用） | 模糊匹配设阈值；低置信匹配不回填 bbox（fallback LLM 坐标或留空）；前端高亮可 hover 核对 source_text |
| 分块导致跨页字段被割裂 | 1 页重叠窗口 + 合并仲裁取高 confidence；评测集回归监控 |
| bbox 坐标系（OCR 像素 vs PDF viewport）换算偏差 | 统一契约 + 前端 mapper；手动验收高亮位置准确性 |
| 鉴权上线影响现有调用方 | API Key 通过配置下发；提供迁移说明；`/health` 放行 |
| 无真实评测样本导致基线无意义 | mock provider 先跑通框架；预留真实样本接入位，不阻塞 |

---

## 9. 决策记录（Decisions Log）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 推进策略选方案 B（闭环优先） | 闭环既交付可用产品又产出准确率标注数据；方案 A/C 地基不稳 |
| D2 | 溯源用 OCR 块模糊匹配，不信 LLM 自报坐标 | LLM 坐标不可靠；OCRBlock 有权威 bbox/page |
| D3 | 分块粒度=页，非 token 硬切 | OCR 天然分页，避免破坏字段语境 |
| D4 | 编辑写 `reviewed_value` 不覆盖 `value` | 保留原始抽取值作准确率度量与 few-shot 真值来源 |
| D5 | 鉴权用 API Key，不做 JWT/多租户 | 最小成本堵裸奔，符合"工程化后置"优先级 |
| D6 | 评测集无真实样本时用 mock 跑通框架 | 度量框架先就位，不阻塞；真实样本后续补 |
| D7 | 前端本期不引入测试框架 | YAGNI；前端无既有测试基建，手动验收即可 |
| D8 | 导出不新增后端端点，纯前端生成 | 数据前端已有，YAGNI |
| D9 | bbox 全局统一为 `{x1,y1,x2,y2}` | 一处定义，消除前后端不一致 |

---

## 10. 后续阶段指引（不在本 spec 范围）

- **Phase 2**：用 Phase 1 的 `reviewed_value` 校正数据做 few-shot / 换更强 Qwen 或云 API；合同摘要；把 `9153b63` 删掉的 `contract_risks` 风险审查补回（建立在 Phase 1 已校验的抽取之上）。用 ⑥ 评测集量化提升幅度。
- **Phase 3**：接线 `clause_service` 死代码 + `ContractClause` 表做条款管理；多人协作审查/修改留痕/版本追溯；多租户、权限、限流、Docker 部署/CI、可观测性正式补齐。
