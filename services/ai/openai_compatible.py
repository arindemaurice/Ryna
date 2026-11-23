"""OpenAI and OpenAI-compatible HTTP provider (OpenAI, Groq, OpenRouter, LM Studio, vLLM)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .base import AIProvider, AIResponse, ChatMessage, ProviderError, ProviderStatus


class OpenAICompatibleProvider(AIProvider):
    name = "openai"
    is_local = False

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4o-mini",
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model.strip() or "gpt-4o-mini"
        # Detect local OpenAI-compatible servers (e.g., LM Studio / llama.cpp / vLLM)
        self.is_local = any(
            host in self.base_url.lower() for host in ("127.0.0.1", "localhost", "::1")
        )

    def status(self) -> ProviderStatus:
        configured = bool(self.api_key) or self.is_local
        return ProviderStatus(
            name=self.name,
            is_local=self.is_local,
            configured=configured,
            available=configured,
            models=[self.default_model] if configured else [],
            active_model=self.default_model if configured else "",
            detail=(
                f"Configured ({self.default_model})"
                if configured
                else "Not configured. Set RYNA_OPENAI_API_KEY in .env and enable cloud AI."
            ),
        )

    def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        timeout: float = 60.0,
    ) -> AIResponse:
        if not self.api_key and not self.is_local:
            raise ProviderError(self.name, "RYNA_OPENAI_API_KEY is not configured.")

        target_model = (model or self.default_model).strip()
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": target_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(self.name, f"OpenAI-compatible HTTP {exc.code}: {err_body}", retryable=exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"OpenAI-compatible request failed: {exc}", retryable=True) from exc

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        choices = data.get("choices", [])
        if not choices:
            raise ProviderError(self.name, "Provider returned no completion choices.")
        content = str(choices[0].get("message", {}).get("content", "")).strip()
        if not content:
            raise ProviderError(self.name, "Provider returned an empty message.")
        return AIResponse(
            content=content,
            provider=self.name,
            model=str(data.get("model", target_model)),
            is_local=self.is_local,
            latency_ms=elapsed_ms,
            usage=dict(data.get("usage", {})),
        )
