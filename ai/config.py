"""Configuration loader and authoritative RYNA identity defaults."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

APP_NAME = "RYNA"
VERSION = "0.1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

RYNA_SYSTEM_PROMPT = (
    "You are RYNA — a friendly, intelligent mini desktop robot assistant living on the user's Windows desktop. "
    "Your voice and personality are calm, warm, witty, confident, and natural. "
    "Because your replies are spoken aloud, keep answers concise (1 to 3 natural sentences) unless the user "
    "specifically asks for a long explanation. Never use markdown bullet clutter in casual spoken answers. "
    "Be strictly truthful: never claim you opened an app, searched the web, or completed an action unless it actually executed."
)

DEFAULT_SETTINGS: dict[str, Any] = {
    # AI & Routing
    "ai.preferred_provider": "auto",
    "ai.fallback_provider": "ollama",
    "ai.allow_cloud": False,
    "ai.send_sensitive_to_cloud": False,
    "ai.ollama_base_url": "http://127.0.0.1:11434",
    "ai.local_model": "",
    "ai.openai_base_url": "https://api.openai.com/v1",
    "ai.openai_model": "gpt-4o-mini",
    "ai.anthropic_model": "claude-3-5-sonnet-latest",
    "ai.gemini_model": "gemini-2.0-flash",
    # Voice
    "voice.enabled": True,
    "voice.auto_speak": True,
    "voice.mode": "push_to_talk",  # push_to_talk | wake_word | continuous
    "voice.wake_word": "Hey Ryna",
    "voice.wake_sensitivity": 0.65,
    "voice.speech_speed": 1.0,
    "voice.selected_voice": "Microsoft Zira Desktop",
    "voice.tts_engine": "windows-sapi",  # windows-sapi (offline) | browser
    "voice.microphone": "default",
    "voice.speaker": "default",
    "voice.vad_enabled": True,
    # Memory & Privacy
    "memory.enabled": True,
    "memory.learning_paused": False,
    "memory.retention_days": 365,
    "privacy.redact_secrets": True,
    "privacy.telemetry": False,
    # Desktop Robot Presence
    "desktop.robot_size": 170,
    "desktop.robot_x": -1,
    "desktop.robot_y": -1,
    "desktop.robot_transparency": 0.98,
    "desktop.robot_always_on_top": True,
    "desktop.robot_visible": True,
    "desktop.robot_walk_enabled": True,
    "desktop.physical_app_search": True,
    "desktop.reduced_animations": False,
    "desktop.startup_enabled": False,
    "desktop.background_mode": True,
    # Computer Control & Vision
    "computer.allowed_root": ".",
    "computer.allow_app_launch": True,
    "computer.allow_screen_vision": True,
    "computer.allow_input_control": False,
    # Permissions & Agent
    "permissions.level1_auto": True,
    "permissions.level2_require_confirm": True,
    "permissions.level3_block_destructive": True,
    "agent.background_execution": True,
    "agent.auto_recover_tasks": True,
    "notifications.enabled": True,
    "notifications.proactive": False,
    "general.language": "en",
}


def load_dotenv(env_path: Path | None = None) -> None:
    path = env_path or (PROJECT_ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class RynaConfig:
    project_root: Path = PROJECT_ROOT
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "ryna.db")
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")
    backup_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "backups")
    robot_asset_path: Path = field(default_factory=lambda: PROJECT_ROOT / "app" / "desktop" / "assets" / "ryna_robot.png")
    system_prompt: str = RYNA_SYSTEM_PROMPT
    default_settings: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    @classmethod
    def load(cls, env_path: Path | None = None, overrides: dict[str, Any] | None = None) -> "RynaConfig":
        load_dotenv(env_path)
        defaults = dict(DEFAULT_SETTINGS)
        for env_k, set_k in (
            ("RYNA_OLLAMA_BASE_URL", "ai.ollama_base_url"),
            ("RYNA_LOCAL_MODEL", "ai.local_model"),
            ("RYNA_OPENAI_BASE_URL", "ai.openai_base_url"),
            ("RYNA_OPENAI_MODEL", "ai.openai_model"),
            ("RYNA_ANTHROPIC_MODEL", "ai.anthropic_model"),
            ("RYNA_GEMINI_MODEL", "ai.gemini_model"),
            ("RYNA_PREFERRED_PROVIDER", "ai.preferred_provider"),
        ):
            if os.environ.get(env_k):
                defaults[set_k] = os.environ[env_k]
        if "RYNA_ALLOW_CLOUD" in os.environ:
            defaults["ai.allow_cloud"] = os.environ["RYNA_ALLOW_CLOUD"].strip().lower() in {"1", "true", "yes", "on"}

        data_raw = Path(os.environ.get("RYNA_DATA_DIR", "data"))
        data_dir = data_raw if data_raw.is_absolute() else (PROJECT_ROOT / data_raw)
        db_raw = os.environ.get("RYNA_DB_PATH")
        db_path = (Path(db_raw) if Path(db_raw).is_absolute() else PROJECT_ROOT / db_raw) if db_raw else (data_dir / "ryna.db")

        cfg = cls(
            host=os.environ.get("RYNA_HOST", "127.0.0.1"),
            port=int(os.environ.get("RYNA_PORT", "8765")),
            log_level=os.environ.get("RYNA_LOG_LEVEL", "INFO").upper(),
            data_dir=data_dir,
            db_path=db_path,
            default_settings=defaults,
            openai_api_key=os.environ.get("RYNA_OPENAI_API_KEY", ""),
            anthropic_api_key=os.environ.get("RYNA_ANTHROPIC_API_KEY", ""),
            gemini_api_key=os.environ.get("RYNA_GEMINI_API_KEY", ""),
        )
        if overrides:
            for k, v in overrides.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        return cfg


def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    import sys

    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ryna")
    lvl = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(lvl)
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(log_dir / "ryna.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # Only attach console StreamHandler when a real console stderr exists (safe under windowless pythonw.exe)
    if sys.stderr is not None and hasattr(sys.stderr, "write"):
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    return logger
