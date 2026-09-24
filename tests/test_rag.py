"""Unit tests for the RAG knowledge base (rag.py)."""

from rag import (
    load_catalog,
    rank_examples,
    read_recipe,
    retrieve_for_checks,
)


def test_catalog_has_core_checks():
    catalog = load_catalog()
    assert "html.json-ld" in catalog
    assert catalog["html.json-ld"]["group"] == "Structured data"
    assert "llms-txt.exists" in catalog


def test_read_recipe_strips_frontmatter():
    recipe = read_recipe("html.json-ld")
    assert recipe is not None
    assert not recipe.startswith("---")
    assert "application/ld+json" in recipe


def test_read_recipe_unknown_returns_none():
    assert read_recipe("does.not.exist") is None


def test_retrieve_for_checks_returns_recipe():
    chunks = retrieve_for_checks([
        {"id": "html.json-ld", "name": "Has parseable JSON-LD block", "group": "Structured data"},
    ])
    assert len(chunks) == 1
    assert chunks[0]["id"] == "html.json-ld"
    assert "content" in chunks[0]


def test_retrieve_for_checks_skips_unknown():
    chunks = retrieve_for_checks([
        {"id": "does.not.exist", "name": "x", "group": "y"},
    ])
    assert chunks == []


def test_retrieve_for_checks_respects_top_k():
    chunks = retrieve_for_checks([
        {"id": "html.json-ld", "name": "a", "group": "b"},
        {"id": "html.headings", "name": "a", "group": "b"},
    ], top_k=1)
    assert len(chunks) == 1


def test_rank_examples_returns_pass_example():
    ranked = rank_examples("structured data JSON-LD breadcrumb")
    assert ranked
    assert ranked[0]["id"] == "test-page"
    assert ranked[0]["verdict"] == "PASS"


def test_parse_frontmatter_extracts_fields():
    from rag import _parse_frontmatter
    fm = _parse_frontmatter(
        '---\ncheck_ids: ["a.b"]\nscore_before: 30\nverdict: PASS\nfinal_score: 76.57\n---\nbody'
    )
    assert fm["check_ids"] == '["a.b"]'
    assert fm["verdict"] == "PASS"
    assert fm["final_score"] == "76.57"
