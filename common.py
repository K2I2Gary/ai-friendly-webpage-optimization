#!/usr/bin/env python3
"""
Shared small utilities used across the workflow scripts (run / ai_eval / reflect / optimize).

Deliberately dependency-free (stdlib only) so it can be imported anywhere without pulling in
`openai` or risking a circular import.
"""

import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"


def console_utf8():
    """Force stdout/stderr to UTF-8 (Windows consoles default to GBK, garbling emoji / CJK)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", line_buffering=True)
        except Exception:
            pass


def is_url(value: str) -> bool:
    """True when `value` is an http(s) URL."""
    return value.startswith("http://") or value.startswith("https://")


_URL_ATTRS = re.compile(r'(?:href|src|action)\s*=\s*["\']([^"\']+)["\']', re.I)


def extract_urls(html: str) -> set:
    """Return the set of URLs referenced by href / src / action (single- or double-quoted)."""
    return set(_URL_ATTRS.findall(html))


def url_to_filename(url: str) -> str:
    """Convert a URL into a safe local filename (no extension)."""
    u = url.split("#", 1)[0].split("?", 1)[0]
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"[^A-Za-z0-9._-]+", "_", u).strip("_")
    return u or "page"
