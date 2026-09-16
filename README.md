# Agentic Page 优化工作流

一套「**评估 → 反思 → 优化落地**」的网页 AI 可读性（a14y）优化流水线。给定一个网页（本地 HTML 或 URL），自动诊断它为何对 AI Agent 不友好，生成优化指令，并在**不改动内容与链接**的前提下，补全元数据（title / og / JSON-LD）、修正结构（标题层级）、注入现代化样式，产出 AI 可读的优化后页面。

每一步产出一个文本产物，交给下一步消费，可单独运行也可一键串联。

```
输入网页 / URL
   │
   ▼
┌─────────────┐   eval.txt   ┌─────────────┐  prompt.txt  ┌──────────────────┐
│  ai_eval.py │ ───────────▶ │ reflect.py  │ ───────────▶ │ optimize_page.py  │ ──▶ *_optimized.html
│  (评估)      │              │  (反思)      │              │  (优化落地)         │
└─────────────┘              └─────────────┘              └──────────────────┘
   a14y CLI + LLM              LLM（规则兜底）               LLM + 规则兜底
```

> 阶段职责一句话：**eval** 说「哪里差、差多少」→ **reflect** 说「怎么改、优先级」→ **optimize** 说「改好了，拿去用」。

---

## 目录结构

| 文件 | 角色 | 输入 → 输出 |
|---|---|---|
| `run.py` | **一键运行**入口（流程可重组） | 目标（文件/URL）→ 按 `PIPELINE` 依次调用各阶段 |
| `ai_eval.py` | ① 评估器 | `URL` → `eval.txt`（a14y 评分 + LLM 深度分析） |
| `reflect.py` | ② 反思器 | `eval.txt` → `prompt.txt`（优化指令） |
| `optimize_page.py` | ③ 优化器 | `源 HTML 或 URL + prompt.txt` → `*_optimized.html` |
| `llm.py` | **统一 LLM API 模块** | 读取 `llm_config.json`，封装多供应商，供各阶段调用 |
| `llm_config.json` | **唯一配置源** | 供应商 / 模型 / base_url / api_key / 分阶段覆盖 |
| `llm_config.example.json` | 配置模板 | 无 key 的示例，可复制为 `llm_config.json` |
| `.gitignore` | 忽略清单 | 忽略 `llm_config.json` / `.env` / `__pycache__` / 中间产物 |
| `test.html` / `test_optimized.html` | 测试输入 / 输出 | — |
| `eval.txt` / `prompt.txt` | 中间产物 | 阶段①/② 的输出 |
| `sample.json` | 示例数据 | — |

---

## 环境依赖

- **Python 3.10+**（本机 3.12）
- **Node.js + `a14y`**（全局 CLI，评估阶段用）：
  ```bash
  npm install -g a14y
  ```
- **Python 依赖**（仅 `openai`，其余用标准库）：
  ```bash
  pip install openai
  ```
- **Playwright（可选，提升 URL 抓取效果）**：URL 模式默认用它渲染 JS 抓真实 DOM；未安装会自动回退 urllib。
  ```bash
  pip install playwright
  playwright install chromium
  ```
- 至少一个 LLM 供应商的 API key，填进 `llm_config.json` 的 `api_key`（默认 DeepSeek，见下方配置）。

---

## 使用流程

从零到产出的一条完整路径，跟着走即可。

### 第 1 步：装依赖（只需一次）

```bash
npm install -g a14y    # 评估阶段用的 CLI（需 Node.js）
pip install openai     # 唯一 Python 依赖
```

### 第 2 步：配置模型与 API key（只需改一个文件）

所有模型 / 供应商 / 密钥配置都集中在 **`llm_config.json`** 一个文件里。打开它，填好 `provider` / `model` / `api_key`：

```json
{
  "provider": "deepseek",
  "model": "deepseek-chat",
  "api_key": "sk-你的key"
}
```

没有该文件时，先复制 `llm_config.example.json` 为 `llm_config.json` 再填。

### 第 3 步：一键运行

```bash
# ① 本地 HTML 文件：自动起本地服务 → 评估 → 反思 → 优化
python run.py test.html

# ② 远程 URL：自动抓取 HTML，走完整三阶段
python run.py https://example.com
```

> 想换供应商 / 模型？直接改 `llm_config.json` 里的 `provider` / `model`，命令本身不用变。

### 第 4 步：查看产物

每次完整运行会在项目根目录生成三个文件：

| 产物 | 由谁生成 | 内容 |
|---|---|---|
| `eval.txt` | `ai_eval.py` | a14y 评分 + LLM 深度分析 + 原始 JSON |
| `prompt.txt` | `reflect.py` | 可落地的 P0/P1/P2 优化指令 |
| `*_optimized.html` | `optimize_page.py` | 优化后页面（本地 `test.html` → `test_optimized.html`；URL `example.com` → `example.com_optimized.html`） |

### 常用命令速查

```bash
python run.py --list                        # 查看支持的供应商
python run.py --list-stages                 # 查看可用阶段
python run.py test.html --port 9000         # 本地文件模式换端口（默认 8765）
python run.py test.html --skip-optimize     # 只评估 + 反思，跳过优化
python run.py test.html --pipeline "eval,reflect,generate,eval,reflect,generate"  # 迭代精修
```

---

## 分阶段使用

```bash
# ① 评估（需目标 URL）
python ai_eval.py http://127.0.0.1:8765/test.html

# ② 反思
python reflect.py                 # 读 eval.txt -> 写 prompt.txt
python reflect.py --no-llm        # 强制规则引擎（离线）

# ③ 优化（本地 HTML 或 URL）
python optimize_page.py test.html            # 读 test.html + prompt.txt
python optimize_page.py https://example.com  # URL：自动抓取 HTML
python optimize_page.py test.html --no-llm   # 强制规则引擎
```

---

## 自定义流程（重组阶段）

`run.py` 的流程不再是写死的三段式，而是一个可编辑的阶段列表 `PIPELINE`（在 `run.py` 顶部）：

```python
PIPELINE = ["eval", "reflect", "generate"]
```

改这一行即可重组 / 重复阶段，例如：

- `["eval", "reflect", "generate", "eval", "reflect", "generate"]` —— **迭代精修**：第二轮会自动评估**刚生成的优化页**，再反思、再生成。
- `["eval", "reflect", "generate", "reflect", "generate"]` —— 生成后再反思、再生成。

也可以在命令行用 `--pipeline` 临时覆盖（分隔符兼容 `,` / `->`）：

```bash
python run.py test.html --pipeline "eval,reflect,generate,eval,reflect,generate"
python run.py test.html --pipeline "eval->reflect->generate->reflect->generate"
python run.py --list-stages    # 列出可用阶段
```

阶段名与脚本的对应关系：

| 阶段名 | 脚本 | 说明 |
|---|---|---|
| `eval` | `ai_eval.py` | 评估当前目标（本地文件自动起临时服务） |
| `reflect` | `reflect.py` | 反思 `eval.txt` → `prompt.txt` |
| `generate` | `optimize_page.py` | 生成优化后页面，并把它设为新的「当前目标」 |

> **数据流向说明**：阶段之间通过 `eval.txt` / `prompt.txt` 文件传递产物。`reflect` 读取的是**最近一次 `eval`** 产出的 `eval.txt`；若想要有意义的二次反思，建议在 `reflect` 前再放一个 `eval`（如上面的迭代精修示例）。多次 `generate` 会自然产出 `test_optimized.html`、`test_optimized_optimized.html`，互不覆盖。

---

## LLM API 配置（单一配置文件）

工作流的 LLM 调用统一走 `llm.py`，**所有模型 / 供应商 / 密钥配置集中在 `llm_config.json` 一个文件里**，默认 **DeepSeek**。调整模型只需改这一个文件。

### 支持的供应商

| 名称 | base_url | 默认模型 |
|---|---|---|
| `deepseek`（默认） | `https://api.deepseek.com` | `deepseek-chat` |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` |
| `anthropic` | `https://api.anthropic.com/v1` | `claude-sonnet-5` |
| `moonshot`（Kimi） | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| `qwen`（通义） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| `zhipu`（智谱GLM） | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| `xai`（Grok） | `https://api.x.ai/v1` | `grok-3-mini` |
| `openrouter` | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` |
| `siliconflow` | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` |

> 非 DeepSeek 的默认模型名是占位，按你的 key/套餐在配置里改即可。

### 配置文件说明

`llm_config.json` 是**唯一**的配置源，各字段含义：

| 字段 | 说明 |
|---|---|
| `provider` | 供应商名（`deepseek` / `openai` / `qwen` …），见上表 |
| `model` | 模型名；留空 = 用该供应商默认模型 |
| `base_url` | 自定义接口地址；留空 = 用该供应商默认地址 |
| `api_key` | 该供应商的 API key |
| `compression` | `false`（默认）= 加 `Accept-Encoding: identity`，绕过旧版 Brotli/httpx 兼容 bug |
| `stages` | 可选，按阶段（`eval` / `reflect` / `generate`）覆盖上面任意字段 |

完整示例（即项目里自带的 `llm_config.example.json`）：

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

> 留空字段会自动回退到「注册表默认值」（base_url / 默认模型）。想换模型，只改 `provider` / `model` 即可。

### 阶段级配置（可选，默认全部继承全局）

三个阶段可以各自用不同的供应商/模型，**同样在 `llm_config.json` 里改**（`stages` 段）。下表是「模块 ↔ 阶段名 ↔ 接口」的对应关系：

| 模块（脚本） | 阶段名（stage key） | 作用 | 是否调用 LLM |
|---|---|---|---|
| `ai_eval.py` | `eval` | 评估打分 + 深度分析 | ✅ 是 |
| `reflect.py` | `reflect` | 反思生成优化指令 | ✅ 是（无 key 退规则引擎，失败则中止） |
| `optimize_page.py` | `generate` | 优化落地输出页面 | ✅ 是（无 key 退规则引擎，失败则中止） |

示例：eval 用 DeepSeek、reflect 用通义、generate 用 OpenAI（各自 key 分开填）：

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

- 某阶段留空 / 不写 = 继承全局配置；填了字段就只影响该阶段。
- 阶段切换供应商后，若该阶段 `api_key` 留空会继承全局 `api_key`，否则需单独填。

---

## 工作原理

### ① 评估 `ai_eval.py`
调用 `a14y check <url> -o json` 拿到评分卡，把失败/警告检查项压成文本，交给 LLM 输出英文深度分析，最后拼「头部元信息 + 分析 + 原始 JSON」写入 `eval.txt`。

### ② 反思 `reflect.py`
从 `eval.txt` 解析出评分卡 JSON 与初步分析，**只针对 `fail`/`warn` 检查项**（`na` 项排除、不批评），用 LLM 生成「P0/P1/P2 优先级 + 可粘贴代码片段 + 验收标准」的优化指令，写入 `prompt.txt`。无 key 时退化为内置规则引擎（`FIX_HINTS` 字典）保证 `prompt.txt` 必出；已配置 key 但调用失败则直接中止。

### ③ 优化 `optimize_page.py`
读取源 HTML（本地文件，或 http(s) URL——URL 优先用 **Playwright** 渲染 JS 抓真实 DOM，未安装则回退 urllib）。LLM 生成新的 `<head>` 元数据（title / meta description / og / JSON-LD / viewport / lang）并参考 `prompt.txt`（reflect 产出的 a14y 问题清单）作为优先修复项，同时输出一份 `<style>` 样式优化（保持内容不变）和图片 `alt` 文本；程序把这些**合并回原文**——body 其余部分原样不动，因此**原始 URL 零丢失**；标题层级（1 个 h1 + ≥2 个 h2）/ 移除 Flash 等结构性修复由规则引擎兜底。

写盘前有三道程序化硬约束，外加自检：

- **保留原始 URL 集合**：body 未改动，源页面每个 `href/src/action/iframe src` 天然保留；
- **无虚构资源**：剥掉源里没有的 `.css/.js` 引用；
- **无伪造内容元素**：剥掉源里没有的 `<a>` 链接，以及源里没有的整块 `<nav>`/`<footer>`。

自检（关键检查项 + URL 保留 + 无虚构资源 + 无新增链接）不通过则 `exit 1`。

---

## 已知问题 / 注意事项

1. **`openai 3.14.0` 的 `httpx2` 与旧 `Brotli` 不兼容**：解压时抛 `process() takes no keyword arguments`。本项目默认给客户端加 `Accept-Encoding: identity`（`compression: false`）绕过。在无此 bug 的环境可把 `llm_config.json` 的 `compression` 改为 `true` 恢复压缩。
2. **Windows 控制台中文乱码**：各脚本已在启动时把 `stdout/stderr` 设为 UTF-8。
3. **本地文件模式会临时起 `http.server`**（默认 8765 端口），运行结束自动清理；端口冲突时用 `--port` 换。
4. 评估阶段需要目标 **URL**，本地 HTML 由 `run.py` 自动起服务暴露；单独跑 `ai_eval.py` 时需自行提供可达 URL。
5. **LLM 无 key / 失败时的行为**：`eval` 缺 key 会报错退出；`reflect` / `optimize` 缺 key 时退化为内置规则引擎（产出基础修复），仍会生成 `prompt.txt` / 优化页。**一旦在 `llm_config.json` 配置了 `api_key`，LLM 调用失败（连不上 / 鉴权失败等）就会直接中止流程**，不再静默降级。
6. **样式注入对重度定制 CSS 的站点效果有限**：新增的 `<style>` 设计系统对轻样式 / 无样式页面提升明显；对已大量使用内联样式 / `!important` 的站点（如百度首页），可能被原样式覆盖，甚至局部冲突。
7. **前后 a14y 对比需剔除站点级检查**：本工具只优化单页（页面级），不产出 `robots.txt` / `llms.txt` / `sitemap` 等站点级文件。把优化后的本地文件重新打分时，这些站点级检查恒失败、会拉低总分；做前后对比时应只统计页面级（page-level）检查。
