#!/usr/bin/env python3
"""
Generate site-level AI-readability files for a single-page site
----------------------------------------------------------------
Reads an HTML page and emits the files AI crawlers look for at the site root:

  robots.txt    allow AI bots + point to sitemap
  llms.txt      a plain-text index for LLM ingestion
  sitemap.xml   machine-readable URL list (urlset + lastmod)
  sitemap.md    human/AI-readable sitemap
  AGENTS.md     agent skill file (overview + usage)
  <page>.md     Markdown mirror of the page (frontmatter + Sitemap section)

All rule-based (no LLM). Content is derived from the page's own title / description /
headings, never fabricated: a page with no <title> is skipped.

Usage:
    python generate_site.py test_optimized.html
    python generate_site.py test_optimized.html --base-url https://example.com/
    python generate_site.py test_optimized.html --out-dir ./site
"""

import argparse
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from common import console_utf8

console_utf8()

BASE_DIR = Path(__file__).resolve().parent


def _parse(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _extract_meta(soup: BeautifulSoup) -> dict:
    """Extract title / description / lang / canonical from the page ('' when absent)."""
    meta = {"title": "", "description": "", "lang": "", "canonical": ""}
    t = soup.find("title")
    if t is not None:
        meta["title"] = t.get_text(" ", strip=True)
    m = soup.find("meta", attrs={"name": "description"})
    if m is not None and m.get("content"):
        meta["description"] = m["content"].strip()
    html_tag = soup.find("html")
    if html_tag is not None:
        meta["lang"] = html_tag.get("lang", "")
    c = soup.find("link", attrs={"rel": "canonical"})
    if c is not None:
        meta["canonical"] = c.get("href", "")
    return meta


def _inline(el) -> str:
    """Render inline content (text + links) as markdown."""
    parts = []
    for node in el.children:
        name = getattr(node, "name", None)
        if name is None:
            txt = str(node).strip()
            if txt:
                parts.append(txt)
        elif name == "a":
            href = node.get("href", "")
            txt = node.get_text(" ", strip=True)
            parts.append(f"[{txt}]({href})" if href else txt)
        elif name in ("strong", "b", "em", "i", "code", "span", "small", "sup", "sub"):
            parts.append(_inline(node))
        else:
            parts.append(node.get_text(" ", strip=True))
    return " ".join(parts).strip()


def _html_to_markdown(soup: BeautifulSoup) -> str:
    """Convert the page body to a basic markdown (headings / paragraphs / lists / code)."""
    body = soup.find("body") or soup
    out = []
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre"]):
        name = el.name
        if name.startswith("h") and name[1:].isdigit():
            out.append("\n" + "#" * int(name[1]) + " " + el.get_text(" ", strip=True))
        elif name == "p":
            txt = _inline(el)
            if txt:
                out.append(txt)
        elif name == "li":
            txt = _inline(el)
            if txt:
                out.append("- " + txt)
        elif name == "pre":
            out.append("\n```\n" + el.get_text().strip() + "\n```")
    return "\n".join(line for line in out if line.strip())


def _render_robots(base: str) -> str:
    return "User-agent: *\nAllow: /\n\nSitemap: {base}sitemap.xml\n".format(base=base)


def _render_llms_txt(meta: dict, base: str, page_md: str) -> str:
    title = meta["title"] or "Site"
    desc = meta["description"] or "Site index for AI agents."
    return (
        f"# {title}\n"
        f"> {desc}\n"
        "\n"
        "## Pages\n"
        f"- [{title}]({base}{page_md}): {desc}\n"
    )


def _render_sitemap_xml(meta: dict, base: str, page_html: str, page_md: str) -> str:
    d = date.today().isoformat()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{base}{page_html}</loc><lastmod>{d}</lastmod></url>\n"
        f"  <url><loc>{base}{page_md}</loc><lastmod>{d}</lastmod></url>\n"
        "</urlset>\n"
    )


def _render_sitemap_md(meta: dict, base: str, page_html: str, page_md: str) -> str:
    title = meta["title"] or "Page"
    return (
        "# Sitemap\n"
        "\n"
        "## Pages\n"
        f"- [{title}]({base}{page_html})\n"
        f"- [{title} (Markdown)]({base}{page_md})\n"
    )


def _render_agents_md(meta: dict, base: str, page_html: str, page_md: str) -> str:
    # `agents-md.has-min-sections` requires >= 2 of install/config/usage, so use Install + Usage.
    return (
        "# AGENTS.md\n"
        "\n"
        "## Install\n"
        "Serve this directory with any static HTTP server.\n"
        "\n"
        "## Usage\n"
        f"Fetch {base}{page_html} for the HTML page, {base}{page_md} for the Markdown "
        f"mirror, {base}llms.txt for the entry index.\n"
    )


def _render_md_mirror(meta: dict, body_md: str, base: str, page_html: str, page_md: str) -> str:
    title = meta["title"] or "Page"
    desc = meta["description"] or ""
    d = date.today().isoformat()
    frontmatter = (
        "---\n"
        f"title: {title}\n"
        f"description: {desc}\n"
        f"dateModified: {d}\n"
        f"canonical: {base}{page_html}\n"
        "---\n"
    )
    sitemap = (
        "\n\n## Sitemap\n"
        f"- [{title}]({base}{page_html})\n"
        f"- [{title} (Markdown)]({base}{page_md})\n"
    )
    return frontmatter + "\n# " + title + "\n\n" + body_md + sitemap


def generate(page_path: Path, base_url: str, out_dir: Path) -> list[Path]:
    """Generate the six site files; returns the written paths ([] when the page has no title)."""
    html = page_path.read_text(encoding="utf-8", errors="replace")
    soup = _parse(html)
    meta = _extract_meta(soup)
    if not meta["title"]:
        print("[warn] page has no <title>; refusing to fabricate site files", file=sys.stderr)
        return []

    page_html = page_path.name
    page_md = page_path.stem + ".md"
    base = base_url.rstrip("/") + "/"

    files = {
        "robots.txt": _render_robots(base),
        "llms.txt": _render_llms_txt(meta, base, page_md),
        "sitemap.xml": _render_sitemap_xml(meta, base, page_html, page_md),
        "sitemap.md": _render_sitemap_md(meta, base, page_html, page_md),
        "AGENTS.md": _render_agents_md(meta, base, page_html, page_md),
        page_md: _render_md_mirror(meta, _html_to_markdown(soup), base, page_html, page_md),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main():
    parser = argparse.ArgumentParser(description="Generate site-level AI-readability files")
    parser.add_argument("page", help="the HTML page to generate site files for")
    parser.add_argument("--base-url", default="",
                        help="site base URL (default: from canonical link, else http://localhost/)")
    parser.add_argument("--out-dir", default="", help="output dir (default: the page's directory)")
    args = parser.parse_args()

    page_path = Path(args.page)
    if not page_path.exists():
        print(f"[error] page not found: {page_path}", file=sys.stderr)
        sys.exit(1)

    if args.base_url:
        base = args.base_url
    else:
        soup = _parse(page_path.read_text(encoding="utf-8", errors="replace"))
        canonical = _extract_meta(soup)["canonical"]
        if canonical and "://" in canonical:
            p = urlparse(canonical)
            base = f"{p.scheme}://{p.netloc}/"
        else:
            base = "http://localhost/"

    out_dir = Path(args.out_dir) if args.out_dir else page_path.parent
    written = generate(page_path, base, out_dir)
    if not written:
        sys.exit(1)
    print(f"[output] {len(written)} site files written to {out_dir}:")
    for p in written:
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
