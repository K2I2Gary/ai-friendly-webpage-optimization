#!/usr/bin/env python3
"""
A/B test harness for the reflect stage's RAG grounding
--------------------------------------------------------
Runs `reflect_with_llm` twice (RAG on / off) over the same eval.txt, N times each, and
reports which configuration produces more complete, traceable optimization instructions.
Each prompt is also saved for manual diffing.

Metrics are computed locally (no LLM judge):
  coverage            fraction of failing check ids that appear in the prompt
  total_mentioned     number of catalog check ids mentioned (proxy for unlocks awareness)
  sub_checks          mentioned ids beyond the failing set (unlocked / na sub-checks)
  flips_annotations   number of "Flips:" traceability annotations

Usage:
    python ab_test.py eval.txt            # 1 run each (2 LLM calls)
    python ab_test.py eval.txt --runs 3   # 3 runs each, averaged
    python ab_test.py eval.txt --out-dir eval/ab
"""

import argparse
import re
from pathlib import Path

from common import console_utf8
from llm import get_config
from rag import load_catalog
from reflect import collect_checks, parse_eval_text, reflect_with_llm

console_utf8()

CATALOG = load_catalog()


def mentioned_ids(text: str) -> list[str]:
    """Catalog check ids that appear (as substrings) in `text`, sorted."""
    return sorted(cid for cid in CATALOG if cid in text)


def compute_metrics(prompt: str, fail_ids: list[str]) -> dict:
    """Compute coverage / traceability metrics for a reflect prompt."""
    mentioned = mentioned_ids(prompt)
    covered = [cid for cid in fail_ids if cid in mentioned]
    sub = [cid for cid in mentioned if cid not in fail_ids]
    return {
        "coverage": len(covered) / len(fail_ids) if fail_ids else 1.0,
        "total_mentioned": len(mentioned),
        "sub_checks": len(sub),
        "flips_annotations": len(re.findall(r"\bFlips:", prompt)),
    }


def _fmt(m: dict) -> str:
    return (f"coverage={m['coverage']:.0%}  checks={m['total_mentioned']:.1f}  "
            f"sub={m['sub_checks']:.1f}  flips={m['flips_annotations']:.1f}")


def main():
    parser = argparse.ArgumentParser(description="A/B test RAG grounding in the reflect stage")
    parser.add_argument("input", nargs="?", default="eval.txt",
                        help="eval.txt path (default ./eval.txt)")
    parser.add_argument("--runs", type=int, default=1,
                        help="number of A/B repetitions per arm (default 1)")
    parser.add_argument("--out-dir", default="eval/ab",
                        help="where to save each prompt (default eval/ab)")
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    scorecard, analysis = parse_eval_text(text)
    fail_ids = sorted(c["id"] for c in collect_checks(scorecard)
                      if c.get("status") in ("fail", "warn", "error"))

    if not get_config("reflect").api_key:
        raise SystemExit("[error] no API key configured; the A/B test needs the LLM "
                         "(set api_key in llm_config.json).")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"failing checks: {len(fail_ids)}")
    print(f"runs: {args.runs} (each = 1 RAG-on + 1 RAG-off LLM call)\n")

    agg = {"on": [], "off": []}
    for i in range(1, args.runs + 1):
        print(f"--- run {i}/{args.runs} ---")
        on = reflect_with_llm(scorecard, analysis, use_rag=True)
        off = reflect_with_llm(scorecard, analysis, use_rag=False)
        m_on, m_off = compute_metrics(on, fail_ids), compute_metrics(off, fail_ids)
        agg["on"].append(m_on)
        agg["off"].append(m_off)
        (out_dir / f"rag_on_{i}.txt").write_text(on + "\n", encoding="utf-8")
        (out_dir / f"rag_off_{i}.txt").write_text(off + "\n", encoding="utf-8")
        print(f"  RAG ON : {_fmt(m_on)}")
        print(f"  RAG OFF: {_fmt(m_off)}")

    print("\n--- averages ---")
    for label in ("on", "off"):
        ms = agg[label]
        n = len(ms)
        avg = {k: sum(m[k] for m in ms) / n for k in ms[0]}
        print(f"  RAG {label.upper():>3}: {_fmt(avg)}")

    print(f"\noutputs written to {out_dir}")


if __name__ == "__main__":
    main()
