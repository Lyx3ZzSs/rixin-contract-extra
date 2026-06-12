"""Clause splitting service — rule-based contract clause segmentation.

Supports two input modes:
1. **Block-based**: from OCR blocks (preferred) — preserves page numbers and bboxes.
2. **Text-based**: from plain text (fallback) — uses regex to find article markers.

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
_CLAUSE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "第一条 …" / "第1条 …"  — Chinese article marker with trailing content
    (
        "article_cn",
        re.compile(
            r"^第\s*([一二三四五六七八九十百零\d]+)\s*条\s*(.*)",
        ),
    ),
    # "一、" / "二、" — Chinese numeral with ideographic comma
    (
        "section_cn",
        re.compile(r"^([一二三四五六七八九十]+)\s*[、，]\s*(.*)"),
    ),
    # "（一）" / "（二）" — fullwidth parenthesised numeral
    (
        "paren_cn",
        re.compile(r"^[（(]\s*([一二三四五六七八九十]+)\s*[）)]\s*(.*)"),
    ),
    # "（1）" / "（2）" — fullwidth parenthesised arabic number
    (
        "paren_num",
        re.compile(r"^[（(]\s*(\d+)\s*[）)]\s*(.*)"),
    ),
    # "1.1.1" — dotted numbering (at least two levels)
    (
        "dotted_multi",
        re.compile(r"^(\d+\.\d+(?:\.\d+)*)\s*(.*)"),
    ),
    # "1." — single-level arabic numbering (must have content after)
    (
        "num_dot",
        re.compile(r"^(\d+)\s*[\.．、]\s*(.*)"),
    ),
    # "Article 1" / "ARTICLE 1" — English-style
    (
        "article_en",
        re.compile(r"^[Aa][Rr][Tt][Ii][Cc][Ll][Ee]\s+(\d+)\s*(.*)"),
    ),
]


def _is_clause_marker(line: str) -> tuple[str | None, str | None, str | None]:
    """Check if a line starts with a clause marker.

    Returns (pattern_name, marker_text, trailing_title) or (None, None, None).
    """
    stripped = line.strip()
    if not stripped or len(stripped) < 2:
        return None, None, None

    for name, pat in _CLAUSE_PATTERNS:
        m = pat.match(stripped)
        if m:
            # Reconstruct the full marker text
            marker = m.group(0)
            trailing = m.group(m.lastindex).strip() if m.lastindex else ""
            # For article_cn, the marker is "第X条" and trailing is the rest
            if name == "article_cn":
                marker_text = f"第{m.group(1)}条"
            elif name == "article_en":
                marker_text = f"Article {m.group(1)}"
            else:
                # The entire match before trailing content
                end = m.start(2) if m.lastindex and m.lastindex >= 2 else m.end()
                marker_text = stripped[:end].strip()
            # The trailing portion is the clause title if non-empty
            title = trailing if trailing else None
            return name, marker_text, title

    return None, None, None


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
    }
    for keyword, ctype in type_map.items():
        if keyword in title:
            return ctype
    return None


# ---------------------------------------------------------------------------
# Block-based splitting (preferred)
# ---------------------------------------------------------------------------

def split_clauses_from_blocks(
    ocr_result: OCRDetailedResult,
) -> list[ClauseSegment]:
    """Split contract text into clauses using OCR block-level results.

    This preserves page_no and bbox for each clause.  Blocks are already
    ordered by (page_no, sort_order).
    """
    clauses: list[ClauseSegment] = []
    current_title: str | None = None
    current_type: str | None = None
    current_blocks: list[tuple[int, OCRTextBlock]] = []  # (page_no, block)

    def _flush():
        nonlocal current_title, current_type, current_blocks
        if not current_blocks:
            return

        # Combine content from all blocks in this clause
        content_parts: list[str] = []
        for _, blk in current_blocks:
            content_parts.append(blk.text.strip())
        content = "\n".join(content_parts).strip()
        if not content:
            current_blocks = []
            return

        # Determine page range and bbox
        page_start = current_blocks[0][0]
        page_end = current_blocks[-1][0]
        # Use first block's bbox as the clause anchor bbox
        first_bbox = current_blocks[0][1].bbox
        # Average confidence across blocks
        avg_conf = sum(b.confidence for _, b in current_blocks) / len(current_blocks)

        # Build clause_title: include marker + trailing title
        clauses.append(ClauseSegment(
            clause_type=current_type,
            clause_title=current_title,
            content=content,
            page_no=page_start,
            page_end=page_end if page_end != page_start else None,
            bbox=first_bbox,
            confidence=round(avg_conf, 4),
        ))
        current_blocks = []

    for page in ocr_result.pages:
        for block in page.blocks:
            text = block.text.strip()
            if not text:
                continue

            pattern_name, marker_text, trailing_title = _is_clause_marker(text)

            if pattern_name is not None:
                # Start of a new clause — flush the previous one
                _flush()

                # The clause title is the marker + trailing text
                if trailing_title:
                    current_title = f"{marker_text} {trailing_title}"
                else:
                    current_title = marker_text

                # Attempt classification from the title
                current_type = _classify_clause_type(current_title)

                # Add the current block as the first block of the new clause
                current_blocks.append((page.page_no, block))
            else:
                # Not a clause marker — append to current clause or preamble
                current_blocks.append((page.page_no, block))

    # Flush the last clause
    _flush()

    return clauses


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def save_clauses(
    db: AsyncSession, contract_id: uuid.UUID, clauses: list[ClauseSegment]
) -> list[ContractClause]:
    """Persist clause segments to DB."""
    records = []
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
) -> list[ContractClause]:
    """High-level helper: split from OCR blocks and persist.

    This is the primary entry point for the pipeline.
    """
    clauses = split_clauses_from_blocks(ocr_result)
    return await save_clauses(db, contract_id, clauses)
