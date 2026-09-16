#!/usr/bin/env python3
"""
LLM API 统一封装模块（单一配置文件）
-------------------------------------
所有模型 / 供应商 / 密钥配置都集中在项目根目录的 llm_config.json 一个文件里。
本模块只负责：
  1) 读取 llm_config.json；
  2) 用内置供应商注册表为「留空字段」补默认值（base_url / 默认模型）；
  3) 按阶段（eval / reflect / generate）覆盖配置；
  4) 返回 OpenAI 兼容客户端 / 统一 chat 接口。

用法（作为库）:
    from llm import chat, get_config, list_providers
    text = chat([{"role": "system", "content": "..."},
                 {"role": "user", "content": "..."}], temperature=0.3)

调整模型 API 只需改 llm_config.json 一个文件，例如:
    {"provider": "openai", "model": "gpt-4o", "api_key": "sk-xxx"}
"""

import json
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "llm_config.json"
DEFAULT_PROVIDER = "deepseek"

# 工作流三个阶段名（供阶段级配置使用）
STAGES = ("eval", "reflect", "generate")

# 供应商注册表：name -> base_url / 默认模型
# （api_key 不在此处，统一在 llm_config.json 里填）
PROVIDERS = {
    "deepseek":    {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "openai":      {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "anthropic":   {"base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-5"},
    "moonshot":    {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "qwen":        {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "zhipu":       {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "xai":         {"base_url": "https://api.x.ai/v1", "model": "grok-3-mini"},
    "openrouter":  {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
}


@dataclass
class LLMConfig:
    provider: str
    model: str
    base_url: str
    api_key: str
    compression: bool = False  # False → 发送 Accept-Encoding: identity（绕过 httpx2/brotli 兼容问题）


def _read_config() -> dict:
    """读取 llm_config.json（单一配置源）。"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _first(*values):
    for v in values:
        if v not in (None, ""):
            return v
    return None


def resolve_config(stage: str | None = None) -> LLMConfig:
    """解析配置：llm_config.json 全局段 + 可选 stages.<stage> 覆盖，留空回退注册表默认。"""
    cfg = _read_config()

    def pick(local, fallback):
        return local if local not in (None, "") else fallback

    provider = pick(cfg.get("provider"), DEFAULT_PROVIDER)
    model = cfg.get("model") or ""
    base_url = cfg.get("base_url") or ""
    api_key = cfg.get("api_key") or ""
    compression = bool(cfg.get("compression", False))

    if stage:
        so = (cfg.get("stages", {}) or {}).get(stage, {}) or {}
        new_provider = pick(so.get("provider"), provider)
        if so.get("provider"):
            # 阶段显式切换供应商：model/base_url 回退到新供应商默认，而非继承全局
            meta = PROVIDERS.get(new_provider, PROVIDERS[DEFAULT_PROVIDER])
            model = pick(so.get("model"), meta["model"])
            base_url = pick(so.get("base_url"), meta["base_url"])
        else:
            model = pick(so.get("model"), model)
            base_url = pick(so.get("base_url"), base_url)
        provider = new_provider
        api_key = pick(so.get("api_key"), api_key)

    meta = PROVIDERS.get(provider, PROVIDERS[DEFAULT_PROVIDER])
    return LLMConfig(
        provider=provider,
        model=model or meta["model"],
        base_url=base_url or meta["base_url"],
        api_key=api_key,
        compression=compression,
    )


_client_cache: dict = {}


def get_client(config: LLMConfig | None = None) -> OpenAI:
    """返回（按 base_url+key 缓存的）OpenAI 兼容客户端。"""
    config = config or resolve_config()
    cache_key = (config.base_url, config.api_key)
    if cache_key not in _client_cache:
        if not config.api_key:
            raise ValueError(
                f"未配置 API key：请在 llm_config.json 的 api_key 字段填写 "
                f"（当前 provider={config.provider}）"
            )
        headers = None if config.compression else {"Accept-Encoding": "identity"}
        _client_cache[cache_key] = OpenAI(
            api_key=config.api_key, base_url=config.base_url, default_headers=headers,
        )
    return _client_cache[cache_key]


def chat(messages, temperature: float = 0.3, max_tokens: int | None = None,
         config: LLMConfig | None = None) -> str:
    """统一对话接口：返回 assistant 的文本内容（已 strip）。"""
    config = config or resolve_config()
    client = get_client(config)
    kwargs = {"model": config.model, "messages": messages,
              "temperature": temperature, "stream": False}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def get_config(stage: str | None = None) -> LLMConfig:
    """便捷入口：返回当前解析后的配置（可指定阶段 eval / reflect / generate）。"""
    return resolve_config(stage=stage)


def list_providers() -> dict:
    return PROVIDERS


if __name__ == "__main__":
    import sys
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("可用供应商（name -> base_url / 默认模型）：")
    for name, meta in PROVIDERS.items():
        mark = "（默认）" if name == DEFAULT_PROVIDER else ""
        print(f"  {name:<12} {meta['base_url']:<46} model={meta['model']} {mark}")
    cfg = get_config()
    print(f"\n当前配置（来自 {CONFIG_FILE.name}）: provider={cfg.provider} model={cfg.model}")
    print(f"  base_url={cfg.base_url}")
    print(f"  api_key={'已配置' if cfg.api_key else '未配置'}")
