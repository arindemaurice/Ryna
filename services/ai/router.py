"""Intelligent AI routing across local and cloud providers.

Enforces privacy rules, offline/online awareness, fallback handling,
and strict honesty (never simulates or fakes AI intelligence when no model is available).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterator

from core.python.config import RynaConfig
from core.python.database import RynaDatabase
from core.python.hardware import check_internet_connectivity

from .anthropic_provider import AnthropicProvider
from .base import AIProvider, AIResponse, ChatMessage, ProviderError, ProviderStatus
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider

SENSITIVE_PATTERNS = [
    re.compile(r"\b(password|passphrase|secret\s*key|private\s*key|api[_\s-]?key)\b", re.IGNORECASE),
    re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z\-_]{20,})\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN-like pattern
]


def contains_sensitive_data(text: str) -> bool:
    return any(p.search(text) for p in SENSITIVE_PATTERNS)


class AIRouter:
    """Routes chat requests across configured local and cloud AI providers."""

    def __init__(
        self,
        config: RynaConfig,
        db: RynaDatabase,
        connectivity_checker: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self._check_online = connectivity_checker or check_internet_connectivity

    def build_providers(self) -> dict[str, AIProvider]:
        s = self.db.get_all_settings()
        return {
            "ollama": OllamaProvider(
                base_url=str(s.get("ai.ollama_base_url", "http://127.0.0.1:11434")),
                default_model=str(s.get("ai.local_model", "")),
            ),
            "openai": OpenAICompatibleProvider(
                api_key=self.config.openai_api_key,
                base_url=str(s.get("ai.openai_base_url", "https://api.openai.com/v1")),
                default_model=str(s.get("ai.openai_model", "gpt-4o-mini")),
            ),
            "anthropic": AnthropicProvider(
                api_key=self.config.anthropic_api_key,
                default_model=str(s.get("ai.anthropic_model", "claude-3-5-sonnet-latest")),
            ),
            "gemini": GeminiProvider(
                api_key=self.config.gemini_api_key,
                default_model=str(s.get("ai.gemini_model", "gemini-2.0-flash")),
            ),
        }

    def provider_statuses(self, online: bool | None = None) -> dict[str, Any]:
        is_online = self._check_online() if online is None else online
        allow_cloud = bool(self.db.get_setting("ai.allow_cloud", False))
        providers = self.build_providers()
        statuses: dict[str, dict[str, Any]] = {}
        active_any = False

        for name, provider in providers.items():
            st: ProviderStatus = provider.status()
            if not st.is_local:
                if not allow_cloud:
                    st.available = False
                    st.detail = "Cloud AI is disabled in Settings (ai.allow_cloud = false)."
                elif not is_online:
                    st.available = False
                    st.detail = "Offline — internet connectivity is required for cloud providers."
            if st.available:
                active_any = True
            statuses[name] = st.to_dict()

        return {
            "online": is_online,
            "allow_cloud": allow_cloud,
            "preferred_provider": self.db.get_setting("ai.preferred_provider", "auto"),
            "fallback_provider": self.db.get_setting("ai.fallback_provider", "ollama"),
            "any_provider_available": active_any,
            "providers": statuses,
        }

    def _candidate_order(
        self,
        preferred: str,
        fallback: str,
        allow_cloud: bool,
        is_online: bool,
        sensitive: bool,
        allow_sensitive_cloud: bool,
        providers: dict[str, AIProvider],
    ) -> list[str]:
        cloud_permitted = allow_cloud and is_online and (not sensitive or allow_sensitive_cloud)
        order: list[str] = []

        if preferred == "auto":
            # Privacy & offline-first: prefer local Ollama first, then configured cloud providers
            order.append("ollama")
            if cloud_permitted:
                order.extend(["openai", "anthropic", "gemini"])
        else:
            if preferred in providers:
                order.append(preferred)
            if fallback in providers and fallback not in order:
                order.append(fallback)
            if "ollama" not in order:
                order.append("ollama")

        filtered: list[str] = []
        for name in order:
            prov = providers[name]
            if not prov.is_local and not cloud_permitted:
                continue
            filtered.append(name)
        return filtered

    def chat(
        self,
        messages: list[ChatMessage],
        requested_provider: str | None = None,
        requested_model: str | None = None,
        online_override: bool | None = None,
    ) -> AIResponse:
        settings = self.db.get_all_settings()
        is_online = self._check_online() if online_override is None else online_override
        allow_cloud = bool(settings.get("ai.allow_cloud", False))
        allow_sensitive_cloud = bool(settings.get("ai.send_sensitive_to_cloud", False))
        preferred = requested_provider or str(settings.get("ai.preferred_provider", "auto"))
        fallback = str(settings.get("ai.fallback_provider", "ollama"))

        user_text = "\n".join(m.content for m in messages if m.role == "user")
        sensitive = contains_sensitive_data(user_text)

        providers = self.build_providers()
        candidates = self._candidate_order(
            preferred=preferred,
            fallback=fallback,
            allow_cloud=allow_cloud,
            is_online=is_online,
            sensitive=sensitive,
            allow_sensitive_cloud=allow_sensitive_cloud,
            providers=providers,
        )

        errors: list[str] = []
        for name in candidates:
            prov = providers[name]
            st = prov.status()
            if not st.configured:
                errors.append(f"{name}: {st.detail}")
                continue
            if prov.is_local and not st.available:
                errors.append(f"{name}: {st.detail}")
                continue
            try:
                model_override = requested_model if name == preferred else None
                return prov.chat(messages=messages, model=model_override)
            except ProviderError as exc:
                errors.append(f"{name}: {exc}")
                continue

        if sensitive and not allow_sensitive_cloud and allow_cloud:
            errors.append(
                "Sensitive content was detected; cloud providers were skipped per privacy settings (ai.send_sensitive_to_cloud = false)."
            )
        elif not allow_cloud:
            errors.append("Cloud AI is currently disabled (ai.allow_cloud = false).")
        elif not is_online:
            errors.append("System is currently offline; cloud providers are unavailable.")

        detail = " | ".join(errors) if errors else "No AI providers are configured or reachable."
        raise ProviderError(
            "router",
            f"No real AI provider is currently available. {detail}",
        )

    def chat_stream(
        self,
        messages: list[ChatMessage],
        requested_provider: str | None = None,
        requested_model: str | None = None,
        online_override: bool | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream tokens from the highest-priority available provider with automatic fallback before first token."""
        settings = self.db.get_all_settings()
        is_online = self._check_online() if online_override is None else online_override
        allow_cloud = bool(settings.get("ai.allow_cloud", False))
        allow_sensitive_cloud = bool(settings.get("ai.send_sensitive_to_cloud", False))
        preferred = requested_provider or str(settings.get("ai.preferred_provider", "auto"))
        fallback = str(settings.get("ai.fallback_provider", "ollama"))

        user_text = "\n".join(m.content for m in messages if m.role == "user")
        sensitive = contains_sensitive_data(user_text)

        providers = self.build_providers()
        candidates = self._candidate_order(
            preferred=preferred,
            fallback=fallback,
            allow_cloud=allow_cloud,
            is_online=is_online,
            sensitive=sensitive,
            allow_sensitive_cloud=allow_sensitive_cloud,
            providers=providers,
        )

        errors: list[str] = []
        for name in candidates:
            prov = providers[name]
            st = prov.status()
            if not st.configured or (prov.is_local and not st.available):
                errors.append(f"{name}: {st.detail}")
                continue
            emitted_any = False
            try:
                model_override = requested_model if name == preferred else None
                for chunk in prov.chat_stream(
                    messages=messages,
                    model=model_override,
                    should_cancel=should_cancel,
                ):
                    emitted_any = True
                    yield chunk
                return
            except ProviderError as exc:
                errors.append(f"{name}: {exc}")
                if emitted_any:
                    raise
                continue

        if sensitive and not allow_sensitive_cloud and allow_cloud:
            errors.append(
                "Sensitive content was detected; cloud providers were skipped per privacy settings (ai.send_sensitive_to_cloud = false)."
            )
        elif not allow_cloud:
            errors.append("Cloud AI is currently disabled (ai.allow_cloud = false).")
        elif not is_online:
            errors.append("System is currently offline; cloud providers are unavailable.")

        detail = " | ".join(errors) if errors else "No AI providers are configured or reachable."
        raise ProviderError("router", f"No real AI provider is currently available. {detail}")

    def operating_mode(self, online: bool | None = None) -> dict[str, Any]:
        """Classify exact real operating mode across the 5 UI states."""
        st = self.provider_statuses(online=online)
        is_online = bool(st["online"])
        provs = st["providers"]
        local_ok = bool(provs.get("ollama", {}).get("available"))
        cloud_ok = any(
            bool(p.get("available")) for k, p in provs.items() if k != "ollama"
        )
        preferred = str(st.get("preferred_provider", "auto"))

        active_provider = ""
        active_model = ""
        if is_online and cloud_ok and (preferred in ("openai", "anthropic", "gemini") or not local_ok):
            mode_id = "online_cloud_ai"
            label = "Online · Cloud AI"
            chosen_key = preferred if preferred in provs and provs[preferred]["available"] else next(
                k for k, p in provs.items() if k != "ollama" and p["available"]
            )
            active_provider = chosen_key
            active_model = provs[chosen_key].get("active_model", "")
        elif is_online and local_ok:
            mode_id = "online_local_ai"
            label = "Online · Local AI"
            active_provider = "ollama"
            active_model = provs["ollama"].get("active_model", "")
        elif not is_online and local_ok:
            mode_id = "offline_local_ai"
            label = "Offline · Local AI"
            active_provider = "ollama"
            active_model = provs["ollama"].get("active_model", "")
        elif not is_online:
            mode_id = "offline_limited"
            label = "Offline · Limited"
        else:
            mode_id = "ai_unavailable"
            label = "AI Unavailable"

        return {
            "mode_id": mode_id,
            "label": label,
            "online": is_online,
            "local_ai_available": local_ok,
            "cloud_ai_available": cloud_ok,
            "active_provider": active_provider,
            "active_model": active_model,
            "status": st,
        }
