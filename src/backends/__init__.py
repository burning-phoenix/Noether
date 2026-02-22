"""
LLM Backend abstraction layer for multi-model orchestration.

Provides unified interface for:
- Local Coder3-Coder-30B (via llama-cpp-python, optional)
- Fireworks API models (Planner-K2, DeepSeek V3)
"""

from .base import LLMBackend, ModelConfig, ModelCapability, Message
from .openai_backend import OpenAICompatibleBackend, FireworksBackend

# LocalBackend requires llama-cpp-python (optional dependency)
try:
    from .local_backend import LocalBackend
except (ImportError, ModuleNotFoundError):
    LocalBackend = None

__all__ = [
    "LLMBackend",
    "ModelConfig",
    "ModelCapability",
    "Message",
    "LocalBackend",
    "OpenAICompatibleBackend",
    "FireworksBackend",  # Alias for backwards compatibility
]
