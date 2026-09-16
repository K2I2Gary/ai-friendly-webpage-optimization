#!/usr/bin/env python3
"""
a14y + LLM web AI-readability evaluation workflow
Usage: python ai_eval.py <target URL> [--mode page|site]
"""

import subprocess
import json
import sys
import shutil
import argparse
from datetime import datetime
from pathlib import Path

from common import console_utf8
from llm import chat, get_config

# Windows console defaults to GBK; force stdout/stderr to UTF-8 to avoid garbled output
console_utf8()

# ============ Config ============
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "eval.txt"

# Score -> grade mapping
GRADE_BANDS = [
    (90, "A"),
    (80, "B"),
    (70, "C"),
    (60, "D"),
    (0,  "F"),
]

def score_to_grade(score: int) -> str:
    for threshold, grade in GRADE_BANDS:
        if score >= threshold:
            return grade
    return "F"


# ============ Step 1: call the a14y CLI ============

def run_a14y(url: str, mode: str = "page") -> dict:
    """Run the a14y CLI against the target URL and return the parsed JSON."""
    a14y_bin = shutil.which("a14y")
    if not a14y_bin:
        raise RuntimeError(
            "a14y not found. Run `npm install -g a14y` and make sure the npm global "
            "bin directory is on PATH."
        )
    print(f"[a14y] executable: {a14y_bin}")

    cmd = [a14y_bin, "check", url, "-o", "json", "-m", mode]
    if mode == "site":
        cmd += ["--max-pages", "50"]

    print(f"[a14y] evaluating: {url} (mode={mode})")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        shell=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"a14y evaluation failed (exit={result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

    # a14y sometimes prepends npm warnings to stdout; cut from the first '{' onward
    stdout = result.stdout
    brace = stdout.find("{")
    if brace > 0:
        stdout = stdout[brace:]

    data = json.loads(stdout)
    print(f"[a14y] total score: {data['summary']['score']}/100")
    return data


# ============ Step 2: build the prompt and call the LLM ============

SYSTEM_PROMPT = """You are a senior AI-readability (Agent Readability) analyst.
You are familiar with the a14y scorecard system, including three core dimensions:
- Discoverability: llms.txt, sitemap, robots.txt, AGENTS.md, etc.
- Parsing: semantic HTML, structured data, Markdown mirrors, content negotiation, etc.
- Comprehension: heading structure, content density, glossary links, etc.

Each check item has one of five statuses: pass / fail / warn / error / na.
Note: 'na' items do not count toward the score; never treat 'na' as a failure.

Your task: given the a14y scorecard data, produce a structured deep-analysis report.
The report must include the following sections:

1. **Overall score overview**: total score and letter grade (A-F), plus a one-line
   summary of the core problem.
2. **Dimension score analysis**: group the checks (Discoverability / HTML metadata /
   Structured data / Content structure / Markdown mirror / HTTP / Code / API), analyze
   score gains and losses per group, and point out the 3-5 most important items to fix.
3. **Prioritized improvement suggestions**: order by ROI (P0/P1/P2), with actionable
   fixes and, where possible, the specific files/tags to add.
4. **Impact on AI agents**: explain how the current score affects the ability of agents
   such as ChatGPT/Claude/Cursor to crawl and understand the page.

Respond in English. Be concise and avoid generic statements."""

def build_user_prompt(scorecard: dict) -> str:
    """Format the a14y JSON scorecard into text the LLM can read"""
    summary = scorecard.get("summary", {})
    lines = [
        f"Target URL: {scorecard.get('url', 'N/A')}",
        f"Mode: {scorecard.get('mode', 'N/A')}",
        f"Scorecard version: {scorecard.get('scorecardVersion', 'N/A')} "
        f"(released {scorecard.get('scorecardReleasedAt', 'N/A')})",
        f"Scoring method: {scorecard.get('scoringMethodology', 'N/A')}",
        f"Total score: {summary.get('score', 'N/A')} / 100",
        f"Stats: pass={summary.get('passed')} fail={summary.get('failed')} "
        f"warn={summary.get('warned')} error={summary.get('errored')} "
        f"na={summary.get('na')} applicable={summary.get('applicable')} "
        f"total={summary.get('total')}",
        "",
        "--- Site-level checks ---",
    ]

    for c in scorecard.get("siteChecks", []):
        mark = {"pass": "✅", "fail": "❌", "warn": "⚠️",
                "error": "💥", "na": "—"}.get(c.get("status"), "?")
        lines.append(
            f"{mark} [{c.get('group')}] {c.get('id')}: {c.get('name')} — {c.get('message')}"
        )

    for page in scorecard.get("pages", []):
        lines.append("")
        lines.append(f"--- Page checks: {page.get('url')} (HTTP {page.get('status')}) ---")
        for c in page.get("checks", []):
            mark = {"pass": "✅", "fail": "❌", "warn": "⚠️",
                    "error": "💥", "na": "—"}.get(c.get("status"), "?")
            lines.append(
                f"{mark} [{c.get('group')}] {c.get('id')}: "
                f"{c.get('name')} — {c.get('message')}"
            )

    return "\n".join(lines)


def analyze_with_llm(scorecard: dict) -> str:
    cfg = get_config("eval")
    user_content = build_user_prompt(scorecard)

    print(f"[LLM] requesting deep analysis (provider={cfg.provider}, model={cfg.model})...")
    return chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        config=cfg,
    )


# ============ Step 3: write eval.txt ============

def write_eval_file(url: str, scorecard: dict, analysis: str):
    summary = scorecard.get("summary", {})
    score = summary.get("score", 0)
    grade = score_to_grade(score)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = f"""
{'=' * 60}
  a14y AI-readability evaluation report
{'=' * 60}
Evaluated at: {now}
Target URL: {url}
Mode: {scorecard.get('mode', 'N/A')}
Scorecard version: {scorecard.get('scorecardVersion', 'N/A')}
Total score: {score}/100  (grade: {grade})
Stats: pass={summary.get('passed')} / fail={summary.get('failed')} /
      warn={summary.get('warned')} / na={summary.get('na')} /
      applicable={summary.get('applicable')}

{'-' * 60}
  LLM analysis
{'-' * 60}

{analysis}

{'=' * 60}
  Raw scorecard data (JSON)
{'=' * 60}
{json.dumps(scorecard, indent=2, ensure_ascii=False)}
"""

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[output] report written to {OUTPUT_FILE}")


# ============ Main ============

def main():
    parser = argparse.ArgumentParser(description="a14y + LLM web AI-readability evaluation")
    parser.add_argument("url", help="the web page URL to evaluate")
    parser.add_argument("--mode", choices=["page", "site"], default="page",
                        help="evaluation mode: page (single page) or site (whole site)")
    args = parser.parse_args()

    scorecard = run_a14y(args.url, args.mode)
    analysis = analyze_with_llm(scorecard)
    write_eval_file(args.url, scorecard, analysis)


if __name__ == "__main__":
    main()
