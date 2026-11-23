"""Google Gemini REST API provider."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import AIProvider, AIResponse, ChatMessage, ProviderError, ProviderStatus


class GeminiProvider(AIProvider):
    name = "gemini"
    is_local = False

    def __init__(
        self,
        api_key: str,
        default_model: str = "gemini-2.0-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        self.api_key = api_key.strip()
        self.default_model = default_model.strip() or "gemini-2.0-flash"
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
                else "Not configured. Set RYNA_GEMINI_API_KEY in .env and enable cloud AI."
            ),
        )

    def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        timeout: float = 60.0,
    ) -> AIResponse:
        if not self.api_key:
            raise ProviderError(self.name, "RYNA_GEMINI_API_KEY is not configured.")
        target_model = (model or self.default_model).strip()
        system_parts = [m.content for m in messages if m.role == "system"]
        contents = []
        for m in messages:
            if m.role == "system":
                continue
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})

        payload: dict = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

        encoded_model = urllib.parse.quote(target_model, safe="")
        url = f"{self.base_url}/models/{encoded_model}:generateContent?key={urllib.parse.quote(self.api_key, safe='')}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(self.name, f"Gemini HTTP {exc.code}: {err_body}", retryable=exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, f"Gemini request failed: {exc}", retryable=True) from exc

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        candidates = data.get("candidates", [])
        if not candidates:
            raise ProviderError(self.name, "Gemini returned no candidates.")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        if not text:
            raise ProviderError(self.name, "Gemini returned empty text.")
        return AIResponse(
            content=text,
            provider=self.name,
            model=target_model,
            is_local=False,
            latency_ms=elapsed_ms,
            usage=dict(data.get("usageMetadata", {})),
        )
