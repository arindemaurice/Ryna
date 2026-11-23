"""Honest modular integration registry (reports ONLY real implemented & active integrations)."""

from __future__ import annotations

from typing import Any


def build_integration_report(ai_status: dict[str, Any], voice_status: dict[str, Any], computer_caps: dict[str, Any]) -> list[dict[str, Any]]:
    provs = ai_status.get("providers", {})
    return [
        {"id": "ollama", "category": "local_ai", "name": "Ollama Local Runtime", "connected": bool(provs.get("ollama", {}).get("available")), "detail": provs.get("ollama", {}).get("detail", "")},
        {"id": "openai", "category": "cloud_ai", "name": "OpenAI-Compatible API", "connected": bool(provs.get("openai", {}).get("available")), "detail": provs.get("openai", {}).get("detail", "")},
        {"id": "anthropic", "category": "cloud_ai", "name": "Anthropic API", "connected": bool(provs.get("anthropic", {}).get("available")), "detail": provs.get("anthropic", {}).get("detail", "")},
        {"id": "gemini", "category": "cloud_ai", "name": "Google Gemini API", "connected": bool(provs.get("gemini", {}).get("available")), "detail": provs.get("gemini", {}).get("detail", "")},
        {"id": "windows_sapi", "category": "voice", "name": "Windows SAPI Offline TTS", "connected": bool(voice_status.get("sapi_voices")), "detail": f"{len(voice_status.get('sapi_voices', []))} voice(s) detected."},
        {"id": "local_files", "category": "files", "name": "Sandboxed Local Filesystem", "connected": True, "detail": "Search, read, attachments, and Level-2 gated write active."},
        {"id": "windows_desktop", "category": "os", "name": "Windows Desktop Bridge", "connected": bool(computer_caps.get("active_window_inspection")), "detail": computer_caps.get("detail", "")},
        {"id": "web_fetch", "category": "browser", "name": "Safe Webpage Fetch", "connected": bool(ai_status.get("online")), "detail": "Public HTTP(S) read with prompt-injection isolation."},
    ]
