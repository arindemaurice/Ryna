"""Configuration loader for RYNA with secure defaults and .env support."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "ryna.default.json"


def _parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_dotenv(env_path: Path | None = None) -> dict[str, str]:
    """Load simple KEY=VALUE lines from .env without overriding existing process env."""
    path = env_path or (PROJECT_ROOT / ".env")
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        loaded[key] = val
        os.environ.setdefault(key, val)
    return loaded


@dataclass
class RynaConfig:
    project_root: Path = PROJECT_ROOT
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "ryna.db")
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")
    system_prompt: str = ""
    default_settings: dict[str, Any] = field(default_factory=dict)
    hardware_model_tiers: list[dict[str, Any]] = field(default_factory=list)

    # Secrets loaded from environment only (never persisted to ordinary memory)
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    @classmethod
    def load(
        cls,
        config_path: Path | None = None,
        env_path: Path | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> "RynaConfig":
        load_dotenv(env_path)
        cfg_file = config_path or DEFAULT_CONFIG_PATH
        raw: dict[str, Any] = {}
        if cfg_file.exists():
            raw = json.loads(cfg_file.read_text(encoding="utf-8"))

        app_cfg = raw.get("app", {})
        defaults = dict(raw.get("default_settings", {}))

        # Apply environment overrides into defaults where appropriate
        env_map = {
            "RYNA_OLLAMA_BASE_URL": "ai.ollama_base_url",
            "RYNA_LOCAL_MODEL": "ai.local_model",
            "RYNA_OPENAI_BASE_URL": "ai.openai_base_url",
            "RYNA_OPENAI_MODEL": "ai.openai_model",
            "RYNA_ANTHROPIC_MODEL": "ai.anthropic_model",
            "RYNA_GEMINI_MODEL": "ai.gemini_model",
            "RYNA_PREFERRED_PROVIDER": "ai.preferred_provider",
        }
        for env_key, setting_key in env_map.items():
            if os.environ.get(env_key):
                defaults[setting_key] = os.environ[env_key]
        if "RYNA_ALLOW_CLOUD" in os.environ:
            defaults["ai.allow_cloud"] = _parse_bool(os.environ.get("RYNA_ALLOW_CLOUD"), False)

        host = os.environ.get("RYNA_HOST", app_cfg.get("host", "127.0.0.1"))
        # Enforce loopback by default unless explicitly overridden intentionally
        port = int(os.environ.get("RYNA_PORT", app_cfg.get("port", 8765)))
        log_level = os.environ.get("RYNA_LOG_LEVEL", app_cfg.get("log_level", "INFO")).upper()

        data_dir_raw = os.environ.get("RYNA_DATA_DIR", "data")
        data_dir = Path(data_dir_raw)
        if not data_dir.is_absolute():
            data_dir = PROJECT_ROOT / data_dir

        db_path_raw = os.environ.get("RYNA_DB_PATH")
        if db_path_raw:
            db_path = Path(db_path_raw)
            if not db_path.is_absolute():
                db_path = PROJECT_ROOT / db_path
        else:
            db_path = data_dir / "ryna.db"

        log_dir = PROJECT_ROOT / "logs"

        instance = cls(
            project_root=PROJECT_ROOT,
            host=host,
            port=port,
            log_level=log_level,
            data_dir=data_dir,
            db_path=db_path,
            log_dir=log_dir,
            system_prompt=raw.get("identity", {}).get(
                "system_prompt",
                "You are RYNA, a personal AI assistant, companion, and autonomous agent.",
            ),
            default_settings=defaults,
            hardware_model_tiers=list(raw.get("hardware_model_tiers", [])),
            openai_api_key=os.environ.get("RYNA_OPENAI_API_KEY", ""),
            anthropic_api_key=os.environ.get("RYNA_ANTHROPIC_API_KEY", ""),
            gemini_api_key=os.environ.get("RYNA_GEMINI_API_KEY", ""),
        )

        if overrides:
            for k, v in overrides.items():
                if hasattr(instance, k):
                    setattr(instance, k, v)

        return instance
