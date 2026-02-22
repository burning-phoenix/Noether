"""
Abstract base class for LLM backends.

Defines the common interface that all backends must implement,
enabling seamless switching between local and API-based models.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Optional, AsyncIterator, Any


class ModelCapability(Enum):
    """Capabilities that a model may support."""
    STREAMING = "streaming"
    LARGE_CONTEXT = "large_context"      # 32K+ context
    CODE_GENERATION = "code_generation"
    REASONING = "reasoning"
    TOOL_USE = "tool_use"


@dataclass
class ModelConfig:
    """Configuration for an LLM backend."""
    name: str
    max_context: int
    capabilities: list[ModelCapability] = field(default_factory=list)
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    max_tokens: int = 2048
    repeat_penalty: float = 1.05


@dataclass
class Message:
    """A single message in a conversation."""
    role: str  # "system", "user", "assistant"
    content: str


class LLMBackend(ABC):
    """
    Abstract base class for all LLM backends.

    Provides a unified interface for streaming and non-streaming
    text generation across local and API-based models.
    """

    def __init__(self, config: ModelConfig):
        """
        Initialize the backend with configuration.

        Args:
            config: Model configuration parameters
        """
        self.config = config
        self._conversation_history: list[Message] = []

    @property
    def name(self) -> str:
        """Get the model name."""
        return self.config.name

    @property
    def max_context(self) -> int:
        """Get the maximum context window size."""
        return self.config.max_context

    def has_capability(self, capability: ModelCapability) -> bool:
        """Check if the model has a specific capability."""
        return capability in self.config.capabilities

    @abstractmethod
    def stream(
        self,
        prompt: str,
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        **kwargs
    ) -> Iterator[Any]:
        """
        Stream a response token by token.

        Args:
            prompt: User prompt/question
            system: Optional system prompt override
            **kwargs: Additional generation parameters

        Yields:
            Text chunks as they are generated
        """
        pass

    @abstractmethod
    async def astream(
        self,
        prompt: str,
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        **kwargs
    ) -> AsyncIterator[Any]:
        """
        Asynchronously stream a response.

        Args:
            prompt: User prompt/question
            system: Optional system prompt override
            **kwargs: Additional generation parameters

        Yields:
            Text chunks as they are generated
        """
        pass

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        **kwargs
    ) -> Any:
        """
        Generate a complete response (non-streaming).

        Args:
            prompt: User prompt/question
            system: Optional system prompt override
            **kwargs: Additional generation parameters

        Returns:
            Complete generated text
        """
        pass

    def add_to_history(self, message: Message) -> None:
        """
        Add a message to conversation history.

        Args:
            message: The message to add
        """
        self._conversation_history.append(message)

    def add_user_message(self, content: str) -> None:
        """Add a user message to history."""
        self.add_to_history(Message(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to history."""
        self.add_to_history(Message(role="assistant", content=content))

    def clear_history(self) -> None:
        """Clear the conversation history."""
        self._conversation_history.clear()

    def get_history(self) -> list[Message]:
        """Get a copy of the conversation history."""
        return list(self._conversation_history)

    def get_context_usage(self) -> dict:
        """
        Get information about current context usage.

        Returns:
            Dictionary with context usage information
        """
        # Rough estimate: 4 chars per token
        history_chars = sum(len(m.content) for m in self._conversation_history)
        estimated_tokens = history_chars // 4

        return {
            "max_context": self.config.max_context,
            "history_messages": len(self._conversation_history),
            "estimated_tokens": estimated_tokens,
            "available_tokens": self.config.max_context - estimated_tokens,
        }

    def get_context_info(self) -> dict:
        """
        Get context info for status display.

        Subclasses can override for more detailed info.
        Default implementation delegates to get_context_usage().
        """
        return self.get_context_usage()

    def _build_messages(
        self,
        prompt: str,
        system: Optional[str] = None,
        include_history: bool = True
    ) -> list[dict]:
        """
        Build the messages list for API calls.

        Args:
            prompt: Current user prompt
            system: Optional system prompt
            include_history: Whether to include conversation history

        Returns:
            List of message dictionaries
        """
        messages = []

        if system:
            messages.append({"role": "system", "content": system})

        if include_history:
            for msg in self._conversation_history:
                messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": prompt})

        return messages
