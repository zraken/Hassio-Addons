"""Provider registry: name -> class, plus key-requirement notes for docs/check mode."""
from __future__ import annotations

from ..config import AccountConfig
from .anthropic import AnthropicProvider
from .base import Provider
from .deepseek import DeepSeekProvider
from .gemini import GeminiProvider
from .meta import MetaProvider
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider
from .zai import ZaiProvider
from .zai_plan import ZaiPlanProvider

PROVIDERS: dict[str, type[Provider]] = {
    "openrouter": OpenRouterProvider,
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "meta": MetaProvider,
    "zai_plan": ZaiPlanProvider,
    "zai": ZaiProvider,
}

#: Which kind of key each provider needs (shown by --check and in README).
KEY_NOTES: dict[str, str] = {
    "openrouter": "management key (openrouter.ai/settings/management-keys; read-only suffices)",
    "deepseek": "normal API key (platform.deepseek.com)",
    "openai": "admin key (sk-admin-...; platform.openai.com/settings/organization/admin-keys)",
    "anthropic": "Admin API key (sk-ant-admin...); organization accounts only",
    "gemini": "normal AI Studio API key",
    "meta": "normal Meta Model API key (dev.meta.ai)",
    "zai_plan": "GLM Coding Plan key",
    "zai": "pay-as-you-go API key (experimental)",
}


def get_provider(account: AccountConfig) -> Provider:
    return PROVIDERS[account.provider](account)
