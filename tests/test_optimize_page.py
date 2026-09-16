"""Unit tests for the page-rewrite helpers in optimize_page.py.

These cover the functions that implement the tool's core guarantees: preserving the original
URL set, stripping fabricated resources/content, injecting alt text, and refusing to invent
content for pages that have no title or heading.
"""

from optimize_page import (
    _inject_alt,
    _json_ld_valid,
    _merge_head,
    _page_title,
    _parse,
    _urls,
    ensure_basics,
    finalize,
    strip_fabricated_content,
    strip_fictional_resources,
)


def test_merge_head_preserves_resources_and_charset():
    source = (
        '<html><head><meta charset="UTF-8">'
        '<link rel="stylesheet" href="/a.css">'
        '<script src="/app.js"></script>'
        '<title>Old</title></head><body>x</body></html>'
    )
    new_head = "<head><title>New</title></head>"
    out = _merge_head(source, new_head)
    assert "/a.css" in out
    assert "/app.js" in out
    assert "charset" in out
    assert "<title>New</title>" in out
    assert "Old</title>" not in out


def test_merge_head_preserves_doctype():
    source = '<!DOCTYPE html><html><head><title>Old</title></head><body>x</body></html>'
    out = _merge_head(source, "<head><title>New</title></head>")
    assert out.lstrip().startswith("<!DOCTYPE html>")


def test_strip_fictional_resources_removes_new_css_js():
    source = '<html><head><link rel="stylesheet" href="/real.css"></head><body></body></html>'
    html = (
        '<html><head><link rel="stylesheet" href="/real.css">'
        '<link rel="stylesheet" href="/fake.css">'
        '<script src="/fake.js"></script></head><body></body></html>'
    )
    out = strip_fictional_resources(source, html)
    assert "/real.css" in out
    assert "/fake.css" not in out
    assert "/fake.js" not in out


def test_strip_fabricated_content_removes_new_links_and_nav():
    source = '<html><body><a href="/keep">k</a></body></html>'
    html = '<html><body><a href="/keep">k</a><a href="/new">n</a><nav>menu</nav></body></html>'
    out = strip_fabricated_content(source, html)
    assert "/keep" in out
    assert "/new" not in out
    assert "<nav" not in out


def test_inject_alt_adds_to_img_without_alt():
    soup = _parse('<img src="/a.png"><img src="/b.png" alt="B"><img src="/c.png" />')
    assert _inject_alt(soup, "/a.png", "A desc") is True
    out = str(soup)
    assert 'alt="A desc"' in out
    assert 'alt="B"' in out
    # the third image has a different src and must stay untouched
    assert not soup.find("img", src="/c.png").has_attr("alt")


def test_ensure_basics_adds_lang_viewport_and_h1():
    html = '<html><head></head><body><h2>One</h2><h3>Two</h3></body></html>'
    out = ensure_basics(html)
    assert 'lang="en"' in out
    assert 'name="viewport"' in out
    assert "<h1" in out
    assert "<h2" in out


def test_page_title_empty_when_missing():
    assert _page_title(_parse("<html><head></head><body></body></html>")) == ""


def test_page_title_from_title_tag():
    assert _page_title(_parse("<html><head><title>Hello</title></head></html>")) == "Hello"


def test_json_ld_valid_ok():
    assert _json_ld_valid('<script type="application/ld+json">{"a": 1}</script>')


def test_json_ld_valid_bad():
    assert not _json_ld_valid('<script type="application/ld+json">{oops</script>')


def test_json_ld_valid_when_absent():
    assert _json_ld_valid("<html></html>")


def test_finalize_does_not_fabricate_for_titleless_page():
    html = '<html><head></head><body><p>x</p></body></html>'
    out = finalize(html)
    assert 'lang="en"' in out
    assert 'name="viewport"' in out
    assert "<h1" not in out
    assert "application/ld+json" not in out
    assert 'name="description"' not in out


def test_finalize_adds_meta_and_h1_when_title_present():
    html = '<html><head><title>Hi</title></head><body><p>x</p></body></html>'
    out = finalize(html)
    assert 'name="description"' in out
    assert "application/ld+json" in out
    assert "<h1>Hi</h1>" in out


def test_urls_normalizes_entities():
    html = '<a href="/x?a=1&amp;b=2"><img src="/i?x=1&y=2">'
    assert _urls(html) == {"/x?a=1&b=2", "/i?x=1&y=2"}


def test_finalize_preserves_urls_with_ampersand():
    source = '<html><head><title>Hi</title></head><body><a href="/go?a=1&b=2">x</a></body></html>'
    out = finalize(source)
    assert _urls(source) <= _urls(out)


def test_strip_fabricated_content_keeps_empty_href_anchor():
    # an <a href=""> wraps content (e.g. an <img>); it must not be removed just because its
    # href is empty (the original rule engine kept anchors without a meaningful href).
    source = '<html><body><a href=""><img src="/x.png"></a></body></html>'
    out = strip_fabricated_content(source, source)
    assert "/x.png" in out


def test_strip_fabricated_content_keeps_regpage_entity_link():
    # "&amp;regPage" must not be double-decoded into "®Page" (which would drop the link).
    source = '<html><body><a href="https://m.163.com/r.htm?from=a&amp;regPage=1">x</a></body></html>'
    out = strip_fabricated_content(source, source)
    assert "regPage" in out


def test_merge_head_preserves_conditional_comment_script():
    source = (
        '<html><head><meta charset="UTF-8">'
        '<!--[if lte IE 8]><script src="//misc.js"></script><![endif]-->'
        '<title>Old</title></head><body>x</body></html>'
    )
    out = _merge_head(source, "<head><title>New</title></head>")
    assert "//misc.js" in out
    assert "<title>New</title>" in out


def test_finalize_injects_default_style_when_unstyled():
    html = '<html><head><title>Hi</title></head><body><h1>Hello</h1><p>x</p></body></html>'
    out = finalize(html)
    assert "prefers-color-scheme" in out
    assert "--accent-strong: #4338ca" in out


def test_finalize_keeps_existing_style():
    html = '<html><head><title>Hi</title><style>body{color:red}</style></head><body><h1>x</h1></body></html>'
    out = finalize(html)
    assert "color:red" in out
    assert "--accent-strong: #4338ca" not in out
