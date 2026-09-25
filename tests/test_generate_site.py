"""Unit tests for the site-file generator (generate_site.py)."""

from generate_site import (
    _extract_meta,
    _html_to_markdown,
    _parse,
    _render_agents_md,
    _render_llms_txt,
    _render_md_mirror,
    _render_robots,
    _render_sitemap_md,
    _render_sitemap_xml,
    generate,
)

META = {"title": "My Page", "description": "A test page.", "lang": "en", "canonical": ""}


def test_render_robots_allows_and_points_to_sitemap():
    r = _render_robots("https://example.com/")
    assert "User-agent: *" in r
    assert "Allow: /" in r
    assert "Sitemap: https://example.com/sitemap.xml" in r


def test_render_llms_txt_has_md_link():
    r = _render_llms_txt(META, "https://example.com/", "page.md")
    assert "## Pages" in r
    assert "[My Page](https://example.com/page.md)" in r


def test_render_sitemap_xml_parses_as_urlset():
    r = _render_sitemap_xml(META, "https://example.com/", "page.html", "page.md")
    assert "<urlset" in r
    assert "<loc>https://example.com/page.html</loc>" in r
    assert "<lastmod>" in r


def test_render_sitemap_md_has_headings_and_links():
    r = _render_sitemap_md(META, "https://example.com/", "page.html", "page.md")
    assert "# Sitemap" in r
    assert "[My Page](https://example.com/page.html)" in r


def test_render_agents_md_covers_install_and_usage():
    r = _render_agents_md(META, "https://example.com/", "page.html", "page.md")
    assert "## Install" in r
    assert "## Usage" in r


def test_render_md_mirror_has_frontmatter_and_sitemap():
    r = _render_md_mirror(META, "Some body", "https://example.com/", "page.html", "page.md")
    assert r.startswith("---")
    assert "title: My Page" in r
    assert "canonical: https://example.com/page.html" in r
    assert "## Sitemap" in r


def test_html_to_markdown_converts_headings_and_links():
    html = '<html><body><h1>Title</h1><p>Hello <a href="/x">link</a></p></body></html>'
    md = _html_to_markdown(_parse(html))
    assert "# Title" in md
    assert "[link](/x)" in md


def test_extract_meta_reads_title_and_description():
    html = ('<html lang="zh-CN"><head><title>Hi</title>'
            '<meta name="description" content="A desc."></head></html>')
    meta = _extract_meta(_parse(html))
    assert meta["title"] == "Hi"
    assert meta["description"] == "A desc."
    assert meta["lang"] == "zh-CN"


def test_generate_skips_titleless_page(tmp_path):
    p = tmp_path / "page.html"
    p.write_text('<html><head></head><body><p>no title</p></body></html>', encoding="utf-8")
    assert generate(p, "http://localhost/", tmp_path) == []


def test_generate_writes_six_files(tmp_path):
    p = tmp_path / "page.html"
    p.write_text(
        '<html><head><title>My Page</title>'
        '<meta name="description" content="A test page."></head>'
        '<body><h1>My Page</h1><p>Hello</p></body></html>',
        encoding="utf-8",
    )
    written = generate(p, "http://localhost/", tmp_path)
    assert {w.name for w in written} == {
        "robots.txt", "llms.txt", "sitemap.xml", "sitemap.md", "AGENTS.md", "page.md"
    }
    mirror = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert mirror.startswith("---")
    assert "title: My Page" in mirror
