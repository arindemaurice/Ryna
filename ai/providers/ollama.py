"""Real local Ollama provider with native token streaming and model discovery."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

from .base import AIProvider, AIResponse, ChatMessage, ProviderError, ProviderStatus


class OllamaProvider(AIProvider):
    name = "ollama"
    is_local = True

    def __init__(self, base_url: str = "http://127.0.0.1:11434", default_model: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model.strip()

    def _http_json(self, path: str, payload: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST" if data else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(self.name, f"Ollama HTTP {exc.code}: {body}", retryable=exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"Ollama unreachable at {self.base_url}: {exc}", retryable=True) from exc

    def list_models(self, timeout: float = 2.0) -> list[str]:
        data = self._http_json("/api/tags", timeout=timeout)
        return [str(m["name"]) for m in data.get("models", []) if m.get("name")]

    def status(self) -> ProviderStatus:
        try:
            models = self.list_models(timeout=1.5)
            chosen = self.default_model if self.default_model in models else (models[0] if models else self.default_model)
            if not models:
                return ProviderStatus(self.name, True, True, False, [], chosen, "Ollama is running, but no models are pulled yet.")
            return ProviderStatus(self.name, True, True, True, models, chosen, f"Local Ollama ready ({len(models)} model(s)).")
        except ProviderError as exc:
            return ProviderStatus(self.name, True, bool(self.base_url), False, [], self.default_model, str(exc))

    def _resolve_model(self, model: str | None) -> str:
        target = (model or self.default_model).strip()
        if target:
            return target
        installed = self.list_models(timeout=3.0)
        if not installed:
            raise ProviderError(self.name, "No local Ollama model installed. Run e.g. 'ollama pull qwen2.5:3b'.")
        return installed[0]

    def chat(self, messages: list[ChatMessage], model: str | None = None, timeout: float = 120.0) -> AIResponse:
        target = self._resolve_model(model)
        t0 = time.perf_counter()
        data = self._http_json(
            "/api/chat",
            {"model": target, "messages": [{"role": m.role, "content": m.content} for m in messages], "stream": False},
            timeout=timeout,
        )
        content = str(data.get("message", {}).get("content", "")).strip()
        if not content:
            raise ProviderError(self.name, "Ollama returned an empty response.")
        return AIResponse(
            content=content,
            provider=self.name,
            model=str(data.get("model", target)),
            is_local=True,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            usage={"eval_count": data.get("eval_count", 0)},
        )

    def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        timeout: float = 180.0,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        target = self._resolve_model(model)
        t0 = time.perf_counter()
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps({"model": target, "messages": [{"role": m.role, "content": m.content} for m in messages], "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        chunks: list[str] = []
        actual_model = target
        usage: dict[str, Any] = {}
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for raw_line in resp:
                    if should_cancel and should_cancel():
                        return
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    actual_model = str(data.get("model", actual_model))
                    delta = str(data.get("message", {}).get("content", ""))
                    if delta:
                        chunks.append(delta)
                        yield {"delta": delta, "provider": self.name, "model": actual_model}
                    if data.get("done"):
                        usage = {"eval_count": data.get("eval_count", 0)}
                        break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(self.name, f"Ollama HTTP {exc.code}: {body}", retryable=exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"Ollama unreachable at {self.base_url}: {exc}", retryable=True) from exc

        full = "".join(chunks).strip()
        if not full:
            raise ProviderError(self.name, "Ollama stream ended with empty output.")
        yield {
            "done": True,
            "response": AIResponse(full, self.name, actual_model, True, int((time.perf_counter() - t0) * 1000), usage),
        }
