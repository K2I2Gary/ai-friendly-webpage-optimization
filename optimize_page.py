#!/usr/bin/env python3
"""
Page optimization agent
-----------------------
Reads the source HTML (a local file or an http(s) URL — URLs are fetched via Playwright first to
render JS, falling back to urllib). The LLM generates new <head> metadata plus a style block and
image alt text; the program merges them back into the original page and writes an "AI-readable,
semantic" optimized page.

The source may be a local HTML file or an http(s) URL (auto-fetched).

Principles:
  - HTML is parsed and re-serialized with BeautifulSoup, so content and links are preserved, but
    formatting / attribute quoting / entity encoding may be normalized (the body is not byte-verbatim).
  - The LLM only rewrites the <head> (title / description / og / JSON-LD / viewport / lang);
    structural fixes such as lang / h1 / removing Flash are handled by the rule engine.

Usage:
    python optimize_page.py test.html             # local file -> test_optimized.html
    python optimize_page.py https://example.com   # URL (auto-fetched) -> *_optimized.html
    python optimize_page.py test.html -o out.html
"""

import argparse
import json
import re
import sys
import urllib.request
from html import unescape
from pathlib import Path

from bs4 import BeautifulSoup, Comment

from common import OUTPUT_DIR, console_utf8, extract_urls, is_url, url_to_filename
from llm import chat, get_config

# Windows console defaults to GBK; force stdout/stderr to UTF-8
console_utf8()

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT = OUTPUT_DIR / "prompt.txt"

HEAD_SYSTEM_PROMPT = """You are a senior web-page metadata engineer.

You receive a page's existing <head> and a preview of its visible content. Produce a COMPLETE,
self-contained <head> section (from <head> to </head>) that is AI-readable and SEO-friendly.

Rules:
- Output the <head>...</head> block first. No <html>, no <body>, no explanation, no Markdown fences.
- Include exactly one each of: <title>, <meta name="viewport">, <meta name="description">,
  and Open Graph tags (og:title / og:description / og:type / og:url / og:site_name).
- Include a JSON-LD <script type="application/ld+json"> with @type "WebSite" (or "Article"/"Product"
  when clearly applicable), using ONLY names/urls/text that appear in the given page.
- Reuse the page's existing <title> text verbatim when present; never invent a different title.
- Derive every description/og/JSON-LD value from the page content; never fabricate names, prices,
  or descriptions, and never copy the example copy in the instruction.
- If an 'a14y findings' section is present, treat its failing items as the priority list to fix.
- For og:url and JSON-LD url, use the given page URL when provided; otherwise omit them.

After the </head>, output a line "LANG:" followed by the page's language code (BCP 47, e.g. "en",
"zh-CN", "ja") inferred from its content.

Then output a line "STYLE:" followed by a single <style>...</style> block that gives the page a
polished, cohesive, magazine-quality look. Add CSS ONLY — never rewrite or add text, links, or content.

Design requirements (aim high — a generic bare "article" look is not acceptable):
1. Content awareness — decide what kind of page this is (product/e-commerce, article/blog, landing
   page, documentation, dashboard, gallery, form, directory, etc.) and design FOR it. A storefront
   needs cards, a hero, and price/product emphasis; an article needs a comfortable reading measure;
   a landing page needs a strong above-the-fold. Tailor the container width to the type (a wide,
   centered container or a responsive card grid for catalogs; a narrower measure only for long-form
   reading) — do NOT force every page into a narrow single column.
2. System, not one-offs — define a :root design-token set: a refined palette (one strong accent +
   neutral grays, with light/dark variants), a font stack (system-ui + a CJK fallback), a spacing
   scale, 2–3 radii, and layered subtle shadows. Reuse the tokens everywhere.
3. Typography — a clear hierarchy: larger, heavier headings with tight leading; comfortable body size
   (16–18px) and line-height (1.6–1.75); muted secondary text; distinct link styling.
4. Layout — center content in a responsive container, generous vertical rhythm between sections, and
   a responsive card grid (auto-fill minmax) for repeated items like products/articles/features.
5. Components — style nav/header, hero, cards, buttons, badges/chips, tables, forms (inputs,
   textareas, selects, buttons), images (max-width, rounded), lists, blockquote, and code/pre so they
   read as one coherent system. Cards: radius + subtle shadow + gentle hover lift. Buttons: solid
   primary + hover/active states + a visible focus ring.
6. Interaction & polish — 150–200ms transitions on hover/focus, a visible :focus-visible outline,
   and a @media (prefers-color-scheme: dark) block so it looks good in both light and dark modes.
7. Responsive — mobile-first, fluid type via clamp(), and collapse multi-column layouts to a single
   column below ~720px.

Keep the CSS self-contained, dependency-free, and as compact as you can while meeting the above.
Output nothing but the single <style>...</style> block.

Then output a single line "ALTS:" followed by a JSON array of alt-text suggestions
for images that currently lack a meaningful alt attribute (derive each from its context / surrounding
text; use the page's language). Use the exact src value as it appears in the page:
ALTS:
[{"src": "<exact img src>", "alt": "<concise alt text>"}]
Include at most 15 images; if none need alt, output ALTS: []. Do not add anything else after the array.
"""


# ============ parsing helpers ============

def _parse(html: str) -> BeautifulSoup:
    """Parse HTML into a BeautifulSoup tree (html.parser, stdlib)."""
    return BeautifulSoup(html, "html.parser")


def _urls(html: str) -> set:
    """Decoded URL set from a serialized HTML string (normalizes &amp; / &lt; etc.)."""
    return {unescape(u) for u in extract_urls(html)}


def _ensure_head(soup: BeautifulSoup):
    """Return the <head> tag, creating one (and a root <html>) if missing."""
    head = soup.find("head")
    if head is not None:
        return head
    head = soup.new_tag("head")
    html_tag = soup.find("html")
    if html_tag is not None:
        html_tag.insert(0, head)
    else:
        soup.insert(0, head)
    return head


# ============ Step: build the LLM context ============

def _build_head_prompt(source: str, url: str | None, prompt: str = "") -> str:
    """Build a compact context for the LLM: original <head> + body text summary + page URL + a14y findings."""
    soup = _parse(source)
    head_tag = soup.find("head")
    head = str(head_tag) if head_tag is not None else "(none)"

    for tag in soup(["script", "style"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()[:2500]

    lines = ["Generate new <head> metadata for the page below."]
    if url:
        lines.append(f"Page URL: {url}")
    lines += ["", "=== Original <head> ===", head or "(none)", "",
              "=== Body text summary (scripts/styles stripped) ===", text or "(no body text)"]

    if prompt:
        # Condense the reflect analysis: strip code blocks, collapse blank lines, truncate
        p = re.sub(r"```.*?```", "", prompt, flags=re.S)
        p = re.sub(r"\n{2,}", "\n", p).strip()[:2000]
        if p:
            lines += ["", "=== a14y findings (fix these first) ===", p]
    return "\n".join(lines)


# ============ LLM path: merge head / lang / style / alts ============

def _merge_head_tag(soup: BeautifulSoup, new_head):
    """Replace the source <head> with `new_head`, preserving the source head's resource tags
    (link / script / style / base), charset, and any other URL-bearing element or comment
    (e.g. conditional-comment scripts, tracking iframes) so no referenced URL is lost."""
    old_head = soup.find("head")
    if old_head is None:
        html_tag = soup.find("html")
        if html_tag is not None:
            html_tag.insert(0, new_head)
        else:
            soup.insert(0, new_head)
        return

    if new_head.find("meta", charset=True) is None:
        charset_meta = old_head.find("meta", charset=True)
        if charset_meta is not None:
            new_head.insert(0, charset_meta)
    for tag in list(old_head.find_all(True)):
        if tag.name in ("link", "script", "style", "base"):
            new_head.append(tag)
        elif any(tag.has_attr(a) for a in ("href", "src", "action")):
            new_head.append(tag)
    for comment in list(old_head.find_all(string=Comment)):
        if "src=" in str(comment) or "href=" in str(comment):
            new_head.append(comment)
    old_head.replace_with(new_head)


def _merge_head(source: str, new_head: str) -> str:
    """String-level wrapper of _merge_head_tag (kept for reuse / tests)."""
    soup = _parse(source)
    new_head_tag = _parse(new_head).find("head")
    if new_head_tag is None:
        return source
    _merge_head_tag(soup, new_head_tag)
    return str(soup)


def _parse_lang(raw: str) -> str | None:
    """Extract the BCP-47 language code from the LLM's `LANG:` line."""
    m = re.search(r"LANG\s*:\s*([A-Za-z][A-Za-z0-9-]*)", raw)
    return m.group(1) if m else None


def _set_lang(soup: BeautifulSoup, lang: str) -> None:
    html_tag = soup.find("html")
    if html_tag is not None:
        html_tag["lang"] = lang


def _parse_alts(raw: str) -> list:
    """Parse the LLM's `ALTS:` JSON array into a list of {src, alt} dicts."""
    m = re.search(r"ALTS\s*:\s*(\[.*?\])", raw, flags=re.S)
    if not m:
        return []
    try:
        alts = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return []
    return [it for it in (alts if isinstance(alts, list) else []) if isinstance(it, dict)]


def _inject_alt(soup: BeautifulSoup, src: str, alt: str) -> bool:
    """Set alt on the first <img> whose src matches and which lacks an alt attribute."""
    for img in soup.find_all("img"):
        if (img.get("src") == src or img.get("src") == unescape(src)) and not img.has_attr("alt"):
            img["alt"] = alt.replace('"', "'")
            return True
    return False


def optimize_with_llm(source: str, url: str | None = None, prompt: str = "") -> str | None:
    """Have the LLM produce the new <head> metadata, then merge it back (content/links preserved).

    Returns the merged full page, or None if the LLM returned no valid <head> (rule fallback).
    """
    cfg = get_config("generate")
    print(f"[optimize] requesting {cfg.provider} (model={cfg.model}) ...")
    raw = chat(
        [
            {"role": "system", "content": HEAD_SYSTEM_PROMPT},
            {"role": "user", "content": _build_head_prompt(source, url, prompt)},
        ],
        temperature=0.2,
        config=cfg,
    )
    # Strip any ```html fences
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)

    raw_soup = _parse(raw)
    new_head = raw_soup.find("head")
    if new_head is None:
        return None

    lang = _parse_lang(raw)
    style = raw_soup.find("style")
    alts = _parse_alts(raw)

    soup = _parse(source)
    _merge_head_tag(soup, new_head)
    if lang:
        _set_lang(soup, lang)
    if style is not None:
        _ensure_head(soup).append(style)
    for item in alts:
        _inject_alt(soup, item.get("src") or "", item.get("alt") or "")
    return str(soup)


# ============ Rule fallback: deterministic minimal fixes ============

# Injected when the page has no <style> of its own, so the no-LLM fallback is still pleasant to read.
DEFAULT_STYLESHEET = """\
:root {
  --bg: #ffffff; --bg-soft: #f7f7f8; --bg-muted: #eef0f3;
  --fg: #17181c; --fg-muted: #5c5f66;
  --accent: #4f46e5; --accent-strong: #4338ca; --accent-soft: #eef2ff;
  --border: #e5e7eb; --success: #16a34a; --danger: #dc2626;
  --radius-sm: 8px; --radius: 14px; --radius-lg: 20px;
  --shadow-sm: 0 1px 2px rgba(17, 24, 39, .05);
  --shadow: 0 4px 14px rgba(17, 24, 39, .07);
  --shadow-lg: 0 16px 40px rgba(17, 24, 39, .12);
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth; }
body {
  margin: 0; font-family: var(--font); font-size: 16px; line-height: 1.7;
  color: var(--fg); background: var(--bg);
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
main, .container, .content { max-width: 1120px; margin: 0 auto; padding: 2rem clamp(1rem, 4vw, 2.5rem); }
h1, h2, h3, h4, h5, h6 { line-height: 1.25; font-weight: 700; color: var(--fg); margin: 1.6em 0 .6em; letter-spacing: -.01em; }
h1 { font-size: clamp(1.8rem, 1.2rem + 2vw, 2.6rem); margin-top: 0; }
h2 { font-size: clamp(1.4rem, 1.1rem + 1vw, 1.8rem); padding-bottom: .35em; border-bottom: 1px solid var(--border); }
h3 { font-size: 1.2rem; }
p { margin: 0 0 1.1em; }
a { color: var(--accent); text-decoration: none; text-underline-offset: 3px; transition: color .15s ease; }
a:hover { color: var(--accent-strong); text-decoration: underline; }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px;
}
ul, ol { padding-left: 1.5em; margin: 0 0 1.2em; }
li { margin: .35em 0; }
img { max-width: 100%; height: auto; display: block; border-radius: var(--radius); }
figure { margin: 1.5em 0; }
figcaption { font-size: .875rem; color: var(--fg-muted); text-align: center; margin-top: .5em; }
blockquote {
  margin: 1.5em 0; padding: .9em 1.25em; border-left: 4px solid var(--accent);
  background: var(--accent-soft); border-radius: 0 var(--radius) var(--radius) 0; color: var(--fg);
}
hr { border: 0; border-top: 1px solid var(--border); margin: 2.5em 0; }
code, kbd, samp { font-family: var(--mono); font-size: .88em; background: var(--bg-muted); padding: .15em .4em; border-radius: 6px; }
pre { font-family: var(--mono); background: #0f172a; color: #e2e8f0; padding: 1.1em 1.3em; border-radius: var(--radius); overflow-x: auto; line-height: 1.55; }
pre code { background: none; padding: 0; color: inherit; }
table { width: 100%; border-collapse: collapse; margin: 1.5em 0; font-size: .95rem; }
th, td { text-align: left; padding: .7em .9em; border-bottom: 1px solid var(--border); }
th { background: var(--bg-soft); font-weight: 600; }
tr:hover td { background: var(--bg-soft); }
button, .btn, input[type="submit"] {
  display: inline-block; font-family: var(--font); font-size: .95rem; font-weight: 600;
  color: #fff; background: var(--accent); border: 1px solid var(--accent);
  padding: .55em 1.15em; border-radius: var(--radius-sm); cursor: pointer;
  transition: background .15s ease, transform .15s ease, box-shadow .15s ease;
}
button:hover, .btn:hover, input[type="submit"]:hover { background: var(--accent-strong); box-shadow: var(--shadow); }
button:active, .btn:active, input[type="submit"]:active { transform: translateY(1px); }
input, select, textarea {
  font-family: var(--font); font-size: .95rem; color: var(--fg);
  padding: .55em .8em; border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--bg); transition: border-color .15s ease, box-shadow .15s ease;
}
input:focus, select:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); outline: none; }
header { background: var(--bg); border-bottom: 1px solid var(--border); }
footer { background: var(--bg-soft); border-top: 1px solid var(--border); padding: 2rem clamp(1rem, 4vw, 2.5rem); color: var(--fg-muted); }
.card {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 1.25rem; box-shadow: var(--shadow-sm); transition: box-shadow .2s ease, transform .2s ease;
}
.card:hover { box-shadow: var(--shadow); transform: translateY(-2px); }
.grid { display: grid; gap: 1.25rem; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1115; --bg-soft: #17191f; --bg-muted: #1f232b;
    --fg: #e5e7eb; --fg-muted: #9ca3af; --accent: #818cf8; --accent-strong: #a5b4fc;
    --accent-soft: #1e1b4b; --border: #262a33;
  }
  pre { background: #0b0f19; color: #cbd5e1; }
}
"""


def _ensure_lang(soup: BeautifulSoup) -> None:
    html_tag = soup.find("html")
    if html_tag is not None and not html_tag.has_attr("lang"):
        html_tag["lang"] = "en"


def _ensure_viewport(soup: BeautifulSoup) -> None:
    if soup.find("meta", attrs={"name": "viewport"}):
        return
    meta = soup.new_tag("meta")
    meta["name"] = "viewport"
    meta["content"] = "width=device-width, initial-scale=1.0"
    _ensure_head(soup).append(meta)


def _ensure_style(soup: BeautifulSoup) -> None:
    """Inject DEFAULT_STYLESHEET only when the page has no <style> of its own."""
    if soup.find("style") is None:
        style = soup.new_tag("style")
        style.string = DEFAULT_STYLESHEET
        _ensure_head(soup).append(style)


def _ensure_heading_hierarchy(soup: BeautifulSoup) -> None:
    # 1) promote the first h2-h6 to h1 when no h1 exists
    if soup.find("h1") is None:
        for level in range(2, 7):
            tag = soup.find(f"h{level}")
            if tag is not None:
                tag.name = "h1"
                break
    # 2) promote h3 -> h2 while fewer than 2 h2s
    while len(soup.find_all("h2")) < 2:
        h3 = soup.find("h3")
        if h3 is None:
            break
        h3.name = "h2"


def _remove_flash(soup: BeautifulSoup) -> None:
    for obj in soup.find_all("object"):
        if "application/x-shockwave-flash" in (obj.get("type") or "").lower():
            obj.replace_with(Comment(" Removed Flash placeholder "))


def _ensure_basics_into(soup: BeautifulSoup) -> None:
    """Apply the structural (content-free) fixes: lang / viewport / heading hierarchy / remove Flash / style."""
    _ensure_lang(soup)
    _ensure_viewport(soup)
    _ensure_heading_hierarchy(soup)
    _remove_flash(soup)
    _ensure_style(soup)


def ensure_basics(html: str) -> str:
    """Apply only the structural fixes (lang / viewport / h1 / remove Flash), never adding content."""
    soup = _parse(html)
    _ensure_basics_into(soup)
    return str(soup)


def _page_title(soup: BeautifulSoup) -> str:
    """Extract the page title from <title> or the first <h1> ('' when neither exists)."""
    for selector in ("title", "h1"):
        tag = soup.find(selector)
        if tag is not None:
            t = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).replace('"', "'").strip()
            if t:
                return t
    return ""


def _ensure_head_meta(soup: BeautifulSoup, title: str) -> None:
    """Add meta description + WebSite JSON-LD (derived from the title) when missing."""
    if soup.find("meta", attrs={"name": "description"}) is None:
        meta = soup.new_tag("meta")
        meta["name"] = "description"
        meta["content"] = title
        _ensure_head(soup).append(meta)
    if soup.find("script", attrs={"type": "application/ld+json"}) is None:
        ld = soup.new_tag("script")
        ld["type"] = "application/ld+json"
        ld.string = json.dumps(
            {"@context": "https://schema.org", "@type": "WebSite", "name": title},
            ensure_ascii=False)
        _ensure_head(soup).append(ld)


def _jsonld_is_semantic(soup: BeautifulSoup) -> bool:
    """True when at least one JSON-LD block carries a top-level @context and @type.

    Mirrors evaluate_result's `jsonld_valid_and_semantic` criterion: an @graph-only block
    (which defers @type to nested nodes) parses as valid JSON but is NOT semantic.
    """
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = (script.string or script.get_text()).strip()
        try:
            d = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(d, dict) and d.get("@context") and d.get("@type"):
            return True
    return False


def _ensure_jsonld_semantic(soup: BeautifulSoup, title: str, url: str | None = None) -> None:
    """Replace a degenerate JSON-LD (e.g. @graph-only, no top-level @type) with a flat WebSite.

    The LLM sometimes emits a valid-but-not-semantic block like {"@context", "@graph": [...]},
    which passes a JSON parse check but fails the evaluator's semantic check. When no block is
    semantic, overwrite the first block with a flat WebSite derived from the real title.
    """
    if _jsonld_is_semantic(soup):
        return
    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    if not scripts:
        ld = soup.new_tag("script")
        ld["type"] = "application/ld+json"
        _ensure_head(soup).append(ld)
        scripts = [ld]
    obj = {"@context": "https://schema.org", "@type": "WebSite", "name": title}
    if url:
        obj["url"] = url
    scripts[0].string = json.dumps(obj, ensure_ascii=False)
    print("[warn] JSON-LD had no top-level @type (e.g. @graph-only); replaced with a flat WebSite",
          file=sys.stderr)


def _ensure_h1(soup: BeautifulSoup, title: str) -> None:
    """Add an <h1> derived from the title when the page has no h1 at all."""
    if soup.find("h1") is None:
        body = soup.find("body")
        if body is not None:
            h1 = soup.new_tag("h1")
            h1.string = title
            body.insert(0, h1)


def finalize(html: str, url: str | None = None) -> str:
    """Unified fallback: structural items + generic meta + fallback h1 (only when a real title/heading exists).

    Also used as a safety net after LLM output. Never fabricates a title: when the page has no
    <title> and no heading at all, no meta description / JSON-LD / h1 is invented.
    """
    soup = _parse(html)
    _ensure_basics_into(soup)
    title = _page_title(soup)
    if not title:
        print("[warn] no title or heading found; skipping meta description / JSON-LD / h1 "
              "(refusing to fabricate content)", file=sys.stderr)
        return str(soup)
    _ensure_head_meta(soup, title)
    _ensure_jsonld_semantic(soup, title, url)
    _ensure_h1(soup, title)
    return str(soup)


def optimize_with_rules(source: str) -> str:
    """Full no-LLM fallback (equivalent to finalize(source))."""
    return finalize(source)


# ============ URL constraints: keep the original set + strip fictional resources ============

def strip_fictional_resources(source: str, html: str) -> str:
    """Remove static-resource references not present in the source (stylesheets / external scripts)."""
    src_urls = _urls(source)
    soup = _parse(html)
    for tag in list(soup.find_all(["link", "script"])):
        url = None
        if tag.name == "link":
            rel = tag.get("rel") or []
            if isinstance(rel, str):
                rel = [rel]
            if "stylesheet" in [r.lower() for r in rel]:
                url = tag.get("href")
        elif tag.name == "script":
            url = tag.get("src")
        if url and url not in src_urls:
            tag.decompose()
    return str(soup)


def strip_fabricated_content(source: str, html: str) -> str:
    """Remove "content elements" that do not exist in the source:
    1. fabricated <a> links (href not in the source URL set)
    2. whole <nav> / <footer> blocks not present in the source
    """
    src_urls = _urls(source)
    soup = _parse(html)

    # 1) remove fabricated links (an <a> whose href is not in the source)
    for a in list(soup.find_all("a")):
        href = a.get("href")
        if href and href not in src_urls:
            a.decompose()

    # 2) remove whole nav / footer blocks not present in the source
    src_soup = _parse(source)
    if src_soup.find("nav") is None:
        for nav in soup.find_all("nav"):
            nav.decompose()
    if src_soup.find("footer") is None:
        for footer in soup.find_all("footer"):
            footer.decompose()

    return str(soup)


# ============ Self-check ============

def _json_ld_valid(html: str) -> bool:
    """True when every JSON-LD <script> block parses as valid JSON (or there are none)."""
    soup = _parse(html)
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = (script.string or script.get_text()).strip()
        try:
            json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return False
    return True


CHECKS = [
    ('viewport', 'name="viewport"'),
    ('meta description', 'name="description"'),
    ('JSON-LD', 'application/ld+json'),
    ('h1 heading', '<h1'),
    ('remove Flash', 'x-shockwave-flash'),
]


def _imgs_missing_alt(soup: BeautifulSoup) -> int:
    return sum(1 for img in soup.find_all("img") if not img.has_attr("alt"))


def self_check(source: str, html: str) -> int:
    failed = 0
    print("[self-check] key items of the optimized page:")

    out_soup = _parse(html)
    src_urls = _urls(source)
    out_urls = _urls(html)

    lang_ok = out_soup.html is not None and out_soup.html.has_attr("lang")
    print(f"  {'[OK]' if lang_ok else '[X]'} lang attribute")
    if not lang_ok:
        failed += 1

    for name, needle in CHECKS:
        # the Flash item should be ABSENT
        ok = (needle not in html) if name == "remove Flash" else (needle in html)
        print(f"  {'[OK]' if ok else '[X]'} {name}")
        if not ok:
            failed += 1

    ld_ok = _json_ld_valid(html)
    print(f"  {'[OK]' if ld_ok else '[X]'} JSON-LD parses as valid JSON")
    if not ld_ok:
        failed += 1

    print(f"  [i]  images missing alt: {_imgs_missing_alt(_parse(source))} -> {_imgs_missing_alt(out_soup)}")

    # original URL set preserved
    missing = sorted(u for u in src_urls if u not in out_urls)
    print(f"  {'[OK]' if not missing else '[X]'} all original URLs preserved ({len(src_urls)} total)")
    for u in missing:
        print(f"     missing: {u}")
    if missing:
        failed += 1

    # fictional resources (new .css/.js references)
    fictional = sorted(u for u in out_urls - src_urls if u.lower().endswith((".css", ".js")))
    print(f"  {'[OK]' if not fictional else '[X]'} no new fabricated resources (css/js)")
    for u in fictional:
        print(f"     added: {u}")
    if fictional:
        failed += 1

    # new links (every <a> href must come from the source)
    a_hrefs = {a.get("href") for a in out_soup.find_all("a") if a.get("href")}
    new_links = sorted(h for h in a_hrefs if h not in src_urls)
    print(f"  {'[OK]' if not new_links else '[X]'} no new links (<a>)")
    for h in new_links:
        print(f"     added: {h}")
    if new_links:
        failed += 1

    return failed


# ============ Fetching ============

def fetch_url(url: str) -> str:
    """Fetch a URL's HTML: prefer Playwright to render JS, fall back to urllib when unavailable/failed."""
    html = _fetch_playwright(url)
    if html is not None:
        return html
    print("[info] Playwright unavailable or failed, falling back to urllib ...", file=sys.stderr)
    return _fetch_urllib(url)


def _fetch_playwright(url: str) -> str | None:
    """Render JS with a headless browser and return the final HTML; None if unavailable/failed."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0 Safari/537.36")
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)  # wait for first-paint JS rendering
                return page.content()
            finally:
                browser.close()
    except Exception:
        return None


def _fetch_urllib(url: str) -> str:
    """urllib fallback (decode per the response header; utf-8 replace on failure)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (AgenticPage Optimizer)",
                      "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace")


def main():
    parser = argparse.ArgumentParser(description="Read source HTML + prompt and generate an optimized page")
    parser.add_argument("source", nargs="?", default=str(BASE_DIR / "test.html"),
                        help="source HTML path (default ./test.html)")
    parser.add_argument("-p", "--prompt", default=str(DEFAULT_PROMPT),
                        help="optimization instruction path (default ./prompt.txt)")
    parser.add_argument("-o", "--output", help="output HTML path (default <source>_optimized.html)")
    parser.add_argument("--no-llm", action="store_true", help="force the rule engine")
    parser.add_argument("--debug", action="store_true", help="print traceback on failure")
    args = parser.parse_args()

    if is_url(args.source):
        print(f"[info] fetching URL: {args.source}")
        source = fetch_url(args.source)
        out_path = Path(args.output) if args.output else \
            OUTPUT_DIR / (url_to_filename(args.source) + "_optimized.html")
    else:
        src_path = Path(args.source)
        if not src_path.exists():
            print(f"[error] source file not found: {src_path}", file=sys.stderr)
            sys.exit(1)
        source = src_path.read_text(encoding="utf-8")
        out_path = Path(args.output) if args.output else \
            OUTPUT_DIR / (src_path.stem + "_optimized.html")

    prompt = ""
    if Path(args.prompt).exists():
        prompt = Path(args.prompt).read_text(encoding="utf-8")
    else:
        print(f"[warning] prompt file not found: {args.prompt}; the LLM will lack a14y findings", file=sys.stderr)

    target_url = args.source if is_url(args.source) else None

    result = None
    if not args.no_llm:
        if get_config("generate").api_key:
            # key configured: call the LLM, abort on failure rather than silently degrading
            try:
                result = optimize_with_llm(source, target_url, prompt)
            except Exception as e:  # noqa: BLE001
                print(f"[error] LLM optimization failed (key configured, aborting): {e}", file=sys.stderr)
                if args.debug:
                    import traceback
                    traceback.print_exc()
                sys.exit(1)

    if not result:
        print("[optimize] using the rule engine ...")
        result = optimize_with_rules(source)
    else:
        # the LLM only replaced <head>; fill structural items and fallback meta/h1 as a safety net
        result = finalize(result, target_url)

    result = strip_fictional_resources(source, result)
    result = strip_fabricated_content(source, result)

    # Safety net: fall back to the rule engine if any original URL was lost.
    lost = _urls(source) - _urls(result)
    if lost:
        print(f"[warning] output lost {len(lost)} original URLs, switching to the rule engine (keeps all links) ...",
              file=sys.stderr)
        result = optimize_with_rules(source)
        result = strip_fictional_resources(source, result)
        result = strip_fabricated_content(source, result)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result + "\n", encoding="utf-8")
    print(f"[output] optimized page written to {out_path}")

    failed = self_check(source, result)
    if failed:
        print(f"[self-check] {failed} item(s) failed", file=sys.stderr)
        sys.exit(1)
    print("[self-check] all passed")


if __name__ == "__main__":
    main()
