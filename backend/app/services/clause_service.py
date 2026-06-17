"""Clause splitting service — rule-based contract clause segmentation.

Supports two input modes:
1. **Block-based**: from OCR blocks (preferred) — preserves page numbers and bboxes.
2. **Text-based**: from plain text (fallback) — uses regex to find article markers.

Layout-aware enhancements (v2):
- ``block_type == "title"`` from OCR providers is treated as a forced clause boundary.
- Title accumulation merges OCR-split titles (e.g. "第一条" + "合同目的").
- Spatial gap analysis uses bbox positions to detect section boundaries.
- Clause hierarchy: article (level=0) > section (level=1) > sub-clause (level=2).

Recognised clause patterns:
  - 第一条 ~ 第九十九条
  - 第1条 ~ 第99条
  - 一、二、三、… (Chinese circled numbers with 、)
  - （一）（二）… （1）（2）…
  - 1.  1.1  1.1.1  (dotted numbering)
  - Article 1 / ARTICLE 1  (English)
"""

from __future__ import annotations

import re
import uuid
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.base import (
    BBox,
    ClauseSegment,
    OCRDetailedResult,
    OCRTextBlock,
)
from app.models.contract import ContractClause


# ---------------------------------------------------------------------------
# Regex patterns for clause markers
# ---------------------------------------------------------------------------

# Ordered from most specific to least specific to avoid false matches.
_CLAUSE_PATTERNS: list[tuple[str, int, re.Pattern[str]]] = [
    # "第一条 …" / "第1条 …"  — Chinese article marker → level 0
    (
        "article_cn", 0,
        re.compile(
            r"^第\s*([一二三四五六七八九十百零\d]+)\s*条\s*(.*)",
        ),
    ),
    # "一、" / "二、" — Chinese numeral with ideographic comma → level 1
    (
        "section_cn", 1,
        re.compile(r"^([一二三四五六七八九十]+)\s*[、，]\s*(.*)"),
    ),
    # "（一）" / "（二）" — parenthesised Chinese numeral → level 1
    (
        "paren_cn", 1,
        re.compile(r"^（([一二三四五六七八九十]+)）\s*(.*)"),
    ),
    # "（1）" / "（2）" — parenthesised digit → level 2
    (
        "paren_num", 2,
        re.compile(r"^（(\d+)）\s*(.*)"),
    ),
    # "1.1.1" / "1.1" / "1." — dotted numbering → level 2 (or deeper)
    (
        "dotted_multi", 2,
        re.compile(r"^(\d+(?:\.\d+)+)\s*(.*)"),
    ),
    # "1 " / "2 " — plain digit prefix → level 2 (check after dotted)
    (
        "num_dot", 2,
        re.compile(r"^(\d+)[.、，]\s*(.*)"),
    ),
    # "Article 1" / "ARTICLE 1" — English-style → level 0
    (
        "article_en", 0,
        re.compile(r"^[Aa][Rr][Tt][Ii][Cc][Ll][Ee]\s+(\d+)\s*(.*)"),
    ),
]

# Common un-numbered section titles that serve as clause boundaries
# (e.g. "定义", "附则", "其他约定")
_SECTION_TITLE_PATTERNS: list[str] = [
    "定义", "附则", "其他约定", "其他", "一般规定",
    "特别约定", "补充条款", "免责条款", "管辖与争议解决",
]


def _is_clause_marker(line: str) -> tuple[str | None, str | None, str | None, int]:
    """Check if a line starts with a clause marker.

    Returns (pattern_name, marker_text, trailing_title, level) or
    (None, None, None, 0).

    ``level``: 0=article, 1=section, 2=sub-clause.
    """
    stripped = line.strip()
    if not stripped or len(stripped) < 2:
        return None, None, None, 0

    for name, level, pat in _CLAUSE_PATTERNS:
        m = pat.match(stripped)
        if m:
            marker = m.group(0)
            trailing = m.group(m.lastindex).strip() if m.lastindex else ""
            if name == "article_cn":
                marker_text = f"第{m.group(1)}条"
            elif name == "article_en":
                marker_text = f"Article {m.group(1)}"
            else:
                end = m.start(2) if m.lastindex and m.lastindex >= 2 else m.end()
                marker_text = stripped[:end].strip()
            title = trailing if trailing else None
            return name, marker_text, title, level

    # Check un-numbered section titles
    for section_title in _SECTION_TITLE_PATTERNS:
        if stripped.startswith(section_title):
            return "section_title", section_title, None, 0

    return None, None, None, 0


def _classify_clause_type(title: str) -> str | None:
    """Best-effort keyword-based clause classification.

    Returns *None* (no classification) when the title contains no known keyword,
    so the LLM can classify later.
    """
    type_map = {
        "付款": "payment",
        "支付": "payment",
        "违约": "breach",
        "保密": "confidentiality",
        "不可抗力": "force_majeure",
        "争议": "dispute",
        "期限": "term",
        "有效": "term",
        "知识产权": "intellectual_property",
        "担保": "guarantee",
        "保证": "guarantee",
        "终止": "termination",
        "解除": "termination",
        "项目": "project",
        "工期": "schedule",
        "交付": "delivery",
        "质量": "quality",
        "验收": "acceptance",
        "适用": "governing_law",
        "通知": "notice",
        "转让": "assignment",
        "签署": "execution",
        "其他": "miscellaneous",
        "附则": "miscellaneous",
        "附件": "appendix",
        "定义": "definition",
        "权利": "rights_obligations",
        "义务": "rights_obligations",
        "管辖": "dispute",
    }
    for keyword, ctype in type_map.items():
        if keyword in title:
            return ctype
    return None


# ---------------------------------------------------------------------------
# Block-based splitting (preferred) — layout-aware v2
# ---------------------------------------------------------------------------

# Spatial gap threshold (in points) above which a page break is inferred.
_VERTICAL_GAP_THRESHOLD = 50.0


def split_clauses_from_blocks(
    ocr_result: OCRDetailedResult,
) -> list[ClauseSegment]:
    """Split contract text into clauses using OCR block-level results.

    Layout-aware enhancements:
    - ``block_type == "title"`` forces a new clause boundary.
    - Title accumulation merges consecutive title-typed blocks.
    - Vertical gaps > 50pt between blocks are treated as section boundaries.
    - Clause level (0=article, 1=section, 2=sub-clause) is tracked.
    """
    clauses: list[ClauseSegment] = []
    current_title: str | None = None
    current_type: str | None = None
    current_level: int = 0
    current_blocks: list[tuple[int, OCRTextBlock]] = []  # (page_no, block)
    pending_title_blocks: list[tuple[int, OCRTextBlock]] = []  # accumulated title parts

    def _flush():
        nonlocal current_title, current_type, current_level, current_blocks, pending_title_blocks

        # Merge any pending title blocks into current_blocks
        if pending_title_blocks:
            current_blocks = pending_title_blocks + current_blocks
            pending_title_blocks = []

        if not current_blocks:
            return

        content_parts: list[str] = []
        for _, blk in current_blocks:
            content_parts.append(blk.text.strip())
        content = "\n".join(content_parts).strip()
        if not content:
            current_blocks = []
            return

        page_start = current_blocks[0][0]
        page_end = current_blocks[-1][0]
        first_bbox = current_blocks[0][1].bbox
        avg_conf = sum(b.confidence for _, b in current_blocks) / len(current_blocks)

        clauses.append(ClauseSegment(
            clause_type=current_type,
            clause_title=current_title,
            content=content,
            page_no=page_start,
            page_end=page_end if page_end != page_start else None,
            bbox=first_bbox,
            confidence=round(avg_conf, 4),
            level=current_level,
        ))
        current_blocks = []

    def _has_vertical_gap(prev_block: OCRTextBlock, curr_block: OCRTextBlock) -> bool:
        """Check if there's a significant gap between two consecutive blocks."""
        if prev_block.bbox is None or curr_block.bbox is None:
            return False
        gap = curr_block.bbox.y1 - prev_block.bbox.y2
        return gap > _VERTICAL_GAP_THRESHOLD

    for page in ocr_result.pages:
        prev_block: OCRTextBlock | None = None
        for block in page.blocks:
            text = block.text.strip()
            if not text:
                continue

            # --- Layout-aware boundary: block_type == "title" ---
            is_title_block = block.block_type == "title"
            # --- Regex-based boundary ---
            pattern_name, marker_text, trailing_title, level = _is_clause_marker(text)

            # --- Spatial gap boundary ---
            large_gap = (
                prev_block is not None
                and _has_vertical_gap(prev_block, block)
                and len(current_blocks) > 0
            )

            should_split = (
                pattern_name is not None       # regex marker found
                or is_title_block               # layout says it's a title
                or large_gap                    # large vertical gap
            )

            if should_split and current_blocks:
                _flush()

            if is_title_block or pattern_name is not None:
                if is_title_block and pattern_name is None:
                    # Layout-detected title with no regex match — use block text as title
                    pending_title_blocks.append((page.page_no, block))
                    current_title = text
                    current_type = _classify_clause_type(text)
                    current_level = 0
                    continue

                if pattern_name is not None:
                    # Consume pending title blocks into this clause
                    if pending_title_blocks:
                        current_blocks = list(pending_title_blocks) + current_blocks
                        pending_title_blocks = []

                    if trailing_title:
                        current_title = f"{marker_text} {trailing_title}"
                    else:
                        current_title = marker_text
                    current_type = _classify_clause_type(current_title or "")
                    current_level = level

                current_blocks.append((page.page_no, block))
            else:
                # Not a clause marker — accumulate into current clause
                current_blocks.append((page.page_no, block))

            prev_block = block

    # Flush the last clause
    _flush()

    return clauses


# ---------------------------------------------------------------------------
# Text-based splitting (fallback)
# ---------------------------------------------------------------------------

def split_clauses_from_text(text: str) -> list[ClauseSegment]:
    """Split plain contract text into clauses using regex patterns only.

    Used as a fallback when OCR block data is not available.
    """
    clauses: list[ClauseSegment] = []
    lines = text.split("\n")
    current_title: str | None = None
    current_type: str | None = None
    current_level: int = 0
    current_lines: list[str] = []

    def _flush():
        nonlocal current_title, current_type, current_level, current_lines
        content = "\n".join(current_lines).strip()
        if content:
            clauses.append(ClauseSegment(
                clause_type=current_type,
                clause_title=current_title,
                content=content,
                level=current_level,
            ))
        current_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue

        pattern_name, marker_text, trailing_title, level = _is_clause_marker(stripped)

        if pattern_name is not None:
            _flush()
            current_title = f"{marker_text} {trailing_title}" if trailing_title else marker_text
            current_type = _classify_clause_type(current_title or "")
            current_level = level
        current_lines.append(stripped)

    _flush()
    return clauses


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def save_clauses(
    db: AsyncSession, contract_id: uuid.UUID, clauses: list[ClauseSegment],
) -> list[ContractClause]:
    """Persist key clause segments (from LLM extraction) to DB."""
    records: list[ContractClause] = []
    for seg in clauses:
        bbox_val = None
        if getattr(seg, "bbox", None) is not None:
            bbox_val = seg.bbox.model_dump()

        record = ContractClause(
            contract_id=contract_id,
            clause_type=seg.clause_type,
            clause_title=seg.clause_title,
            content=seg.content,
            page_no=seg.page_no,
            bbox=bbox_val,
            start_char=seg.start_char,
            end_char=seg.end_char,
            confidence=seg.confidence,
            level=getattr(seg, 'level', 0),
            review_status="pending",
        )
        db.add(record)
        records.append(record)
    await db.flush()
    return records


async def split_and_save_clauses(
    db: AsyncSession,
    contract_id: uuid.UUID,
    ocr_result: OCRDetailedResult,
) -> list[ClauseSegment]:
    """Split OCR blocks into clauses and persist to DB."""
    clauses = split_clauses_from_blocks(ocr_result)

    # Delete previous clauses for this contract
    from sqlalchemy import delete
    await db.execute(
        delete(ContractClause).where(ContractClause.contract_id == contract_id)
    )

    index = 0
    for seg in clauses:
        index += 1
        db.add(ContractClause(
            contract_id=contract_id,
            clause_type=seg.clause_type,
            clause_title=seg.clause_title,
            content=seg.content,
            page_no=seg.page_no,
            page_end=seg.page_end,
            start_char=seg.start_char,
            end_char=seg.end_char,
            bbox=seg.bbox.model_dump() if seg.bbox else None,
            confidence=seg.confidence,
            sort_order=index,
            level=seg.level,
        ))

    await db.flush()
    return clauses
