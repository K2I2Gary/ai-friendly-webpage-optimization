# Agentic Page Optimization Workflow

A web AI-readability (a14y) optimization pipeline: **evaluate → reflect → optimize**. Given a web page (a local HTML file or a URL), it automatically diagnoses why it is unfriendly to AI agents, produces an optimization instruction, and — **without changing any content or links** — completes the metadata (title / og / JSON-LD), fixes the structure (heading hierarchy), and injects a modern stylesheet, producing an AI-readable optimized page.

Each step emits a text artifact consumed by the next step. Stages can run individually or be chained in one shot.

```
input page / URL
   │
   ▼
┌─────────────┐   eval.txt   ┌─────────────┐  prompt.txt  ┌──────────────────┐
│  ai_eval.py │ ───────────> │ reflect.py  │ ───────────> │ optimize_page.py  │ ──> *_optimized.html
│  (evaluate) │              │  (reflect)  │              │  (optimize)       │
└─────────────┘              └─────────────┘              └──────────────────┘
   a14y CLI + LLM              LLM (rule fallback)           LLM + rule fallback
```

> One-liner: **eval** says "what is bad and by how much" → **reflect** says "how to fix it, by priority" → **optimize** says "fixed, here you go".

---

## Directory layout

| File | Role | Input → output |
|---|---|---|
| `run.py` | **One-shot** entry (recomposable pipeline) | target (file/URL) → runs each stage per `PIPELINE` |
| `ai_eval.py` | ① Evaluator | `URL` → `eval.txt` (a14y score + LLM deep analysis) |
| `reflect.py` | ② Reflector | `eval.txt` → `prompt.txt` (optimization instruction) |
| `optimize_page.py` | ③ Optimizer | `source HTML or URL + prompt.txt` → `*_optimized.html` |
| `evaluate_result.py` | **End-to-end evaluator** | `source + optimized HTML` → `evaluation_report.json/.md` (objective + LLM-judge scoring) |
| `llm.py` | **Unified LLM API module** | reads `llm_config.json`, wraps multiple providers for all stages |
| `llm_config.json` | **Single config source** | provider / model / base_url / api_key / per-stage overrides |
| `llm_config.example.json` | Config template | a key-less example; copy it to `llm_config.json` |
| `common.py` | Shared utilities | `console_utf8` / `is_url` / `url_to_filename` / `extract_urls`, used by all scripts |
| `rag.py` | **RAG knowledge base** | deterministic check-id lookup of fix recipes, grounding the `reflect` stage |
| `fetch_a14y_docs.py` | Docs crawler | fetches a14y official check docs → `knowledge/docs/` |
| `ab_test.py` | A/B harness | runs `reflect` with RAG on/off and compares coverage/traceability metrics |
| `generate_site.py` | Site-file generator | reads an HTML page → `robots.txt` / `llms.txt` / `sitemap.xml` / `sitemap.md` / `AGENTS.md` / `<page>.md` |
| `knowledge/` | RAG corpus | `catalog.json` + curated `checks/` + crawled `docs/` + `examples/` (verified prompts) |
| `requirements.txt` | Python deps | `openai` + `beautifulsoup4` (+ optional `playwright`) |
| `tests/` | pytest unit tests | cover the page-rewrite helpers (head merge / URL preservation / stripping) |
| `.gitignore` | Ignore list | ignores `llm_config.json` / `.env` / `__pycache__` / `output/` (workflow artifacts) |
| `test.html` | Test input | the source page being optimized |
| `output/` | Workflow artifacts | `eval.txt` / `prompt.txt` / `*_optimized.html` / site files (gitignored) |
| `sample.json` | Sample data | — |
| `evaluation_plan.json` | Evaluation spec | the two-part rubric, weights, thresholds & step plan |
| `eval/` | Evaluation output | generated `evaluation_report.json` / `.md` (gitignored) |

---

## Dependencies

- **Python 3.10+** (3.12 on this machine)
- **Node.js + `a14y`** (global CLI, used by the evaluate stage):
  ```bash
  npm install -g a14y
  ```
- **Python deps** (`openai` + `beautifulsoup4`; the rest uses the stdlib). Install with:
  ```bash
  pip install -r requirements.txt   # or: pip install openai beautifulsoup4
  ```
- **Playwright (optional, better URL fetching)**: URL mode uses it by default to render JS and fetch the real DOM; if not installed it falls back to urllib.
  ```bash
  pip install playwright
  playwright install chromium
  ```
- At least one LLM provider API key, put into `llm_config.json`'s `api_key` (defaults to DeepSeek, see below).

---

## Usage

A complete path from zero to output.

### Step 1: install dependencies (once)

```bash
npm install -g a14y                    # CLI used by the evaluate stage (needs Node.js)
pip install -r requirements.txt        # openai + beautifulsoup4
```

### Step 2: configure model & API key (edit one file only)

All model / provider / key config lives in **`llm_config.json`**. Open it and fill in `provider` / `model` / `api_key`:

```json
{
  "provider": "deepseek",
  "model": "deepseek-chat",
  "api_key": "sk-your-key"
}
```

If the file does not exist, copy `llm_config.example.json` to `llm_config.json` first.

### Step 3: run once

```bash
# ① Local HTML file: start a local server -> evaluate -> reflect -> optimize -> site files
python run.py test.html

# ② Remote URL: auto-fetch the HTML and run the full pipeline
python run.py https://example.com
```

> Want to switch provider / model? Just edit `provider` / `model` in `llm_config.json`; the command stays the same.

### Step 4: inspect the outputs

Every generated artifact lands in **`output/`** (a gitignored working directory), keeping the project root clean:

| Output | Produced by | Content |
|---|---|---|
| `output/eval.txt` | `ai_eval.py` | a14y score + LLM deep analysis + raw JSON |
| `output/prompt.txt` | `reflect.py` | actionable P0/P1/P2 optimization instructions |
| `output/*_optimized.html` | `optimize_page.py` | optimized page (`test.html` → `output/test_optimized.html`) |
| `output/robots.txt` / `llms.txt` / `sitemap.xml` / `sitemap.md` / `AGENTS.md` / `<page>.md` | `generate_site.py` | site-level AI-readability files |

### Quick command reference

```bash
python run.py --list                        # list supported providers
python run.py --list-stages                 # list available stages
python run.py test.html --port 9000         # change the local-server port (default 8765)
python run.py test.html --skip-optimize     # evaluate + reflect only, skip optimize
python run.py test.html --pipeline "eval,reflect,generate,eval,reflect,generate"  # iterative refinement
python reflect.py --no-rag                 # reflect without RAG grounding (A/B baseline)
python ab_test.py output/eval.txt          # A/B: reflect with RAG on/off + coverage metrics
python ab_test.py output/eval.txt --runs 3 # 3 runs each, averaged
python generate_site.py test.html --base-url http://127.0.0.1:8765/   # generate site files
```

---

## Running stages individually

```bash
# ① Evaluate (needs a target URL)
python ai_eval.py http://127.0.0.1:8765/test.html

# ② Reflect
python reflect.py                 # read eval.txt -> write prompt.txt
python reflect.py --no-llm        # force the rule engine (offline)

# ③ Optimize (local HTML or URL)
python optimize_page.py test.html            # read test.html + prompt.txt
python optimize_page.py https://example.com  # URL: auto-fetch HTML
python optimize_page.py test.html --no-llm   # force the rule engine

# ④ Site files (robots.txt / llms.txt / sitemap / AGENTS.md / .md mirror)
python generate_site.py output/test_optimized.html --base-url http://127.0.0.1:8765/
```

---

## Recomposing the pipeline

`run.py`'s pipeline is not hard-coded — it is an editable stage list `PIPELINE` (top of `run.py`):

```python
PIPELINE = ["eval", "reflect", "generate", "site"]
```

Edit that line to reorder / repeat stages, e.g.:

- `["eval", "reflect", "generate", "eval", "reflect", "generate"]` — **iterative refinement**: the second round re-evaluates the **just-generated optimized page**, then reflects and regenerates.
- `["eval", "reflect", "generate", "reflect", "generate"]` — reflect and regenerate again after generating.

You can also override it on the command line with `--pipeline` (`,` and `->` both work as separators):

```bash
python run.py test.html --pipeline "eval,reflect,generate,eval,reflect,generate"
python run.py test.html --pipeline "eval->reflect->generate->reflect->generate"
python run.py --list-stages    # list available stages
```

Stage name <-> script mapping:

| Stage | Script | Description |
|---|---|---|
| `eval` | `ai_eval.py` | evaluate the current target (local files get a temporary server) |
| `reflect` | `reflect.py` | reflect `eval.txt` → `prompt.txt` |
| `generate` | `optimize_page.py` | generate the optimized page and make it the new "current target" |
| `site` | `generate_site.py` | generate `robots.txt` / `llms.txt` / `sitemap.xml` / `sitemap.md` / `AGENTS.md` / `<page>.md` from the current page |

> **Data flow**: stages pass artifacts via `eval.txt` / `prompt.txt` files (all under `output/`). `reflect` reads the **most recent `eval`**'s `eval.txt`; for a meaningful second reflection, put another `eval` before `reflect` (as in the iterative example). Multiple `generate` runs naturally produce `output/test_optimized.html`, `output/test_optimized_optimized.html`, etc., without overwriting each other.

---

## LLM API configuration (single config file)

All LLM calls go through `llm.py`, and **all model / provider / key config lives in one file, `llm_config.json`**, defaulting to **DeepSeek**. To change the model, edit that one file.

### Supported providers

| Name | base_url | Default model |
|---|---|---|
| `deepseek` (default) | `https://api.deepseek.com` | `deepseek-chat` |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` |
| `anthropic` | `https://api.anthropic.com/v1` | `claude-sonnet-5` |
| `moonshot` (Kimi) | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| `qwen` (Tongyi) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| `zhipu` (GLM) | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| `xai` (Grok) | `https://api.x.ai/v1` | `grok-3-mini` |
| `openrouter` | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` |
| `siliconflow` | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` |

> Non-DeepSeek default model names are placeholders; adjust them in the config to match your key/plan.

### Config file fields

`llm_config.json` is the **single** config source. Fields:

| Field | Meaning |
|---|---|
| `provider` | provider name (`deepseek` / `openai` / `qwen` …), see the table above |
| `model` | model name; empty = the provider's default model |
| `base_url` | custom API endpoint; empty = the provider's default endpoint |
| `api_key` | the provider's API key |
| `compression` | `false` (default) = add `Accept-Encoding: identity` to work around the old Brotli/httpx bug |
| `stages` | optional per-stage (`eval` / `reflect` / `generate`) overrides of the fields above |

### API key via environment variable

Instead of putting the key in `llm_config.json`, you can leave `api_key` empty and export it:

| Provider | Environment variable |
|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `moonshot` | `MOONSHOT_API_KEY` |
| `qwen` | `DASHSCOPE_API_KEY` |
| `zhipu` | `ZHIPU_API_KEY` |
| `xai` | `XAI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `siliconflow` | `SILICONFLOW_API_KEY` |

A generic `LLM_API_KEY` works for any provider. Precedence: the `api_key` field in `llm_config.json` wins when set; otherwise the provider-specific variable, then `LLM_API_KEY`.

Full example (this is the `llm_config.example.json` shipped with the project):

```json
{
  "provider": "deepseek",
  "model": "deepseek-chat",
  "base_url": "",
  "api_key": "",
  "compression": false,
  "stages": {
    "eval":     {"provider": "", "model": "", "base_url": "", "api_key": ""},
    "reflect":  {"provider": "", "model": "", "base_url": "", "api_key": ""},
    "generate": {"provider": "", "model": "", "base_url": "", "api_key": ""}
  }
}
```

> Empty fields fall back to the registry defaults (base_url / default model). To switch models, just change `provider` / `model`.

### Per-stage config (optional; defaults to the global config)

Each of the three LLM stages (`eval` / `reflect` / `generate`; the `site` stage is rule-based and calls no LLM) can use a different provider/model, **also edited in `llm_config.json`** (the `stages` section). Module <-> stage <-> API mapping:

| Module (script) | Stage key | Role | Calls the LLM? |
|---|---|---|---|
| `ai_eval.py` | `eval` | scoring + deep analysis | [OK] yes |
| `reflect.py` | `reflect` | reflect into optimization instructions | [OK] yes (rule engine if no key; aborts on failure) |
| `optimize_page.py` | `generate` | optimize into the output page | [OK] yes (rule engine if no key; aborts on failure) |

Example: eval uses DeepSeek, reflect uses Tongyi, generate uses OpenAI (each with its own key):

```json
{
  "provider": "deepseek",
  "model": "deepseek-chat",
  "api_key": "sk-deepseek-xxx",
  "stages": {
    "eval":     {"provider": "",      "model": "",          "api_key": ""},
    "reflect":  {"provider": "qwen",   "model": "qwen-plus", "api_key": "sk-qwen-xxx"},
    "generate": {"provider": "openai", "model": "gpt-4o",    "api_key": "sk-openai-xxx"}
  }
}
```

- A stage left empty / omitted inherits the global config; filling a field affects only that stage.
- When a stage switches provider, its `api_key` (if left empty) inherits the global `api_key`; otherwise fill it in per stage.

---

## How it works

### ① Evaluate — `ai_eval.py`
Runs `a14y check <url> -o json` to get the scorecard, condenses the failed/warning checks into text, asks the LLM for an English deep analysis, and writes "header metadata + analysis + raw JSON" into `eval.txt`.

### ② Reflect — `reflect.py`
Parses the scorecard JSON and initial analysis out of `eval.txt`, handles **only `fail`/`warn` checks** (`na` items excluded, never criticized), and uses the LLM to produce an optimization instruction with "P0/P1/P2 priorities + copy-pasteable snippets + acceptance criteria" into `prompt.txt`. With no key it degrades to the built-in rule engine (the `FIX_HINTS` dict) so `prompt.txt` is always produced; with a key configured, a failed call aborts.

**RAG grounding:** before calling the LLM, `reflect` retrieves a fix recipe for every fail/warn check id from `knowledge/` (curated `checks/<id>.md` first, then crawled `docs/<id>.md`) via `rag.retrieve_for_checks`, and injects it into the prompt as a "Reference knowledge" section. This keeps the generated snippets accurate and consistent instead of relying on the model's memory. The rule engine likewise falls back to the same recipes for checks not in `FIX_HINTS`. Pass `--no-rag` to reflect without retrieval (the A/B baseline), and run `ab_test.py output/eval.txt` to compare the two arms on coverage / traceability metrics. It also recalls similar verified examples from `knowledge/examples/` via `rag.rank_examples` (boosting `verdict: PASS` examples) as a structure reference.

### ③ Optimize — `optimize_page.py`
Reads the source HTML (a local file, or an http(s) URL — URLs use **Playwright** to render JS and fetch the real DOM, falling back to urllib when not installed). The LLM generates new `<head>` metadata (title / meta description / og / JSON-LD / viewport / lang), consulting `prompt.txt` (the a14y findings from reflect) as the priority list, and also outputs a content-aware `<style>` design system (the LLM first identifies the page type — product / article / landing page — and designs for it) plus image `alt` text; the program **merges these back into the original**. The page is parsed and re-serialized with **BeautifulSoup**, so content and links are preserved but formatting / attribute quoting / entity encoding may be normalized (the body is no longer byte-verbatim); **zero original URLs are lost**. Heading hierarchy (1 `<h1>` + ≥2 `<h2>`), removing Flash, and (when the page has no stylesheet of its own) injecting a built-in default design system are handled by the rule engine.

Three programmatic hard constraints run before writing, plus a self-check:

- **Preserve the original URL set**: the body's content/links are kept; every `href/src/action/iframe src` is preserved (compared after HTML-entity decoding);
- **No fictional resources**: strip `.css/.js` references not present in the source;
- **No fabricated content**: strip `<a>` links not in the source, and whole `<nav>`/`<footer>` blocks not in the source.
- **Semantic JSON-LD**: if the LLM emits a JSON-LD block with no top-level `@type` (e.g. an `@graph`-only block), it is replaced with a flat `WebSite` derived from the real title — such a block parses as JSON but fails the evaluator's "semantic" check otherwise.

The self-check (key items + URL preservation + no fictional resources + no new links) exits `1` if it fails.

### RAG knowledge base — `knowledge/` + `rag.py`

The reflect stage is grounded in a small, versioned knowledge base (see `knowledge/README.md` for the full format):

- `knowledge/catalog.json` — one entry per a14y check id (`name` / `group` / `scope` / `priority` / `unlocks` dependency graph).
- `knowledge/checks/<id>.md` — curated, hand-written fix recipes (What it checks / How to fix / Gotchas / Acceptance).
- `knowledge/docs/<id>.md` — the official a14y reference per check, fetched by `fetch_a14y_docs.py` (re-run after a scorecard version bump).
- `knowledge/examples/<name>.md` — verified historical prompts (frontmatter `check_ids` / `score_before` / `score_after` / `verdict` / `final_score`); `rag.rank_examples` recalls them by BM25 and boosts `verdict: PASS` examples.

### ④ Site files — `generate_site.py`

The optimizer only rewrites the page HTML; the site-level files AI crawlers look for (listed in
the layout table above) are produced by `generate_site.py`, a rule-based (no-LLM) step added to the
default pipeline as `site`. It derives everything from the page's own title / description /
headings — a page with no `<title>` is skipped rather than padded. It writes into `output/`
alongside the optimized page (the root served by `run.py`'s temporary server); pass `--base-url` to
set the absolute site URL, and `--out-dir` for remote targets where the files are meant to be
deployed rather than served in place.

---

## Evaluating the optimization result (end-to-end)

`evaluate_result.py` evaluates the **final optimized page** against its source with two independent lenses (spec in `evaluation_plan.json`):

1. **Objective scoring** (deterministic, no LLM):
   - **a14y** — before/after page-level score (`a14y check … -m page`), the delta, and per-check flips; plus a site-level score (`robots.txt` / `llms.txt` / `sitemap` / `AGENTS.md`) reported as a single "current" value (`N/M` applicable checks pass), since those files are shared by the whole site.
   - **Content integrity (hard gate)** — original URLs preserved, visible-text similarity ≥ 0.95, no fabricated links / css/js / nav / footer. Any failure fails the whole result regardless of scores.
   - **Structural/metadata completeness** — a 13-item checklist (title / description / og / canonical / lang / viewport / JSON-LD / h1 / h2 / text-ratio), 100 points.
   - `objective_score = 0.45·a14y_page_after + 0.15·a14y_site_after + 0.25·integrity + 0.15·structural`

2. **Subjective scoring** (LLM-as-judge, 1–5 scale, 3 runs): six dimensions — metadata accuracy, content fidelity, readability improvement, style quality, structured-data quality, overall quality — each with rubric anchors, reported as mean ± std.

`final_score = 0.6·objective + 0.4·subjective`, with a **PASS / FAIL / REVIEW** verdict.

```bash
python evaluate_result.py test.html output/test_optimized.html            # full (a14y + LLM judge)
python evaluate_result.py test.html output/test_optimized.html --no-llm --skip-a14y   # objective only, offline
python evaluate_result.py test.html output/test_optimized.html --judge-runs 5 --out-dir ./eval
```

| Flag | Effect |
|---|---|
| `--skip-a14y` | skip the a14y stage (objective falls back to integrity + structural) |
| `--no-llm` | skip the subjective LLM-judge stage |
| `--judge-runs N` | judge runs for reliability (default 3) |
| `--judge-temperature T` | judge sampling temperature (default 0.7) |
| `--out-dir DIR` | report output dir (default `eval/`) |

Outputs `evaluation_report.json` (machine-readable) and `evaluation_report.md` (summary). The judge reuses `llm_config.json`; add a `stages.judge` override to judge with a different model than the generator.

> **Note:** the injected design system raises the a14y score but *lowers* `html.text-ratio` (more markup), so the `text_ratio_improved` checklist item is often legitimately [X] — a visual-polish vs text-density tradeoff, not a bug.

---

## Tests

The core page-rewrite helpers (head merge, URL preservation, fabricated-content stripping, alt
injection, JSON-LD validation, pipeline parsing), the RAG knowledge base (`rag.py`), the A/B
harness metrics (`ab_test.py`), and the site-file generator (`generate_site.py`) have pytest unit
tests:

```bash
pip install pytest
pytest tests/
```

---

## Known issues / notes

1. **`openai 3.14.0`'s `httpx2` is incompatible with old `Brotli`**: decompression raises `process() takes no keyword arguments`. This project defaults to `Accept-Encoding: identity` (`compression: false`) to work around it; on an unaffected environment set `llm_config.json`'s `compression` to `true` to restore compression.
2. **Garbled console text on Windows**: each script forces `stdout/stderr` to UTF-8 at startup.
3. **Local-file mode starts a temporary `http.server`** (port 8765 by default) and cleans up afterwards; use `--port` on port conflicts.
4. The evaluate stage needs a **URL**; local HTML is exposed by `run.py`'s auto-started server. Running `ai_eval.py` standalone requires a reachable URL.
5. **No key / failure behavior**: `eval` errors out with no key; `reflect` / `optimize` degrade to the rule engine (basic fixes) with no key, still producing `prompt.txt` / the optimized page. **Once `api_key` is set in `llm_config.json`, a failed LLM call (unreachable / auth failure, etc.) aborts the pipeline** instead of silently degrading.
6. **Style injection is less effective on heavily-customized CSS sites**: the injected `<style>` design system clearly improves lightly-styled or unstyled pages; on sites that heavily use inline styles / `!important` (e.g. the Baidu homepage), it may be overridden or even conflict locally.
7. **Site-level a14y is a shared site property, not a per-page delta**: `robots.txt` / `llms.txt` / `sitemap.xml` / `sitemap.md` / `AGENTS.md` describe the whole site (and are shared by the before/after pages served from the same directory), so `evaluate_result.py` reports the site-level a14y as a single "current" score, not a before/after delta. After the `site` stage runs it reads 14/14; without it, ~2/7.
8. **Pages with no title and no headings are not padded**: the optimizer refuses to invent a title / description / `<h1>`. For such a page the rule fallback only adds `lang` + `viewport`, and the self-check then fails (`meta description` / `JSON-LD` / `h1 heading`), exiting `1` rather than writing fabricated content. Supply a real `<title>` or heading to get a full optimization.
9. **The optimized page is re-serialized by BeautifulSoup** (`html.parser`): content and links are preserved, but the output is normalized — tag/attribute names are lower-cased, attribute values are double-quoted, `&` in URLs becomes `&amp;` (functionally identical), and void elements become self-closing. If you need byte-identical output, use the source file rather than the optimized one as the reference for diffing.
10. **ASCII markers instead of emoji in output**: reports and intermediate files use `[OK]` / `[X]` / `[!]` / `[!!]` / `[i]` instead of emoji (check / cross / warning / burst / info), so they stay readable in GBK/CP936 terminals that can't render emoji.
