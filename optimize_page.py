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
  - The body is kept verbatim, so URL references (img src, link href, iframe src, ...) are never lost.
  - The LLM only rewrites the <head> (title / description / og / JSON-LD / viewport / lang);
    structural fixes such as lang / h1 / removing Flash are handled by the rule engine.

Usage:
    python optimize_page.py test.html             # local file -> test_optimized.html
    python optimize_page.py https://example.com   # URL (auto-fetched) -> *_optimized.html
    python optimize_page.py test.html -o out.html
"""

import json
import os
import re
import sys
import argparse
import urllib.request
from pathlib import Path

from llm import chat, get_config

# Windows console defaults to GBK; force stdout/stderr to UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT = BASE_DIR / "prompt.txt"

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

Then output a line "STYLE:" followed by a single <style>...</style> block with clean,
modern, responsive CSS that improves the page's typography, spacing, colors and layout WITHOUT changing
any content. Prefer a small design system: CSS variables for a readable color palette, a comfortable
font stack, generous line-height, a max-width container for the main text, and sensible styles for
headings / links / images / lists / tables / forms / code. Do NOT rewrite any text or links; only add
CSS. Keep it self-contained.

Then output a single line "ALTS:" followed by a JSON array of alt-text suggestions
for images that currently lack a meaningful alt attribute (derive each from its context / surrounding
text; use the page's language). Use the exact src value as it appears in the page:
ALTS:
[{"src": "<exact img src>", "alt": "<concise alt text>"}]
Include at most 15 images; if none need alt, output ALTS: []. Do not add anything else after the array.
"""


def _build_head_prompt(source: str, url: str | None, prompt: str = "") -> str:
    """Build a compact context for the LLM: original <head> + body text summary + page URL + a14y findings."""
    head = ""
    m = re.search(r"<head[^>]*>(.*?)</head>", source, flags=re.I | re.S)
    if m:
        head = m.group(0)

    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", source, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:2500]

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


def _merge_head(source: str, new_head: str) -> str:
    """Merge the LLM's <head> into the source page while preserving the source head's resource tags
    (link / script / style / base) so stylesheet / script / icon URLs are not lost. Also carry over
    the charset when the source has one and the new head does not, to avoid encoding issues."""
    sm = re.search(r"<head[^>]*>(.*?)</head>", source, flags=re.I | re.S)
    if not sm:
        return re.sub(r"(<html[^>]*>)", lambda mm: mm.group(1) + new_head, source,
                      count=1, flags=re.I)

    old_inner = sm.group(1)
    keep = re.findall(
        r'<link\b[^>]*>|<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>|<base\b[^>]*>',
        old_inner, flags=re.I | re.S)

    # charset: add it if the source has one and the new head does not
    if not re.search(r"<meta\b[^>]*\bcharset\b", new_head, flags=re.I):
        cm = re.search(r"<meta\b[^>]*\bcharset\b[^>]*>", old_inner, flags=re.I)
        if cm:
            keep.insert(0, cm.group(0))

    if keep:
        joined = "\n".join(keep)
        # Use a lambda replacement: `joined` may contain backslashes (CSS hacks / JS escapes),
        # which must not be interpreted as group references in a string replacement.
        new_head = re.sub(r"(</head>)", lambda m: joined + "\n" + m.group(1), new_head,
                          count=1, flags=re.I)

    return re.sub(r"<head[^>]*>.*?</head>", lambda _: new_head, source,
                  count=1, flags=re.I | re.S)


def _apply_lang(html: str, raw: str) -> str:
    """Parse LANG from the LLM output and set it on the root <html lang="...">; leave unchanged on failure."""
    m = re.search(r"LANG\s*:\s*([A-Za-z][A-Za-z0-9-]*)", raw)
    if not m:
        return html
    lang = m.group(1)

    def set_lang(hm):
        tag = hm.group(0)
        if re.search(r"\blang\s*=", tag, flags=re.I):
            return re.sub(r"\blang\s*=\s*[\"'][^\"']*[\"']",
                          'lang="' + lang + '"', tag, count=1, flags=re.I)
        return tag[:-1] + ' lang="' + lang + '"' + tag[-1:]
    return re.sub(r"<html\b[^>]*>", set_lang, html, count=1, flags=re.I)


def _apply_style(html: str, raw: str) -> str:
    """Extract the <style> from the LLM output and inject it at the end of the head (after the original
    resources), so the new styles take precedence."""
    m = re.search(r"STYLE\s*:\s*(<style\b[^>]*>.*?</style>)", raw, flags=re.I | re.S)
    if m:
        style = m.group(1)
    else:
        styles = re.findall(r"<style\b[^>]*>.*?</style>", raw, flags=re.I | re.S)
        style = styles[-1] if styles else ""
    if not style:
        return html
    if re.search(r"</head>", html, flags=re.I):
        return re.sub(r"(</head>)", lambda mm: style + "\n" + mm.group(1), html,
                      count=1, flags=re.I)
    return re.sub(r"(<body[^>]*>)", lambda mm: style + "\n" + mm.group(1), html,
                  count=1, flags=re.I)


def _apply_alts(html: str, raw: str) -> str:
    """Parse the ALTS array from the LLM output and inject alt text into <img> tags lacking alt.
    Return unchanged on failure."""
    m = re.search(r"ALTS\s*:\s*(\[.*?\])", raw, flags=re.S)
    if not m:
        return html
    try:
        alts = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return html
    for item in (alts if isinstance(alts, list) else []):
        if not isinstance(item, dict):
            continue
        src = item.get("src") or ""
        alt = item.get("alt") or ""
        if src and alt:
            html = _inject_alt(html, src, alt)
    return html


def _inject_alt(html: str, src: str, alt: str) -> str:
    """Inject alt into the first <img> whose src matches and which lacks alt (handles <img> and <img />)."""
    alt = alt.replace('"', "'")
    pat = re.compile(
        r'<img\b(?![^>]*\balt\s*=)[^>]*?\bsrc\s*=\s*["\']' + re.escape(src) + r'["\'][^>]*>',
        flags=re.I)

    def repl(m):
        tag = m.group(0)
        if tag.endswith('/>'):
            return tag[:-2].rstrip() + ' alt="' + alt + '" />'
        return tag[:-1].rstrip() + ' alt="' + alt + '">'
    return pat.sub(repl, html)


def optimize_with_llm(source: str, url: str | None = None, prompt: str = "") -> str | None:
    """Have the LLM produce only the new <head> metadata, then merge it back (body kept verbatim -> zero
    URL loss). Returns the merged full page, or None if the LLM returned no valid <head> (rule fallback)."""
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
    m = re.search(r"<head[^>]*>.*?</head>", raw, flags=re.I | re.S)
    if not m:
        return None
    merged = _merge_head(source, m.group(0))
    merged = _apply_lang(merged, raw)
    merged = _apply_style(merged, raw)
    return _apply_alts(merged, raw)


# ============ Rule fallback: deterministic minimal fixes ============
# ensure_basics: only fill "structural" items (lang / viewport / h1 / remove Flash), never adding content.
# optimize_with_rules: on top of ensure_basics, also add meta description / JSON-LD (no-LLM fallback).

def ensure_basics(html: str) -> str:
    # 1) lang
    html = re.sub(r"<html(?![^>]*\blang=)", '<html lang="en"', html, count=1)

    # 2) viewport
    if 'name="viewport"' not in html and "name='viewport'" not in html:
        html = re.sub(r"(<head[^>]*>)", r'\1\n    <meta name="viewport" '
                      'content="width=device-width, initial-scale=1.0">',
                      html, count=1)

    # 3) heading hierarchy: promote the first h2-h6 to h1; promote h3 to h2 while fewer than 2 h2s
    if "<h1" not in html:
        html = re.sub(r"<h([2-6])([^>]*)>(.*?)</h\1>", r"<h1\2>\3</h1>", html,
                      count=1, flags=re.S)
    while len(re.findall(r"<h2\b", html, flags=re.I)) < 2:
        new = re.sub(r"<h3([^>]*)>(.*?)</h3>", r"<h2\1>\2</h2>", html,
                     count=1, flags=re.S)
        if new == html:
            break
        html = new

    # 4) remove Flash objects
    html = re.sub(r"<object[^>]*type=[\"']application/x-shockwave-flash[\"'][^>]*>.*?</object>",
                  "<!-- Removed Flash placeholder -->", html, flags=re.S)

    return html


def _page_title(html: str) -> str:
    """Extract the page title from <title> or the first <h1> (used for fallback description / JSON-LD)."""
    for pat in (r"<title[^>]*>(.*?)</title>", r"<h1[^>]*>(.*?)</h1>"):
        m = re.search(pat, html, flags=re.I | re.S)
        if m:
            t = re.sub(r"<[^>]+>", "", m.group(1))
            t = re.sub(r"\s+", " ", t).replace('"', "'").strip()
            if t:
                return t
    return "Web page"


def ensure_head_meta(html: str, title: str) -> str:
    """Fill in meta description and WebSite JSON-LD (derived from title when missing)."""
    if 'name="description"' not in html and "name='description'" not in html:
        meta = '<meta name="description" content="' + title + '">'
        html = re.sub(r"(</head>)", lambda m: meta + "\n" + m.group(1),
                      html, count=1, flags=re.I)
    if "application/ld+json" not in html:
        ld = ('<script type="application/ld+json">\n'
              '{"@context": "https://schema.org", "@type": "WebSite", "name": '
              + json.dumps(title, ensure_ascii=False) + '}\n</script>')
        html = re.sub(r"(</head>)", lambda m: ld + "\n" + m.group(1),
                      html, count=1, flags=re.I)
    return html


def ensure_h1(html: str, title: str) -> str:
    """Add an <h1> derived from the title when the page has no headings at all."""
    if "<h1" not in html:
        html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + "<h1>" + title + "</h1>",
                      html, count=1, flags=re.I)
    return html


def finalize(html: str) -> str:
    """Unified fallback: structural items + generic meta + fallback h1. Also used as a safety net after LLM output."""
    html = ensure_basics(html)
    title = _page_title(html)
    html = ensure_head_meta(html, title)
    return ensure_h1(html, title)


def optimize_with_rules(source: str) -> str:
    """Full no-LLM fallback (equivalent to finalize(source))."""
    return finalize(source)


# ============ URL constraints: keep the original set + strip fictional resources ============

def extract_urls(html: str) -> set:
    """Extract all URLs referenced by href / src / action."""
    return set(re.findall(r'(?:href|src|action)\s*=\s*["\']([^"\']+)["\']', html))


def strip_fictional_resources(source: str, html: str) -> str:
    """Remove static-resource references not present in the source (stylesheets / external scripts)."""
    src_urls = extract_urls(source)
    # stylesheets: drop <link rel="stylesheet"> whose URL is not in the source
    html = re.sub(
        r'<link\b[^>]*rel=["\']stylesheet["\'][^>]*>',
        lambda m: m.group(0) if (extract_urls(m.group(0)) & src_urls) else "",
        html, flags=re.I,
    )
    # external scripts: drop <script src="..."></script> whose URL is not in the source
    html = re.sub(
        r'<script\b[^>]*\bsrc\s*=[^>]*>\s*</script>',
        lambda m: m.group(0) if (extract_urls(m.group(0)) & src_urls) else "",
        html, flags=re.I,
    )
    return html


def strip_fabricated_content(source: str, html: str) -> str:
    """Remove "content elements" that do not exist in the source:
    1. fabricated <a> links (href not in the source URL set)
    2. whole <nav> / <footer> blocks not present in the source
    """
    src_urls = extract_urls(source)

    # 1) remove fabricated links (whole <a>...</a>)
    def keep_a(m):
        tag = m.group(0)
        hrefs = extract_urls(tag)
        if not hrefs:  # keep <a> without href (e.g. <a name=...>)
            return tag
        return tag if all(h in src_urls for h in hrefs) else ""
    html = re.sub(r'<a\b[^>]*>.*?</a>', keep_a, html, flags=re.I | re.S)

    # 2) remove whole nav / footer blocks not present in the source
    if "<nav" not in source.lower():
        html = re.sub(r'<nav\b[^>]*>.*?</nav>', "", html, flags=re.I | re.S)
    if "<footer" not in source.lower():
        html = re.sub(r'<footer\b[^>]*>.*?</footer>', "", html, flags=re.I | re.S)

    return html


# ============ Self-check ============

CHECKS = [
    ('viewport', 'name="viewport"'),
    ('meta description', 'name="description"'),
    ('JSON-LD', 'application/ld+json'),
    ('h1 heading', '<h1'),
    ('remove Flash', 'x-shockwave-flash'),
]


def self_check(source: str, html: str) -> int:
    failed = 0
    print("[self-check] key items of the optimized page:")
    # lang: the root <html> must have a lang attribute (any value, following the page's actual language)
    lang_ok = bool(re.search(r"<html[^>]*\blang\s*=\s*[\"']", html, flags=re.I))
    print(f"  {'✅' if lang_ok else '❌'} lang attribute")
    if not lang_ok:
        failed += 1
    for name, needle in CHECKS:
        # the Flash item should be ABSENT
        ok = (needle not in html) if name == 'remove Flash' else (needle in html)
        print(f"  {'✅' if ok else '❌'} {name}")
        if not ok:
            failed += 1

    # original URL set preserved
    src_urls = extract_urls(source)
    out_urls = extract_urls(html)
    missing = sorted(u for u in src_urls if u not in out_urls)
    print(f"  {'✅' if not missing else '❌'} all original URLs preserved ({len(src_urls)} total)")
    for u in missing:
        print(f"     missing: {u}")
    if missing:
        failed += 1

    # fictional resources (new .css/.js references)
    fictional = sorted(u for u in out_urls - src_urls
                       if u.lower().endswith((".css", ".js")))
    print(f"  {'✅' if not fictional else '❌'} no new fabricated resources (css/js)")
    for u in fictional:
        print(f"     added: {u}")
    if fictional:
        failed += 1

    # new links (every <a> href must come from the source)
    a_hrefs = set(re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\']', html))
    new_links = sorted(h for h in a_hrefs if h not in src_urls)
    print(f"  {'✅' if not new_links else '❌'} no new links (<a>)")
    for h in new_links:
        print(f"     added: {h}")
    if new_links:
        failed += 1

    return failed


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


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


def url_to_filename(url: str) -> str:
    """Convert a URL into a safe local filename (without extension)."""
    u = url.split("#", 1)[0].split("?", 1)[0]
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"[^A-Za-z0-9._-]+", "_", u).strip("_")
    return u or "page"


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
            BASE_DIR / (url_to_filename(args.source) + "_optimized.html")
    else:
        src_path = Path(args.source)
        if not src_path.exists():
            print(f"[error] source file not found: {src_path}", file=sys.stderr)
            sys.exit(1)
        source = src_path.read_text(encoding="utf-8")
        out_path = Path(args.output) if args.output else \
            src_path.with_name(src_path.stem + "_optimized.html")
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
        # the LLM only replaced <head>; fill structural items and fallback meta/h1 (body verbatim -> zero URL loss)
        result = finalize(result)

    result = strip_fictional_resources(source, result)
    result = strip_fabricated_content(source, result)

    # Safety net: in theory the body is untouched so no URL is lost; fall back to the rule engine otherwise.
    lost = extract_urls(source) - extract_urls(result)
    if lost:
        print(f"[warning] output lost {len(lost)} original URLs, switching to the rule engine (keeps all links) ...",
              file=sys.stderr)
        result = optimize_with_rules(source)
        result = strip_fictional_resources(source, result)
        result = strip_fabricated_content(source, result)

    out_path.write_text(result + "\n", encoding="utf-8")
    print(f"[output] optimized page written to {out_path}")

    failed = self_check(source, result)
    if failed:
        print(f"[self-check] {failed} item(s) failed", file=sys.stderr)
        sys.exit(1)
    print("[self-check] all passed")


if __name__ == "__main__":
    main()
