#!/usr/bin/env python3
"""
a14y + DeepSeek 网页 AI 可读性评估工作流
用法: python ai_eval.py <目标URL> [--mode page|site]
"""

import subprocess
import json
import os
import sys
import shutil
import argparse
from datetime import datetime

from llm import chat, get_config

# Windows 控制台默认 GBK，强制 stdout/stderr 用 UTF-8，避免中文打印乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ============ 配置 ============
OUTPUT_FILE = "eval.txt"

# 分数 → 等级映射
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


# ============ Step 1: 调用 a14y CLI ============

def run_a14y(url: str, mode: str = "page") -> dict:
    """调用 a14y CLI 对目标 URL 评估，返回解析后的 JSON。"""
    a14y_bin = shutil.which("a14y")
    if not a14y_bin:
        raise RuntimeError(
            "找不到 a14y，请确认已运行 `npm install -g a14y`，"
            "并且 npm 全局 bin 目录已加入 PATH。"
        )
    print(f"[a14y] 可执行文件: {a14y_bin}")

    cmd = [a14y_bin, "check", url, "-o", "json", "-m", mode]
    if mode == "site":
        cmd += ["--max-pages", "50"]

    print(f"[a14y] 正在评估: {url} (mode={mode})")

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
            f"a14y 评估失败 (exit={result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

    # a14y 有时会在 stdout 前面混入 npm 警告，从第一个 { 开始截取
    stdout = result.stdout
    brace = stdout.find("{")
    if brace > 0:
        stdout = stdout[brace:]

    data = json.loads(stdout)
    print(f"[a14y] 总分: {data['summary']['score']}/100")
    return data


# ============ Step 2: 构造 Prompt 并调用 DeepSeek ============

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
    """将 a14y JSON 评分卡格式化为 DeepSeek 可读的文本"""
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


def analyze_with_deepseek(scorecard: dict) -> str:
    cfg = get_config("eval")
    user_content = build_user_prompt(scorecard)

    print(f"[LLM] 正在请求深度分析 (provider={cfg.provider}, model={cfg.model})...")
    return chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        config=cfg,
    )


# ============ Step 3: 写入 eval.txt ============

def write_eval_file(url: str, scorecard: dict, analysis: str):
    summary = scorecard.get("summary", {})
    score = summary.get("score", 0)
    grade = score_to_grade(score)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = f"""
{'=' * 60}
  a14y AI 可读性评估报告
{'=' * 60}
评估时间: {now}
目标 URL: {url}
模式: {scorecard.get('mode', 'N/A')}
评分卡版本: {scorecard.get('scorecardVersion', 'N/A')}
总分: {score}/100  (等级: {grade})
统计: pass={summary.get('passed')} / fail={summary.get('failed')} /
      warn={summary.get('warned')} / na={summary.get('na')} /
      applicable={summary.get('applicable')}

{'-' * 60}
  DeepSeek 深度分析
{'-' * 60}

{analysis}

{'=' * 60}
  原始评分卡数据 (JSON)
{'=' * 60}
{json.dumps(scorecard, indent=2, ensure_ascii=False)}
"""

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[输出] 报告已写入 {OUTPUT_FILE}")


# ============ 主流程 ============

def main():
    parser = argparse.ArgumentParser(description="a14y + DeepSeek 网页 AI 可读性评估")
    parser.add_argument("url", help="要评估的网页 URL")
    parser.add_argument("--mode", choices=["page", "site"], default="page",
                        help="评估模式: page(单页) 或 site(整站)")
    args = parser.parse_args()

    scorecard = run_a14y(args.url, args.mode)
    analysis = analyze_with_deepseek(scorecard)
    write_eval_file(args.url, scorecard, analysis)


if __name__ == "__main__":
    main()