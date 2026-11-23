"""Anthropic Messages API provider."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .base import AIProvider, AIResponse, ChatMessage, ProviderError, ProviderStatus


class AnthropicProvider(AIProvider):
    name = "anthropic"
    is_local = False

    def __init__(
        self,
        api_key: str,
        default_model: str = "claude-3-5-sonnet-latest",
        base_url: str = "https://api.anthropic.com/v1",
    ) -> None:
        self.api_key = api_key.strip()
        self.default_model = default_model.strip() or "claude-3-5-sonnet-latest"
        self.base_url = base_url.rstrip("/")

    def status(self) -> ProviderStatus:
        configured = bool(self.api_key)
        return ProviderStatus(
            name=self.name,
            is_local=False,
            configured=configured,
            available=configured,
            models=[self.default_model] if configured else [],
            active_model=self.default_model if configured else "",
            detail=(
                f"Configured ({self.default_model})"
                if configured
                else "Not configured. Set RYNA_ANTHROPIC_API_KEY in .env and enable cloud AI."
            ),
        )

    def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        timeout: float = 60.0,
    ) -> AIResponse:
        if not self.api_key:
            raise ProviderError(self.name, "RYNA_ANTHROPIC_API_KEY is not configured.")
        target_model = (model or self.default_model).strip()
        system_parts = [m.content for m in messages if m.role == "system"]
        conv_messages = [
            {"role": m.role, "content": m.content} for m in messages if m.role != "system"
        ]
        payload = {
            "model": target_model,
            "max_tokens": 1024,
            "system": "\n\n".join(system_parts),
            "messages": conv_messages,
        }
        req = urllib.request.Request(
            f"{self.base_url}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(self.name, f"Anthropic HTTP {exc.code}: {err_body}", retryable=exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"Anthropic request failed: {exc}", retryable=True) from exc

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        blocks = data.get("content", [])
        text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if not text:
            raise ProviderError(self.name, "Anthropic returned empty content.")
        return AIResponse(
            content=text,
            provider=self.name,
            model=str(data.get("model", target_model)),
            is_local=False,
            latency_ms=elapsed_ms,
            usage=dict(data.get("usage", {})),
        )
