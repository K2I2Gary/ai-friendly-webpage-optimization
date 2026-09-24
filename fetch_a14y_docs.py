#!/usr/bin/env python3
"""
Crawl the official a14y scorecard docs into knowledge/docs/<id>.md
-------------------------------------------------------------------
For every check id in knowledge/catalog.json, fetch
https://a14y.dev/scorecards/<version>/checks/<id>/ and distill the page into a compact
markdown reference: name, description, "How the check decides", "How to implement it"
(with Pass/Fail snippets) and references. Written to knowledge/docs/<id>.md.

This seeds the RAG knowledge base with authoritative, versioned reference material that
complements the hand-curated knowledge/checks/*.md recipes.

Usage:
    python fetch_a14y_docs.py               # fetch all checks in catalog.json
    python fetch_a14y_docs.py --ids html.json-ld llms-txt.exists
    python fetch_a14y_docs.py --limit 5     # fetch only the first N (smoke test)
    python fetch_a14y_docs.py --force       # overwrite existing docs/<id>.md
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

from common import console_utf8

console_utf8()

BASE_DIR = Path(__file__).resolve().parent
CATALOG_FILE = BASE_DIR / "knowledge" / "catalog.json"
DOCS_DIR = BASE_DIR / "knowledge" / "docs"

USER_AGENT = "Mozilla/5.0 (a14y-docs-crawler)"


def load_catalog() -> dict:
    if CATALOG_FILE.exists():
        try:
            return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"scorecardVersion": "0.2.0", "checks": {}}


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace")


def _render(el, out: list[str]) -> None:
    """Append markdown for `el` (and its relevant descendants) to `out`, in document order."""
    name = getattr(el, "name", None)
    if name is None:
        return
    if name == "h3":
        out.append("\n### " + el.get_text(" ", strip=True))
    elif name == "p":
        out.append(el.get_text(" ", strip=True))
    elif name == "pre":
        out.append("\n```\n" + el.get_text().strip() + "\n```")
    elif name in ("ul", "ol"):
        for li in el.find_all("li", recursive=False):
            out.append("- " + li.get_text(" ", strip=True))
    elif name in ("div", "section", "article", "main", "body", "span"):
        for child in el.children:
            if getattr(child, "name", None):
                _render(child, out)


def parse_doc(html: str, check_id: str) -> str:
    """Distill a docs page into compact markdown."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup

    h1 = main.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else check_id

    # description = lead paragraphs that are not the "back to scorecard" breadcrumb link
    desc_parts = []
    for p in main.find_all("p", class_="lead"):
        link = p.find("a")
        if link and "scorecards" in (link.get("href") or ""):
            continue
        desc_parts.append(p.get_text(" ", strip=True))
    description = " ".join(d for d in desc_parts if d)

    chips = [c.get_text(" ", strip=True) for c in main.select(".meta-row .chip")]

    lines = [f"# {name}"]
    if description:
        lines.append("")
        lines.append(f"> {description}")
    if chips:
        lines.append("")
        lines.append(" | ".join(f"`{c}`" for c in chips))

    for h2 in main.find_all("h2"):
        title = h2.get_text(" ", strip=True)
        body: list[str] = []
        el = h2.find_next_sibling()
        while el is not None and getattr(el, "name", None) != "h2":
            _render(el, body)
            el = el.find_next_sibling()
        if body:
            lines.append(f"\n## {title}")
            lines.extend(line for line in body if line.strip())

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main():
    parser = argparse.ArgumentParser(description="Crawl a14y check docs into knowledge/docs/")
    parser.add_argument("--ids", nargs="*", help="only these check ids (default: all in catalog)")
    parser.add_argument("--limit", type=int, default=0, help="fetch at most N checks")
    parser.add_argument("--force", action="store_true", help="overwrite existing docs/<id>.md")
    parser.add_argument("--delay", type=float, default=0.3, help="seconds between requests")
    args = parser.parse_args()

    catalog = load_catalog()
    version = catalog.get("scorecardVersion", "0.2.0")
    checks = catalog.get("checks", {})

    ids = args.ids if args.ids else list(checks.keys())
    if args.limit:
        ids = ids[: args.limit]

    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    ok = fail = skipped = 0
    for cid in ids:
        out_path = DOCS_DIR / f"{cid}.md"
        if out_path.exists() and not args.force:
            skipped += 1
            continue
        url = f"https://a14y.dev/scorecards/{version}/checks/{cid}/"
        try:
            md = parse_doc(fetch(url), cid)
            if not md.strip():
                raise ValueError("empty extraction")
            out_path.write_text(md + "\n", encoding="utf-8")
            ok += 1
            print(f"[ok]   {cid}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[fail] {cid}: {e}", file=sys.stderr)
        time.sleep(args.delay)

    print(f"\nDone: {ok} fetched, {skipped} skipped, {fail} failed "
          f"(version {version}, out dir {DOCS_DIR})")


if __name__ == "__main__":
    main()
