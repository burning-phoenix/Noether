"""
Local LLM backend implementation using llama-cpp-python.
"""

from typing import Iterator, Optional, AsyncIterator
import json
from .base import LLMBackend, ModelConfig, ModelCapability

class LocalBackend(LLMBackend):
    """Backend for local GGUF models using llama_cpp."""

    def __init__(self, model_path: str, config: Optional[ModelConfig] = None):
        """
        Initialize the local backend.

        Args:
            model_path: Path to the .gguf model file
            config: Optional override for model configuration
        """
        default_config = ModelConfig(
            name=model_path.split("/")[-1] if "/" in model_path else model_path,
            max_context=32768,
            capabilities=[
                ModelCapability.STREAMING,
                ModelCapability.CODE_GENERATION,
                ModelCapability.LARGE_CONTEXT
            ]
        )
        super().__init__(config or default_config)
        self.model_path = model_path
        self._llm = None
        self._init_model()

    def _init_model(self):
        """Lazy initialization of the Llama model."""
        if self._llm is None:
            try:
                from llama_cpp import Llama
                # Assuming reasonable defaults for Apple Silicon (Metal)
                self._llm = Llama(
                    model_path=self.model_path,
                    n_ctx=self.config.max_context,
                    n_gpu_layers=-1, # Accelerate as much as possible
                    verbose=False
                )
            except ImportError:
                raise ImportError(
                    "llama-cpp-python is required for LocalBackend. "
                    "Install with: pip install llama-cpp-python"
                )

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> str:
        """Generate a complete response synchronously."""
        self._init_model()
        
        messages = self._build_messages(prompt, system)
        
        response = self._llm.create_chat_completion(
            messages=messages,
            temperature=kwargs.get("temperature", self.config.temperature),
            top_p=kwargs.get("top_p", self.config.top_p),
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            stream=False
        )
        
        return response["choices"][0]["message"]["content"]

    def stream(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> Iterator[str]:
        """Stream a response synchronously."""
        self._init_model()
        
        messages = self._build_messages(prompt, system)
        
        response_stream = self._llm.create_chat_completion(
            messages=messages,
            temperature=kwargs.get("temperature", self.config.temperature),
            top_p=kwargs.get("top_p", self.config.top_p),
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            stream=True
        )
        
        for chunk in response_stream:
            if "content" in chunk["choices"][0]["delta"]:
                yield chunk["choices"][0]["delta"]["content"]

    async def astream(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Async streaming - calls synchronous stream under the hood via standard asyncio wrappers if needed, but for now just yields."""
        # llama_cpp python's async support requires `llama_cpp.llama_cpp_async` or similar, 
        # but for simplicity we wrap the synchronous generator if an event loop wants it.
        # This implementation delegates to the sync stream.
        import asyncio
        loop = asyncio.get_event_loop()
        
        def _get_stream():
            return self.stream(prompt, system, **kwargs)
            
        # This is a naive async wrapper; in production, `llama-cpp-python` async client is preferred.
        stream_iter = await loop.run_in_executor(None, _get_stream)
        for chunk in stream_iter:
            yield chunk
