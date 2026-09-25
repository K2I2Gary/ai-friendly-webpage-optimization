#!/usr/bin/env python3
"""
End-to-end evaluation of the page optimization result
-------------------------------------------------------
Evaluates the FINAL optimized page (e.g. test_optimized.html) against its source
(test.html) across two independent lenses, per evaluation_plan.json:

  A. Objective scoring  (deterministic, no LLM)
       A1  a14y score before/after (PAGE-LEVEL checks only, see README note #7)
       A2  content integrity (HARD GATE: URL preservation / text similarity /
           no fabricated content)
       A3  structural & metadata completeness (13-item checklist)
       -> objective_score = 0.6*a14y_after + 0.25*integrity + 0.15*structural

  B. Subjective scoring (LLM-as-judge, rubric-based, 1-5 scale, N runs)
       6 dimensions: metadata_accuracy / content_fidelity / readability_improvement /
       style_quality / structured_data_quality / overall_quality
       -> subjective_score = mean over dimensions (x20 -> 0-100)

  C. Final aggregation
       final_score = 0.6*objective + 0.4*subjective ; verdict PASS / FAIL / REVIEW

Usage:
    python evaluate_result.py test.html test_optimized.html
    python evaluate_result.py test.html test_optimized.html --no-llm --skip-a14y
    python evaluate_result.py test.html test_optimized.html --judge-runs 3 --out-dir ./eval
"""

import argparse
import json
import re
import socket
import statistics
import subprocess
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

from common import BASE_DIR, console_utf8, is_url
from llm import chat, get_config
from optimize_page import _json_ld_valid, _parse, _urls, fetch_url

# Windows console defaults to GBK; force stdout/stderr to UTF-8
console_utf8()

OUTPUT_DIR = BASE_DIR / "eval"
OUTPUT_JSON = OUTPUT_DIR / "evaluation_report.json"
OUTPUT_MD = OUTPUT_DIR / "evaluation_report.md"

# ============ Tunable config (mirrors evaluation_plan.json config_defaults) ============
OBJECTIVE_WEIGHTS = {"a14y_page_after": 0.45, "a14y_site_after": 0.15, "integrity": 0.25, "structural": 0.15}
FINAL_WEIGHTS = {"objective": 0.6, "subjective": 0.4}
TEXT_SIMILARITY_THRESHOLD = 0.95
PASS_THRESHOLDS = {"final_score": 70, "objective_score": 70, "subjective_1to5": 3.5}
JUDGE_RUNS = 3
JUDGE_TEMPERATURE = 0.7
JUDGE_VARIANCE_FLAG = 1.0

# ============ Text helpers ============


def _visible_text(html: str) -> str:
    """Body text (scripts/styles stripped), whitespace-normalized. Falls back to the whole
    document when there is no <body>."""
    soup = _parse(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body = soup.find("body")
    root = body if body is not None else soup
    return re.sub(r"\s+", " ", root.get_text(" ", strip=True)).strip()


def _text_ratio(html: str) -> float:
    """Fraction of the raw HTML that is visible text (a14y html.text-ratio proxy)."""
    total = len(html)
    return len(_visible_text(html)) / total if total else 0.0


def _similarity(before_html: str, after_html: str) -> float:
    """Normalized visible-text similarity between two pages (0.0-1.0)."""
    a, b = _visible_text(before_html), _visible_text(after_html)
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _jsonld_texts(html: str) -> list:
    soup = _parse(html)
    out = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        t = (s.string or s.get_text()).strip()
        if t:
            out.append(t)
    return out


def _readable_summary(html: str, max_chars: int = 3500) -> str:
    """Compact human-readable digest of a page, for the subjective judge."""
    soup = _parse(html)
    parts = []
    title = soup.find("title")
    if title and title.get_text(strip=True):
        parts.append("TITLE: " + title.get_text(" ", strip=True))
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        parts.append("DESCRIPTION: " + desc.get("content", "").strip())
    headings = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"])
                if h.get_text(strip=True)]
    if headings:
        parts.append("HEADINGS: " + " | ".join(headings[:20]))
    text = _visible_text(html)
    if text:
        parts.append("BODY TEXT:\n" + text[:max_chars])
    ld = _jsonld_texts(html)
    if ld:
        parts.append("JSON-LD:\n" + "\n".join(ld)[:800])
    return "\n\n".join(parts)


# ============ Local server (to expose local files to the a14y CLI) ============


class _LocalServer:
    """Serve local files as http URLs (one http.server per directory)."""

    def __init__(self, base_port=8765):
        self.base_port = base_port
        self._procs = {}

    def url_for(self, path: Path) -> str:
        directory = path.parent
        if directory not in self._procs:
            port = self.base_port + len(self._procs)
            proc = subprocess.Popen(
                [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                cwd=str(directory), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._procs[directory] = (port, proc)
            self._wait_ready(port, proc)
        port, _ = self._procs[directory]
        return f"http://127.0.0.1:{port}/{path.name}"

    def _wait_ready(self, port, proc, timeout=5.0):
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
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
        self._procs.clear()


# ============ Step 1: load inputs ============


def load_html(target: str) -> str:
    """Return the HTML text for a local file path or an http(s) URL."""
    if is_url(target):
        return fetch_url(target)
    path = Path(target)
    if not path.exists():
        raise FileNotFoundError(f"input not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


# ============ A1: a14y scoring (page-level) ============


def run_a14y(url: str) -> dict | None:
    """Run `a14y check <url> -o json -m page`; return the parsed scorecard, or None + a
    printed warning when the CLI is missing / errors."""
    import shutil
    a14y_bin = shutil.which("a14y")
    if not a14y_bin:
        print("[a14y] executable not found -> skipping A1 (install via `npm install -g a14y`)",
              file=sys.stderr)
        return None
    cmd = [a14y_bin, "check", url, "-o", "json", "-m", "page"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=600)
    except Exception as e:  # noqa: BLE001
        print(f"[a14y] run failed: {e} -> skipping A1", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"[a14y] exit={result.returncode} -> skipping A1", file=sys.stderr)
        return None
    stdout = result.stdout
    brace = stdout.find("{")
    if brace > 0:
        stdout = stdout[brace:]
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        print(f"[a14y] JSON parse failed: {e} -> skipping A1", file=sys.stderr)
        return None


def _page_metrics(scorecard: dict) -> dict:
    """PAGE-LEVEL metrics: pages[].summary.score + flattened page checks (siteChecks excluded)."""
    pages = scorecard.get("pages", [])
    if not pages:
        return {"score": None, "checks": []}
    checks = []
    for p in pages:
        checks.extend(p.get("checks", []))
    score = pages[0].get("summary", {}).get("score")
    return {"score": score, "checks": checks}


def _site_metrics(scorecard: dict) -> dict:
    """SITE-LEVEL metrics: siteChecks + flat-pool score over them (pages excluded)."""
    checks = scorecard.get("siteChecks", [])
    applicable = [c for c in checks if c.get("status") in ("pass", "fail", "warn", "error")]
    passed = [c for c in applicable if c.get("status") == "pass"]
    score = round(len(passed) / len(applicable) * 100) if applicable else None
    return {"score": score, "checks": checks, "passed": len(passed), "applicable": len(applicable)}


def _check_flips(before_checks: list, after_checks: list) -> dict:
    b = {c.get("id"): c.get("status") for c in before_checks}
    a = {c.get("id"): c.get("status") for c in after_checks}
    flips = {
        "fail_to_pass": 0, "warn_to_pass": 0, "pass_to_fail": 0,
        "unchanged_fail": 0, "unchanged_pass": 0, "other": 0,
    }
    for cid in set(b) | set(a):
        bs, as_ = b.get(cid, "na"), a.get(cid, "na")
        if bs == "fail" and as_ == "pass":
            flips["fail_to_pass"] += 1
        elif bs == "warn" and as_ == "pass":
            flips["warn_to_pass"] += 1
        elif bs == "pass" and as_ == "fail":
            flips["pass_to_fail"] += 1
        elif bs == "fail" and as_ == "fail":
            flips["unchanged_fail"] += 1
        elif bs == "pass" and as_ == "pass":
            flips["unchanged_pass"] += 1
        else:
            flips["other"] += 1
    return flips


# ============ A2: content integrity (hard gate) ============


def _integrity(before_html: str, after_html: str) -> dict:
    src_urls = _urls(before_html)
    out_urls = _urls(after_html)

    missing = sorted(src_urls - out_urls)
    urls_preserved = len(missing) == 0

    sim = _similarity(before_html, after_html)

    out_soup = _parse(after_html)
    a_hrefs = {a.get("href") for a in out_soup.find_all("a") if a.get("href")}
    new_links = sorted(h for h in a_hrefs if h not in src_urls)
    fictional = sorted(u for u in out_urls - src_urls if u.lower().endswith((".css", ".js")))

    src_soup = _parse(before_html)
    new_nav = len(out_soup.find_all("nav")) > len(src_soup.find_all("nav"))
    new_footer = len(out_soup.find_all("footer")) > len(src_soup.find_all("footer"))

    no_new_links = len(new_links) == 0
    no_fictional_resources = len(fictional) == 0
    no_new_nav_footer = not (new_nav or new_footer)
    no_fabricated_content = no_new_links and no_fictional_resources and no_new_nav_footer

    integrity_score = (
        0.3 * (100 if urls_preserved else 0)
        + 0.4 * (sim * 100)
        + 0.3 * (100 if no_fabricated_content else 0)
    )
    hard_gate_failed = not (
        urls_preserved and sim >= TEXT_SIMILARITY_THRESHOLD and no_fabricated_content
    )

    return {
        "urls_preserved": urls_preserved,
        "missing_urls": missing,
        "text_similarity": round(sim, 4),
        "no_new_links": no_new_links,
        "new_links": new_links,
        "no_fictional_resources": no_fictional_resources,
        "fictional_resources": fictional,
        "no_new_nav_footer": no_new_nav_footer,
        "no_fabricated_content": no_fabricated_content,
        "integrity_score": round(integrity_score, 2),
        "hard_gate_failed": hard_gate_failed,
    }


# ============ A3: structural & metadata completeness ============

STRUCT_ITEMS = [
    ("title_present", 10),
    ("meta_description_ge_50_chars", 10),
    ("og_title", 5),
    ("og_description", 5),
    ("og_url", 3),
    ("og_type", 3),
    ("canonical", 6),
    ("lang_attribute", 6),
    ("viewport", 6),
    ("jsonld_valid_and_semantic", 16),
    ("h1_exactly_one", 10),
    ("h2_at_least_two", 10),
    ("text_ratio_improved", 10),
]


def _meta_ok(soup, key, attr="name"):
    return any(m.get(attr) == key and (m.get("content") or "").strip()
               for m in soup.find_all("meta"))


def _jsonld_semantic(html: str) -> bool:
    if not _json_ld_valid(html):
        return False
    for t in _jsonld_texts(html):
        try:
            d = json.loads(t)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("@context") and d.get("@type"):
            return True
    return False


def _structural(before_html: str, after_html: str) -> dict:
    soup = _parse(after_html)

    title = soup.find("title")
    title_ok = bool(title and title.get_text(strip=True))
    desc = soup.find("meta", attrs={"name": "description"})
    desc_ok = bool(desc and desc.get("content") and len(desc.get("content", "").strip()) >= 50)
    canonical_ok = any(
        t.name == "link" and "canonical" in (t.get("rel") or []) and t.get("href")
        for t in soup.find_all("link")
    )
    lang_ok = bool(soup.find("html") and soup.find("html").get("lang"))

    checks = {
        "title_present": title_ok,
        "meta_description_ge_50_chars": desc_ok,
        "og_title": _meta_ok(soup, "og:title", "property"),
        "og_description": _meta_ok(soup, "og:description", "property"),
        "og_url": _meta_ok(soup, "og:url", "property"),
        "og_type": _meta_ok(soup, "og:type", "property"),
        "canonical": canonical_ok,
        "lang_attribute": lang_ok,
        "viewport": _meta_ok(soup, "viewport", "name"),
        "jsonld_valid_and_semantic": _jsonld_semantic(after_html),
        "h1_exactly_one": len(soup.find_all("h1")) == 1,
        "h2_at_least_two": len(soup.find_all("h2")) >= 2,
        "text_ratio_improved": _text_ratio(after_html) > _text_ratio(before_html),
    }

    items = [
        {"id": cid, "weight": w, "satisfied": checks[cid]}
        for cid, w in STRUCT_ITEMS
    ]
    structural_score = round(sum(it["weight"] for it in items if it["satisfied"]), 2)
    return {
        "items": items,
        "structural_score": structural_score,
        "text_ratio_before": round(_text_ratio(before_html), 4),
        "text_ratio_after": round(_text_ratio(after_html), 4),
    }


# ============ A: objective scoring ============


def objective_score(a14y_page_after, a14y_site_after, integrity, structural) -> tuple:
    """Return (score, used_weights). Reweights integrity/structural when a14y is unavailable."""
    if a14y_page_after is not None:
        w = OBJECTIVE_WEIGHTS
    else:
        denom = OBJECTIVE_WEIGHTS["integrity"] + OBJECTIVE_WEIGHTS["structural"]
        w = {
            "a14y_page_after": 0.0,
            "a14y_site_after": 0.0,
            "integrity": OBJECTIVE_WEIGHTS["integrity"] / denom,
            "structural": OBJECTIVE_WEIGHTS["structural"] / denom,
        }
    score = (w["a14y_page_after"] * (a14y_page_after or 0)
             + w["a14y_site_after"] * (a14y_site_after or 0)
             + w["integrity"] * integrity["integrity_score"]
             + w["structural"] * structural["structural_score"])
    return round(score, 2), w


# ============ B: subjective scoring (LLM-as-judge) ============

JUDGE_DIMENSIONS = [
    ("metadata_accuracy",
     "Do title / meta description / og tags accurately and concisely reflect the page "
     "content, with no hallucinated facts?",
     {"1": "fabricated or irrelevant", "3": "partially relevant, minor errors",
      "5": "precisely reflects content, no hallucination"}),
    ("content_fidelity",
     "Is the meaning and information content preserved (nothing important added, removed, "
     "or changed)?",
     {"1": "major content lost or changed", "3": "minor phrasing changes only",
      "5": "content meaning identical"}),
    ("readability_improvement",
     "How much clearer / more AI-readable is the optimized page than the original?",
     {"1": "worse or confusing", "3": "moderate clarity gain",
      "5": "dramatically clearer and better structured"}),
    ("style_quality",
     "Is the injected design system appropriate, consistent, responsive, and non-destructive "
     "to existing styles?",
     {"1": "broken or conflicting", "3": "acceptable but generic",
      "5": "cohesive, on-brand, responsive, non-destructive"}),
    ("structured_data_quality",
     "Is the JSON-LD semantically correct and grounded in real page content (not fabricated)?",
     {"1": "invalid or irrelevant", "3": "valid but generic / partially wrong",
      "5": "valid, semantically grounded, rich"}),
    ("overall_quality",
     "Holistic judgment: would you ship this optimized page as-is?",
     {"1": "poor", "3": "acceptable", "5": "excellent"}),
]


def _judge_system_prompt() -> str:
    lines = [
        "You are an expert web-page quality reviewer evaluating an AI-readability optimization.",
        "You are shown two versions of the SAME page: ORIGINAL and OPTIMIZED.",
        "Score the OPTIMIZED version on 6 dimensions, each an integer 1-5 (5 = best), and give "
        "a one-line justification per dimension.",
        "",
        "Dimensions and rubric anchors:",
    ]
    for dim_id, label, rubric in JUDGE_DIMENSIONS:
        anchors = " ; ".join(f"{k}={v}" for k, v in rubric.items())
        lines.append(f"- {dim_id}: {label} [{anchors}]")
    lines += [
        "",
        "Respond with ONLY a JSON object (no markdown fences, no prose) in this exact shape:",
        '{"scores": {"metadata_accuracy": <int>, "content_fidelity": <int>, '
        '"readability_improvement": <int>, "style_quality": <int>, '
        '"structured_data_quality": <int>, "overall_quality": <int>},',
        '"justification": {"metadata_accuracy": "<one line>", "content_fidelity": "<one line>", '
        '"readability_improvement": "<one line>", "style_quality": "<one line>", '
        '"structured_data_quality": "<one line>", "overall_quality": "<one line>"}}',
    ]
    return "\n".join(lines)


def _judge_user_prompt(before_html: str, after_html: str) -> str:
    return ("=== ORIGINAL PAGE ===\n" + _readable_summary(before_html) +
            "\n\n=== OPTIMIZED PAGE ===\n" + _readable_summary(after_html))


def _extract_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _run_judge_once(before_html: str, after_html: str, cfg, temperature: float):
    raw = chat(
        [
            {"role": "system", "content": _judge_system_prompt()},
            {"role": "user", "content": _judge_user_prompt(before_html, after_html)},
        ],
        temperature=temperature,
        max_tokens=2000,
        config=cfg,
    )
    data = _extract_json(raw)
    if not data:
        return None
    scores = data.get("scores") or {}
    justification = data.get("justification") or {}
    for dim_id, _, _ in JUDGE_DIMENSIONS:
        v = scores.get(dim_id)
        if not isinstance(v, int) or not 1 <= v <= 5:
            return None
    return {"scores": {d: scores[d] for d, _, _ in JUDGE_DIMENSIONS},
            "justification": {d: justification.get(d, "") for d, _, _ in JUDGE_DIMENSIONS}}


def _subjective(before_html: str, after_html: str, cfg, runs: int, temperature: float):
    if not cfg.api_key:
        print("[judge] no API key -> skipping subjective scoring", file=sys.stderr)
        return None

    judgments = []
    for i in range(runs):
        print(f"[judge] run {i + 1}/{runs} (provider={cfg.provider}, model={cfg.model}) ...")
        try:
            j = _run_judge_once(before_html, after_html, cfg, temperature)
        except Exception as e:  # noqa: BLE001
            print(f"[judge] run {i + 1} failed: {e}", file=sys.stderr)
            j = None
        if j:
            judgments.append(j)

    if not judgments:
        print("[judge] no valid judgments -> subjective unavailable", file=sys.stderr)
        return None

    dims = [d for d, _, _ in JUDGE_DIMENSIONS]
    per_dim = {}
    for d in dims:
        vals = [j["scores"][d] for j in judgments]
        per_dim[d] = {
            "mean": round(statistics.mean(vals), 2),
            "std": round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0,
        }
    high_variance = [d for d in dims if per_dim[d]["std"] >= JUDGE_VARIANCE_FLAG]

    score_1to5 = statistics.mean(per_dim[d]["mean"] for d in dims)
    return {
        "runs_requested": runs,
        "runs_completed": len(judgments),
        "per_dimension": per_dim,
        "high_variance_flags": high_variance,
        "subjective_score_1to5": round(score_1to5, 2),
        "subjective_score_0to100": round(score_1to5 * 20, 2),
        "justifications": [j["justification"] for j in judgments],
    }


# ============ C: final aggregation ============


def _verdict(hard_gate_failed, objective, subjective) -> tuple:
    if hard_gate_failed:
        return "FAIL", "content-integrity hard gate triggered"
    if subjective is not None:
        subj_1to5 = subjective["subjective_score_1to5"]
        final = (FINAL_WEIGHTS["objective"] * objective
                 + FINAL_WEIGHTS["subjective"] * subjective["subjective_score_0to100"])
        ok = (final >= PASS_THRESHOLDS["final_score"]
              and objective >= PASS_THRESHOLDS["objective_score"]
              and subj_1to5 >= PASS_THRESHOLDS["subjective_1to5"])
        return ("PASS" if ok else "REVIEW",
                f"final={final:.1f} objective={objective:.1f} subjective(1-5)={subj_1to5}")
    final = objective
    ok = (final >= PASS_THRESHOLDS["final_score"]
          and objective >= PASS_THRESHOLDS["objective_score"])
    return ("PASS" if ok else "REVIEW", f"final={final:.1f} objective={objective:.1f} (no subjective)")


# ============ Report output ============


def _write_report(report: dict, out_json: Path, out_md: Path):
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    a14y = report["objective"]["a14y"]
    a14y_site = report["objective"]["a14y_site"]
    integ = report["objective"]["integrity"]
    struct = report["objective"]["structural"]
    subj = report["subjective"]

    def yn(v):
        return "[OK]" if v else "[X]"

    lines = [
        "# End-to-End Evaluation Report",
        "",
        f"- Original page: `{report['inputs']['original']}`",
        f"- Optimized page: `{report['inputs']['optimized']}`",
        f"- Verdict: **{report['verdict']}** — final score **{report['final_score']}/100**",
        "",
        "## 1. Objective scoring",
        "",
        "### a14y (page-level)",
        f"- Before: **{a14y['before']}** / After: **{a14y['after']}** / Delta: **{a14y['delta']}**",
        f"- Check flips: {a14y['flips']}",
        "",
        "### a14y (site-level: robots.txt / llms.txt / sitemap / AGENTS.md)",
        f"- Score: **{a14y_site['score']}/100** "
        f"({a14y_site['passed']}/{a14y_site['applicable']} applicable checks pass)",
        "",
        "### Content integrity (hard gate)",
        f"- URLs preserved: {yn(integ['urls_preserved'])}"
        f"{'  missing: ' + str(integ['missing_urls']) if integ['missing_urls'] else ''}",
        f"- Text similarity: **{integ['text_similarity']}** (threshold {TEXT_SIMILARITY_THRESHOLD})",
        f"- No new links: {yn(integ['no_new_links'])}"
        f"{'  added: ' + str(integ['new_links']) if integ['new_links'] else ''}",
        f"- No fictional css/js: {yn(integ['no_fictional_resources'])}"
        f"{'  added: ' + str(integ['fictional_resources']) if integ['fictional_resources'] else ''}",
        f"- No new nav/footer: {yn(integ['no_new_nav_footer'])}",
        f"- Hard gate: **{'FAILED' if integ['hard_gate_failed'] else 'passed'}**",
        "",
        "### Structural & metadata completeness",
    ]
    for it in struct["items"]:
        lines.append(f"- {yn(it['satisfied'])} `{it['id']}` (weight {it['weight']})")
    lines += [
        f"- text-ratio: {struct['text_ratio_before']} -> {struct['text_ratio_after']}",
        "",
        f"**Objective score: {report['objective']['score']}/100** "
        f"(weights {report['objective']['weights']})",
        "",
        "## 2. Subjective scoring (LLM-as-judge, 1-5)",
    ]
    if subj is None:
        lines.append("- not available (no API key / no valid judgments)")
    else:
        lines += [
            f"- Runs: {subj['runs_completed']}/{subj['runs_requested']}",
            "",
            "| Dimension | Mean | Std |",
            "|---|---|---|",
        ]
        for d in subj["per_dimension"]:
            flag = " [!]" if d in subj["high_variance_flags"] else ""
            lines.append(f"| {d} | {subj['per_dimension'][d]['mean']} "
                         f"| {subj['per_dimension'][d]['std']}{flag} |")
        lines += [
            "",
            f"**Subjective score: {subj['subjective_score_1to5']}/5 "
            f"({subj['subjective_score_0to100']}/100)**",
        ]
        if subj["high_variance_flags"]:
            lines.append(f"[!] high-variance dimensions: {subj['high_variance_flags']}")

    lines += [
        "",
        "## 3. Final aggregation",
        f"- final_score = {FINAL_WEIGHTS['objective']}*objective "
        f"+ {FINAL_WEIGHTS['subjective']}*subjective",
        f"- **Verdict: {report['verdict']}** — {report['verdict_reason']}",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============ Main ============


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end evaluation of the page optimization result (objective + subjective)")
    parser.add_argument("original", help="source page (local HTML file or URL)")
    parser.add_argument("optimized", help="optimized page (local HTML file or URL)")
    parser.add_argument("--skip-a14y", action="store_true", help="skip the a14y scoring stage")
    parser.add_argument("--no-llm", action="store_true", help="skip the subjective (LLM judge) stage")
    parser.add_argument("--judge-runs", type=int, default=JUDGE_RUNS, help=f"judge runs (default {JUDGE_RUNS})")
    parser.add_argument("--judge-temperature", type=float, default=JUDGE_TEMPERATURE,
                        help=f"judge temperature (default {JUDGE_TEMPERATURE})")
    parser.add_argument("--out-dir", default=str(OUTPUT_DIR), help="report output directory (default eval/)")
    parser.add_argument("--port", type=int, default=8765, help="local server base port (default 8765)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "evaluation_report.json"
    out_md = out_dir / "evaluation_report.md"

    # --- Step 1: load inputs ---
    before_html = load_html(args.original)
    after_html = load_html(args.optimized)
    print(f"[1/10] loaded original ({len(before_html)} chars) + optimized ({len(after_html)} chars)")

    server = _LocalServer(args.port)
    try:
        # --- Step 2: A1 a14y before/after ---
        a14y_before = a14y_after = a14y_delta = None
        a14y_site_after = a14y_site_passed = a14y_site_applicable = None
        flips = None
        if not args.skip_a14y:
            before_url = args.original if is_url(args.original) else server.url_for(Path(args.original).resolve())
            after_url = args.optimized if is_url(args.optimized) else server.url_for(Path(args.optimized).resolve())

            sc_before = run_a14y(before_url)
            sc_after = run_a14y(after_url)
            if sc_before and sc_after:
                mb, ma = _page_metrics(sc_before), _page_metrics(sc_after)
                a14y_before, a14y_after = mb["score"], ma["score"]
                a14y_delta = (a14y_after - a14y_before) if (a14y_before is not None and a14y_after is not None) else None
                flips = _check_flips(mb["checks"], ma["checks"])
                # Site-level checks are a shared property of the served directory (robots.txt /
                # llms.txt / sitemap apply to the whole site, not one page), so report the "after"
                # state as a single score rather than a before/after delta.
                sm = _site_metrics(sc_after)
                a14y_site_after = sm["score"]
                a14y_site_passed, a14y_site_applicable = sm["passed"], sm["applicable"]
                print(f"[2/10] a14y page: {a14y_before}->{a14y_after} (Δ{a14y_delta}); "
                      f"site: {a14y_site_after}/100 ({a14y_site_passed}/{a14y_site_applicable} pass)")
            else:
                print("[2/10] a14y unavailable -> objective falls back to integrity+structural",
                      file=sys.stderr)
        else:
            print("[2/10] a14y skipped (--skip-a14y)")

        # --- Step 3-4: A2 integrity + A3 structural ---
        integrity = _integrity(before_html, after_html)
        print(f"[3/10] integrity: urls_preserved={integrity['urls_preserved']} "
              f"similarity={integrity['text_similarity']} "
              f"no_fabricated={integrity['no_fabricated_content']}")
        structural = _structural(before_html, after_html)
        print(f"[4/10] structural score: {structural['structural_score']}/100")

        # --- Step 5: A objective ---
        obj_score, used_weights = objective_score(a14y_after, a14y_site_after, integrity, structural)
        obj_verdict = "FAIL" if integrity["hard_gate_failed"] else "PASS"
        print(f"[5/10] objective score: {obj_score}/100 (verdict {obj_verdict})")

        # --- Steps 6-8: B subjective ---
        subj = None
        if not args.no_llm:
            cfg = get_config("judge")
            print(f"[6/10] judge config: provider={cfg.provider} model={cfg.model} "
                  f"key={'set' if cfg.api_key else 'NOT set'}")
            subj = _subjective(before_html, after_html, cfg, args.judge_runs, args.judge_temperature)
            if subj:
                print(f"[7-8/10] subjective score: {subj['subjective_score_1to5']}/5 "
                      f"({subj['subjective_score_0to100']}/100) from {subj['runs_completed']} runs")
        else:
            print("[6-8/10] subjective skipped (--no-llm)")

        # --- Step 9: C final ---
        verdict, reason = _verdict(integrity["hard_gate_failed"], obj_score, subj)
        final_score = (
            (FINAL_WEIGHTS["objective"] * obj_score
             + FINAL_WEIGHTS["subjective"] * subj["subjective_score_0to100"])
            if subj is not None else obj_score
        )
        final_score = round(final_score, 2)
        print(f"[9/10] final score: {final_score}/100 -> verdict {verdict}")

        # --- Step 10: report ---
        report = {
            "title": "End-to-End Evaluation Report",
            "inputs": {"original": args.original, "optimized": args.optimized},
            "objective": {
                "a14y": {"before": a14y_before, "after": a14y_after, "delta": a14y_delta,
                         "flips": flips, "available": a14y_after is not None},
                "a14y_site": {"score": a14y_site_after, "passed": a14y_site_passed,
                              "applicable": a14y_site_applicable,
                              "available": a14y_site_after is not None},
                "integrity": integrity,
                "structural": structural,
                "score": obj_score,
                "verdict": obj_verdict,
                "weights": {k: round(v, 4) for k, v in used_weights.items()},
            },
            "subjective": subj,
            "final_score": final_score,
            "verdict": verdict,
            "verdict_reason": reason,
            "config": {
                "objective_weights": OBJECTIVE_WEIGHTS,
                "final_weights": FINAL_WEIGHTS,
                "text_similarity_threshold": TEXT_SIMILARITY_THRESHOLD,
                "pass_thresholds": PASS_THRESHOLDS,
                "judge_runs": args.judge_runs,
                "judge_temperature": args.judge_temperature,
            },
        }
        _write_report(report, out_json, out_md)
        print(f"[10/10] report written to {out_json} and {out_md}")
    finally:
        server.stop_all()

    print(f"\n[result] final={final_score}/100  verdict={verdict}")


if __name__ == "__main__":
    main()
