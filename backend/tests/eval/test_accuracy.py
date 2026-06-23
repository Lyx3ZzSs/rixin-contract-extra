"""Unit tests for the field-accuracy metric (no LLM calls)."""
from tests.eval.accuracy import compute_field_accuracy


def test_true_positive():
    r = compute_field_accuracy(
        {"party-a-name": "北京日新科技有限公司"},
        {"party-a-name": "北京日新科技有限公司"},
    )
    assert r["tp"] == 1 and r["fp"] == 0 and r["fn"] == 0
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["f1"] == 1.0


def test_false_positive_wrong_value():
    r = compute_field_accuracy({"amount": "100"}, {"amount": "200"})
    assert r["fp"] == 1 and r["tp"] == 0 and r["fn"] == 0
    assert r["precision"] == 0.0 and r["f1"] == 0.0


def test_false_negative_missing_value():
    r = compute_field_accuracy({"amount": ""}, {"amount": "200"})
    assert r["fn"] == 1 and r["recall"] == 0.0


def test_golden_empty_field_is_ignored():
    # Field absent from golden → not applicable, not counted.
    r = compute_field_accuracy({"amount": "100"}, {})
    assert r["tp"] == r["fp"] == r["fn"] == 0


def test_whitespace_normalized():
    r = compute_field_accuracy({"name": "  ABC  "}, {"name": "ABC"})
    assert r["tp"] == 1


def test_aggregate_metrics_mixed():
    r = compute_field_accuracy(
        {"a": "1", "b": "wrong", "c": ""},   # tp, fp, fn
        {"a": "1", "b": "2", "c": "3"},
    )
    assert r["tp"] == 1 and r["fp"] == 1 and r["fn"] == 1
    assert r["precision"] == 0.5 and r["recall"] == 0.5 and r["f1"] == 0.5


def test_per_field_verdicts_populated():
    r = compute_field_accuracy({"a": "1"}, {"a": "1", "b": "2"})
    assert r["per_field"]["a"]["verdict"] == "tp"
    assert r["per_field"]["b"]["verdict"] == "fn"
