"""
Registry for OpenAI-compatible LLM providers.

Defines connection details, environment variable keys, and default models
for each supported provider (e.g., Fireworks, OpenRouter).
"""

from typing import Any

# Registry of supported OpenAI-compatible providers
PROVIDERS: dict[str, dict[str, Any]] = {
    "fireworks": {
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "env_key": "FIREWORKS_API_KEY",
        "key_prefix": "fw_",
        "models_endpoint": None,  # Models are currently hardcoded for Fireworks
        "default_models": {
            "chat": "accounts/fireworks/models/kimi-k2p5",
            "coder": "accounts/fireworks/models/deepseek-v3p2",
            "explorer": "accounts/fireworks/models/glm-5",
        },
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "key_prefix": "sk-or-",
        "models_endpoint": "/models",  # GET to list active models
        "default_models": {
            "chat": "moonshotai/kimi-k2.5",
            "coder": "qwen/qwen3-coder-next",
            "explorer": "deepseek/deepseek-v3.2",
        },
    },
}

def get_provider_config(provider_id: str) -> dict[str, Any]:
    """Get configuration dictionary for a provider by ID."""
    if provider_id not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider_id}. Available: {list(PROVIDERS.keys())}")
    return PROVIDERS[provider_id]
