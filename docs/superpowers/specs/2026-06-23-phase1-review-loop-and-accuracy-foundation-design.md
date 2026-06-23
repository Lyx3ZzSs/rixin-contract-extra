# Phase 1 设计：准确率地基 + 可用的产品复核闭环

- **日期**：2026-06-23
- **状态**：待评审（Draft v2，待用户确认后进入 writing-plans）
- **范围**：rixin-contract-extract（AI 合同信息抽取系统）优化的 **Phase 1**
- **关联**：本 spec 是「合同抽取系统优化路线图」三阶段中的第一阶段
- **v2 变更**：解析器升级（PP-StructureV3）由 Phase 2 拉入 Phase 1 作为准确率地基；② 溯源由"像素级 bbox 高亮"简化为"source_text 片段 + page_no 页码"；评测集升为 Phase 1 第一步；路线图 Phase 2 收窄。

---

## 1. 背景与上下文

rixin-contract-extract 是一个 FastAPI(async) + React/TS + 自建 Qwen3-30B-A3B / PP-OCR 的合同信息抽取系统，管线为：
`上传 → OCR → Qwen 结构化字段抽取 → 规则校验 → 人工复核`。

市面对标（e签宝 / 通义法睿 / 法大大）的核心能力是：高精度抽取、合同审查/风险预警、条款识别、抽取结果可编辑复核、原文溯源、多人协作。本项目当前最大的短板是：**OCR 层降级丢掉版面/表格能力（准确率痛点）、抽取结果只读、长合同会崩、不可量化、裸奔无鉴权**。

### 1.1 已确认的优化约束（来自头脑风暴）

| 维度 | 结论 |
|---|---|
| 首要目标 | 提升抽取准确率 + 补齐产品功能对标竞品 + 新增智能能力（工程化暂缓） |
| 使用场景 | **商业化产品**（需复核闭环、溯源、协作；多租户/权限可后置） |
| 模型设施 | 灵活，可换更强模型 / 云 API；**OCR 解析器可换、需本地部署** |
| 业务规模 | 小规模（<100 份/天）→ SQLite 单 worker 队列够用，架构重构一律后置 |

### 1.2 关键技术判断（驱动 v2 调整）

1. **坐标对"抽取准确率"零贡献**：LLM 基于文本抽字段，bbox 只服务于"点字段→跳原文高亮"这个 UX 功能（即 ②），不是准确率功能。
2. **PP-OCR 只识别文字、丢了版面/表格**：`0e46b91` 把 PP-Structure 换成 PP-OCR，丢掉了版面分析、表格结构、阅读顺序、KIE。合同满是表格（付款节点/价格/标的），表格被当散行吐、丢单元格结构 → 表格内字段抽取质量掉。**这是准确率最大痛点与最大杠杆**。
3. **PP-StructureV3 是低摩擦补救**：PaddleOCR 3.0（2025.05）的 PP-StructureV3 在现有 PaddlePaddle 生态内、可 HTTP serving，迁移摩擦最小，直接补回表格/版面。升级后其版面区域坐标可"免费"支撑未来的像素级溯源高亮。
4. **解析层对合同准确率的影响 > 换 LLM**：Qwen3-30B-A3B 暂时够用，LLM 升级留 Phase 2 且需评测集数据支撑。

### 1.3 推进策略：方案 B（产品闭环优先）+ 准确率地基前置

```
Phase 1（本 spec）：准确率地基（解析器升级 + 评测集 + 分块）+ 产品复核闭环
Phase 2：LLM 升级 + 智能能力（few-shot/换模型、合同摘要、风险审查）
Phase 3：条款管理与协作（接线 clause_service、协作留痕、多租户/部署）
```

---

## 2. 目标与非目标

### 2.1 Phase 1 目标
1. **准确率地基**：用 PP-StructureV3 补回表格/版面能力；建评测集让准确率可量化；修长合同分块防爆。
2. **产品闭环**：让系统从"抽取完只能看"变成"能校、能溯源（片段+页码）、能量化、能导出、不裸奔"。

### 2.2 非目标（明确排除，留待后续阶段）
- ❌ 像素级 bbox 原文高亮（② 本期只做片段+页码；像素高亮等解析器坐标稳定后作低成本增量）
- ❌ 多租户、用户体系、细粒度权限、RBAC（→ Phase 3）
- ❌ 多人协作审查、修改留痕 UI、版本树（→ Phase 3）
- ❌ 合同摘要、风险/合规审查（→ Phase 2）
- ❌ 条款提取与管理（→ Phase 3）
- ❌ 队列重构（Redis/Celery）、多 worker 水平扩展
- ❌ LLM 更换 / few-shot / 微调（→ Phase 2，依赖本阶段评测基线）
- ❌ Docker / CI/CD / 可观测性体系（→ Phase 3）

---

## 3. 关键发现：后端骨架已就绪（重大去风险）

深入读码后确认，Phase 1 的"闭环"部分大量基础设施**已经存在**，真正的工作是解析器升级 + 接线 + 少数新模块：

| 能力 | 现状证据 | Phase 1 实际要做 |
|---|---|---|
| OCR provider 抽象 | `extraction/ocr/` 有 base/mock/ppocr + 工厂（`__init__.py:4`），HTTP serving 模式可复用 | **新增 `ppstructurev3` provider 即可切换** |
| 复核数据模型 | `ExtractedField` 已有 `reviewed_value`/`review_status`/`reviewer_id`/`reviewed_at`/`confidence`/`page_no`/`source_*`（`models/contract.py:90-121`） | **零数据库迁移** |
| 复核 API | `api/review.py` Phase9 已完整：`POST /review`、`PATCH /fields/{fid}/review`、`POST /review/batch`、`GET /review/records`；`reviewed_value` 不覆盖原 `value`（`review.py:43-358`） | **后端几乎不动，纯前端接线** |
| 置信度落库 | `save_fields` 已持久化 `confidence`/`page_no`/`source_text`（`extraction_service.py:218-229`） | 已落库 |
| LLM 返回溯源 | prompt 已要求 `source_text`/`source_page`/`confidence`，`_convert_raw_fields` 已映射（`qwen.py:84-90, 219-250`） | 已产出 |
| 字段返回 schema | `FieldDetail` 已返回 confidence/reviewed_value/review_status 等（`schemas/contract.py:56-74`） | 前端类型需对齐 |

---

## 4. Phase 1 详细设计（8 项工作，按实施顺序）

> 实施顺序：**先度量（① 评测集）→ 升引擎（② 解析器）→ 硬化（③ 分块）→ 产品化（④⑤⑥⑦）→ 安全（⑧）**。

### 4.1 ① 评测集 / 回归基线（度量地基，最先做）⚠️
**为什么先做**：它是后续解析器升级、分块、Phase 2 LLM 升级的**唯一量化标尺**。没有它，"换了 PP-StructureV3 是否更准"只能靠感觉。

**新增**：
- `backend/tests/eval/samples/`：脱敏样本合同（PDF/图片）+ 每份 `golden.json`（期望字段值）。
- `backend/tests/eval/test_extraction_accuracy.py`：对每份样本跑真实抽取 → 与 golden 比对 → 输出**字段级 Precision/Recall/F1 + 整体 + null 率 + 表格字段 vs 非表格字段分项**。
- pytest 标记 `@pytest.mark.eval`，默认不在常规 `pytest` 跑（依赖真实 provider，慢），`pytest -m eval` 手动触发。
- 命中语义：抽取值规范化后 `==` golden（或 `==` 人工 `reviewed_value`）算命中。

**关键决策（D6）**：当前若无脱敏真实样本，先用 **mock provider** 跑通评测流程并产出基线数字，`samples/` 预留真实样本接入位 + README 标注格式。**真实样本到位后，立即用它对比 PP-OCR vs PP-StructureV3**，作为 ② 的验收依据。

### 4.2 ② 解析器升级：集成 PP-StructureV3（最大准确率杠杆）⚠️ 核心技术点
**现状**：`PPOCRProvider`（`extraction/ocr/ppocr.py`）只做文字检测+识别，无版面/表格结构。

**方案**：
- 新增 provider `extraction/ocr/ppstructurev3.py`，复用既有 provider 抽象与 HTTP serving 模式（PaddleOCR 3.0 / PaddleX 提供 serving）。config 新增 `ocr_provider="ppstructurev3"`。
- PP-StructureV3 输出：版面区域（title/text/table/figure/list，带 bbox）+ 表格结构（HTML/Markdown）+ 阅读顺序 + 公式/印章识别。
- **OCR 数据模型扩展**（`extraction/base.py`）：
  - `OCRTextBlock.block_type` 由 PP-StructureV3 权威填充（取代当前 born-digital 路径的 font-size 猜测，`ppocr.py:142`）。
  - 新增**表格结构表达**：table block 携带结构化 cell 布局；并提供"整页 Markdown"输出（表格保留为 markdown 表格）。
- **喂给 LLM 的文本升级**：把每页**结构化 Markdown**（表格保留为 markdown 表格）喂给 Qwen，取代当前扁平全文（`qwen.py:115`）—— **这是表格字段准确率提升的主要来源**。
- 保留 `ppocr` provider 作 fallback（抽象已支持切换）；`OCRService` 与 `pipeline` 无需改逻辑，仅工厂切换。
- 用 ① 评测集量化：同批合同 PP-OCR vs PP-StructureV3 的字段级 F1（尤其表格字段）对比，作为验收。

**关键决策**：
- 选 PP-StructureV3 而非 MinerU（D10）：留在 PaddlePaddle 生态、复用 HTTP serving、迁移摩擦最小；若实测复杂表格仍不行，Phase 2 再评估 MinerU 2.5。
- 喂 LLM 用结构化 Markdown 而非扁平文本（D12）：让模型"看见"表格结构。
- 版面区域 bbox 一并落库（`OCRBlock` 已有 bbox/page 字段），为未来像素级溯源高亮铺路（不在本期做）。

### 4.3 ③ 长合同分块（`qwen.py` 核心改动）⚠️ 核心技术点
**现状**：`extract_fields` 把全文塞进单个 prompt（`qwen.py:184`），长合同超模型上下文窗口与 `llm_max_tokens`(4096) 必崩。

**方案（按页分窗 + 合并仲裁）**：
- 按 OCR **页**切分为窗口（默认每窗口 N 页、1 页重叠，N 由 `llm_chunk_pages` 配置，默认 6），估算 token 超安全水位则缩小窗口。
- 每窗口独立走 `_build_dynamic_prompt` + Instructor 调用，得分段结果。
- **合并仲裁** `_merge_chunk_results`：按 `field_key` 聚合；非空优先于空；冲突取 `confidence` 最高、并列取页码靠前（合同首部优先）；`source_*` 跟随胜出值。
- 短合同（单窗口）退化为现有路径，行为零变化（回归安全）。
- 单窗口失败 → 该窗口降级为空，不中断整体；全失败则沿用既有 `RuntimeError` 路径。

**关键决策（D3）**：分块粒度=**页**（OCR 已天然分页，避免硬按 token 切破坏字段语境）。注：② 落地后"页"= PP-StructureV3 的结构化页 Markdown。

### 4.4 ④ 人工复核闭环（纯前端接线 + 极小后端微调）
**现状**：后端复核 API 全部就绪（`review.py`）；前端 `ExtractionPage` 结果区只读。

**改动**：前端每个字段卡片增加：
- **inline 编辑**：保存调 `PATCH /api/v1/contracts/{id}/fields/{fid}/review`（`action="modify", new_value=...`）。
- **复核状态徽章**：extracted/corrected/approved/rejected。
- **批量复核**：`POST /api/v1/contracts/{id}/review/batch`。
- **复核历史抽屉**：`GET /api/v1/contracts/{id}/review/records`。
- 合同级联动：既有 `POST /contracts/{id}/approve|reject`（`contract.py:191,232`）。

**关键决策（D4）**：编辑写 `reviewed_value`，**永不覆盖原始 `value`**——原始值是 Phase 2 准确率度量与 few-shot 的真值来源；前端展示"原值/校正值"对比。
**顺手修**：前端 `FieldDetail`（`types.ts:93-106`）补 `reviewed_value`/`reviewer_id`/`reviewed_at`/`value_type`，删幻影字段 `extract_method`（后端不返回）；简化 `fieldDetailToExtractionFieldValue` 冗余 status 逻辑（`types.ts:160-167`）。`bbox`/`page_end` 本期不补（⑤ 不渲染坐标，YAGNI，留待像素高亮阶段）。

### 4.5 ⑤ 原文溯源（简化版：片段 + 页码）
**现状**：`source_text` 与 `page_no` 已落库并随 `FieldDetail` 返回。

**改动**：
- 前端字段卡片/记录页展示 `source_text` 摘录 + "第 N 页"标签；复核时人工可据此快速核对原文。
- **不做**像素级 bbox 高亮、不做 OCR 块模糊匹配、不动 bbox 坐标契约（全部后置）。
- 当 ② PP-StructureV3 落地、版面区域 bbox 稳定后，像素级高亮可作为低成本增量补回（届时坐标由解析器权威产出，无需 LLM 猜坐标）。

**关键决策（D11）**：v2 将 ② 从"像素级高亮"降级为"片段+页码"，砍掉 OCR 块匹配与坐标契约修复的复杂度，避免在即将被解析器升级替代的基础上做脆弱工作。

### 4.6 ⑥ 置信度展示与兜底
**现状**：`confidence` 已落库并随 `FieldDetail` 返回。
**改动**：前端按阈值（**前端常量** `low_confidence_threshold`，默认 `0.7`，无需后端配置）给低置信度字段打"低置信·建议复核"标并置顶/高亮；批量复核提供"仅复核低置信度字段"快捷过滤。
**关键决策**：阈值判定 + 阈值本身都在前端（confidence 已在响应里，YAGNI 不加后端配置接口）。

### 4.7 ⑦ 导出增强（前端为主）
**现状**：仅批量手写 xlsx（`lib/excelExport.ts`、`ExtractionPage.tsx:640`）。
**改动**：前端在结果区与记录页加"导出"下拉，支持 **单结果/批量 × CSV/JSON/XLSX**，纯客户端用已拉取字段数据生成（含 confidence/review_status/page_no/校正值），XLSX 复用既有手写逻辑补列。**不新增后端端点**（YAGNI）。

### 4.8 ⑧ 最小鉴权（商业化底线）
**现状**：前端硬编码 `admin/123456`（`App.tsx:19`），后端裸奔；CORS `["*"]` + credentials（`main.py:107`）。
**方案（D5，API Key 路线）**：
- 后端新增依赖 `verify_api_key`，校验请求头 `X-API-Key` ∈ 环境变量 `APP_API_KEYS`，挂到 `/api/v1` 全部路由（router 级 `dependencies=`）；`/health` 放行。
- 前端登录页改为"输入 API Key"，存 localStorage；`api.ts` 统一注入 `X-API-Key`；401 回登录页。
- **不做**：用户体系/JWT/多租户/细粒度权限/限流（→ Phase 3）。CORS 收紧到配置 `allowed_origins`。
- 安全债顺手清：`.env`（含 key）移出版控、修 `.env` vs `.env.example` 矛盾（PPOCR 端口、LLM URL 路径）。

---

## 5. 统一契约与跨切关注点

### 5.1 OCR 数据模型扩展（为 ② 服务）
- `OCRTextBlock.block_type`：由 PP-StructureV3 权威填充（text/title/table/figure/list），取代 font-size 启发。
- 新增**表格结构**：table 类型 block 携带 cell 布局（行列/合并），并提供整页 Markdown（表格保留结构）供 LLM 消费。
- 版面区域 bbox 一并写入 `OCRBlock`（既有列），为未来像素级溯源铺路。

### 5.2 前后端 `FieldDetail` 对齐（为 ④⑥⑦ 服务）
后端 `FieldDetail`（`schemas/contract.py:56-74`）为权威。前端 `FieldDetail`（`types.ts:93-106`）需：
- **补**：`value_type`、`reviewed_value`、`reviewer_id`、`reviewed_at`。
- **删**：幻影 `extract_method`（后端不返回；mapper 相关映射移除）。
- `bbox`/`page_end` 本期不补、bbox 坐标契约（后端 `{x1,y1,x2,y2}` vs 前端 `{x0,y0,x1,y1}`）**本期不修**（⑤ 不做像素高亮），整体留待像素高亮增量阶段统一为 `{x1,y1,x2,y2}`。

### 5.3 错误处理
- 解析器（PP-StructureV3）HTTP 失败 → 复用既有重试（`ppocr.py:464-485` 模式）；全失败则任务失败（不静默吞）。
- 分块单窗口失败 → 该窗口降级为空，不中断整体。
- 鉴权失败 → 401，前端回登录页。
- 导出生成失败 → 前端 toast，不影响已抽取数据。
- 复用既有 task 重试/租约/取消，**不动队列**。

### 5.4 测试策略
- **后端**（pytest，沿用 `backend/tests/`）：
  - 解析器：PP-StructureV3 provider 的响应归一化/表格结构解析单测（mock HTTP）。
  - 分块合并 `_merge_chunk_results`：空值优先/冲突取高 confidence/并列取靠前页/单窗口退化。
  - 鉴权中间件：无 key/错 key/正确 key。
  - 评测集 `@pytest.mark.eval`（默认跳过，真实样本到位后启用）。
- **前端**：本期**不引入测试框架**（YAGNI），靠手动验收清单（见 §7）。

### 5.5 迁移与配置
- **零 Alembic 迁移**（所需列均已存在；OCR 数据模型扩展是 Pydantic 类型，非 DB 列）。
- 新增配置项（`config.py` Settings + `.env.example`）：
  - `ocr_provider: str = "ppstructurev3"`（默认升级，保留 `ppocr`/`mock` 可切）
  - `ppstructurev3_url: str`（解析器 serving 地址）
  - `llm_chunk_pages: int = 6`
  - `app_api_keys: str = ""`
  - `allowed_origins: str`（CORS 收紧）
- 修 `.env` vs `.env.example` 矛盾；`.env` 移出版控（保留 `.env.example`）。

---

## 6. 受影响文件清单（预估）

**后端**
- `app/extraction/ocr/ppstructurev3.py` —— 新 provider（②，核心）
- `app/extraction/ocr/__init__.py` —— 工厂注册新 provider（②）
- `app/extraction/base.py` —— OCR 数据模型扩展（表格结构/Markdown 输出）（②⑤.1）
- `app/extraction/llm/qwen.py` —— 分块 + 合并 + 消费结构化 Markdown（③）
- `app/services/ocr_service.py` —— 适配新 provider 输出（②）
- `app/config.py` —— 新增配置项
- `app/main.py` —— 鉴权依赖挂载、CORS 收紧（⑧）
- `app/api/router.py` 或各 router —— 鉴权依赖（⑧）
- `backend/tests/eval/` —— 评测集 + 脚本（①）
- `backend/tests/` —— 解析器/分块/鉴权单测（②③⑧）

**前端**
- `src/types.ts` —— FieldDetail 对齐、mapper 修正（④⑥⑦）
- `src/lib/api.ts` —— 复核端点调用、API Key 注入（④⑧）
- `src/pages/ExtractionPage.tsx` —— inline 编辑、复核状态、溯源片段+页码、置信度、导出（④⑤⑥⑦）
- `src/pages/ExtractionRecordsPage.tsx` —— 复核历史、导出（④⑦）
- `src/pages/LoginPage.tsx` / `src/App.tsx` —— API Key 登录（⑧）
- `src/lib/excelExport.ts` —— 补列、CSV/JSON（⑦）

**无变化**：数据模型(ORM)、Alembic、队列、worker、复核 API 后端逻辑。

---

## 7. 完成定义（Definition of Done）

**准确率地基验收**：
1. 评测集（真实样本到位后）显示 **PP-StructureV3 的表格字段 F1 显著优于 PP-OCR**（量化对比报告）。
2. 上传**长合同**（多页）→ 不再崩溃，字段完整。
3. `pytest -m eval` 能跑通并产出字段级 P/R/F1。

**产品闭环验收（手动）**：
4. 上传合同 → 看到带置信度标注的抽取结果。
5. 每个字段可见 `source_text` 片段 + 页码，可据此核对原文。
6. **修正**错误字段并保存 → "原值/校正值"对比 + 复核历史留痕。
7. 选中多字段 → **批量复核**。
8. 导出单结果与批量，CSV/JSON/XLSX 均含置信度/页码/校正值。
9. 不带 API Key 访问任意 `/api/v1` → 401。

**工程验收**：
10. 后端新增逻辑（解析器、分块合并、鉴权）有 pytest 覆盖且通过。
11. `.env` 已移出版控，`.env.example` 与实际配置一致。

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| PP-StructureV3 serving 部署/兼容问题 | provider 抽象可随时切回 `ppocr`；先在评测集小批量验证再切默认 |
| 表格结构 → Markdown 转换丢信息 | 评测集表格字段分项 F1 监控；必要时同时保留 cell 结构化数据 |
| 分块割裂跨页字段 | 1 页重叠窗口 + 合并仲裁取高 confidence；评测集回归监控 |
| 无真实评测样本致基线无意义 | mock 先跑通框架；预留样本接入位，不阻塞 |
| 鉴权上线影响现有调用方 | API Key 配置下发 + 迁移说明；`/health` 放行 |
| PP-StructureV3 实测复杂表格仍弱 | Phase 2 评估 MinerU 2.5 作替代 |

---

## 9. 决策记录（Decisions Log）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 推进策略选方案 B（闭环优先） | 闭环既交付可用产品又产出准确率标注数据 |
| D2 | 解析器升级拉进 Phase 1（准确率优先） | 解析层是合同准确率最大杠杆；用户 #1 目标是准确率 |
| D3 | 分块粒度=页，非 token 硬切 | OCR 天然分页，避免破坏字段语境 |
| D4 | 编辑写 `reviewed_value` 不覆盖 `value` | 保留原始抽取值作准确率度量与 few-shot 真值来源 |
| D5 | 鉴权用 API Key，不做 JWT/多租户 | 最小成本堵裸奔，符合"工程化后置" |
| D6 | 评测集无真实样本时用 mock 跑通框架 | 度量框架先就位，不阻塞；真实样本后续补 |
| D7 | 前端本期不引入测试框架 | YAGNI；前端无既有测试基建 |
| D8 | 导出不新增后端端点，纯前端生成 | 数据前端已有，YAGNI |
| D9 | 选 PP-StructureV3 而非 MinerU | 留 PaddlePaddle 生态、复用 HTTP serving、迁移摩擦最小 |
| D10 | 喂 LLM 用结构化 Markdown 而非扁平文本 | 让模型"看见"表格结构，表格字段准确率主来源 |
| D11 | ② 溯源简化为片段+页码，砍像素高亮 | 避免在即将被解析器替代的基础上做脆弱的坐标匹配；像素高亮留低成本增量 |
| D12 | 置信度阈值定前端常量 | confidence 已在响应里，YAGNI 不加后端配置 |

---

## 10. 后续阶段指引（不在本 spec 范围）

- **Phase 2**：用 Phase 1 的 `reviewed_value` 校正数据做 few-shot / 横向评测换 LLM（Qwen3-235B-A22B / DeepSeek-V3.1）；合同摘要；把 `9153b63` 删掉的 `contract_risks` 风险审查补回（建立在 Phase 1 已校验抽取之上）；若 PP-StructureV3 表格仍弱，评估 MinerU 2.5。用 ① 评测集量化提升。像素级原文高亮作低成本增量补回（复用 PP-StructureV3 版面坐标）。
- **Phase 3**：接线 `clause_service` 死代码 + `ContractClause` 表做条款管理；多人协作审查/修改留痕/版本追溯；多租户、权限、限流、Docker 部署/CI、可观测性正式补齐。

---

## 附录 A：技术调研依据（2026-06）
- **PP-StructureV3**（PaddleOCR 3.0, 2025.05）：版面分析 + 表格识别 + 公式 + 图表 + 印章 + 阅读顺序，Apache 2.0，本地/serving 部署。
- **MinerU 2.5**（上海 AI Lab）：1.2B VLM，OmniDocBench 声称超越 Gemini2.5-Pro/GPT-4o/Qwen2.5-VL-72B，表格/版面质量最高。
- **PP-OCR vs PP-Structure**：前者仅文字检测+识别；后者在其上叠加版面/表格/KIE（本项目 `0e46b91` 降级丢失）。
- 其他候选（备选）：Surya（650M，版面+表格+阅读顺序）、olmOCR-2-7B（VLM OCR 基准）。
