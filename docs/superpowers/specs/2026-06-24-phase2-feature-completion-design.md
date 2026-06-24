# Phase 2 设计：功能闭环补全（接线优先）+ 构造性零误差溯源

- **日期**：2026-06-24
- **状态**：待评审（Draft v1，待用户复核后进入 writing-plans）
- **范围**：rixin-contract-extract（AI 合同信息抽取系统）优化的 **Phase 2**
- **关联**：本 spec 是「合同抽取系统优化路线图」三阶段中的第二阶段；承接 `2026-06-23-phase1-review-loop-and-accuracy-foundation-design.md`

---

## 1. 背景与上下文

rixin-contract-extract 是 FastAPI(async) + React/TS + 自建 Qwen3-30B-A3B / PP-StructureV3 的合同信息抽取系统，管线为：
`上传 → OCR → Qwen 结构化字段抽取 → 规则校验 → 人工复核`。

**Phase 1 已交付**（基线）：PP-StructureV3 解析器、评测集框架、长合同分块、人工复核闭环（字段级 review + 审计 `ReviewRecord`）、置信度展示、CSV/JSON/XLSX 导出、最小 API-Key 鉴权。

### 1.1 Phase 2 的发现：原路线图 vs 实际代码的张力

Phase 1 spec §10 原把 Phase 2 预留为「LLM 升级 + 智能能力（摘要/风险）」。但 Phase 2 规划期深入读码后发现：**一批已开发的高价值能力是"死代码"，从未接入流水线**；同时**原文溯源的数据虽全链路就绪，前端从未渲染**。这批"接线即功能"的缺口 ROI 远高于换大模型。

| 发现（有代码证据） | 现状 | Phase 2 处置 |
|---|---|---|
| `clause_service.split_and_save_clauses` 条款拆分 | 零调用方，`ContractClause` 表永远空 | 接入 pipeline（模型/Schema/review API 已全就绪） |
| `rule_validation_service` 规则校验（必填/日期/金额/比例/置信度等内置规则） | 零调用方，纯内存计算不落库 | 接入 pipeline + 新表落库 |
| `classify_contract_type` 合同类型分类 | 函数就绪，pipeline 未调用（现靠 prompt 内联） | 接入 pipeline 作字段集驱动 |
| PDF 原文 bbox 溯源 | 后端全链路返回 bbox/page_no，前端从未渲染高亮 | 前端接线（Tier 2 零误差方案） |

### 1.2 已确认的优化约束（来自本阶段头脑风暴）

| 维度 | 结论 |
|---|---|
| 主线方向 | **功能闭环补全（接线优先）** —— 把已写好的能力接通、把数据已就绪的功能接线 |
| 纳入轨道 | ①规则校验接线 ②PDF 原文高亮 ③合同类型分类接线 ④条款管理最小版 |
| 规则校验语义（决策 A） | 非阻断 + 三级严重度(error/warning/info) + 落库 `rule_violations` + 可「忽略」留痕 |
| classify 用途（决策 B） | 分类**驱动字段集**：`field_definitions` 加 `contract_type` 列，按类型加载（通用+专属） |
| 流水线编排 | **方案 2**：classify 门控 extract + clause-split 从 OCR 分叉双轨并发 |
| 原文高亮深度 | **Tier 2 构造性零误差**：本地光栅化 → 发图像给 OCR → bbox 与显示面同源 |

### 1.3 关键技术判断

1. **接线优先 ROI 最高**：3 项后端模块代码已就绪（clause/rule/classify），零/极小迁移即变功能；不必等 LLM 升级。
2. **规则预警 + 原文溯源是法务产品核心卖点**：对标 e签宝/通义法睿的"规则预警"与"点字段→跳原文"，本期一次补齐。
3. **坐标映射是最高风险点，须用成熟方案而非 hack**：bbox 坐标空间因 provider/文档类型而异（PDF 点 vs 远端栅格像素），OCR 光栅图从未持久化。采用 **Tier 2** 让 backend 成为光栅化唯一真相源，构造性消除映射误差。

---

## 2. 目标与非目标

### 2.1 Phase 2 目标
1. **规则校验闭环**：抽取后自动跑内置规则集（必填/日期/金额/比例/置信度等），违规分级落库、前端告警、可「忽略」留痕、复核修正触发重算。
2. **原文零误差溯源**：点字段/条款 → 跳转对应页 → bbox 框选高亮，与原文**构造性对齐**。
3. **分类驱动抽取**：classify 定类型 → 按类型加载字段集（通用+专属）。
4. **条款最小版**：pipeline 拆分入库条款 → 扁平列表 + 条款级复核 + 原文高亮。

### 2.2 非目标（明确排除，留待后续阶段）
- ❌ 条款层级树（`parent_id` 仅存储，UI 后置 Phase 3）
- ❌ 合同摘要、风险/合规深度审查（路线图后续）
- ❌ LLM 更换 / few-shot / 微调（依赖评测基线，后续阶段）
- ❌ 多租户、用户/角色/RBAC、限流（Phase 3）
- ❌ 队列重构（Redis/Celery）、多 worker 横向扩展
- ❌ Docker / CI/CD / 可观测性体系（Phase 3）
- ❌ `ExtractionPage.tsx` 整文件大重构（本期仅抽离预览组件 + 加 ErrorBoundary）
- ❌ 前端广覆盖测试（本期仅 vitest 最小安全网，可选）

---

## 3. 架构与数据流

### 3.1 总览：OCR 流水线增光栅化基建 + 抽取流水线改双轨

```
[OCR 流水线 - Tier 2 改造]
  上传文件
    ├─ PDF：本地光栅化 page.get_pixmap(dpi) → 逐页图像（落盘 uploads/.../pages/）
    └─ 图片：直接使用
       │
       ▼  把「页面图像」（非 PDF 字节）发给 PP-StructureV3
  OCR → 返回 bbox（已在我们的图像像素空间）+ 文本 + 版面
       │
       ├─ bbox 保持原始像素（OCR 图像空间；与 clause_service 像素阈值兼容）
       └─ 持久化 OCRBlock（bbox + page_width/height）+ OCRDetailedResult + 页面图像文件

[抽取流水线 - 方案 2 双轨]
  OCR 完成
       │
       ├── Track A（LLM 重，顺序链）          ├── Track B（CPU，独立 session）
       │   1. classify_contract_type          │   clause_service.split_and_save_clauses
       │      → Contract.contract_type         │   → ContractClause 表（level/bbox/page_no）
       │   2. load_field_definitions(type)     │      （bbox 已归一化）
       │      → 通用 + 该类型专属
       │   3. extract_and_save
       │      → ExtractedField（值/confidence/归一化bbox/source）
       │   4. rule_validation_service.validate_contract
       │      → rule_violations（落库）
       └─────────── asyncio.gather ────────────┘
                       │
                       ▼
            任务完成 → 前端拉 ContractDetail（fields + clauses + violations + page_dimensions）
```

### 3.2 关键设计点
- **Track A 内强顺序**：classify 必先于 extract（类型驱动字段集）；rule-validate 必后于 extract（读字段）。两次 LLM 调用 + 一次规则计算。
- **Track B 独立**：clause-split 只消费 OCR 结果，与 Track A 的两次 LLM 调用**重叠执行**，单文档延迟实收益。
- **并发写库**：Track A 写 `extracted_fields`/`rule_violations`，Track B 写 `contract_clauses`，不同表；Track B 用**独立 `AsyncSession`**（WAL 下并发写安全）。
- **classify 权威**：类型来自独立 `classify_contract_type`；extract 内联返回的类型仅交叉校验，不一致 → 一条 `info` 违规。
- **回归安全**：任一后处理步骤失败 → 该步降级为空（不影响其余结果）；仅 classify/extract 失败才走既有 `RuntimeError` → 任务失败。

---

## 4. Tier 2 后端基建：构造性零误差溯源

> bbox 与显示面必须来自**同一次光栅化**。让 backend 成为光栅化 + OCR + 显示的唯一真相源。

### 4.1 本地光栅化（接线 dead config）
- PDF：PyMuPDF `page.get_pixmap(dpi=settings.ocr_pdf_dpi)` 逐页栅格化（默认 200，可配 150–300）。当前 `config.py:25` 的 `ppocr_pdf_dpi` 是 dead config，本期接线。
- 直接上传图片：本身即图像，无需栅格化，直接使用。

### 4.2 OCR provider 输入改为页面图像（关键改动）
- **现状**：把 PDF 字节 base64 发给 PP-StructureV3（`fileType=0`），bbox 落在**远端**栅格空间（不可控 DPI）。
- **Tier 2**：把本地栅格化的**每页图像**发给 PP-StructureV3（`fileType=1`，逐页图像），bbox 返回时**就在我们图像的像素空间**。
- 保留 PDF 直发路径作 fallback provider 模式（配置切换），降级退路。

### 4.3 页面图像持久化
- 路径：`uploads/contracts/<contract_uuid>/pages/page_<n>.png`。
- 重跑 OCR 时先清理该合同旧页面图（与 `save_blocks` 删旧 OCRBlock 对齐）；合同删除端点尚不存在，`delete_contract_pages` 清理函数预置待用（扩展 `file_service`，沿用本地存储，无对象存储新依赖）。
- 鉴权：`GET /pages/{n}/image` 沿用 API-Key（`<img src>` 场景支持 `?api_key=`，与下载端点一致）+ `Cache-Control`。

### 4.4 bbox 存储策略：保持原始像素（不归一化）
- OCR 回流后 bbox **保持原始像素值**（OCR 图像空间），**不做 [0,1] 归一化**。
- 原因：① Tier 2 的零误差来自"显示同一张 OCR 图像"，bbox（图像像素）与 `<img>`（同图）天然同空间，按 `bbox × 显示宽/page_width` 精确叠加即可，**无需归一化**；② `clause_service._VERTICAL_GAP_THRESHOLD=50.0` 是像素空间阈值，归一化会破坏条款拆分。
- bbox 列、page_width/height 列、存值**全部不变**（与现状一致），**零 schema 迁移、零存值变换**。
- 前端叠加：`rect.left = bbox.x1 × (displayWidth / page_width)`（page_width 来自 OCRBlock，与 bbox 同源于 OCR 图像）。

### 4.5 存储与成本缓解
- DPI 200、A4 ≈ 1654×2339，PNG ≈ 1–3MB/页；30 页 ≈ 30–90MB。
- 缓解：DPI 可配；合同删除清页面图；可选 JPEG（照片页）/PNG（文本页）。

---

## 5. 数据模型与迁移

**两处轻量迁移 + bbox 归一化（零迁移）**，其余接线零迁移。

### 5.1 新表 `rule_violations`（决策 A）

| 列 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| contract_id | FK → contracts | |
| field_key | str \| None | 关联字段；NULL = 合同级规则（如总比例求和） |
| rule_key | str | required / date_consistency / amount_consistency / ratio_sum / confidence_threshold / type_mismatch |
| severity | str | error / warning / info |
| message | str | 人可读，如「甲方税号缺失（必填）」 |
| status | str | active / ignored（复核可「忽略」） |
| detail | json \| None | 规则上下文（期望值/实际值/相关字段） |
| created_at | ts | |
| ignored_at | ts \| None | |
| ignored_by | str \| None | reviewer_id |

- 规则**在抽取时算一次、落库**（非读时重算）。
- 自然键 `rule_key + field_key`（field_key 为 NULL 时用 rule_key）upsert。
- 字段被复核修正（`reviewed_value` 改变）→ 重算该合同受影响规则 → upsert 更新。

### 5.2 新列 `field_definitions.contract_type`（决策 B）
- `contract_type: str | None = None`（NULL = 通用，所有类型适用）。
- `load_field_definitions(db, contract_type=None)`：有类型时 `WHERE contract_type IS NULL OR contract_type = :type`（通用 + 专属）。
- 默认 23 字段保持 NULL（通用）；本期**只交付能力 + 列**，类型专属字段通过既有字段管理 UI 增量配置（不强制 seed）。
- 分类命不到任何专属字段 → 回退全通用（graceful）。

### 5.3 无变化项（明确）
- ❌ 不动 `ExtractedField` / `ContractClause` / `OCRBlock` schema（字段已齐；bbox 列与存值均不变，保持原始像素）。
- ❌ 不动 worker / 队列 / 租约 / 重试。
- ❌ 不动 OCR/LLM provider 抽象基类（仅新增 provider 的"图像输入"方法）。

---

## 6. 后端 API 扩展

**大复用**：条款 `GET /clauses`、`PATCH /clauses/{cid}/review`、字段 `PATCH /fields/{fid}/review`、`GET /review/records` **已全部存在**，本期不新增条款/字段端点。

| 改动 | 说明 |
|---|---|
| `FieldDetail` 补 `bbox`（原始像素） | 高亮必需。`page_no` 已返回，bbox 列已在模型，仅 schema 暴露 |
| `ContractDetail` 嵌入 `violations` + `clauses` + `page_dimensions` | 单次拉取拿全：违规列表、条款列表、每页宽高（高亮缩放参考，源自 OCRBlock） |
| 新 schema `RuleViolationDetail` | 映射 `rule_violations` 表 |
| 新端点 `PATCH /api/v1/contracts/{id}/violations/{vid}` | 「忽略/取消忽略」→ 改 status + ignored_at/by + 写 ReviewRecord |
| 新端点 `GET /api/v1/contracts/{id}/pages/{n}/image` | 返回该页图像（Tier 2） |
| 复核触发规则重算（服务层 hook） | `PATCH /fields/{fid}/review` 改值后 → `recompute_violations(contract_id, field_key)` upsert |

> 规则违规嵌入 ContractDetail 返回，不单独建 `GET /violations`（YAGNI）。
> 加 kill-switch：`enable_rule_validation` / `enable_clause_split`（默认 True），便于灰度回滚。

---

## 7. 前端

### 7.1 原文高亮（Tier 2：`<img>` 预览 + 归一化 bbox 叠加）
- 预览从 **pdfjs 改为 `<img>`**：`<img src="/api/v1/contracts/{id}/pages/{n}/image">`，叠加 `position:absolute` 覆盖层。
- 高亮框 = `bbox × (显示宽 / page_width)`（page_width 来自 OCRBlock，与 bbox 同源于 OCR 图像）。bbox 与显示图像同空间 → **零误差，无需坐标映射或宽高比闸门**。
- 移除 pdfjs 渲染复杂度（worker、canvas 逐页、缩放适配全免），`ExtractionPage` 反而更轻；可移除 `pdfjs-dist` 依赖。
- 交互：点字段/条款 → 切到 `page_no` 的 `<img>` → 覆盖层画归一化框 → 滚入视区 + 短暂脉冲。多选可叠多个框。
- 补充：提供「下载原始 PDF」链接（既有 `/files/download`），弥补图像预览无矢量文本。
- 兜底：bbox 缺失/page_no 越界 → 仅跳页不画框，不报错。
- 抽离组件 `PageImagePreview`（该特性必需，顺带缓解 `ExtractionPage.tsx` 臃肿；不做整文件重构）。

### 7.2 规则违规展示
- `ContractDetail.violations` 按 severity 分组：字段级 → 字段卡片角标（error 红 / warning 黄 / info 灰）；合同级（field_key=null）→ 顶部汇总面板。
- 角标点击展开 message；「忽略」→ `PATCH /violations/{vid}` → 置灰 + 留痕。
- 顶部 severity 图例 +「仅看有违规字段」过滤。

### 7.3 classify 展示
- 合同头类型徽章 + 置信度；低置信度 →「类型待确认」浅提示。纯展示，零交互成本。

### 7.4 条款最小版
- 记录页/结果页加「条款」区：扁平列表（按 `sort_order`），每条显 title/content/页码 + 复核状态徽章 + 复核操作（复用 `PATCH /clauses/{cid}/review`）。
- 点条款 → 复用 7.1 高亮跳转。**不做 parent_id 层级树**（仅存储）。

### 7.5 顺手工程债（特性必需范围内）
- 抽 `PageImagePreview` 组件；顶层 `ErrorBoundary`（高亮/渲染异常不白屏）；移除/清理 pdfjs 相关死代码。

### 7.6 前端测试（推荐但轻量）
- 引入 vitest 最小配置 + 仅测最易错纯函数：归一化 bbox → 像素 rect 的缩放计算（含 bbox 缺失/越界降级）。广覆盖留 Phase 3。是否纳入可选。

---

## 8. 流水线实现要点

改造 `run_extraction_pipeline_inner`（`pipeline.py:207`）：

```python
ocr = <OCR 完成（Tier 2：已光栅化页面图 + 归一化 bbox）>

async def track_a(db_main):
    ctype, conf = await LLMService.classify_contract_type(ocr.full_text)
    contract.contract_type, contract_type_confidence = ctype, conf
    fields = load_field_definitions(db_main, contract_type=ctype)   # 通用+专属
    extracted = await extract_and_save(db_main, contract, ocr, fields)
    if settings.enable_rule_validation:
        try:
            vs = rule_validation_service.validate_contract(extracted, ctype)
            upsert_violations(db_main, contract.id, vs)             # 自然键幂等
        except Exception:
            logger.warning(...); contract.violation_status = "failed"  # 降级不崩

async def track_b(db_own):
    if settings.enable_clause_split:
        try:
            await clause_service.split_and_save_clauses(db_own, contract.id, ocr)
        except Exception:
            logger.warning(...); contract.clause_status = "failed"   # 降级不崩

await asyncio.gather(track_a(db_main), track_b(db_own))
```

- Track B 独立 `AsyncSession`（并发写不同表，WAL 安全）。
- 非致命降级：rule/clause 失败 → 该项为空，不影响其余；仅 classify/extract 失败才任务失败。
- 复核重算：`PATCH /fields/{fid}/review`（action=modify）成功后调 `recompute_violations(contract_id, field_key)`。

---

## 9. 测试与完成定义（DoD）

### 9.1 后端 pytest（扩 `backend/tests/`）
- 规则接线：已知违规 fixture → pipeline 产出 `rule_violations` + severity 正确 + active 状态。
- classify 接线：pipeline 设 `Contract.contract_type`；`load_field_definitions(type)` 返回专属+通用的并集，命中不到回退全通用。
- 条款接线：pipeline 写 `ContractClause`，校验 level/bbox(归一化)/page_no/sort_order 落库。
- 复核重算：改 `reviewed_value` → 受影响规则 upsert（自然键幂等、未受影响规则不动）。
- 忽略违规：`PATCH /violations/{vid}` → status=ignored + ReviewRecord 写入。
- Tier 2 光栅化：PDF → 页面图落盘；`GET /pages/{n}/image` 返回；bbox 归一化正确。
- 迁移：`rule_violations` 建表 + `field_definitions.contract_type` 加列（Alembic upgrade/downgrade 双向）。
- 评测回归：`@pytest.mark.eval` 断言 OCR 改图像输入后字段级 F1 不退化；接线后整体不退化。

### 9.2 前端（7.6 若纳入）
- vitest 测归一化 bbox → 像素 rect 纯函数（含降级）。

### 9.3 DoD 验收清单
1. 上传合同 → 自动 classify 定类型 → 字段集按类型加载（通用+专属）。
2. 字段卡片可见规则违规角标（error/warning/info）；合同级违规顶部汇总。
3. **点字段 → 跳转对应页 + bbox 框选高亮，与原文构造性对齐**（抽样 ≥5 份肉眼对齐）。
4. 点条款 → 同机制跳转高亮。
5. 「忽略违规」→ 置灰 + 留痕；修正字段值 → 受影响规则自动重算。
6. 条款扁平列表可见 + 可条款级复核。
7. 评测集字段 F1 不退化（含 OCR 图像输入）；后端 pytest 全绿含新增覆盖。
8. Tier 2：页面图端点可用、bbox 零误差对齐、重跑 OCR 时清理旧页面图（合同删除端点尚不存在，清理函数预置待用）。

### 9.4 风险与缓解

| 风险 | 缓解 |
|---|---|
| OCR 改图像输入后版面/抽取质量变化 | 评测集回归把关；保留 PDF 直发 fallback provider 模式 |
| 坐标映射偏差 | **Tier 2 构造性消除**（bbox 与显示面同源 + 归一化） |
| 并发写库 session 冲突 | Track B 独立 AsyncSession + WAL；测试覆盖并发 |
| 复核重算与并发复核竞争 | upsert 自然键幂等；单写者序列化 |
| classify 误分类致字段集错配 | 命不到专属字段回退全通用；低置信「待确认」；kill-switch |
| 页面图存储增长 | DPI 可配；合同删除清理；可选 JPEG |
| 评测样本仍是 mock 致基线弱 | 沿用 Phase 1 评测框架；真实样本到位即用 |

---

## 10. 受影响文件清单（预估）

**后端**
- `app/config.py` — 接线 `ocr_pdf_dpi`、加 `enable_rule_validation`/`enable_clause_split`、页面图存储路径
- `app/extraction/ocr/ppstructurev3.py` — 改为接收/发送页面图像、bbox 归一化
- `app/extraction/ocr/ppocr.py` — 同上（PDF 本地光栅化 + 图像输入）
- `app/extraction/base.py` — 新增 `PageImage`/`extract_from_images` 相关类型（bbox 保持原始像素，不变）
- `app/services/ocr_service.py` — 光栅化编排、页面图持久化、调用 `extract_from_images`（bbox 存值不变）
- `app/services/file_service.py` — 页面图保存/删除
- `app/services/pipeline.py` — 双轨编排（classify 门控 + gather）
- `app/services/extraction_service.py` — `load_field_definitions(type)`、`extract_and_save` 入参
- `app/services/rule_validation_service.py` — 接入 pipeline + 复核重算
- `app/services/clause_service.py` — 接入 pipeline（已有，仅调用）
- `app/services/llm_service.py` — `classify_contract_type` 接入
- `app/models/rule_violation.py` — **新** RuleViolation 模型
- `app/models/field_definition.py` — 加 `contract_type` 列
- `app/schemas/contract.py` — FieldDetail 补 bbox；ContractDetail 嵌入 violations/clauses/page_dimensions；RuleViolationDetail
- `app/api/contract.py` — `GET /pages/{n}/image`；ContractDetail 聚合
- `app/api/review.py` — `PATCH /violations/{vid}`；复核触发规则重算 hook
- `alembic/versions/` — 新迁移（rule_violations 表 + field_definitions.contract_type）
- `backend/tests/` — 规则/classify/条款/光栅化/迁移/评测回归测试

**前端**
- `src/types.ts` — FieldDetail 补 bbox；ContractDetail 补 violations/clauses/pageDimensions；RuleViolation 类型
- `src/lib/api.ts` — ContractDetail 解析、`getPageImageUrl`、`patchViolation`
- `src/lib/pdfCoordinates.ts` — 重写为归一化 bbox → 像素 rect（或新建 overlay util）
- `src/pages/ExtractionPage.tsx` — pdfjs→`<img>` 预览、规则角标、classify 徽章、条款区
- `src/pages/ExtractionRecordsPage.tsx` — 规则汇总、条款列表、classify 展示
- `src/components/PageImagePreview.tsx` — **新**（图像预览 + 高亮覆盖层）
- `src/components/ErrorBoundary.tsx` — **新**
- `package.json` — 移除/降级 pdfjs-dist（可选）；加 vitest（7.6 若纳入）

**无变化**：worker、队列、租约/重试、OCR/LLM provider 抽象基类、既有复核/条款 API 逻辑、ORM 现有列。

---

## 11. 决策记录（Decisions Log）

| # | 决策 | 理由 |
|---|---|---|
| P2-D1 | 主线选「功能闭环补全（接线优先）」 | 已开发模块代码就绪，接线 ROI 远高于换 LLM；规则预警+溯源是法务核心卖点 |
| P2-D2 | 流水线方案 2：classify 门控 + 双轨并发 | classify→extract 是硬依赖；clause-split 从 OCR 分叉与两次 LLM 重叠，免费提速 |
| P2-D3 | 规则校验非阻断 + 三级严重度 + 落库 | 法务产品规则预警+复核留痕是核心；纯实时不落库丢失审计能力 |
| P2-D4 | classify 驱动字段集（加 contract_type 列） | 让分类真正有用而非标签；命不到回退全通用，graceful |
| P2-D5 | 条款做最小版（扁平列表+复核），不做层级树 | parent_id 仅存储，UI 树后置 Phase 3，YAGNI |
| P2-D6 | 原文高亮选 Tier 2（本地光栅化+图像端点+归一化） | 构造性零误差；前端去 pdfjs 反简化；成熟业界范式（Textract/Document AI） |
| P2-D7 | bbox 保持原始像素、不归一化 | Tier 2 零误差来自"显示同一张 OCR 图像"，无需归一化；且避免破坏 clause_service 的像素空间阈值（_VERTICAL_GAP_THRESHOLD=50.0） |
| P2-D8 | ContractDetail 嵌入 violations/clauses/page_dimensions | 单次加载拿全，减少前端往返，YAGNI 不建独立 GET |
| P2-D9 | 加 kill-switch enable_rule_validation/enable_clause_split | 灰度回滚便利 |
| P2-D10 | 前端仅 vitest 最小安全网（可选） | 主线是接线非工程化；仅覆盖最易错的归一化缩放纯函数 |

---

## 12. 后续阶段指引（不在本 spec 范围）

- **Phase 3**：条款层级树 UI（消费 parent_id）；多人协作审查/修改留痕/版本追溯；多租户、用户/角色/RBAC、限流；Docker 部署/CI、可观测性（metrics/审计）；`ExtractionPage` 大重构；前端测试广覆盖。
- **后续（依赖评测基线）**：LLM 升级（Qwen3-235B-A22B / DeepSeek-V3.1）/ few-shot / 微调；合同摘要；风险/合规深度审查。
- **若 Tier 2 评测发现图像输入质量下降**：评估保留 PDF 直发 + MinerU 2.5 替代 PP-StructureV3。
