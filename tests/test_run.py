"""Unit tests for run.py's pipeline parsing."""

from run import parse_pipeline


def test_parse_pipeline_comma():
    assert parse_pipeline("eval,reflect,generate") == ["eval", "reflect", "generate"]


def test_parse_pipeline_arrow():
    assert parse_pipeline("eval->reflect->generate") == ["eval", "reflect", "generate"]


def test_parse_pipeline_keeps_hyphen_in_name():
    # Hyphens are not separators: future stage names may legitimately contain them.
    assert parse_pipeline("eval,my-stage") == ["eval", "my-stage"]
