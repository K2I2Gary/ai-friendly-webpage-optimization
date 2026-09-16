# Agentic Page Optimization Workflow

A web AI-readability (a14y) optimization pipeline: **evaluate → reflect → optimize**. Given a web page (a local HTML file or a URL), it automatically diagnoses why it is unfriendly to AI agents, produces an optimization instruction, and — **without changing any content or links** — completes the metadata (title / og / JSON-LD), fixes the structure (heading hierarchy), and injects a modern stylesheet, producing an AI-readable optimized page.

Each step emits a text artifact consumed by the next step. Stages can run individually or be chained in one shot.

```
input page / URL
   │
   ▼
┌─────────────┐   eval.txt   ┌─────────────┐  prompt.txt  ┌──────────────────┐
│  ai_eval.py │ ───────────▶ │ reflect.py  │ ───────────▶ │ optimize_page.py  │ ──▶ *_optimized.html
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
| `llm.py` | **Unified LLM API module** | reads `llm_config.json`, wraps multiple providers for all stages |
| `llm_config.json` | **Single config source** | provider / model / base_url / api_key / per-stage overrides |
| `llm_config.example.json` | Config template | a key-less example; copy it to `llm_config.json` |
| `common.py` | Shared utilities | `console_utf8` / `is_url` / `url_to_filename` / `extract_urls`, used by all scripts |
| `requirements.txt` | Python deps | `openai` + `beautifulsoup4` (+ optional `playwright`) |
| `tests/` | pytest unit tests | cover the page-rewrite helpers (head merge / URL preservation / stripping) |
| `.gitignore` | Ignore list | ignores `llm_config.json` / `.env` / `__pycache__` / intermediates |
| `test.html` / `test_optimized.html` | Test input / output | — |
| `eval.txt` / `prompt.txt` | Intermediates | stage ①/② outputs |
| `sample.json` | Sample data | — |

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
# ① Local HTML file: start a local server -> evaluate -> reflect -> optimize
python run.py test.html

# ② Remote URL: auto-fetch the HTML and run the full three stages
python run.py https://example.com
```

> Want to switch provider / model? Just edit `provider` / `model` in `llm_config.json`; the command stays the same.

### Step 4: inspect the outputs

Each full run produces three files in the project root:

| Output | Produced by | Content |
|---|---|---|
| `eval.txt` | `ai_eval.py` | a14y score + LLM deep analysis + raw JSON |
| `prompt.txt` | `reflect.py` | actionable P0/P1/P2 optimization instructions |
| `*_optimized.html` | `optimize_page.py` | optimized page (local `test.html` → `test_optimized.html`; URL `example.com` → `example.com_optimized.html`) |

### Quick command reference

```bash
python run.py --list                        # list supported providers
python run.py --list-stages                 # list available stages
python run.py test.html --port 9000         # change the local-server port (default 8765)
python run.py test.html --skip-optimize     # evaluate + reflect only, skip optimize
python run.py test.html --pipeline "eval,reflect,generate,eval,reflect,generate"  # iterative refinement
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
```

---

## Recomposing the pipeline

`run.py`'s pipeline is not hard-coded — it is an editable stage list `PIPELINE` (top of `run.py`):

```python
PIPELINE = ["eval", "reflect", "generate"]
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

Stage name ↔ script mapping:

| Stage | Script | Description |
|---|---|---|
| `eval` | `ai_eval.py` | evaluate the current target (local files get a temporary server) |
| `reflect` | `reflect.py` | reflect `eval.txt` → `prompt.txt` |
| `generate` | `optimize_page.py` | generate the optimized page and make it the new "current target" |

> **Data flow**: stages pass artifacts via `eval.txt` / `prompt.txt` files. `reflect` reads the **most recent `eval`**'s `eval.txt`; for a meaningful second reflection, put another `eval` before `reflect` (as in the iterative example). Multiple `generate` runs naturally produce `test_optimized.html`, `test_optimized_optimized.html`, etc., without overwriting each other.

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

Each of the three stages can use a different provider/model, **also edited in `llm_config.json`** (the `stages` section). Module ↔ stage ↔ API mapping:

| Module (script) | Stage key | Role | Calls the LLM? |
|---|---|---|---|
| `ai_eval.py` | `eval` | scoring + deep analysis | ✅ yes |
| `reflect.py` | `reflect` | reflect into optimization instructions | ✅ yes (rule engine if no key; aborts on failure) |
| `optimize_page.py` | `generate` | optimize into the output page | ✅ yes (rule engine if no key; aborts on failure) |

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

### ③ Optimize — `optimize_page.py`
Reads the source HTML (a local file, or an http(s) URL — URLs use **Playwright** to render JS and fetch the real DOM, falling back to urllib when not installed). The LLM generates new `<head>` metadata (title / meta description / og / JSON-LD / viewport / lang), consulting `prompt.txt` (the a14y findings from reflect) as the priority list, and also outputs a `<style>` stylesheet (content unchanged) plus image `alt` text; the program **merges these back into the original**. The page is parsed and re-serialized with **BeautifulSoup**, so content and links are preserved but formatting / attribute quoting / entity encoding may be normalized (the body is no longer byte-verbatim); **zero original URLs are lost**. Heading hierarchy (1 `<h1>` + ≥2 `<h2>`) and removing Flash are handled by the rule engine.

Three programmatic hard constraints run before writing, plus a self-check:

- **Preserve the original URL set**: the body's content/links are kept; every `href/src/action/iframe src` is preserved (compared after HTML-entity decoding);
- **No fictional resources**: strip `.css/.js` references not present in the source;
- **No fabricated content**: strip `<a>` links not in the source, and whole `<nav>`/`<footer>` blocks not in the source.

The self-check (key items + URL preservation + no fictional resources + no new links) exits `1` if it fails.

---

## Tests

The core page-rewrite helpers (head merge, URL preservation, fabricated-content stripping, alt
injection, JSON-LD validation, pipeline parsing) have pytest unit tests:

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
7. **Before/after a14y comparison must exclude site-level checks**: this tool only optimizes a single page (page-level) and does not produce `robots.txt` / `llms.txt` / `sitemap` (site-level). Re-scoring the optimized local file always fails those site-level checks and drags the total down; compare page-level checks only.
8. **Pages with no title and no headings are not padded**: the optimizer refuses to invent a title / description / `<h1>`. For such a page the rule fallback only adds `lang` + `viewport`, and the self-check then fails (`meta description` / `JSON-LD` / `h1 heading`), exiting `1` rather than writing fabricated content. Supply a real `<title>` or heading to get a full optimization.
9. **The optimized page is re-serialized by BeautifulSoup** (`html.parser`): content and links are preserved, but the output is normalized — tag/attribute names are lower-cased, attribute values are double-quoted, `&` in URLs becomes `&amp;` (functionally identical), and void elements become self-closing. If you need byte-identical output, use the source file rather than the optimized one as the reference for diffing.
