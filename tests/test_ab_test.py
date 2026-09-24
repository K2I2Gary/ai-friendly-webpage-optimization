"""Unit tests for the A/B harness metrics (ab_test.py)."""

from ab_test import compute_metrics, mentioned_ids

FAIL = ["html.json-ld", "llms-txt.exists", "html.text-ratio"]


def test_mentioned_ids_matches_substrings():
    ids = mentioned_ids("Fix html.json-ld and llms-txt.exists")
    assert "html.json-ld" in ids
    assert "llms-txt.exists" in ids
    # a sub-check is only matched when its full dotted id is written out
    assert "html.json-ld.date-modified" not in ids


def test_compute_metrics_coverage():
    m = compute_metrics("Fix html.json-ld and llms-txt.exists", FAIL)
    assert abs(m["coverage"] - 2 / 3) < 1e-9


def test_compute_metrics_sub_checks():
    m = compute_metrics("Fix html.json-ld.date-modified and html.json-ld", ["html.json-ld"])
    assert m["sub_checks"] == 1


def test_compute_metrics_flips_annotations():
    m = compute_metrics("Flips: `a.b`. And Flips: `c.d`.", ["x.y"])
    assert m["flips_annotations"] == 2
