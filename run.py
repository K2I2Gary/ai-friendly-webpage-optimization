#!/usr/bin/env python3
"""
One-shot pipeline runner
------------------------
python run.py <target> [--pipeline "eval,reflect,generate"]

LLM model / provider / key are configured in llm_config.json in the project root;
this script only orchestrates the stages.

target supports:
  - Local HTML file: start a local server -> evaluate -> reflect -> optimize (full 3 stages)
  - http(s) URL: evaluate -> reflect -> optimize (auto-fetch the URL's HTML, full 3 stages)

The pipeline is recomposable: default eval -> reflect -> generate (see the PIPELINE constant
below), or override with --pipeline, e.g.:
  python run.py test.html --pipeline "eval,reflect,generate,eval,reflect,generate"
  python run.py test.html --pipeline "eval->reflect->generate->reflect->generate"

Examples:
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
from urllib.parse import urlparse

from common import OUTPUT_DIR, console_utf8, is_url, url_to_filename
from llm import PROVIDERS, DEFAULT_PROVIDER, resolve_config

BASE_DIR = Path(__file__).resolve().parent


# ============ Recomposable pipeline definition ============
# Each stage maps to a standalone script. Reorder / repeat stage names in PIPELINE to recompose.
#   needs_url     : the stage needs the "current target" exposed as a URL (local files get a
#                   temporary http server).
#   produces_page : the stage emits a new HTML file; the pipeline updates the "current target"
#                   from it so a later eval stage automatically scores the newly generated page
#                   (iterative refinement).
STAGES = {
    "eval":     dict(script="ai_eval.py",       needs_url=True,  produces_page=False,
                     label="Evaluate"),
    "reflect":  dict(script="reflect.py",       needs_url=False, produces_page=False,
                     label="Reflect"),
    "generate": dict(script="optimize_page.py", needs_url=False, produces_page=True, needs_page=True,
                     label="Generate/Optimize"),
    "site":     dict(script="generate_site.py", needs_url=False, produces_page=False, needs_page=True,
                     label="Generate site files"),
}
PIPELINE = ["eval", "reflect", "generate", "site"]  # Default pipeline: edit here to recompose


def canonical_stages():
    """Dedupe by script and return the canonical stage names (for --list-stages / error hints)."""
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
        print(f"[workflow] {label} failed (exit={proc.returncode}), aborting.", file=sys.stderr)
        sys.exit(proc.returncode)


def kill_tree(proc):
    """Best-effort kill of the process tree (http.server child on Windows)."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass


def generated_output(target: str) -> Path:
    """Infer the generate stage's output path (mirrors optimize_page.py's default naming)."""
    if is_url(target):
        return OUTPUT_DIR / (url_to_filename(target) + "_optimized.html")
    src = Path(target)
    return OUTPUT_DIR / (src.stem + "_optimized.html")


class LocalServer:
    """Lazy local-server manager: serve a directory so local files are exposed as URLs."""

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
            self._wait_ready(port, proc)
        port, _ = self._procs[directory]
        return f"http://127.0.0.1:{port}/{path.name}"

    def _wait_ready(self, port: int, proc, timeout: float = 5.0) -> None:
        """Poll until the http.server accepts connections (bail early if the process exits)."""
        import socket
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    return
            except OSError:
                time.sleep(0.1)

    def stop_all(self):
        for _, (_, proc) in self._procs.items():
            kill_tree(proc)
        self._procs.clear()


def parse_pipeline(text: str) -> list:
    """Parse 'eval,reflect,generate' / 'eval->reflect->generate' into a list of stage names.

    `->` is normalized to `,` first, so hyphens are NOT separators and future stage names may
    legitimately contain them.
    """
    return [s for s in re.split(r"[,>\s]+", text.replace("->", ",")) if s]


def _site_base_url(target: str, current: str, server) -> str:
    """Base URL for the site-file stage: the remote origin for URLs, else the local server root."""
    ref = target if is_url(target) else server.url_for(Path(current))
    p = urlparse(ref)
    return f"{p.scheme}://{p.netloc}/"


def run_pipeline(pipeline, target, server):
    """Run stages in order; the generate stage advances current_target for later eval reuse."""
    py = sys.executable
    current = target
    n = len(pipeline)
    for i, stage in enumerate(pipeline, 1):
        meta = STAGES[stage]
        label = f"Stage {i}/{n} {meta['label']} ({meta['script']})"
        if meta["needs_url"]:
            url = current if is_url(current) else server.url_for(Path(current))
            run_step([py, str(BASE_DIR / meta["script"]), url], label)
        elif meta.get("needs_page"):
            cmd = [py, str(BASE_DIR / meta["script"]), current]
            if stage == "site":
                cmd += ["--base-url", _site_base_url(target, current, server)]
            run_step(cmd, label)
            if meta["produces_page"]:
                current = str(generated_output(current))
        else:
            run_step([py, str(BASE_DIR / meta["script"])], label)


def main():
    console_utf8()
    parser = argparse.ArgumentParser(description="One-shot run of the evaluate->reflect->optimize workflow")
    parser.add_argument("target", nargs="?",
                        help="local HTML file path, or http(s) URL")
    parser.add_argument("--port", type=int, default=8765,
                        help="local server port (local-file mode, default 8765)")
    parser.add_argument("--pipeline", help="override the pipeline, e.g. 'eval,reflect,generate' or "
                                          "'eval->reflect->generate->reflect->generate'")
    parser.add_argument("--skip-optimize", action="store_true", help="skip the generate/optimize stage")
    parser.add_argument("--list", action="store_true", help="list available providers")
    parser.add_argument("--list-stages", action="store_true", help="list available stages")
    args = parser.parse_args()

    if args.list:
        print("Available providers (name -> base_url / default model):")
        for name, meta in PROVIDERS.items():
            mark = " (default)" if name == DEFAULT_PROVIDER else ""
            print(f"  {name:<12} {meta['base_url']:<46} model={meta['model']}{mark}")
        return

    if args.list_stages:
        print("Available stages (edit the PIPELINE constant or use --pipeline to recompose):")
        for name in canonical_stages():
            meta = STAGES[name]
            print(f"  {name:<10} {meta['script']:<18} {meta['label']}")
        print(f"\nDefault pipeline: {' -> '.join(PIPELINE)}")
        return

    if not args.target:
        parser.print_help()
        return

    # Build the pipeline: default PIPELINE, overridden by --pipeline, with --skip-optimize dropping generate
    pipeline = list(PIPELINE)
    if args.pipeline:
        pipeline = parse_pipeline(args.pipeline)
    if args.skip_optimize:
        pipeline = [s for s in pipeline
                    if STAGES[s]["script"] not in ("optimize_page.py", "generate_site.py")]

    unknown = [s for s in pipeline if s not in STAGES]
    if unknown:
        print(f"[error] unknown stage(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"  available: {', '.join(canonical_stages())}", file=sys.stderr)
        sys.exit(1)
    if not pipeline:
        print("[workflow] pipeline is empty, nothing to run.", file=sys.stderr)
        sys.exit(1)

    cfg = resolve_config()
    print(f"[workflow] LLM: provider={cfg.provider} model={cfg.model} base_url={cfg.base_url}")
    if not cfg.api_key:
        print("[workflow] warning: no API key configured; stages will fall back to the rule engine.", file=sys.stderr)
    print(f"[workflow] pipeline: {' -> '.join(pipeline)}")

    # Resolve a local file to an absolute path first; a URL is used as-is
    target = args.target
    server = LocalServer(sys.executable, args.port)
    try:
        if not is_url(target):
            path = Path(target).resolve()
            if not path.exists():
                print(f"[error] file not found: {path}", file=sys.stderr)
                sys.exit(1)
            target = str(path)

        run_pipeline(pipeline, target, server)
        print("\n[workflow] all done")
    finally:
        server.stop_all()


if __name__ == "__main__":
    main()
