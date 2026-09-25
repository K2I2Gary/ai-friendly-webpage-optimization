"""Unit tests for the site-level a14y scoring in evaluate_result.py."""

from evaluate_result import _site_metrics, objective_score


def test_site_metrics_computes_flat_pool_score():
    scorecard = {
        "siteChecks": [
            {"id": "a", "status": "pass"},
            {"id": "b", "status": "pass"},
            {"id": "c", "status": "fail"},
            {"id": "d", "status": "na"},
        ]
    }
    m = _site_metrics(scorecard)
    assert m["score"] == 67  # 2/3 applicable pass -> 66.67 -> round 67
    assert m["passed"] == 2
    assert m["applicable"] == 3
    assert len(m["checks"]) == 4


def test_site_metrics_none_when_no_applicable():
    assert _site_metrics({"siteChecks": [{"id": "x", "status": "na"}]})["score"] is None


def test_objective_score_includes_site_after():
    integrity = {"integrity_score": 100.0}
    structural = {"structural_score": 90.0}
    score, w = objective_score(62, 100, integrity, structural)
    # 0.45*62 + 0.15*100 + 0.25*100 + 0.15*90 = 81.4
    assert abs(score - 81.4) < 0.01
    assert w["a14y_site_after"] == 0.15


def test_objective_score_reweights_when_a14y_missing():
    integrity = {"integrity_score": 100.0}
    structural = {"structural_score": 90.0}
    score, w = objective_score(None, None, integrity, structural)
    assert w["a14y_page_after"] == 0.0
    assert w["a14y_site_after"] == 0.0
    # integrity + structural reweighted to sum to 1
    assert abs(w["integrity"] + w["structural"] - 1.0) < 1e-9
    assert abs(score - (0.625 * 100 + 0.375 * 90)) < 0.01
