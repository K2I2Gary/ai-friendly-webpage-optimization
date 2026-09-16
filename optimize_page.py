#!/usr/bin/env python3
"""
网页优化落地器 (Optimize Agent)
--------------------------------
读取源 HTML 页面（本地文件或 http(s) URL，URL 优先用 Playwright 渲染 JS 抓取），
调用 LLM 生成新的 <head> 元数据，再合并回原文，输出「AI 可读、语义化」的优化页面。

源可以是本地 HTML 文件，也可以是 http(s) URL（自动抓取 HTML）。

原则：
  - body 原样保留，URL 指向（图片 src、链接 href、iframe src 等）零丢失。
  - LLM 只重写 <head>（title / description / og / JSON-LD / viewport）；
    lang / h1 / 移除 Flash 等结构性修复由规则引擎兜底。

用法:
    python optimize_page.py test.html             # 本地文件 -> test_optimized.html
    python optimize_page.py https://example.com   # URL（自动抓取）-> *_optimized.html
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

# Windows 控制台默认 GBK，强制 stdout/stderr 用 UTF-8
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
- If an 'a14y 评估发现的问题' section is present, treat its failing items as the priority list to fix.
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
    """构造给 LLM 的精简上下文：原文 <head> + 正文摘要 + 页面 URL + a14y 发现的问题。"""
    head = ""
    m = re.search(r"<head[^>]*>(.*?)</head>", source, flags=re.I | re.S)
    if m:
        head = m.group(0)

    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", source, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:2500]

    lines = ["请为下面的网页生成新的 <head> 元数据。"]
    if url:
        lines.append(f"页面 URL: {url}")
    lines += ["", "=== 原文 <head> ===", head or "（无）", "",
              "=== 正文摘要（去脚本/样式后） ===", text or "（无正文）"]

    if prompt:
        # 精简 reflect 的分析：去代码块、压缩空行、截断
        p = re.sub(r"```.*?```", "", prompt, flags=re.S)
        p = re.sub(r"\n{2,}", "\n", p).strip()[:2000]
        if p:
            lines += ["", "=== a14y 评估发现的问题（优先修复这些） ===", p]
    return "\n".join(lines)


def _merge_head(source: str, new_head: str) -> str:
    """把 LLM 生成的 <head> 合并进源页面，同时保留源 head 里的资源标签
    （link / script / style / base），避免丢失样式表、脚本、图标等 URL；
    源有 charset 而新 head 没有时也补上，防止编码错乱。"""
    sm = re.search(r"<head[^>]*>(.*?)</head>", source, flags=re.I | re.S)
    if not sm:
        return re.sub(r"(<html[^>]*>)", lambda mm: mm.group(1) + new_head, source,
                      count=1, flags=re.I)

    old_inner = sm.group(1)
    keep = re.findall(
        r'<link\b[^>]*>|<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>|<base\b[^>]*>',
        old_inner, flags=re.I | re.S)

    # charset：源有而新 head 没有，则补上
    if not re.search(r"<meta\b[^>]*\bcharset\b", new_head, flags=re.I):
        cm = re.search(r"<meta\b[^>]*\bcharset\b[^>]*>", old_inner, flags=re.I)
        if cm:
            keep.insert(0, cm.group(0))

    if keep:
        joined = "\n".join(keep)
        # 用 lambda 替换：joined 里可能含反斜杠（CSS hack / JS 转义），不能走字符串替换的组引用解析
        new_head = re.sub(r"(</head>)", lambda m: joined + "\n" + m.group(1), new_head,
                          count=1, flags=re.I)

    return re.sub(r"<head[^>]*>.*?</head>", lambda _: new_head, source,
                  count=1, flags=re.I | re.S)


def _apply_lang(html: str, raw: str) -> str:
    """从 LLM 输出里解析 LANG，设置到根 <html lang="...">；失败则保留原样。"""
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
    """从 LLM 输出里提取 <style> 注入到 head 末尾（原资源之后），让新样式优先生效。"""
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
    """从 LLM 输出里解析 ALTS 数组，给缺 alt 的 <img> 注入 alt 文本。失败则原样返回。"""
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
    """给 src 匹配、且缺少 alt 的首个 <img> 注入 alt（兼容 <img> 与 <img />）。"""
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
    """让 LLM 只产出新的 <head> 元数据，再合并回原文（body 原样保留 → URL 零丢失）。
    返回合并后的完整页面；LLM 未返回有效 <head> 时返回 None（交由规则兜底）。"""
    cfg = get_config("generate")
    print(f"[优化] 正在请求 {cfg.provider} (model={cfg.model}) ...")
    raw = chat(
        [
            {"role": "system", "content": HEAD_SYSTEM_PROMPT},
            {"role": "user", "content": _build_head_prompt(source, url, prompt)},
        ],
        temperature=0.2,
        config=cfg,
    )
    # 剥离可能的 ```html 围栏
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    m = re.search(r"<head[^>]*>.*?</head>", raw, flags=re.I | re.S)
    if not m:
        return None
    merged = _merge_head(source, m.group(0))
    merged = _apply_lang(merged, raw)
    merged = _apply_style(merged, raw)
    return _apply_alts(merged, raw)


# ============ 规则兜底：确定性最小修复 ============
# ensure_basics：只补「纯结构」项（lang / viewport / h1 / 移除 Flash），不新增任何内容。
# optimize_with_rules：在 ensure_basics 基础上再补 meta description / JSON-LD（无 LLM 时的兜底）。

def ensure_basics(html: str) -> str:
    # 1) lang
    html = re.sub(r"<html(?![^>]*\blang=)", '<html lang="zh-CN"', html, count=1)

    # 2) viewport
    if 'name="viewport"' not in html and "name='viewport'" not in html:
        html = re.sub(r"(<head[^>]*>)", r'\1\n    <meta name="viewport" '
                      'content="width=device-width, initial-scale=1.0">',
                      html, count=1)

    # 3) 标题层级：首个 h2-h6 提级为 h1；不足 2 个 h2 时把 h3 提级为 h2
    if "<h1" not in html:
        html = re.sub(r"<h([2-6])([^>]*)>(.*?)</h\1>", r"<h1\2>\3</h1>", html,
                      count=1, flags=re.S)
    while len(re.findall(r"<h2\b", html, flags=re.I)) < 2:
        new = re.sub(r"<h3([^>]*)>(.*?)</h3>", r"<h2\1>\2</h2>", html,
                     count=1, flags=re.S)
        if new == html:
            break
        html = new

    # 4) 移除 Flash object
    html = re.sub(r"<object[^>]*type=[\"']application/x-shockwave-flash[\"'][^>]*>.*?</object>",
                  "<!-- 已移除 Flash 占位 -->", html, flags=re.S)

    return html


def _page_title(html: str) -> str:
    """从 <title> 或首个 <h1> 提取页面标题（用于兜底的 description / JSON-LD）。"""
    for pat in (r"<title[^>]*>(.*?)</title>", r"<h1[^>]*>(.*?)</h1>"):
        m = re.search(pat, html, flags=re.I | re.S)
        if m:
            t = re.sub(r"<[^>]+>", "", m.group(1))
            t = re.sub(r"\s+", " ", t).replace('"', "'").strip()
            if t:
                return t
    return "网页页面"


def ensure_head_meta(html: str, title: str) -> str:
    """补齐 meta description 与 WebSite JSON-LD（缺失时用 title 派生通用内容）。"""
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
    """页面无任何标题时，用 title 补一个 <h1>。"""
    if "<h1" not in html:
        html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + "<h1>" + title + "</h1>",
                      html, count=1, flags=re.I)
    return html


def finalize(html: str) -> str:
    """统一兜底：纯结构项 + 通用 meta + 兜底 h1。LLM 产出后也用它做安全网。"""
    html = ensure_basics(html)
    title = _page_title(html)
    html = ensure_head_meta(html, title)
    return ensure_h1(html, title)


def optimize_with_rules(source: str) -> str:
    """无 LLM 时的完整兜底（等价于 finalize(source)）。"""
    return finalize(source)


# ============ URL 约束：保留原始集合 + 去除虚构资源 ============

def extract_urls(html: str) -> set:
    """提取 html 中所有 href / src / action 指向的 URL。"""
    return set(re.findall(r'(?:href|src|action)\s*=\s*["\']([^"\']+)["\']', html))


def strip_fictional_resources(source: str, html: str) -> str:
    """删除输出中源页面不存在的静态资源引用（样式表 / 外部脚本），避免虚构资源。"""
    src_urls = extract_urls(source)
    # 样式表：源里没有对应 URL 的 <link rel="stylesheet"> 直接移除
    html = re.sub(
        r'<link\b[^>]*rel=["\']stylesheet["\'][^>]*>',
        lambda m: m.group(0) if (extract_urls(m.group(0)) & src_urls) else "",
        html, flags=re.I,
    )
    # 外部脚本：源里没有对应 URL 的 <script src="..."></script> 直接移除
    html = re.sub(
        r'<script\b[^>]*\bsrc\s*=[^>]*>\s*</script>',
        lambda m: m.group(0) if (extract_urls(m.group(0)) & src_urls) else "",
        html, flags=re.I,
    )
    return html


def strip_fabricated_content(source: str, html: str) -> str:
    """移除输出中源页面不存在的「内容元素」：
    1. 伪造的 <a> 链接（href 不在源 URL 集合里）
    2. 源里没有的整块 <nav> / <footer>
    """
    src_urls = extract_urls(source)

    # 1) 移除伪造链接（整个 <a>...</a>）
    def keep_a(m):
        tag = m.group(0)
        hrefs = extract_urls(tag)
        if not hrefs:  # 无 href 的 <a>（如 <a name=...>）保留
            return tag
        return tag if all(h in src_urls for h in hrefs) else ""
    html = re.sub(r'<a\b[^>]*>.*?</a>', keep_a, html, flags=re.I | re.S)

    # 2) 源里没有的整块 nav / footer 删除
    if "<nav" not in source.lower():
        html = re.sub(r'<nav\b[^>]*>.*?</nav>', "", html, flags=re.I | re.S)
    if "<footer" not in source.lower():
        html = re.sub(r'<footer\b[^>]*>.*?</footer>', "", html, flags=re.I | re.S)

    return html


# ============ 自检 ============

CHECKS = [
    ('viewport', 'name="viewport"'),
    ('meta description', 'name="description"'),
    ('JSON-LD', 'application/ld+json'),
    ('h1 标题', '<h1'),
    ('移除 Flash', 'x-shockwave-flash'),
]


def self_check(source: str, html: str) -> int:
    failed = 0
    print("[自检] 优化后页面关键项：")
    # lang：根 <html> 需有 lang 属性（值不限，跟随页面实际语言）
    lang_ok = bool(re.search(r"<html[^>]*\blang\s*=\s*[\"']", html, flags=re.I))
    print(f"  {'✅' if lang_ok else '❌'} lang 属性")
    if not lang_ok:
        failed += 1
    for name, needle in CHECKS:
        # Flash 项是“应不存在”
        ok = (needle not in html) if name == '移除 Flash' else (needle in html)
        print(f"  {'✅' if ok else '❌'} {name}")
        if not ok:
            failed += 1

    # 原始 URL 集合保留校验
    src_urls = extract_urls(source)
    out_urls = extract_urls(html)
    missing = sorted(u for u in src_urls if u not in out_urls)
    print(f"  {'✅' if not missing else '❌'} 原始 URL 全部保留（{len(src_urls)} 个）")
    for u in missing:
        print(f"     丢失: {u}")
    if missing:
        failed += 1

    # 虚构资源校验（新增 .css/.js 引用）
    fictional = sorted(u for u in out_urls - src_urls
                       if u.lower().endswith((".css", ".js")))
    print(f"  {'✅' if not fictional else '❌'} 无新增虚构资源（css/js）")
    for u in fictional:
        print(f"     新增: {u}")
    if fictional:
        failed += 1

    # 新增链接校验（<a> 的 href 都应来自源页面）
    a_hrefs = set(re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\']', html))
    new_links = sorted(h for h in a_hrefs if h not in src_urls)
    print(f"  {'✅' if not new_links else '❌'} 无新增链接（<a>）")
    for h in new_links:
        print(f"     新增: {h}")
    if new_links:
        failed += 1

    return failed


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def fetch_url(url: str) -> str:
    """抓取 URL 的 HTML：优先 Playwright 渲染 JS，未安装/失败则回退 urllib。"""
    html = _fetch_playwright(url)
    if html is not None:
        return html
    print("[信息] Playwright 不可用或失败，回退 urllib 抓取 ...", file=sys.stderr)
    return _fetch_urllib(url)


def _fetch_playwright(url: str) -> str | None:
    """用无头浏览器渲染 JS 并返回最终 HTML；不可用/失败返回 None。"""
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
                page.wait_for_timeout(1500)  # 等待 JS 首屏渲染
                return page.content()
            finally:
                browser.close()
    except Exception:
        return None


def _fetch_urllib(url: str) -> str:
    """urllib 兜底抓取（按响应头解码，失败则 utf-8 替换）。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (AgenticPage Optimizer)",
                      "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace")


def url_to_filename(url: str) -> str:
    """把 URL 转成安全的本地文件名（不含扩展名）。"""
    u = url.split("#", 1)[0].split("?", 1)[0]
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"[^A-Za-z0-9._-]+", "_", u).strip("_")
    return u or "page"


def main():
    parser = argparse.ArgumentParser(description="读取源 HTML + prompt 生成优化页面")
    parser.add_argument("source", nargs="?", default=str(BASE_DIR / "test.html"),
                        help="源 HTML 路径（默认 ./test.html）")
    parser.add_argument("-p", "--prompt", default=str(DEFAULT_PROMPT),
                        help="优化指令路径（默认 ./prompt.txt）")
    parser.add_argument("-o", "--output", help="输出 HTML 路径（默认 <source>_optimized.html）")
    parser.add_argument("--no-llm", action="store_true", help="强制规则引擎")
    parser.add_argument("--debug", action="store_true", help="失败时打印堆栈")
    args = parser.parse_args()

    if is_url(args.source):
        print(f"[信息] 抓取 URL: {args.source}")
        source = fetch_url(args.source)
        out_path = Path(args.output) if args.output else \
            BASE_DIR / (url_to_filename(args.source) + "_optimized.html")
    else:
        src_path = Path(args.source)
        if not src_path.exists():
            print(f"[错误] 找不到源文件: {src_path}", file=sys.stderr)
            sys.exit(1)
        source = src_path.read_text(encoding="utf-8")
        out_path = Path(args.output) if args.output else \
            src_path.with_name(src_path.stem + "_optimized.html")
    prompt = ""
    if Path(args.prompt).exists():
        prompt = Path(args.prompt).read_text(encoding="utf-8")
    else:
        print(f"[警告] 未找到 prompt 文件: {args.prompt}，LLM 将缺少 a14y 问题参考", file=sys.stderr)

    target_url = args.source if is_url(args.source) else None

    result = None
    if not args.no_llm:
        if get_config("generate").api_key:
            # 已配置 key：调用 LLM，失败则直接中止，不再静默降级
            try:
                result = optimize_with_llm(source, target_url, prompt)
            except Exception as e:  # noqa: BLE001
                print(f"[错误] LLM 优化失败（已配置 API key，中止）: {e}", file=sys.stderr)
                if args.debug:
                    import traceback
                    traceback.print_exc()
                sys.exit(1)

    if not result:
        print("[优化] 使用规则引擎生成 ...")
        result = optimize_with_rules(source)
    else:
        # LLM 只替换了 <head>；补齐结构项与兜底 meta/h1（body 原样 → URL 零丢失）
        result = finalize(result)

    result = strip_fictional_resources(source, result)
    result = strip_fabricated_content(source, result)

    # 安全网：理论上 body 未动不会丢 URL；万一仍丢失则回退规则引擎（保留全部链接）。
    lost = extract_urls(source) - extract_urls(result)
    if lost:
        print(f"[警告] 输出丢失 {len(lost)} 个原始 URL，改用规则引擎（保留全部链接）...",
              file=sys.stderr)
        result = optimize_with_rules(source)
        result = strip_fictional_resources(source, result)
        result = strip_fabricated_content(source, result)

    out_path.write_text(result + "\n", encoding="utf-8")
    print(f"[输出] 优化后页面已写入 {out_path}")

    failed = self_check(source, result)
    if failed:
        print(f"[自检] {failed} 项未通过", file=sys.stderr)
        sys.exit(1)
    print("[自检] 全部通过 ✔")


if __name__ == "__main__":
    main()
