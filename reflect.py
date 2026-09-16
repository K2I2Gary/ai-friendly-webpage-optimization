#!/usr/bin/env python3
"""
eval.txt 反思器 (Reflection Engine)
-----------------------------------
读取 ai_eval.py 生成的 eval.txt，对评估结果进行"反思"：
  1. 解析出原始评分卡 JSON + DeepSeek 初步分析 + 元信息（URL / 总分 / 等级）。
  2. 只针对 fail / warn 检查项（na 项不参与、不批评），提炼出可落地的修复动作。
  3. 生成一份可直接执行、可验收的"网页优化指令"（prompt），写入 prompt.txt，
     交给下一个 agent 落地修改网页。

优先用 DeepSeek 做反思（与 ai_eval.py 同一套 API），
无 key / 调用失败时自动退化为规则引擎，保证 prompt.txt 一定能产出。

用法:
    python reflect.py                     # 读取 eval.txt -> 写 prompt.txt
    python reflect.py eval.txt -o out.txt
    python reflect.py --no-llm            # 强制使用规则引擎（离线）
"""

import json
import os
import sys
import argparse
import traceback
from pathlib import Path

from llm import chat, get_config

# Windows 控制台默认 GBK，强制 stdout/stderr 用 UTF-8，避免中文打印乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ============ 路径与配置 ============
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "eval.txt"
DEFAULT_OUTPUT = BASE_DIR / "prompt.txt"

STATUS_MARK = {"pass": "✅", "fail": "❌", "warn": "⚠️", "error": "💥", "na": "—"}


# ============ Step 1: 解析 eval.txt ============

def parse_eval_text(text: str):
    """从 eval.txt 提取 (scorecard dict, analysis str)。

    返回 None 表示解析失败。
    """
    json_marker = "原始评分卡数据"
    analysis_marker = "DeepSeek 深度分析"

    # 1) 原始评分卡 JSON：取 json_marker 之后的第一个 '{'
    scorecard = {}
    idx = text.rfind(json_marker)
    if idx != -1:
        brace = text.find("{", idx)
        if brace != -1:
            try:
                scorecard = json.loads(text[brace:])
            except json.JSONDecodeError as e:
                print(f"[警告] 评分卡 JSON 解析失败: {e}", file=sys.stderr)

    # 2) DeepSeek 分析文本：analysis_marker 与 json_marker 之间
    analysis = ""
    a_start = text.find(analysis_marker)
    a_end = text.find(json_marker)
    if a_start != -1:
        analysis = text[a_start:a_end if a_end != -1 else len(text)].strip()

    return scorecard, analysis


def collect_checks(scorecard: dict):
    """汇总 siteChecks 与 pages[].checks 为单个检查项列表。"""
    checks = list(scorecard.get("siteChecks", []))
    for page in scorecard.get("pages", []):
        checks.extend(page.get("checks", []))
    return checks


def build_digest(scorecard: dict, analysis: str) -> str:
    """把评分卡压缩成给反思模型看的精简文本（只突出 fail/warn）。"""
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
            f"{c.get('id')} — {c.get('name')} ｜ {c.get('message')}"
        )
    for c in warns + errors:
        lines.append(
            f"{STATUS_MARK.get(c.get('status'), '?')} [{c.get('group')}] "
            f"{c.get('id')} — {c.get('name')} ｜ {c.get('message')}"
        )
    if not fails and not warns and not errors:
        lines.append("(no failed/warning items)")

    if analysis:
        lines.append("")
        lines.append("--- Initial LLM analysis (for reference) ---")
        lines.append(analysis)

    return "\n".join(lines)


# ============ Step 2: 反思系统提示词 ============

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

Output format: Output only the instruction body addressed to the next Agent — no explanation,
reasoning, or pleasantries. The body must follow this fixed structure:
  1. Objective (target URL, current score/grade, target score)
  2. Optimization checklist (grouped by P0/P1/P2, each with file/tag/snippet)
  3. Acceptance criteria (list each check that should turn green)"""


def reflect_with_llm(scorecard: dict, analysis: str) -> str:
    """用 LLM 对评估结果做反思，返回给下一 agent 的指令文本。"""
    cfg = get_config("reflect")
    user_content = build_digest(scorecard, analysis)

    print(f"[反思] 正在请求 {cfg.provider} (model={cfg.model}) ...")
    return chat(
        [
            {"role": "system", "content": REFLECT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        config=cfg,
    )


# ============ Step 3: 规则引擎兜底 ============

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
        "code": '<html lang="zh-CN">',
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
    """规则引擎：根据 fail/warn 检查项确定性生成指令（离线兜底）。"""
    summary = scorecard.get("summary", {})
    url = scorecard.get("url") or scorecard.get("baseUrl") or "N/A"
    score = summary.get("score", 0)
    applicable = summary.get("applicable", 0)
    failed = summary.get("failed", 0)

    fails = [c for c in collect_checks(scorecard) if c.get("status") == "fail"]
    warns = [c for c in collect_checks(scorecard) if c.get("status") == "warn"]

    # 目标分：把全部 fail 翻绿后的估算分（flat-pool 近似：score + failed 项权重）
    target = min(100, score + failed) if applicable else score

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
                buf.append(f"- Action: fix the '{c.get('name', cid)}' check (see {c.get('docsUrl', 'a14y docs')}).")
        return buf

    ordered = sorted(
        fails,
        key=lambda c: (
            c.get("id") in p0_ids,  # P0 项排最前
            c.get("id") in p2_ids,  # P2 项排最后
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


# ============ 主流程 ============

def main():
    parser = argparse.ArgumentParser(description="反思 eval.txt 生成网页优化 prompt")
    parser.add_argument("input", nargs="?", default=str(DEFAULT_INPUT),
                        help="eval.txt 路径（默认 ./eval.txt）")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT),
                        help="输出 prompt 路径（默认 ./prompt.txt）")
    parser.add_argument("--no-llm", action="store_true",
                        help="强制使用规则引擎，不调用 LLM")
    parser.add_argument("--debug", action="store_true", help="LLM 失败时打印完整堆栈")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[错误] 找不到输入文件: {in_path}", file=sys.stderr)
        sys.exit(1)

    text = in_path.read_text(encoding="utf-8")
    scorecard, analysis = parse_eval_text(text)
    if not scorecard:
        print("[错误] 未能从 eval.txt 解析出评分卡 JSON，请确认文件由 ai_eval.py 生成。",
              file=sys.stderr)
        sys.exit(1)

    # 反思：优先 LLM；无 key 才用规则引擎，已配置 key 但调用失败则直接中止
    prompt_text = None
    if not args.no_llm:
        cfg = get_config("reflect")
        if cfg.api_key:
            try:
                prompt_text = reflect_with_llm(scorecard, analysis)
            except Exception as e:  # noqa: BLE001
                print(f"[错误] LLM 反思失败（已配置 API key，中止）: {e}", file=sys.stderr)
                if args.debug:
                    traceback.print_exc()
                sys.exit(1)

    if not prompt_text:
        print("[反思] 使用规则引擎生成 ...")
        prompt_text = reflect_with_rules(scorecard)

    out_path = Path(args.output)
    out_path.write_text(prompt_text + "\n", encoding="utf-8")
    print(f"[输出] 优化 prompt 已写入 {out_path}")


if __name__ == "__main__":
    main()
