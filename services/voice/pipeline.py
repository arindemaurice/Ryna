"""Modular voice pipeline architecture, interruption state machine, and backend detection.

Checks actual installed offline voice engines (faster-whisper, whisper, piper, sounddevice)
instead of assuming availability, and manages barge-in / speech cancellation cleanly.
"""

from __future__ import annotations

import importlib.util
import shutil
import threading
from dataclasses import asdict, dataclass
from typing import Any

from core.python.database import RynaDatabase

VOICE_MODES = ("wake_word", "push_to_talk", "continuous")
COMPANION_STATES = ("idle", "listening", "thinking", "speaking", "working", "waiting", "alert", "sleeping")


@dataclass
class VoiceBackendStatus:
    name: str
    role: str  # stt | tts | vad | wake_word | audio_io
    available: bool
    offline: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _has_python_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


class VoicePipeline:
    """Tracks real voice backend availability, mode, listening/speaking state, and barge-in."""

    def __init__(self, db: RynaDatabase) -> None:
        self.db = db
        self._lock = threading.RLock()
        self._state = "idle"
        self._interrupted = False
        self._active_generation_id: str | None = None

    def detect_backends(self) -> list[dict[str, Any]]:
        has_fw = _has_python_module("faster_whisper")
        has_whisper = _has_python_module("whisper")
        has_sd = _has_python_module("sounddevice")
        has_piper = shutil.which("piper") is not None or _has_python_module("piper")
        has_silero = _has_python_module("torch") and _has_python_module("torchaudio")
        has_oww = _has_python_module("openwakeword")

        backends = [
            VoiceBackendStatus(
                name="faster-whisper",
                role="stt",
                available=has_fw,
                offline=True,
                detail="Installed and ready for offline STT." if has_fw else "Not installed. Install optional extra: pip install faster-whisper",
            ),
            VoiceBackendStatus(
                name="openai-whisper",
                role="stt",
                available=has_whisper,
                offline=True,
                detail="Installed." if has_whisper else "Not installed.",
            ),
            VoiceBackendStatus(
                name="piper",
                role="tts",
                available=has_piper,
                offline=True,
                detail="Installed and ready for offline neural TTS." if has_piper else "Not installed. Install Piper TTS binary or piper-tts.",
            ),
            VoiceBackendStatus(
                name="silero-vad",
                role="vad",
                available=has_silero,
                offline=True,
                detail="PyTorch audio runtime detected." if has_silero else "Not installed (optional offline VAD).",
            ),
            VoiceBackendStatus(
                name="openwakeword",
                role="wake_word",
                available=has_oww,
                offline=True,
                detail="Installed for 'Hey Ryna' wake-word detection." if has_oww else "Not installed (wake-word engine planned for Phase 3).",
            ),
            VoiceBackendStatus(
                name="sounddevice",
                role="audio_io",
                available=has_sd,
                offline=True,
                detail="Native microphone/speaker I/O available." if has_sd else "Not installed. Install sounddevice for native audio capture.",
            ),
        ]
        return [b.to_dict() for b in backends]

    def get_state(self) -> str:
        with self._lock:
            return self._state

    def set_state(self, state: str) -> str:
        if state not in COMPANION_STATES:
            raise ValueError(f"Invalid companion/voice state '{state}'. Expected one of {COMPANION_STATES}.")
        with self._lock:
            self._state = state
            if state in ("speaking", "thinking"):
                self._interrupted = False
            return self._state

    def begin_generation(self, generation_id: str) -> None:
        with self._lock:
            self._active_generation_id = generation_id
            self._interrupted = False
            self._state = "thinking"

    def end_generation(self, generation_id: str) -> None:
        with self._lock:
            if self._active_generation_id == generation_id:
                self._active_generation_id = None
                if self._state in ("thinking", "speaking"):
                    self._state = "idle"

    def interrupt(self, reason: str = "User interrupted speech/generation") -> dict[str, Any]:
        """Immediately halt active speech/generation and transition cleanly to listening/idle."""
        with self._lock:
            was_state = self._state
            self._interrupted = True
            self._active_generation_id = None
            voice_enabled = bool(self.db.get_setting("voice.enabled", False))
            self._state = "listening" if voice_enabled else "idle"
            return {
                "interrupted": True,
                "previous_state": was_state,
                "new_state": self._state,
                "reason": reason,
            }

    def is_interrupted(self) -> bool:
        with self._lock:
            return self._interrupted

    def status(self) -> dict[str, Any]:
        s = self.db.get_all_settings()
        backends = self.detect_backends()
        return {
            "enabled": bool(s.get("voice.enabled", False)),
            "mode": str(s.get("voice.mode", "push_to_talk")),
            "wake_word": str(s.get("voice.wake_word", "Hey Ryna")),
            "sensitivity": float(s.get("voice.sensitivity", 0.65)),
            "speech_speed": float(s.get("voice.speech_speed", 1.0)),
            "selected_voice": str(s.get("voice.selected_voice", "system_default")),
            "microphone": str(s.get("voice.microphone", "default")),
            "state": self.get_state(),
            "interrupted": self.is_interrupted(),
            "offline_stt_ready": any(b["role"] == "stt" and b["available"] for b in backends),
            "offline_tts_ready": any(b["role"] == "tts" and b["available"] for b in backends),
            "wake_word_ready": any(b["role"] == "wake_word" and b["available"] for b in backends),
            "backends": backends,
        }
