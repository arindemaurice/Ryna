"""Base interfaces and data types for RYNA AI providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterator


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class AIResponse:
    content: str
    provider: str
    model: str
    is_local: bool
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderStatus:
    name: str
    is_local: bool
    configured: bool
    available: bool
    models: list[str] = field(default_factory=list)
    active_model: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderError(RuntimeError):
    """Raised when an AI provider fails or is unreachable."""

    def __init__(self, provider: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class AIProvider(ABC):
    name: str
    is_local: bool

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Return live availability and detected models for this provider."""

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        timeout: float = 60.0,
    ) -> AIResponse:
        """Send a real chat completion request to the provider."""

    def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        timeout: float = 120.0,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield streaming delta dicts (`{"delta": str, ...}`) followed by final `{"done": True, "response": AIResponse}`.

        Subclasses supporting native HTTP streaming override this method.
        """
        resp = self.chat(messages=messages, model=model, timeout=timeout)
        if should_cancel and should_cancel():
            return
        yield {"delta": resp.content, "provider": resp.provider, "model": resp.model}
        yield {"done": True, "response": resp}
