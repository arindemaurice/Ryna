"""Cloud AI providers (OpenAI-compatible with native streaming, Anthropic, Google Gemini)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator

from .base import AIProvider, AIResponse, ChatMessage, ProviderError, ProviderStatus


class OpenAICompatibleProvider(AIProvider):
    name = "openai"
    is_local = False

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", default_model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model.strip() or "gpt-4o-mini"
        self.is_local = any(h in self.base_url.lower() for h in ("127.0.0.1", "localhost", "::1"))

    def status(self) -> ProviderStatus:
        cfg = bool(self.api_key) or self.is_local
        return ProviderStatus(
            self.name, self.is_local, cfg, cfg,
            [self.default_model] if cfg else [],
            self.default_model if cfg else "",
            f"Configured ({self.default_model})" if cfg else "Set RYNA_OPENAI_API_KEY in .env to enable.",
        )

    def chat(self, messages: list[ChatMessage], model: str | None = None, timeout: float = 90.0) -> AIResponse:
        if not self.api_key and not self.is_local:
            raise ProviderError(self.name, "RYNA_OPENAI_API_KEY not configured.")
        target = (model or self.default_model).strip()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps({"model": target, "messages": [{"role": m.role, "content": m.content} for m in messages]}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderError(self.name, f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}", exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"OpenAI-compatible request failed: {exc}", True) from exc
        content = str((data.get("choices") or [{}])[0].get("message", {}).get("content", "")).strip()
        if not content:
            raise ProviderError(self.name, "Empty completion returned.")
        return AIResponse(content, self.name, str(data.get("model", target)), self.is_local, int((time.perf_counter() - t0) * 1000), dict(data.get("usage", {})))

    def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        timeout: float = 120.0,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not self.api_key and not self.is_local:
            raise ProviderError(self.name, "RYNA_OPENAI_API_KEY not configured.")
        target = (model or self.default_model).strip()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps({"model": target, "messages": [{"role": m.role, "content": m.content} for m in messages], "stream": True}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        t0 = time.perf_counter()
        chunks: list[str] = []
        actual_model = target
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for raw in resp:
                    if should_cancel and should_cancel():
                        return
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    data = json.loads(payload)
                    actual_model = str(data.get("model", actual_model))
                    delta = str((data.get("choices") or [{}])[0].get("delta", {}).get("content", ""))
                    if delta:
                        chunks.append(delta)
                        yield {"delta": delta, "provider": self.name, "model": actual_model}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"OpenAI-compatible stream failed: {exc}", True) from exc
        full = "".join(chunks).strip()
        if not full:
            raise ProviderError(self.name, "Empty streamed completion.")
        yield {"done": True, "response": AIResponse(full, self.name, actual_model, self.is_local, int((time.perf_counter() - t0) * 1000))}


class AnthropicProvider(AIProvider):
    name = "anthropic"
    is_local = False

    def __init__(self, api_key: str, default_model: str = "claude-3-5-sonnet-latest") -> None:
        self.api_key = api_key.strip()
        self.default_model = default_model.strip() or "claude-3-5-sonnet-latest"

    def status(self) -> ProviderStatus:
        cfg = bool(self.api_key)
        return ProviderStatus(self.name, False, cfg, cfg, [self.default_model] if cfg else [], self.default_model if cfg else "", f"Configured ({self.default_model})" if cfg else "Set RYNA_ANTHROPIC_API_KEY in .env.")

    def chat(self, messages: list[ChatMessage], model: str | None = None, timeout: float = 90.0) -> AIResponse:
        if not self.api_key:
            raise ProviderError(self.name, "RYNA_ANTHROPIC_API_KEY not configured.")
        target = (model or self.default_model).strip()
        sys_txt = "\n\n".join(m.content for m in messages if m.role == "system")
        body = {"model": target, "max_tokens": 1024, "system": sys_txt, "messages": [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]}
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            method="POST",
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"Anthropic request failed: {exc}", True) from exc
        txt = "\n".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        if not txt:
            raise ProviderError(self.name, "Anthropic returned empty content.")
        return AIResponse(txt, self.name, str(data.get("model", target)), False, int((time.perf_counter() - t0) * 1000))


class GeminiProvider(AIProvider):
    name = "gemini"
    is_local = False

    def __init__(self, api_key: str, default_model: str = "gemini-2.0-flash") -> None:
        self.api_key = api_key.strip()
        self.default_model = default_model.strip() or "gemini-2.0-flash"

    def status(self) -> ProviderStatus:
        cfg = bool(self.api_key)
        return ProviderStatus(self.name, False, cfg, cfg, [self.default_model] if cfg else [], self.default_model if cfg else "", f"Configured ({self.default_model})" if cfg else "Set RYNA_GEMINI_API_KEY in .env.")

    def chat(self, messages: list[ChatMessage], model: str | None = None, timeout: float = 90.0) -> AIResponse:
        if not self.api_key:
            raise ProviderError(self.name, "RYNA_GEMINI_API_KEY not configured.")
        target = (model or self.default_model).strip()
        sys_txt = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]} for m in messages if m.role != "system"]
        payload: dict[str, Any] = {"contents": contents}
        if sys_txt:
            payload["systemInstruction"] = {"parts": [{"text": sys_txt}]}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(target, safe='')}:generateContent?key={urllib.parse.quote(self.api_key, safe='')}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"Gemini request failed: {exc}", True) from exc
        parts = ((data.get("candidates") or [{}])[0]).get("content", {}).get("parts", [])
        txt = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        if not txt:
            raise ProviderError(self.name, "Gemini returned empty text.")
        return AIResponse(txt, self.name, target, False, int((time.perf_counter() - t0) * 1000))
