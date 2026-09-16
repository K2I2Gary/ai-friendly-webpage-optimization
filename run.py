#!/usr/bin/env python3
"""
一键运行工作流
----------------
python run.py <target> [--pipeline "eval,reflect,generate"]

LLM 模型 / 供应商 / 密钥统一在项目根目录 llm_config.json 配置，本脚本只负责编排阶段。

target 支持：
  - 本地 HTML 文件：起本地服务 → 评估 → 反思 → 优化（完整 3 阶段）
  - http(s) URL：评估 → 反思 → 优化（自动抓取 URL 的 HTML，完整 3 阶段）

流程可重组：默认 eval → reflect → generate（见下方 PIPELINE 常量），
也可用 --pipeline 覆盖，例如：
  python run.py test.html --pipeline "eval,reflect,generate,eval,reflect,generate"
  python run.py test.html --pipeline "eval->reflect->generate->reflect->generate"

示例：
  python run.py test.html
  python run.py https://example.com
  python run.py --list
  python run.py --list-stages
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

from llm import PROVIDERS, DEFAULT_PROVIDER, resolve_config

BASE_DIR = Path(__file__).resolve().parent


# ============ 可重组流水线定义 ============
# 每个阶段对应一个独立脚本，把阶段名按任意顺序 / 重复写入 PIPELINE 即可重组流程。
#   needs_url     ：该阶段需要把「当前目标」暴露成 URL（本地文件会临时起 http 服务）。
#   produces_page ：该阶段会产出一个新的 HTML 页面文件，流水线据此更新「当前目标」，
#                   使后续的 eval 阶段自动评估新产出的页面（迭代精修）。
STAGES = {
    "eval":     dict(script="ai_eval.py",       needs_url=True,  produces_page=False,
                     label="评估"),
    "reflect":  dict(script="reflect.py",       needs_url=False, produces_page=False,
                     label="反思"),
    "generate": dict(script="optimize_page.py", needs_url=False, produces_page=True,
                     label="生成/优化"),
}
PIPELINE = ["eval", "reflect", "generate"]  # 默认流程：改这里即可重组


def _console_utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", line_buffering=True)
        except Exception:
            pass


def canonical_stages():
    """按脚本去重，返回规范阶段名列表（供 --list-stages / 报错提示用）。"""
    seen, out = set(), []
    for name, meta in STAGES.items():
        if meta["script"] not in seen:
            seen.add(meta["script"])
            out.append(name)
    return out


def run_step(cmd, label):
    print(f"\n{'=' * 52}\n  {label}\n{'=' * 52}")
    proc = subprocess.run(cmd, cwd=str(BASE_DIR))
    if proc.returncode != 0:
        print(f"[工作流] {label} 失败 (exit={proc.returncode})，中断。", file=sys.stderr)
        sys.exit(proc.returncode)


def is_url(t):
    return t.startswith("http://") or t.startswith("https://")


def kill_tree(proc):
    """尽量杀掉进程树（Windows 下 http.server 子进程）。"""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass


def url_to_filename(url: str) -> str:
    """把 URL 转成安全的本地文件名（镜像 optimize_page.url_to_filename，需保持同步）。"""
    u = url.split("#", 1)[0].split("?", 1)[0]
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"[^A-Za-z0-9._-]+", "_", u).strip("_")
    return u or "page"


def generated_output(target: str) -> Path:
    """推算 generate 阶段产物路径（镜像 optimize_page.py 的默认命名规则）。"""
    if is_url(target):
        return BASE_DIR / (url_to_filename(target) + "_optimized.html")
    src = Path(target)
    return src.with_name(src.stem + "_optimized.html")


class LocalServer:
    """惰性本地服务管理：按目录起 http.server，把本地文件暴露成 URL。"""

    def __init__(self, py, base_port):
        self.py = py
        self.base_port = base_port
        self._procs = {}  # directory(Path) -> (port, Popen)

    def url_for(self, path: Path) -> str:
        directory = path.parent
        if directory not in self._procs:
            port = self.base_port + len(self._procs)
            proc = subprocess.Popen(
                [self.py, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                cwd=str(directory),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._procs[directory] = (port, proc)
            time.sleep(2)
        port, _ = self._procs[directory]
        return f"http://127.0.0.1:{port}/{path.name}"

    def stop_all(self):
        for _, (_, proc) in self._procs.items():
            kill_tree(proc)
        self._procs.clear()


def parse_pipeline(text: str) -> list:
    """把 'eval,reflect,generate' / 'eval->reflect->generate' 解析成阶段名列表。"""
    return [s for s in re.split(r"[,>\s-]+", text) if s]


def run_pipeline(pipeline, target, server):
    """按给定顺序执行阶段；generate 阶段会推进 current_target，供后续 eval 复用。"""
    py = sys.executable
    current = target
    n = len(pipeline)
    for i, stage in enumerate(pipeline, 1):
        meta = STAGES[stage]
        label = f"阶段 {i}/{n} {meta['label']} ({meta['script']})"
        if meta["needs_url"]:
            url = current if is_url(current) else server.url_for(Path(current))
            run_step([py, str(BASE_DIR / meta["script"]), url], label)
        elif meta["produces_page"]:
            run_step([py, str(BASE_DIR / meta["script"]), current], label)
            current = str(generated_output(current))
        else:
            run_step([py, str(BASE_DIR / meta["script"])], label)


def main():
    _console_utf8()
    parser = argparse.ArgumentParser(description="一键运行 评估→反思→优化 工作流")
    parser.add_argument("target", nargs="?",
                        help="本地 HTML 文件路径，或 http(s) URL")
    parser.add_argument("--port", type=int, default=8765,
                        help="本地服务端口（本地文件模式，默认 8765）")
    parser.add_argument("--pipeline", help="覆盖流程，如 'eval,reflect,generate' 或 "
                                          "'eval->reflect->generate->reflect->generate'")
    parser.add_argument("--skip-optimize", action="store_true", help="跳过优化/生成阶段")
    parser.add_argument("--list", action="store_true", help="列出可用供应商")
    parser.add_argument("--list-stages", action="store_true", help="列出可用阶段")
    args = parser.parse_args()

    if args.list:
        print("可用供应商（name -> base_url / 默认模型）：")
        for name, meta in PROVIDERS.items():
            mark = "（默认）" if name == DEFAULT_PROVIDER else ""
            print(f"  {name:<12} {meta['base_url']:<46} model={meta['model']} {mark}")
        return

    if args.list_stages:
        print("可用阶段（改 PIPELINE 常量或 --pipeline 重组流程）：")
        for name in canonical_stages():
            meta = STAGES[name]
            print(f"  {name:<10} {meta['script']:<18} {meta['label']}")
        print(f"\n默认流程：{' → '.join(PIPELINE)}")
        return

    if not args.target:
        parser.print_help()
        return

    # 组装流程：默认用 PIPELINE 常量，可用 --pipeline 覆盖，--skip-optimize 剔除生成阶段
    pipeline = list(PIPELINE)
    if args.pipeline:
        pipeline = parse_pipeline(args.pipeline)
    if args.skip_optimize:
        pipeline = [s for s in pipeline if STAGES[s]["script"] != "optimize_page.py"]

    unknown = [s for s in pipeline if s not in STAGES]
    if unknown:
        print(f"[错误] 未知阶段: {', '.join(unknown)}", file=sys.stderr)
        print(f"  可用阶段: {', '.join(canonical_stages())}", file=sys.stderr)
        sys.exit(1)
    if not pipeline:
        print("[工作流] 流程为空，无阶段可执行。", file=sys.stderr)
        sys.exit(1)

    cfg = resolve_config()
    print(f"[工作流] LLM: provider={cfg.provider} model={cfg.model} base_url={cfg.base_url}")
    if not cfg.api_key:
        print("[工作流] 警告：未配置 API key，各阶段将回退到规则引擎。", file=sys.stderr)
    print(f"[工作流] 流程: {' → '.join(pipeline)}")

    # 本地文件先解析为绝对路径；URL 则原样使用
    target = args.target
    server = LocalServer(sys.executable, args.port)
    try:
        if not is_url(target):
            path = Path(target).resolve()
            if not path.exists():
                print(f"[错误] 找不到文件: {path}", file=sys.stderr)
                sys.exit(1)
            target = str(path)

        run_pipeline(pipeline, target, server)
        print("\n[工作流] 全部完成 ✔")
    finally:
        server.stop_all()


if __name__ == "__main__":
    main()
