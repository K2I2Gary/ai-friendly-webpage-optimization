#!/usr/bin/env python3
"""
eval.txt reflection engine
--------------------------
Reads eval.txt produced by ai_eval.py and "reflects" on the results:
  1. Parses the raw scorecard JSON + the initial LLM analysis + metadata (URL / score / grade).
  2. Handles only fail / warn checks (na items are excluded, never criticized) and distills
     actionable fixes.
  3. Produces an executable, verifiable "page optimization instruction" (prompt) into prompt.txt,
     to be applied by the next agent.

Prefers the LLM for reflection (same API as ai_eval.py); falls back to the rule engine when
there is no key or the LLM call fails, guaranteeing prompt.txt is always produced.

Usage:
    python reflect.py                     # read eval.txt -> write prompt.txt
    python reflect.py eval.txt -o out.txt
    python reflect.py --no-llm            # force the rule engine (offline)
"""

import json
import sys
import argparse
import traceback
from pathlib import Path

from common import OUTPUT_DIR, console_utf8
from llm import chat, get_config
from rag import rank_examples, read_recipe, retrieve_for_checks

# Windows console defaults to GBK; force stdout/stderr to UTF-8 to avoid garbled output
console_utf8()

# ============ Paths & config ============
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = OUTPUT_DIR / "eval.txt"
DEFAULT_OUTPUT = OUTPUT_DIR / "prompt.txt"

STATUS_MARK = {"pass": "[OK]", "fail": "[X]", "warn": "[!]", "error": "[!!]", "na": "—"}


# ============ Step 1: parse eval.txt ============

def parse_eval_text(text: str):
    """Extract (scorecard dict, analysis str) from eval.txt.

    Returns None if parsing fails.
    """
    json_marker = "Raw scorecard data"
    analysis_marker = "LLM analysis"

    # 1) raw scorecard JSON: the first '{' after json_marker
    scorecard = {}
    idx = text.rfind(json_marker)
    if idx != -1:
        brace = text.find("{", idx)
        if brace != -1:
            try:
                scorecard = json.loads(text[brace:])
            except json.JSONDecodeError as e:
                print(f"[warning] scorecard JSON parse failed: {e}", file=sys.stderr)

    # 2) LLM analysis text: between analysis_marker and json_marker
    analysis = ""
    a_start = text.find(analysis_marker)
    a_end = text.find(json_marker)
    if a_start != -1:
        analysis = text[a_start:a_end if a_end != -1 else len(text)].strip()

    return scorecard, analysis


def collect_checks(scorecard: dict):
    """Merge siteChecks and pages[].checks into a single list of checks."""
    checks = list(scorecard.get("siteChecks", []))
    for page in scorecard.get("pages", []):
        checks.extend(page.get("checks", []))
    return checks


def build_digest(scorecard: dict, analysis: str) -> str:
    """Compress the scorecard into a concise text for the reflection model (highlight fail/warn only)."""
    summary = scorecard.get("summary", {})
    url = scorecard.get("url") or scorecard.get("baseUrl") or "N/A"
    lines = [
        f"Target URL: {url}",
        f"Total score: {summary.get('score', 'N/A')} / 100",
        f"Stats: pass={summary.get('passed')} fail={summary.get('failed')} "
        f"warn={summary.get('warned')} error={summary.get('errored')} "
        f"na={summary.get('na')} applicable={summary.get('applicable')}",
        "",
        "--- Failed / warning checks ---",
    ]

    fails = [c for c in collect_checks(scorecard) if c.get("status") == "fail"]
    warns = [c for c in collect_checks(scorecard) if c.get("status") == "warn"]
    errors = [c for c in collect_checks(scorecard) if c.get("status") == "error"]

    for c in fails:
        lines.append(
            f"{STATUS_MARK.get(c.get('status'), '?')} [{c.get('group')}] "
            f"{c.get('id')} — {c.get('name')} | {c.get('message')}"
        )
    for c in warns + errors:
        lines.append(
            f"{STATUS_MARK.get(c.get('status'), '?')} [{c.get('group')}] "
            f"{c.get('id')} — {c.get('name')} | {c.get('message')}"
        )
    if not fails and not warns and not errors:
        lines.append("(no failed/warning items)")

    if analysis:
        lines.append("")
        lines.append("--- Initial LLM analysis (for reference) ---")
        lines.append(analysis)

    return "\n".join(lines)


# ============ Step 2: reflection system prompt ============

REFLECT_SYSTEM_PROMPT = """You are the "Reflection Engine" for web AI-readability optimization.

An upstream a14y evaluator has produced a web-page score report. Your job is to *reflect*
on that report: turn its failing/warning checks (fail / warn — never treat 'na' as a problem)
into a **directly actionable, verifiable** page-optimization instruction (prompt) for the next
Agent to apply to the page.

Reflection requirements:
1. Only handle fail / warn items; 'na' items must not be considered or criticized.
2. For each failing item, state the concrete change to make: file name, tag, attribute, or
   configuration, ideally with a copy-pasteable snippet (robots.txt / llms.txt / sitemap.xml /
   JSON-LD / meta tags / headings, etc.).
3. Order by ROI: P0 (do first, biggest win) -> P1 (do soon) -> P2 (only if time permits).
4. Give explicit acceptance criteria: which checks should flip from fail to pass, and the
   target total score.
5. Respond in English; keep the instruction concise and executable, with no filler or repetition.
6. If a "Reference knowledge" section is supplied below, base each fix on it: use its exact
   tags / files / snippets and do not contradict it; fill in any domain-specific placeholders
   (URLs, titles, dates) from the scorecard.

Output format: Output only the instruction body addressed to the next Agent — no explanation,
reasoning, or pleasantries. The body must follow this fixed structure:
  1. Objective (target URL, current score/grade, target score)
  2. Optimization checklist (grouped by P0/P1/P2, each with file/tag/snippet)
  3. Acceptance criteria (list each check that should turn green)"""


def reflect_with_llm(scorecard: dict, analysis: str, use_rag: bool = True) -> str:
    """Reflect on the results with the LLM, returning the instruction text for the next agent."""
    cfg = get_config("reflect")
    user_content = build_digest(scorecard, analysis)

    # RAG: ground the reflection in curated fix recipes / official a14y docs for the failing
    # checks, so the LLM emits accurate, copy-pasteable snippets instead of guessing.
    if use_rag:
        issue_checks = [c for c in collect_checks(scorecard)
                        if c.get("status") in ("fail", "warn", "error")]
        refs = retrieve_for_checks(issue_checks)
        if refs:
            user_content += "\n\n--- Reference knowledge (retrieved; follow these) ---\n"
            for r in refs:
                user_content += f"\n### [{r['group']}] {r['id']}\n{r['content']}\n"

        # Feedback loop: recall similar verified examples (frontmatter verdict PASS is boosted).
        examples = rank_examples(user_content, top_k=2)
        if examples:
            user_content += "\n\n--- Similar verified examples (structure reference only; do not copy URLs/titles) ---\n"
            for ex in examples:
                user_content += f"\n### Example `{ex['id']}` (verdict: {ex['verdict']})\n{ex['content'][:2000]}\n"

    print(f"[reflect] requesting {cfg.provider} (model={cfg.model}) ...")
    return chat(
        [
            {"role": "system", "content": REFLECT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        config=cfg,
    )


# ============ Step 3: rule-engine fallback ============

FIX_HINTS = {
    "robots-txt.allows-ai-bots": {
        "action": "Edit /robots.txt: replace the Disallow rules for GPTBot / ClaudeBot / CCBot / Google-Extended with Allow.",
        "code": "User-agent: GPTBot\nAllow: /\n\n"
                "User-agent: ClaudeBot\nAllow: /\n\n"
                "User-agent: CCBot\nAllow: /\n\n"
                "User-agent: Google-Extended\nAllow: /\n\n"
                "Sitemap: https://<your-domain>/sitemap.xml",
    },
    "robots-txt.allows-llms-txt": {
        "action": "Remove the Disallow rules for /llms.txt and /.well-known/llms.txt in robots.txt.",
        "code": "# Remove lines like these two:\n# Disallow: /llms.txt\n# Disallow: /.well-known/llms.txt",
    },
    "llms-txt.exists": {
        "action": "Publish /llms.txt at the site root (Content-Type: text/plain; charset=utf-8).",
        "code": "# llms.txt\n> Entry index provided by this site for AI agents.\n\n"
                "- [Home](https://<your-domain>/): site homepage\n"
                "- [About](https://<your-domain>/about.md): about us",
    },
    "sitemap-xml.exists": {
        "action": "Publish /sitemap.xml at the site root and declare it in robots.txt with a Sitemap: line.",
        "code": '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                '  <url><loc>https://<your-domain>/</loc><lastmod>2026-09-15</lastmod></url>\n'
                '</urlset>',
    },
    "sitemap-md.exists": {
        "action": "Publish /sitemap.md at the site root, listing key page links.",
        "code": "# Sitemap\n\n- [Home](https://<your-domain>/)\n- [About](https://<your-domain>/about)",
    },
    "agents-md.exists": {
        "action": "Publish /AGENTS.md describing the site's purpose, available entry points, and crawl suggestions (cover at least 2 of install/config/usage).",
        "code": "# AGENTS.md\n\n## Install\n(how to deploy / install)\n\n## Usage\n(how to use this site)",
    },
    "html.canonical-link": {
        "action": "Add a canonical link in <head>.",
        "code": '<link rel="canonical" href="https://<your-domain>/">',
    },
    "html.og-title": {
        "action": "Add og:title in <head>.",
        "code": '<meta property="og:title" content="<page title>">',
    },
    "html.og-description": {
        "action": "Add og:description in <head> (recommend >= 50 characters).",
        "code": '<meta property="og:description" content="<page description>">',
    },
    "html.lang-attribute": {
        "action": "Add a lang attribute to the root <html> tag.",
        "code": '<html lang="en">',
    },
    "html.json-ld": {
        "action": "Inject JSON-LD structured data (WebSite) into <head> or <body>.",
        "code": '<script type="application/ld+json">\n'
                '{"@context": "https://schema.org", "@type": "WebSite", '
                '"name": "<site name>", "url": "https://<your-domain>/"}\n</script>',
    },
    "html.headings": {
        "action": "The page needs at least 3 section headings (1 <h1> + at least 2 <h2>).",
        "code": "<h1>Main title</h1>\n<h2>Section one</h2>\n<h2>Section two</h2>",
    },
    "html.glossary-link": {
        "action": "Link to a glossary page from within the page.",
        "code": '<a href="/glossary">Glossary</a>',
    },
    "markdown.mirror-suffix": {
        "action": "Provide a .md mirror for the page (e.g. /index.md).",
        "code": "Publish /index.md containing a Markdown version of the current page.",
    },
    "markdown.alternate-link": {
        "action": "Declare the Markdown mirror in the HTML <head>.",
        "code": '<link rel="alternate" type="text/markdown" href="/index.md">',
    },
    "markdown.content-negotiation": {
        "action": "The server should support Accept: text/markdown, return text/markdown, and add a canonical Link header.",
        "code": 'Link: <https://<your-domain>/>; rel="canonical"',
    },
}


def reflect_with_rules(scorecard: dict) -> str:
    """Rule engine: deterministically generate instructions from fail/warn checks (offline fallback)."""
    summary = scorecard.get("summary", {})
    url = scorecard.get("url") or scorecard.get("baseUrl") or "N/A"
    score = summary.get("score", 0)
    applicable = summary.get("applicable", 0)
    failed = summary.get("failed", 0)
    passed = summary.get("passed", 0)

    fails = [c for c in collect_checks(scorecard) if c.get("status") == "fail"]
    warns = [c for c in collect_checks(scorecard) if c.get("status") == "warn"]

    # Flat-pool scoring (a14y flat-pool-v1): score ≈ passed / applicable * 100. Flipping every
    # failed check green raises passed to passed + failed, so estimate the resulting score.
    if applicable:
        target = round((passed + failed) / applicable * 100)
        target = min(100, max(score, target))
    else:
        target = score

    lines = [
        "1. Objective",
        f"- Target URL: {url}",
        f"- Current score: {score}/100 ({failed} failed, {applicable} applicable)",
        f"- Target score: >= {target} (turn all failing items green)",
        "",
        "2. Optimization checklist",
    ]

    p0_ids = ["robots-txt.allows-ai-bots", "robots-txt.allows-llms-txt",
              "llms-txt.exists", "sitemap-xml.exists", "agents-md.exists"]
    p2_ids = ["html.glossary-link", "markdown.content-negotiation",
              "markdown.mirror-suffix", "markdown.alternate-link", "sitemap-md.exists"]

    def emit(priority: str, checks):
        buf = []
        for c in checks:
            cid = c.get("id", "")
            hint = FIX_HINTS.get(cid)
            buf.append(f"### {priority} · [{c.get('group')}] {cid}")
            buf.append(f"- Problem: {c.get('message', '')}")
            if hint:
                buf.append(f"- Action: {hint['action']}")
                buf.append("```\n" + hint["code"] + "\n```")
            else:
                recipe = read_recipe(cid)  # RAG fallback: curated recipe / crawled docs
                if recipe:
                    buf.append(f"- Action: fix '{c.get('name', cid)}' (retrieved recipe below)")
                    buf.append("```\n" + recipe + "\n```")
                else:
                    buf.append(f"- Action: fix the '{c.get('name', cid)}' check (see {c.get('docsUrl', 'a14y docs')}).")
        return buf

    ordered = sorted(
        fails,
        key=lambda c: (
            c.get("id") in p0_ids,  # P0 items first
            c.get("id") in p2_ids,  # P2 items last
        ),
    )
    p0 = [c for c in ordered if c.get("id") in p0_ids]
    p2 = [c for c in ordered if c.get("id") in p2_ids]
    p1 = [c for c in ordered if c not in p0 and c not in p2]

    if p0:
        lines.append("## P0 — Do first (unblock, biggest win)")
        lines.extend(emit("P0", p0))
    if p1:
        lines.append("## P1 — Do soon (complete semantic structure)")
        lines.extend(emit("P1", p1))
    if p2:
        lines.append("## P2 — If time permits (agent-only channels)")
        lines.extend(emit("P2", p2))
    if warns:
        lines.append("## Warnings (handle along the way)")
        lines.extend(emit("Warn", warns))

    lines.append("")
    lines.append("3. Acceptance criteria")
    for c in fails:
        lines.append(f"- [ ] {c.get('id')} flips from fail to pass")
    for c in warns:
        lines.append(f"- [ ] {c.get('id')} flips from warn to pass/na")
    lines.append(f"- [ ] a14y total score rises from {score} to >= {target}")

    return "\n".join(lines)


# ============ Main ============

def main():
    parser = argparse.ArgumentParser(description="Reflect on eval.txt to generate a page optimization prompt")
    parser.add_argument("input", nargs="?", default=str(DEFAULT_INPUT),
                        help="eval.txt path (default ./eval.txt)")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT),
                        help="output prompt path (default ./prompt.txt)")
    parser.add_argument("--no-llm", action="store_true",
                        help="force the rule engine, skip the LLM")
    parser.add_argument("--no-rag", action="store_true",
                        help="disable RAG grounding (retrieval) in the LLM reflection")
    parser.add_argument("--debug", action="store_true", help="print full traceback on LLM failure")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[error] input file not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    text = in_path.read_text(encoding="utf-8")
    scorecard, analysis = parse_eval_text(text)
    if not scorecard:
        print("[error] could not parse the scorecard JSON from eval.txt; "
              "make sure the file was generated by ai_eval.py.", file=sys.stderr)
        sys.exit(1)

    # Reflect: prefer the LLM; use the rule engine only when there is no key.
    # With a key configured, an LLM failure aborts instead of silently degrading.
    prompt_text = None
    if not args.no_llm:
        cfg = get_config("reflect")
        if cfg.api_key:
            try:
                prompt_text = reflect_with_llm(scorecard, analysis, use_rag=not args.no_rag)
            except Exception as e:  # noqa: BLE001
                print(f"[error] LLM reflection failed (key configured, aborting): {e}", file=sys.stderr)
                if args.debug:
                    traceback.print_exc()
                sys.exit(1)

    if not prompt_text:
        print("[reflect] using the rule engine ...")
        prompt_text = reflect_with_rules(scorecard)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(prompt_text + "\n", encoding="utf-8")
    print(f"[output] optimization prompt written to {out_path}")


if __name__ == "__main__":
    main()
