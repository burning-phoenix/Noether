"""
OpenAI-compatible AI backend for API-based models.

Supports models via providers like Fireworks and OpenRouter.
"""

from typing import Iterator, Optional, AsyncIterator, Any

from openai import OpenAI, AsyncOpenAI
from openai import RateLimitError, APITimeoutError, InternalServerError, APIConnectionError
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from .base import LLMBackend, ModelConfig, ModelCapability
from .token_tracker import TokenTracker, TokenUsage
from .provider_registry import get_provider_config
from ..prompts import PLANNER_CHAT_SYSTEM, DEEPSEEK_EXPLORE_SYSTEM, CODER_CODING_SYSTEM


class OpenAICompatibleBackend(LLMBackend):
    """
    OpenAI-compatible generic backend.
    
    Can be configured for Fireworks, OpenRouter, etc.
    """

    def __init__(
        self,
        provider: str,
        model_id: str,
        api_key: str,
        system_prompt: Optional[str] = None,
        max_context: int = 131072,  # Sub-optimal but fine default
        capabilities: Optional[list[ModelCapability]] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ):
        """
        Initialize the backend.

        Args:
            provider: Provider ID (e.g., 'fireworks' or 'openrouter')
            model_id: Model ID string
            api_key: API key
            system_prompt: Default system prompt
        """
        if not api_key:
            raise ValueError(f"API key required for {provider}.")

        self.provider = provider
        self.provider_config = get_provider_config(provider)
        self.model_id = model_id
        
        config = ModelConfig(
            name=f"{self.provider_config['name']}/{model_id.split('/')[-1]}",
            max_context=max_context,
            capabilities=capabilities or [
                ModelCapability.STREAMING,
                ModelCapability.CODE_GENERATION,
                ModelCapability.REASONING,
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        super().__init__(config)

        self.default_system = system_prompt
        self.api_key = api_key

        # Token usage tracking
        self.token_tracker = TokenTracker()

        # Initialize OpenAI clients
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.provider_config["base_url"],
        )
        self._async_client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.provider_config["base_url"],
        )

    def stream(
        self,
        prompt: str,
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        include_reasoning: bool = True,
        **kwargs
    ) -> Iterator[str]:
        """
        Stream a response from the provider's API.
        """
        messages = self._build_messages(
            prompt,
            system=system or self.default_system,
            include_history=True
        )

        @retry(
            wait=wait_exponential(multiplier=1, min=2, max=10),
            stop=stop_after_attempt(3),
            retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)),
            reraise=True
        )
        def _execute_create():
            kwargs_dict = {
                "model": self.model_id,
                "messages": messages,
                "max_tokens": max_tokens or self.config.max_tokens,
                "temperature": temperature or self.config.temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                kwargs_dict["tools"] = tools
            return self._client.chat.completions.create(**kwargs_dict)

        response = _execute_create()

        last_chunk = None
        char_count = 0
        tool_calls_dict = {}
        for chunk in response:
            last_chunk = chunk
            if chunk.choices:
                delta = chunk.choices[0].delta
                
                # Assemble tool calls
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    for tc in delta.tool_calls:
                        if tc.index not in tool_calls_dict:
                            tool_calls_dict[tc.index] = {
                                "id": tc.id or "",
                                "type": "function",
                                "function": {
                                    "name": tc.function.name or "",
                                    "arguments": tc.function.arguments or ""
                                }
                            }
                        else:
                            if tc.function.arguments:
                                tool_calls_dict[tc.index]["function"]["arguments"] += tc.function.arguments
                            # Backfill id/name from continuation chunks if initially None
                            if tc.id and not tool_calls_dict[tc.index]["id"]:
                                tool_calls_dict[tc.index]["id"] = tc.id
                            if tc.function.name and not tool_calls_dict[tc.index]["function"]["name"]:
                                tool_calls_dict[tc.index]["function"]["name"] = tc.function.name

                # Handle reasoning models that have reasoning_content
                if include_reasoning and hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    char_count += len(delta.reasoning_content)
                    yield delta.reasoning_content
                # Handle regular content
                if hasattr(delta, 'content') and delta.content:
                    char_count += len(delta.content)
                    yield delta.content

        # Yield any completed tool calls as dictionaries at the end of the stream
        for tc in tool_calls_dict.values():
            yield {"type": "tool_call", "tool_call": tc}

        # Record token usage
        if last_chunk and hasattr(last_chunk, 'usage') and last_chunk.usage:
            self.token_tracker.record(
                prompt_tokens=last_chunk.usage.prompt_tokens or 0,
                completion_tokens=last_chunk.usage.completion_tokens or 0,
                model=self.model_id,
            )
        else:
            # Estimate from character count (~4 chars per token)
            est_tokens = max(char_count // 4, 1)
            self.token_tracker.record(
                prompt_tokens=0,
                completion_tokens=est_tokens,
                model=self.model_id,
            )

    async def astream(
        self,
        prompt: str,
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        include_reasoning: bool = True,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Asynchronously stream a response.
        """
        messages = self._build_messages(
            prompt,
            system=system or self.default_system,
            include_history=True
        )

        @retry(
            wait=wait_exponential(multiplier=1, min=2, max=10),
            stop=stop_after_attempt(3),
            retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)),
            reraise=True
        )
        async def _execute_acreate():
            kwargs_dict = {
                "model": self.model_id,
                "messages": messages,
                "max_tokens": max_tokens or self.config.max_tokens,
                "temperature": temperature or self.config.temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                kwargs_dict["tools"] = tools
            return await self._async_client.chat.completions.create(**kwargs_dict)

        response = await _execute_acreate()

        last_chunk = None
        char_count = 0
        tool_calls_dict = {}
        async for chunk in response:
            last_chunk = chunk
            if chunk.choices:
                delta = chunk.choices[0].delta
                
                # Assemble tool calls
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    for tc in delta.tool_calls:
                        if tc.index not in tool_calls_dict:
                            tool_calls_dict[tc.index] = {
                                "id": tc.id or "",
                                "type": "function",
                                "function": {
                                    "name": tc.function.name or "",
                                    "arguments": tc.function.arguments or ""
                                }
                            }
                        else:
                            if tc.function.arguments:
                                tool_calls_dict[tc.index]["function"]["arguments"] += tc.function.arguments
                            # Backfill id/name from continuation chunks if initially None
                            if tc.id and not tool_calls_dict[tc.index]["id"]:
                                tool_calls_dict[tc.index]["id"] = tc.id
                            if tc.function.name and not tool_calls_dict[tc.index]["function"]["name"]:
                                tool_calls_dict[tc.index]["function"]["name"] = tc.function.name

                if include_reasoning and hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    char_count += len(delta.reasoning_content)
                    yield delta.reasoning_content
                if hasattr(delta, 'content') and delta.content:
                    char_count += len(delta.content)
                    yield delta.content

        # Yield any completed tool calls as dictionaries at the end of the stream
        for tc in tool_calls_dict.values():
            yield {"type": "tool_call", "tool_call": tc}

        # Record token usage
        if last_chunk and hasattr(last_chunk, 'usage') and last_chunk.usage:
            self.token_tracker.record(
                prompt_tokens=last_chunk.usage.prompt_tokens or 0,
                completion_tokens=last_chunk.usage.completion_tokens or 0,
                model=self.model_id,
            )
        else:
            est_tokens = max(char_count // 4, 1)
            self.token_tracker.record(
                prompt_tokens=0,
                completion_tokens=est_tokens,
                model=self.model_id,
            )

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        **kwargs
    ) -> Any:
        """Generate a complete response (non-streaming)."""
        # If no tools, simple string join
        if not tools:
            return "".join(self.stream(prompt, system, tools=tools, **kwargs))
        
        # If tools present, return a structured dict containing both string text and parsed tool calls
        text = ""
        tool_calls = []
        for chunk in self.stream(prompt, system, tools=tools, **kwargs):
            if isinstance(chunk, str):
                text += chunk
            elif isinstance(chunk, dict) and chunk.get("type") == "tool_call":
                tool_calls.append(chunk["tool_call"])
                
        return {"text": text, "tool_calls": tool_calls}

    def get_last_usage(self) -> Optional[TokenUsage]:
        """Get the most recent token usage."""
        return self.token_tracker.get_last()

    @classmethod
    def create(
        cls,
        provider: str,
        role: str,
        api_key: str,
        model_id: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> "OpenAICompatibleBackend":
        """
        Factory method to create a backend for a specific role.

        Args:
            provider: Provider ID
            role: 'chat', 'coder', or 'explorer'
            api_key: API key
            model_id: Override default model ID if provided
            system_prompt: Override default system prompt
        """
        p_cfg = get_provider_config(provider)
        final_model_id = model_id or p_cfg["default_models"].get(role)
        if not final_model_id:
            raise ValueError(f"No default model for role '{role}' in provider '{provider}'.")
        
        # Determine defaults based on role
        if role == "chat":
            temp = 1.0
            sys_prompt = system_prompt or PLANNER_CHAT_SYSTEM
        elif role == "coder":
            temp = 0.2
            sys_prompt = system_prompt or CODER_CODING_SYSTEM
        elif role == "explorer":
            temp = 0.2
            sys_prompt = system_prompt or DEEPSEEK_EXPLORE_SYSTEM
        else:
            temp = 0.7
            sys_prompt = system_prompt

        return cls(
            provider=provider,
            model_id=final_model_id,
            api_key=api_key,
            system_prompt=sys_prompt,
            temperature=temp
        )

    # Legacy factories for backwards compatibility during refactor
    @classmethod
    def create_planner(cls, api_key: str, system_prompt: Optional[str] = None) -> "OpenAICompatibleBackend":
        return cls.create("fireworks", "chat", api_key=api_key, system_prompt=system_prompt)

    @classmethod
    def create_deepseek(cls, api_key: str, system_prompt: Optional[str] = None) -> "OpenAICompatibleBackend":
        return cls.create("fireworks", "explorer", api_key=api_key, system_prompt=system_prompt)

    @classmethod
    def create_fast_coder(cls, api_key: str, system_prompt: Optional[str] = None) -> "OpenAICompatibleBackend":
        return cls.create("fireworks", "coder", api_key=api_key, system_prompt=system_prompt)

# Alias for backwards compatibility
FireworksBackend = OpenAICompatibleBackend
