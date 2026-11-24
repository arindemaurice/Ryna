"""Connectivity-aware, privacy-enforcing hybrid AI router (local Ollama + cloud providers)."""

from __future__ import annotations

from typing import Any, Callable, Iterator

from ai.config import RynaConfig
from ai.local_models.detector import check_internet_connectivity
from ai.providers.base import AIProvider, AIResponse, ChatMessage, ProviderError, ProviderStatus
from ai.providers.cloud import AnthropicProvider, GeminiProvider, OpenAICompatibleProvider
from ai.providers.ollama import OllamaProvider
from database.store import RynaDatabase
from security.privacy import contains_sensitive_data


class AIRouter:
    def __init__(self, config: RynaConfig, db: RynaDatabase, connectivity_checker: Callable[[], bool] | None = None) -> None:
        self.config = config
        self.db = db
        self._check_online = connectivity_checker or check_internet_connectivity

    def build_providers(self) -> dict[str, AIProvider]:
        s = self.db.get_all_settings()
        return {
            "ollama": OllamaProvider(str(s.get("ai.ollama_base_url", "http://127.0.0.1:11434")), str(s.get("ai.local_model", ""))),
            "openai": OpenAICompatibleProvider(self.config.openai_api_key, str(s.get("ai.openai_base_url", "https://api.openai.com/v1")), str(s.get("ai.openai_model", "gpt-4o-mini"))),
            "anthropic": AnthropicProvider(self.config.anthropic_api_key, str(s.get("ai.anthropic_model", "claude-3-5-sonnet-latest"))),
            "gemini": GeminiProvider(self.config.gemini_api_key, str(s.get("ai.gemini_model", "gemini-2.0-flash"))),
        }

    def provider_statuses(self, online: bool | None = None) -> dict[str, Any]:
        is_online = self._check_online() if online is None else online
        allow_cloud = bool(self.db.get_setting("ai.allow_cloud", False))
        providers = self.build_providers()
        statuses: dict[str, dict[str, Any]] = {}
        any_ok = False
        for name, prov in providers.items():
            st: ProviderStatus = prov.status()
            if not st.is_local:
                if not allow_cloud:
                    st.available = False
                    st.detail = "Cloud AI disabled in Settings."
                elif not is_online:
                    st.available = False
                    st.detail = "Offline — internet required for cloud AI."
            if st.available:
                any_ok = True
            statuses[name] = st.to_dict()
        return {
            "online": is_online,
            "allow_cloud": allow_cloud,
            "preferred_provider": self.db.get_setting("ai.preferred_provider", "auto"),
            "fallback_provider": self.db.get_setting("ai.fallback_provider", "ollama"),
            "any_provider_available": any_ok,
            "providers": statuses,
        }

    def operating_mode(self, online: bool | None = None) -> dict[str, Any]:
        st = self.provider_statuses(online=online)
        is_online = bool(st["online"])
        provs = st["providers"]
        local_ok = bool(provs["ollama"]["available"])
        cloud_ok = any(bool(p["available"]) for k, p in provs.items() if k != "ollama")
        pref = str(st["preferred_provider"])
        active_prov, active_model = "", ""

        if is_online and cloud_ok and (pref in ("openai", "anthropic", "gemini") or not local_ok):
            mode_id, label = "online_cloud_ai", "Online · Cloud AI"
            active_prov = pref if pref in provs and provs[pref]["available"] else next(k for k, p in provs.items() if k != "ollama" and p["available"])
            active_model = provs[active_prov]["active_model"]
        elif is_online and local_ok:
            mode_id, label = "online_local_ai", "Online · Local AI"
            active_prov, active_model = "ollama", provs["ollama"]["active_model"]
        elif not is_online and local_ok:
            mode_id, label = "offline_local_ai", "Offline · Local AI"
            active_prov, active_model = "ollama", provs["ollama"]["active_model"]
        elif not is_online:
            mode_id, label = "offline_limited", "Offline · Limited"
        else:
            mode_id, label = "ai_unavailable", "AI Unavailable"

        return {
            "mode_id": mode_id,
            "label": label,
            "online": is_online,
            "local_ai_available": local_ok,
            "cloud_ai_available": cloud_ok,
            "active_provider": active_prov,
            "active_model": active_model,
            "status": st,
        }

    def _candidates(
        self,
        preferred: str,
        fallback: str,
        cloud_ok: bool,
        providers: dict[str, AIProvider],
    ) -> list[str]:
        order: list[str] = []
        if preferred == "auto":
            order.append("ollama")
            if cloud_ok:
                order.extend(["openai", "anthropic", "gemini"])
        else:
            if preferred in providers:
                order.append(preferred)
            if fallback in providers and fallback not in order:
                order.append(fallback)
            if "ollama" not in order:
                order.append("ollama")
        return [n for n in order if providers[n].is_local or cloud_ok]

    def chat(
        self,
        messages: list[ChatMessage],
        requested_provider: str | None = None,
        requested_model: str | None = None,
        online_override: bool | None = None,
    ) -> AIResponse:
        s = self.db.get_all_settings()
        is_online = self._check_online() if online_override is None else online_override
        allow_cloud = bool(s.get("ai.allow_cloud", False))
        allow_sens = bool(s.get("ai.send_sensitive_to_cloud", False))
        pref = requested_provider or str(s.get("ai.preferred_provider", "auto"))
        fb = str(s.get("ai.fallback_provider", "ollama"))
        sensitive = contains_sensitive_data("\n".join(m.content for m in messages if m.role == "user"))
        cloud_ok = allow_cloud and is_online and (not sensitive or allow_sens)

        providers = self.build_providers()
        errors: list[str] = []
        for name in self._candidates(pref, fb, cloud_ok, providers):
            prov = providers[name]
            st = prov.status()
            if not st.configured or (prov.is_local and not st.available):
                errors.append(f"{name}: {st.detail}")
                continue
            try:
                return prov.chat(messages, model=requested_model if name == pref else None)
            except ProviderError as exc:
                errors.append(f"{name}: {exc}")
        if sensitive and allow_cloud and not allow_sens:
            errors.append("Sensitive content blocked from cloud providers by privacy policy.")
        raise ProviderError("router", "No real AI provider available. " + " | ".join(errors))

    def chat_stream(
        self,
        messages: list[ChatMessage],
        requested_provider: str | None = None,
        requested_model: str | None = None,
        online_override: bool | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        s = self.db.get_all_settings()
        is_online = self._check_online() if online_override is None else online_override
        allow_cloud = bool(s.get("ai.allow_cloud", False))
        allow_sens = bool(s.get("ai.send_sensitive_to_cloud", False))
        pref = requested_provider or str(s.get("ai.preferred_provider", "auto"))
        fb = str(s.get("ai.fallback_provider", "ollama"))
        sensitive = contains_sensitive_data("\n".join(m.content for m in messages if m.role == "user"))
        cloud_ok = allow_cloud and is_online and (not sensitive or allow_sens)

        providers = self.build_providers()
        errors: list[str] = []
        for name in self._candidates(pref, fb, cloud_ok, providers):
            prov = providers[name]
            st = prov.status()
            if not st.configured or (prov.is_local and not st.available):
                errors.append(f"{name}: {st.detail}")
                continue
            emitted = False
            try:
                for chunk in prov.chat_stream(messages, model=requested_model if name == pref else None, should_cancel=should_cancel):
                    emitted = True
                    yield chunk
                return
            except ProviderError as exc:
                errors.append(f"{name}: {exc}")
                if emitted:
                    raise
        if sensitive and allow_cloud and not allow_sens:
            errors.append("Sensitive content blocked from cloud providers by privacy policy.")
        raise ProviderError("router", "No real AI provider available. " + " | ".join(errors))
