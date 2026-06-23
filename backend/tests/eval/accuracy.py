"""Pure field-level accuracy metric for contract extraction evaluation.

Semantics (per spec §4.1):
  - A field is a True Positive when extracted (non-empty) AND equals golden.
  - False Positive: extracted non-empty but wrong.
  - False Negative: golden non-empty but extracted empty/missing.
  - Fields absent from ``golden`` (or empty golden) are "not applicable"
    and skipped — they never count against accuracy.
Values are whitespace-normalized before comparison.
"""
from __future__ import annotations


def _normalize(value: str | None) -> str:
    return (value or "").strip()


def compute_field_accuracy(
    extracted: dict[str, str | None],
    golden: dict[str, str],
) -> dict:
    tp = fp = fn = 0
    per_field: dict[str, dict] = {}
    for key in set(extracted) | set(golden):
        g = _normalize(golden.get(key))
        e = _normalize(extracted.get(key))
        if not g:
            continue  # not applicable
        if e == g:
            verdict, hit = "tp", True
        elif e:
            verdict, hit = "fp", False
        else:
            verdict, hit = "fn", False
        per_field[key] = {"golden": g, "extracted": e, "verdict": verdict}
        if verdict == "tp":
            tp += 1
        elif verdict == "fp":
            fp += 1
        else:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "per_field": per_field,
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
