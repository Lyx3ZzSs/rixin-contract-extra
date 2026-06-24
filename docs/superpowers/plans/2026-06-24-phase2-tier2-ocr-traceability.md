# Phase 2 / Plan 1: Tier 2 零误差溯源基建 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 OCR 在本地光栅化页面图像、把图像（而非 PDF 字节）发给 OCR 服务、持久化页面图并提供图像端点，使 bbox 与前端显示的页面图像处于同一像素空间，实现构造性零误差原文溯源。

**Architecture:** 新增本地光栅化（PyMuPDF）→ 持久化页面 PNG（`uploads/.../pages/<contract_id>/`）→ OCR provider 新增 `extract_from_images`（逐页图像输入）→ `OCRService.process` 改为光栅化+持久化+图像OCR → 新增 `GET /contracts/{id}/pages/{n}/image` 端点。bbox 保持原始像素（不归一化），与 `clause_service` 像素空间阈值兼容。

**Tech Stack:** Python 3.12 / FastAPI(async) / SQLAlchemy 2.0 async / aiosqlite / PyMuPDF(fitz) / pytest(asyncio_mode=auto)。

**对应 spec：** `docs/superpowers/specs/2026-06-24-phase2-feature-completion-design.md` §4（Tier 2 基建）。

## Global Constraints

- Python ≥3.12；依赖见 `backend/pyproject.toml`；PyMuPDF(`fitz`) 已是间接依赖（`ppocr.py` 已 `import fitz`），无需新增。
- bbox 存储保持**原始像素**值，**不做归一化**（spec §4.4 / D7）。
- `OCRBlock` / `ExtractedField` / `ContractClause` 的 bbox 列与 page_width/page_height 列**不变**（零 schema 迁移）。
- 现有 OCR provider 抽象 `extract_detailed(file_path, file_type)` **保留**作 fallback（spec §4.2 保留 PDF 直发路径）。
- 测试沿用 `backend/tests/`，`pytest -m eval` 为评测标记（默认跳过）。
- 页面图存储于本地 `settings.upload_dir`（默认 `uploads/contracts`）下 `pages/<contract_id>/page_NNNN.png`，沿用 `file_service` 本地存储模式，**不引入对象存储**。
- 提交规范：conventional commits，每个任务一次提交。

## File Structure

| 文件 | 职责 | 任务 |
|---|---|---|
| `backend/app/config.py` | 新增 `ocr_rasterize_locally: bool = True`（`ocr_pdf_dpi` 已存在:25） | T1 |
| `backend/app/extraction/base.py` | 新增 `PageImage` 数据类型 | T1 |
| `backend/app/extraction/ocr/rasterize.py` | **新建**：`rasterize_pdf_to_pages()` 本地光栅化 | T1 |
| `backend/app/services/file_service.py` | 新增页面图保存/读取/删除函数 | T2 |
| `backend/app/extraction/ocr/base.py` | `OCRProvider` 新增 `extract_from_images()` 默认实现（NotImplementedError） | T3 |
| `backend/app/extraction/ocr/ppstructurev3.py` | 实现 `extract_from_images()`（逐页图像 HTTP + 聚合） | T3 |
| `backend/app/extraction/ocr/mock.py` | 实现 `extract_from_images()`（返回 canned 结果） | T3 |
| `backend/app/services/ocr_service.py` | `process()` 改为光栅化→持久化→`extract_from_images`；新增 `_prepare_page_images()` | T4 |
| `backend/app/api/contract.py` | 新增 `GET /{contract_id}/pages/{page_no}/image` | T5 |
| `backend/tests/test_rasterize.py` | **新建**：光栅化测试 | T1 |
| `backend/tests/test_file_service_pages.py` | **新建**：页面图持久化测试 | T2 |
| `backend/tests/test_ppstructurev3_provider.py` | 扩展：`extract_from_images` 测试 | T3 |
| `backend/tests/test_ocr_service.py` | 更新：适配 `process()` 新流程 | T4 |
| `backend/tests/test_contract_api.py` | 扩展：页面图端点测试 | T5 |

---

## Task 1: PDF 本地光栅化工具

**Files:**
- Modify: `backend/app/config.py`（在 `ppocr_page_concurrency: int = 3`（:26）后加一行）
- Modify: `backend/app/extraction/base.py`（在 `ClauseSegment` 之前，`OCRDetailedResult` 之后加类型）
- Create: `backend/app/extraction/ocr/rasterize.py`
- Test: `backend/tests/test_rasterize.py`

**Interfaces:**
- Produces: `PageImage`（base.py，字段 `page_no: int`, `png_bytes: bytes`, `width: int | None`, `height: int | None`）；`rasterize_pdf_to_pages(file_path: str, dpi: int = 200) -> list[PageImage]`；config `ocr_rasterize_locally: bool`。
- Consumes: 无（基础工具）。

- [ ] **Step 1: 写失败测试** `backend/tests/test_rasterize.py`

```python
"""Tests for local PDF rasterization (Tier 2 zero-error traceability)."""
import fitz

from app.extraction.base import PageImage
from app.extraction.ocr.rasterize import rasterize_pdf_to_pages


def _make_pdf(tmp_path, pages: int = 2) -> str:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    p = tmp_path / "test.pdf"
    doc.save(str(p))
    doc.close()
    return str(p)


def test_rasterize_produces_one_image_per_page(tmp_path):
    pdf = _make_pdf(tmp_path, pages=3)
    images = rasterize_pdf_to_pages(pdf, dpi=144)

    assert [img.page_no for img in images] == [1, 2, 3]
    assert all(img.width and img.height for img in images)
    # PNG magic header
    assert all(img.png_bytes[:8] == b"\x89PNG\r\n\x1a\n" for img in images)


def test_rasterize_dpi_scales_dimensions(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    low = rasterize_pdf_to_pages(pdf, dpi=72)[0]
    high = rasterize_pdf_to_pages(pdf, dpi=144)[0]

    # 144 dpi == 2x the pixels of 72 dpi
    assert high.width == low.width * 2
    assert high.height == low.height * 2


def test_rasterize_returns_page_image_type(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    img = rasterize_pdf_to_pages(pdf)[0]

    assert isinstance(img, PageImage)
    assert img.page_no == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_rasterize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.extraction.ocr.rasterize'`

- [ ] **Step 3: 写最小实现**

`backend/app/config.py` — 在 `ppocr_page_concurrency: int = 3` 行之后插入：

```python
    # Tier 2 traceability: rasterize PDFs locally so bbox lives in the same
    # pixel space as the page image we serve for highlight overlay. When False,
    # fall back to the legacy PDF-bytes path (provider.extract_detailed).
    ocr_rasterize_locally: bool = True
```

`backend/app/extraction/base.py` — 在 `OCRDetailedResult` 类之后、`FieldSpec` 类之前插入：

```python
class PageImage(BaseModel):
    """A rasterized page image (Tier 2). Lives in a known pixel space so the
    bbox returned by OCR maps exactly to the page image we persist + serve."""
    page_no: int
    png_bytes: bytes
    width: int | None = None
    height: int | None = None
```

`backend/app/extraction/ocr/rasterize.py`（新建）：

```python
"""Local PDF rasterization — produces page images in a known pixel space.

Tier 2 zero-error traceability: we rasterize locally so the bbox returned by
OCR lives in the SAME pixel space as the page image we persist and the frontend
displays. The overlay maps exactly via ``displayWidth / page_width``.
"""
from __future__ import annotations

import fitz  # PyMuPDF

from app.extraction.base import PageImage


def rasterize_pdf_to_pages(file_path: str, dpi: int = 200) -> list[PageImage]:
    """Rasterize every page of *file_path* (a PDF) to a PNG at *dpi*.

    Returns one ``PageImage`` per page, 1-indexed, with pixel width/height.
    """
    doc = fitz.open(file_path)
    zoom = dpi / 72.0  # PDF points (72/inch) -> device pixels at the requested DPI
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[PageImage] = []
    try:
        for idx, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(PageImage(
                page_no=idx,
                png_bytes=pix.tobytes("png"),
                width=pix.width,
                height=pix.height,
            ))
    finally:
        doc.close()
    return pages
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_rasterize.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/config.py backend/app/extraction/base.py backend/app/extraction/ocr/rasterize.py backend/tests/test_rasterize.py
git commit -m "feat(ocr): add local PDF rasterization for Tier 2 traceability"
```

---

## Task 2: 页面图持久化（file_service）

**Files:**
- Modify: `backend/app/services/file_service.py`（在 `read_file` 之后追加）
- Test: `backend/tests/test_file_service_pages.py`

**Interfaces:**
- Produces: `page_images_dir(contract_id) -> Path`、`save_page_image(contract_id, page_no, png_bytes) -> str`、`page_image_path(contract_id, page_no) -> Path`、`delete_contract_pages(contract_id) -> None`。
- Consumes: `settings.upload_dir`，`_upload_dir()`。

- [ ] **Step 1: 写失败测试** `backend/tests/test_file_service_pages.py`

```python
"""Tests for page-image persistence (Tier 2)."""
import uuid

from app.services import file_service


def test_save_and_locate_page_image(tmp_upload_dir):
    contract_id = uuid.uuid4()
    path = file_service.save_page_image(contract_id, 1, b"\x89PNG\r\n\x1a\nfake")

    assert file_service.page_image_path(contract_id, 1).exists()
    assert file_service.page_image_path(contract_id, 1).read_bytes() == b"\x89PNG\r\n\x1a\nfake"
    assert path.endswith("page_0001.png")


def test_save_multiple_pages_zero_padded(tmp_upload_dir):
    contract_id = uuid.uuid4()
    file_service.save_page_image(contract_id, 1, b"a")
    file_service.save_page_image(contract_id, 12, b"b")

    assert file_service.page_image_path(contract_id, 1).exists()
    assert file_service.page_image_path(contract_id, 12).name == "page_0012.png"


def test_delete_contract_pages_removes_dir(tmp_upload_dir):
    contract_id = uuid.uuid4()
    file_service.save_page_image(contract_id, 1, b"a")
    file_service.save_page_image(contract_id, 2, b"b")

    file_service.delete_contract_pages(contract_id)

    assert not file_service.page_image_path(contract_id, 1).exists()
    # deleting a non-existent contract's pages is a no-op (no error)
    file_service.delete_contract_pages(uuid.uuid4())


def test_delete_is_isolated_per_contract(tmp_upload_dir):
    a = uuid.uuid4()
    b = uuid.uuid4()
    file_service.save_page_image(a, 1, b"a")
    file_service.save_page_image(b, 1, b"b")

    file_service.delete_contract_pages(a)

    assert not file_service.page_image_path(a, 1).exists()
    assert file_service.page_image_path(b, 1).exists()
```

> **Note:** `tmp_upload_dir` is an existing fixture (used in `test_ocr_service.py`); it points `settings.upload_dir` at a temp dir.

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_file_service_pages.py -v`
Expected: FAIL — `AttributeError: module 'app.services.file_service' has no attribute 'save_page_image'`

- [ ] **Step 3: 写最小实现**

`backend/app/services/file_service.py` — 在文件末尾（`read_file` 之后）追加：

```python
import shutil
import uuid as _uuid


def page_images_dir(contract_id: _uuid.UUID) -> Path:
    """Return (creating) the per-contract page-image directory."""
    d = _upload_dir() / "pages" / str(contract_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_page_image(contract_id: _uuid.UUID, page_no: int, png_bytes: bytes) -> str:
    """Persist one rasterized page image. Overwrites if it already exists.

    Returns the absolute path written.
    """
    dest = page_images_dir(contract_id) / f"page_{page_no:04d}.png"
    dest.write_bytes(png_bytes)
    return str(dest)


def page_image_path(contract_id: _uuid.UUID, page_no: int) -> Path:
    """Return the expected path for a page image (may not exist yet)."""
    return _upload_dir() / "pages" / str(contract_id) / f"page_{page_no:04d}.png"


def delete_contract_pages(contract_id: _uuid.UUID) -> None:
    """Remove all persisted page images for a contract (no-op if none)."""
    d = _upload_dir() / "pages" / str(contract_id)
    if d.exists():
        shutil.rmtree(d)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_file_service_pages.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/file_service.py backend/tests/test_file_service_pages.py
git commit -m "feat(files): persist/delete per-contract page images"
```

---

## Task 3: OCR provider `extract_from_images`

**Files:**
- Modify: `backend/app/extraction/ocr/base.py`（新增默认方法）
- Modify: `backend/app/extraction/ocr/ppstructurev3.py`（实现图像输入）
- Modify: `backend/app/extraction/ocr/mock.py`（实现图像输入）
- Test: `backend/tests/test_ppstructurev3_provider.py`（扩展）

**Interfaces:**
- Produces: `OCRProvider.extract_from_images(page_images: list[bytes]) -> OCRDetailedResult`（base 默认 `raise NotImplementedError`；ppstructurev3/mock 实现覆盖）。
- Consumes: 无。

- [ ] **Step 1: 写失败测试** — 在 `backend/tests/test_ppstructurev3_provider.py` 末尾追加：

```python
def test_extract_from_images_aggregates_pages(monkeypatch):
    """extract_from_images sends each image (fileType=1) and aggregates pages."""
    provider = PPStructureV3Provider()

    # Captured single-page PaddleX response (one text block at a known bbox).
    one_page = {
        "result": {"layoutParsingResults": [{"prunedResult": {
            "width": 800, "height": 600,
            "parsing_res_list": [
                {"block_label": "text", "block_content": "hello", "block_bbox": [10, 20, 110, 50]},
            ],
        }}]},
    }

    call_args: list[dict] = []

    def fake_post(_url, payload):
        call_args.append(payload)
        return one_page

    monkeypatch.setattr(provider, "_http_post", fake_post)

    result = provider.extract_from_images([b"img1", b"img2"])

    assert len(result.pages) == 2
    assert [p.page_no for p in result.pages] == [1, 2]
    # every call sent fileType=1 (image)
    assert all(p["fileType"] == 1 for p in call_args)
    # page dims + bbox came back in image space
    assert result.pages[0].width == 800 and result.pages[0].height == 600
    assert result.pages[0].blocks[0].bbox.x1 == 10.0
    assert result.pages[0].blocks[0].text == "hello"


def test_extract_from_images_empty_input():
    provider = PPStructureV3Provider()
    result = provider.extract_from_images([])
    assert result.pages == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_ppstructurev3_provider.py::test_extract_from_images_aggregates_pages -v`
Expected: FAIL — `AttributeError: 'PPStructureV3Provider' object has no attribute 'extract_from_images'`

- [ ] **Step 3: 写最小实现**

`backend/app/extraction/ocr/base.py` — 在 `extract_detailed` 之后追加默认方法：

```python
    def extract_from_images(self, page_images: list[bytes]) -> OCRDetailedResult:
        """OCR pre-rasterized page images (Tier 2). bbox returns in the same
        pixel space as the supplied images. Providers that don't support image
        input leave this raising NotImplementedError (callers fall back to
        ``extract_detailed``)."""
        raise NotImplementedError
```

`backend/app/extraction/ocr/ppstructurev3.py` — 在 `extract_detailed` 方法之后追加（复用现有 `_http_post` / `_extract_payload` / `_blocks_for`）：

```python
    def extract_from_images(self, page_images: list[bytes]) -> OCRDetailedResult:
        """OCR each pre-rasterized page image (fileType=1) and aggregate.

        Because we sent OUR images, the returned bbox lives in each image's
        pixel space — identical to the page image we persist for display.
        """
        pages: list[OCRPageResult] = []
        for idx, img_bytes in enumerate(page_images, start=1):
            encoded = base64.b64encode(img_bytes).decode("ascii")
            payload = {"file": encoded, "fileType": 1, "useLayout": True, "useTable": True}
            data = self._http_post(self._url, payload)
            pages_raw = self._extract_payload(data) or []
            page_raw = pages_raw[0] if pages_raw else {}
            blocks = self._blocks_for(page_raw)
            pruned = page_raw.get("prunedResult") or {}
            pages.append(OCRPageResult(
                page_no=idx,
                blocks=blocks,
                width=pruned.get("width"),
                height=pruned.get("height"),
            ))
        return OCRDetailedResult(pages=pages, provider="ppstructurev3")
```

`backend/app/extraction/ocr/mock.py` — 在 `MockOCRProvider.extract_detailed` 之后追加：

```python
    def extract_from_images(self, page_images: list[bytes]) -> OCRDetailedResult:
        # Tier 2 image-input path — return the same canned result so OCRService
        # tests exercise the new flow without a live provider. One page per
        # image keeps page numbering faithful when callers pass N images.
        if not page_images:
            return OCRDetailedResult(pages=[], provider="mock")
        return MOCK_DETAILED_RESULT.model_copy(deep=True)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_ppstructurev3_provider.py -v`
Expected: PASS (all, including 2 new)

- [ ] **Step 5: 提交**

```bash
git add backend/app/extraction/ocr/base.py backend/app/extraction/ocr/ppstructurev3.py backend/app/extraction/ocr/mock.py backend/tests/test_ppstructurev3_provider.py
git commit -m "feat(ocr): add provider.extract_from_images for image input"
```

---

## Task 4: OCRService.process 改为光栅化 + 图像输入

**Files:**
- Modify: `backend/app/services/ocr_service.py`（`process` 重写 + 新增 `_prepare_page_images`）
- Test: `backend/tests/test_ocr_service.py`（更新 2 处）

**Interfaces:**
- Produces: `OCRService.process` 现在会持久化页面图（副作用）并调用 `extract_from_images`；`_prepare_page_images(file_path, file_type) -> list[PageImage]`。
- Consumes: T1 `rasterize_pdf_to_pages`、`PageImage`、`settings.ocr_rasterize_locally` / `ocr_pdf_dpi`；T2 `save_page_image` / `delete_contract_pages`；T3 `provider.extract_from_images`。

- [ ] **Step 1: 写失败测试** — 在 `backend/tests/test_ocr_service.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_ocr_service_persists_page_images(sample_pdf_content, tmp_upload_dir):
    """process() must rasterize the PDF and persist one page image per page."""
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file, page_image_path
    from app.services.contract_service import create_contract
    from app.models.contract import ContractFile

    file_path, file_type, _size, content_hash = save_file(sample_pdf_content, "pages.pdf")
    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        db.add(ContractFile(contract_id=contract.id, file_name="pages.pdf", file_path=file_path,
                            file_type=file_type, file_size=len(sample_pdf_content),
                            content_type="application/pdf"))
        await db.flush()
        await OCRService.process(db, contract.id, file_path, file_type)
        await db.commit()
        contract_id = contract.id

    # at least page 1 must be persisted as an image
    assert page_image_path(contract_id, 1).exists()


@pytest.mark.asyncio
async def test_ocr_service_rejects_empty_results(monkeypatch):
    """process() must fail when the provider returns no text (new image path)."""
    from app.services import ocr_service
    from app.extraction.base import OCRDetailedResult, OCRPageResult, PageImage

    class EmptyProvider:
        def extract_from_images(self, _imgs):
            return OCRDetailedResult(pages=[OCRPageResult(page_no=1, blocks=[])], provider="empty")

    monkeypatch.setattr(ocr_service, "get_ocr_provider", lambda: EmptyProvider())
    # skip real rasterization — feed a dummy page image
    monkeypatch.setattr(
        ocr_service.OCRService, "_prepare_page_images",
        classmethod(lambda cls, fp, ft: [PageImage(page_no=1, png_bytes=b"x", width=1, height=1)]),
    )

    with pytest.raises(ValueError, match="OCR result is empty"):
        await ocr_service.OCRService.process(
            None, "00000000-0000-0000-0000-000000000000", "/tmp/empty.pdf", "pdf",
        )
```

> **Note:** the existing `test_ocr_service_rejects_empty_results` (old version using `extract_detailed` on a bare provider) is REPLACED by the above. Delete the old test body and replace with this one.

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_ocr_service.py -v`
Expected: FAIL — `test_ocr_service_persists_page_images` (no page image written); old empty-results test may error.

- [ ] **Step 3: 写最小实现**

`backend/app/services/ocr_service.py` — 调整 imports（顶部）：

```python
from pathlib import Path

from app.config import settings
from app.extraction.base import BBox, OCRDetailedResult, OCRPageResult, OCRTextBlock, PageImage
from app.extraction.ocr import get_ocr_provider
from app.extraction.ocr.rasterize import rasterize_pdf_to_pages
from app.models.ocr import OCRBlock
from app.services import file_service
```

替换 `process` 方法（:58-83）为：

```python
    @classmethod
    async def process(
        cls,
        db: AsyncSession,
        contract_id: uuid.UUID,
        file_path: str,
        file_type: str,
    ) -> OCRDetailedResult:
        """Run OCR and persist block-level results + page images.

        Tier 2: rasterize locally so bbox lives in the same pixel space as the
        page images we serve for highlight overlay.
        """
        provider = get_ocr_provider()

        # 1. Prepare + persist page images (rasterize PDF, or use image as-is).
        page_images = cls._prepare_page_images(file_path, file_type)
        if page_images:
            file_service.delete_contract_pages(contract_id)
            for img in page_images:
                file_service.save_page_image(contract_id, img.page_no, img.png_bytes)

        # 2. OCR — prefer image input (bbox in our pixel space); fall back to
        #    legacy PDF-bytes path when disabled or unsupported.
        if settings.ocr_rasterize_locally and page_images:
            try:
                result = await asyncio.to_thread(
                    provider.extract_from_images, [img.png_bytes for img in page_images],
                )
            except NotImplementedError:
                result = await asyncio.to_thread(provider.extract_detailed, file_path, file_type)
        else:
            result = await asyncio.to_thread(provider.extract_detailed, file_path, file_type)

        if not result.full_text.strip():
            raise ValueError("OCR result is empty")

        # 3. Persist blocks.
        records = await cls.save_blocks(db, contract_id, result)
        if not records:
            raise ValueError("OCR result contains no text blocks")

        cls._log_ocr_diagnostics(contract_id, result, len(records))
        return result

    @classmethod
    def _prepare_page_images(cls, file_path: str, file_type: str) -> list[PageImage]:
        """Return page images for OCR + display.

        PDFs are rasterized locally (known pixel space); image files are used
        directly as a single page. Returns [] only when rasterization is off
        AND the file is a PDF (legacy path will be used instead).
        """
        if not settings.ocr_rasterize_locally:
            return []
        if file_type.lower() == "pdf":
            return rasterize_pdf_to_pages(file_path, dpi=settings.ocr_pdf_dpi)
        # Image upload: the file itself is the (single) page image.
        return [PageImage(page_no=1, png_bytes=Path(file_path).read_bytes())]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_ocr_service.py -v`
Expected: PASS (all, including the 2 new/rewritten). Existing `test_ocr_service_persists_blocks` and `test_ocr_service_logs_subject_keyword_diagnostics` must still pass (mock provider returns canned blocks via `extract_from_images`).

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/ocr_service.py backend/tests/test_ocr_service.py
git commit -m "feat(ocr): OCRService rasterizes + persists page images, uses image input"
```

---

## Task 5: 页面图端点 GET /pages/{n}/image

**Files:**
- Modify: `backend/app/api/contract.py`（在 `download_contract_file` 之后追加）
- Test: `backend/tests/test_contract_api.py`（扩展）

**Interfaces:**
- Produces: `GET /api/v1/contracts/{contract_id}/pages/{page_no}/image` → PNG `FileResponse` + `Cache-Control`。
- Consumes: T2 `file_service.page_image_path`。

- [ ] **Step 1: 写失败测试** — 在 `backend/tests/test_contract_api.py` 末尾追加（复用该文件已有的 `client` fixture 与 `_prepare` 辅助）：

```python
@pytest.mark.asyncio
async def test_get_page_image_returns_png(client, sample_pdf_content, tmp_upload_dir):
    """GET /contracts/{id}/pages/{n}/image returns the persisted page PNG."""
    from app.services import file_service

    resp = await _prepare(client, "pages.pdf", sample_pdf_content)
    assert resp.status_code == 201
    cid = resp.json()["data"]["contract_id"]

    file_service.save_page_image(uuid.UUID(cid), 1, b"\x89PNG\r\n\x1a\nfake-bytes")

    resp = await client.get(f"/api/v1/contracts/{cid}/pages/1/image")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"].startswith("private")


@pytest.mark.asyncio
async def test_get_page_image_not_found(client, sample_pdf_content, tmp_upload_dir):
    """Contract exists but page image absent → 404; unknown contract → 404."""
    resp = await _prepare(client, "pages.pdf", sample_pdf_content)
    cid = resp.json()["data"]["contract_id"]

    # contract exists, no page image persisted
    missing = await client.get(f"/api/v1/contracts/{cid}/pages/1/image")
    assert missing.status_code == 404

    # unknown contract
    unknown = await client.get(f"/api/v1/contracts/{uuid.uuid4()}/pages/1/image")
    assert unknown.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_contract_api.py -k page_image -v`
Expected: FAIL — `404` / route not found (no such path).

- [ ] **Step 3: 写最小实现**

`backend/app/api/contract.py` — 在 `download_contract_file`（:295）之后追加：

```python
@router.get("/{contract_id}/pages/{page_no}/image")
async def get_page_image(
    contract_id: uuid.UUID,
    page_no: int,
    db: AsyncSession = Depends(get_db),
):
    """Serve the rasterized page image (Tier 2 highlight surface)."""
    exists = await db.execute(select(Contract.id).where(Contract.id == contract_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(404, "Contract not found")

    path = file_service.page_image_path(contract_id, page_no)
    if not path.exists():
        raise HTTPException(404, "Page image not found")

    return _FileResponse(
        path=str(path),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )
```

(`file_service` is already imported at module top via `from app.services import contract_service, file_service, task_service`.)

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_contract_api.py -k page_image -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/contract.py backend/tests/test_contract_api.py
git commit -m "feat(api): add GET /contracts/{id}/pages/{n}/image endpoint"
```

---

## Task 6: 全量回归与评测冒烟

**Files:** 无（验证任务）

- [ ] **Step 1: 跑全量后端测试**

Run: `cd backend && python -m pytest -q`
Expected: all green. 特别关注：
- `tests/test_rasterize.py`、`tests/test_file_service_pages.py`（T1/T2 新增）
- `tests/test_ppstructurev3_provider.py`、`tests/test_ocr_service.py`（T3/T4 改动）
- `tests/test_contract_api.py`（T5）
- `tests/test_clause_service.py` — **必须仍绿**（验证 bbox 保持像素未破坏条款拆分阈值）
- `tests/test_ocr_markdown.py`、`tests/test_services.py`

- [ ] **Step 2: 跑评测冒烟（默认跳过，手动触发）**

Run: `cd backend && python -m pytest -m eval -q`
Expected: 通过（沿用 Phase 1 评测框架；当前样本为 mock，验证 harness 未被破坏）。记录输出字段级 P/R/F1 作为图像输入路径的基线对照点；**真实样本到位后**用本路径 vs 旧 PDF 直发路径对比，确认图像输入不退化（spec §9.4 风险把关）。

- [ ] **Step 3: 手动 smoke（可选，需真实 provider）**

配置 `.env`：`OCR_PROVIDER=ppstructurev3`、`OCR_RASTERIZE_LOCALLY=true`、`OCR_PDF_DPI=200`，上传一份真实 PDF → 确认 `uploads/contracts/pages/<id>/page_0001.png` 生成、`GET /pages/1/image` 可取、`OCRBlock` 有 bbox+page_width/height。若图像输入版面质量明显下降 → 回退 `OCR_RASTERIZE_LOCALLY=false` 走 legacy 路径，记录待评估。

- [ ] **Step 4: 提交（若有 smoke 笔记）**

```bash
# 若记录了评测基线/手动 smoke 结果到文档：
git add docs/superpowers/notes/  # 视情况
git commit -m "test(ocr): verify Tier 2 image-input regression baseline"
```

---

## Spec coverage（Plan 1 对照 spec §4）

| Spec 条目 | 任务 |
|---|---|
| §4.1 本地光栅化（接线 `ocr_pdf_dpi`） | T1 |
| §4.2 provider 输入改图像 + 保留 PDF 直发 fallback | T3 + T4（`NotImplementedError`/`ocr_rasterize_locally` 回退） |
| §4.3 页面图持久化 + 重跑清理 | T2 + T4（`delete_contract_pages` 在 process 开头） |
| §4.4 bbox 保持原始像素 | T4（`save_blocks` 不变，验证 `test_clause_service` 不退化） |
| §4.5 存储/成本（DPI 可配） | T1（`dpi` 参数）+ T6 Step3（手动 smoke） |
| §6 `GET /pages/{n}/image` 端点 | T5 |
| §9 评测回归把关 | T6 Step2 |

**Plan 1 不含**（属 Plan 2/3）：规则校验/分类/条款接线、ContractDetail 嵌入、FieldDetail bbox 暴露、前端 `<img>` 预览——这些在后续 plan。
