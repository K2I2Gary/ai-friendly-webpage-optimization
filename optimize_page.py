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

from common import console_utf8, extract_urls, is_url, url_to_filename
from llm import chat, get_config

# Windows console defaults to GBK; force stdout/stderr to UTF-8
console_utf8()

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
    (link / script / style / base) and charset (if the new head lacks one)."""
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
    for tag in list(old_head.find_all(["link", "script", "style", "base"])):
        new_head.append(tag)
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
        if unescape(img.get("src") or "") == unescape(src) and not img.has_attr("alt"):
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
    """Apply the structural (content-free) fixes: lang / viewport / heading hierarchy / remove Flash."""
    _ensure_lang(soup)
    _ensure_viewport(soup)
    _ensure_heading_hierarchy(soup)
    _remove_flash(soup)


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


def _ensure_h1(soup: BeautifulSoup, title: str) -> None:
    """Add an <h1> derived from the title when the page has no h1 at all."""
    if soup.find("h1") is None:
        body = soup.find("body")
        if body is not None:
            h1 = soup.new_tag("h1")
            h1.string = title
            body.insert(0, h1)


def finalize(html: str) -> str:
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
        if url and unescape(url) not in src_urls:
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
        if href is not None and unescape(href) not in src_urls:
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
    print(f"  {'✅' if lang_ok else '❌'} lang attribute")
    if not lang_ok:
        failed += 1

    for name, needle in CHECKS:
        # the Flash item should be ABSENT
        ok = (needle not in html) if name == "remove Flash" else (needle in html)
        print(f"  {'✅' if ok else '❌'} {name}")
        if not ok:
            failed += 1

    ld_ok = _json_ld_valid(html)
    print(f"  {'✅' if ld_ok else '❌'} JSON-LD parses as valid JSON")
    if not ld_ok:
        failed += 1

    print(f"  ℹ️  images missing alt: {_imgs_missing_alt(_parse(source))} -> {_imgs_missing_alt(out_soup)}")

    # original URL set preserved
    missing = sorted(u for u in src_urls if u not in out_urls)
    print(f"  {'✅' if not missing else '❌'} all original URLs preserved ({len(src_urls)} total)")
    for u in missing:
        print(f"     missing: {u}")
    if missing:
        failed += 1

    # fictional resources (new .css/.js references)
    fictional = sorted(u for u in out_urls - src_urls if u.lower().endswith((".css", ".js")))
    print(f"  {'✅' if not fictional else '❌'} no new fabricated resources (css/js)")
    for u in fictional:
        print(f"     added: {u}")
    if fictional:
        failed += 1

    # new links (every <a> href must come from the source)
    a_hrefs = {a.get("href") for a in out_soup.find_all("a") if a.get("href")}
    new_links = sorted(h for h in a_hrefs if h not in src_urls)
    print(f"  {'✅' if not new_links else '❌'} no new links (<a>)")
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
        # the LLM only replaced <head>; fill structural items and fallback meta/h1 as a safety net
        result = finalize(result)

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

    out_path.write_text(result + "\n", encoding="utf-8")
    print(f"[output] optimized page written to {out_path}")

    failed = self_check(source, result)
    if failed:
        print(f"[self-check] {failed} item(s) failed", file=sys.stderr)
        sys.exit(1)
    print("[self-check] all passed")


if __name__ == "__main__":
    main()
