#!/usr/bin/env python3
"""
Unified LLM API module (single config file)
-------------------------------------------
All model / provider / key configuration lives in one file: llm_config.json in the project root.
This module only:
  1) reads llm_config.json;
  2) fills empty fields with defaults from the built-in provider registry (base_url / default model);
  3) applies per-stage overrides (eval / reflect / generate);
  4) returns an OpenAI-compatible client / a unified chat interface.

Usage (as a library):
    from llm import chat, get_config, list_providers
    text = chat([{"role": "system", "content": "..."},
                 {"role": "user", "content": "..."}], temperature=0.3)

To change the model API, edit llm_config.json only, e.g.:
    {"provider": "openai", "model": "gpt-4o", "api_key": "sk-xxx"}
"""

import json
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "llm_config.json"
DEFAULT_PROVIDER = "deepseek"

# The three pipeline stages (used for per-stage config overrides)
STAGES = ("eval", "reflect", "generate")

# Provider registry: name -> base_url / default model
# (api_key is NOT here; it goes in llm_config.json)
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
    compression: bool = False  # False -> send Accept-Encoding: identity (workaround for httpx2/brotli incompatibility)


def _read_config() -> dict:
    """Read llm_config.json (the single config source)."""
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
    """Resolve config: llm_config.json global section + optional stages.<stage> override,
    with empty fields falling back to registry defaults."""
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
            # Stage switched provider explicitly: fall back model/base_url to the new
            # provider's defaults rather than inheriting the global ones.
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
    """Return an OpenAI-compatible client (cached by base_url + key)."""
    config = config or resolve_config()
    cache_key = (config.base_url, config.api_key)
    if cache_key not in _client_cache:
        if not config.api_key:
            raise ValueError(
                f"No API key configured: set llm_config.json's api_key field "
                f"(current provider={config.provider})"
            )
        headers = None if config.compression else {"Accept-Encoding": "identity"}
        _client_cache[cache_key] = OpenAI(
            api_key=config.api_key, base_url=config.base_url, default_headers=headers,
        )
    return _client_cache[cache_key]


def chat(messages, temperature: float = 0.3, max_tokens: int | None = None,
         config: LLMConfig | None = None) -> str:
    """Unified chat interface: returns the assistant text (stripped)."""
    config = config or resolve_config()
    client = get_client(config)
    kwargs = {"model": config.model, "messages": messages,
              "temperature": temperature, "stream": False}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def get_config(stage: str | None = None) -> LLMConfig:
    """Convenience: return the resolved config (optionally for stage eval / reflect / generate)."""
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
    print("Available providers (name -> base_url / default model):")
    for name, meta in PROVIDERS.items():
        mark = " (default)" if name == DEFAULT_PROVIDER else ""
        print(f"  {name:<12} {meta['base_url']:<46} model={meta['model']}{mark}")
    cfg = get_config()
    print(f"\nCurrent config (from {CONFIG_FILE.name}): provider={cfg.provider} model={cfg.model}")
    print(f"  base_url={cfg.base_url}")
    print(f"  api_key={'set' if cfg.api_key else 'NOT set'}")
