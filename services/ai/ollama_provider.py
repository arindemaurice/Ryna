"""Real local Ollama HTTP provider for offline RYNA operation."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .base import AIProvider, AIResponse, ChatMessage, ProviderError, ProviderStatus


class OllamaProvider(AIProvider):
    name = "ollama"
    is_local = True

    def __init__(self, base_url: str = "http://127.0.0.1:11434", default_model: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model.strip()

    def _http_json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(self.name, f"Ollama HTTP {exc.code}: {err_body}", retryable=exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"Ollama unreachable at {self.base_url}: {exc}", retryable=True) from exc

    def list_models(self, timeout: float = 2.0) -> list[str]:
        data = self._http_json("/api/tags", timeout=timeout)
        models = []
        for item in data.get("models", []):
            name = item.get("name")
            if name:
                models.append(str(name))
        return models

    def status(self) -> ProviderStatus:
        try:
            models = self.list_models(timeout=1.5)
            chosen = self.default_model if self.default_model in models else (models[0] if models else self.default_model)
            if not models:
                return ProviderStatus(
                    name=self.name,
                    is_local=True,
                    configured=True,
                    available=False,
                    models=[],
                    active_model=chosen,
                    detail="Ollama is running, but no local models are installed. Run e.g. 'ollama pull qwen2.5:3b'.",
                )
            return ProviderStatus(
                name=self.name,
                is_local=True,
                configured=True,
                available=True,
                models=models,
                active_model=chosen,
                detail=f"Local Ollama ready ({len(models)} model(s) installed).",
            )
        except ProviderError as exc:
            return ProviderStatus(
                name=self.name,
                is_local=True,
                configured=bool(self.base_url),
                available=False,
                models=[],
                active_model=self.default_model,
                detail=str(exc),
            )

    def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        timeout: float = 120.0,
    ) -> AIResponse:
        target_model = (model or self.default_model).strip()
        if not target_model:
            installed = self.list_models(timeout=3.0)
            if not installed:
                raise ProviderError(
                    self.name,
                    "No local Ollama model configured or installed. Pull a model with 'ollama pull <model>'.",
                )
            target_model = installed[0]

        t0 = time.perf_counter()
        payload = {
            "model": target_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }
        data = self._http_json("/api/chat", payload=payload, timeout=timeout)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        message_obj = data.get("message", {})
        content = str(message_obj.get("content", "")).strip()
        if not content:
            raise ProviderError(self.name, "Ollama returned an empty response.")
        return AIResponse(
            content=content,
            provider=self.name,
            model=str(data.get("model", target_model)),
            is_local=True,
            latency_ms=elapsed_ms,
            usage={
                "prompt_eval_count": data.get("prompt_eval_count", 0),
                "eval_count": data.get("eval_count", 0),
            },
        )
