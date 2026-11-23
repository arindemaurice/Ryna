"""Modular voice pipeline with real Windows SAPI offline TTS, instant barge-in interruption,
and honest detection of local STT/TTS/VAD/wake-word capabilities.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from typing import Any

from core.python.database import RynaDatabase

VOICE_MODES = ("wake_word", "push_to_talk", "continuous")
COMPANION_STATES = (
    "idle",
    "listening",
    "processing",
    "speaking",
    "executing",
    "waiting",
    "offline",
    "error",
    # Backward-compatible aliases accepted from older clients
    "thinking",
    "working",
    "alert",
    "sleeping",
)

STATE_CANONICAL_MAP = {
    "thinking": "processing",
    "working": "executing",
    "alert": "error",
    "sleeping": "idle",
}


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
    """Manages real voice state, Windows native SAPI offline TTS, barge-in cancellation, and backend detection."""

    def __init__(self, db: RynaDatabase) -> None:
        self.db = db
        self._lock = threading.RLock()
        self._state = "idle"
        self._interrupted = False
        self._active_generation_id: str | None = None
        self._speech_proc: subprocess.Popen[str] | None = None
        self._sapi_voices_cache: list[str] | None = None

    def list_sapi_voices(self, force_refresh: bool = False) -> list[str]:
        """Detect real installed Windows SAPI offline voices."""
        if sys.platform != "win32":
            return []
        with self._lock:
            if self._sapi_voices_cache is not None and not force_refresh:
                return list(self._sapi_voices_cache)
        try:
            cmd = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }; "
                "$s.Dispose()"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=4.0,
            )
            voices = [line.strip() for line in res.stdout.splitlines() if line.strip()] if res.returncode == 0 else []
        except Exception:
            voices = []
        with self._lock:
            self._sapi_voices_cache = voices
        return list(voices)

    def detect_backends(self) -> list[dict[str, Any]]:
        has_fw = _has_python_module("faster_whisper")
        has_whisper = _has_python_module("whisper")
        has_sd = _has_python_module("sounddevice")
        has_piper = shutil.which("piper") is not None or _has_python_module("piper")
        has_silero = _has_python_module("torch") and _has_python_module("torchaudio")
        has_oww = _has_python_module("openwakeword")
        sapi_voices = self.list_sapi_voices()

        backends = [
            VoiceBackendStatus(
                name="windows-sapi",
                role="tts",
                available=len(sapi_voices) > 0,
                offline=True,
                detail=(
                    f"Windows native offline TTS ready ({', '.join(sapi_voices)})."
                    if sapi_voices
                    else "Windows SAPI voices unavailable in current execution context."
                ),
            ),
            VoiceBackendStatus(
                name="browser-web-speech",
                role="stt",
                available=True,
                offline=False,
                detail="Real microphone capture, live waveform, speech recognition, and TTS available in Chromium/Edge desktop UI.",
            ),
            VoiceBackendStatus(
                name="faster-whisper",
                role="stt",
                available=has_fw,
                offline=True,
                detail="Installed and ready for offline STT." if has_fw else "Optional offline STT not installed (pip install faster-whisper).",
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
                detail="Installed and ready for neural offline TTS." if has_piper else "Optional neural TTS not installed.",
            ),
            VoiceBackendStatus(
                name="silero-vad",
                role="vad",
                available=has_silero,
                offline=True,
                detail="PyTorch audio runtime detected." if has_silero else "Optional offline VAD not installed.",
            ),
            VoiceBackendStatus(
                name="openwakeword",
                role="wake_word",
                available=has_oww,
                offline=True,
                detail="Installed for offline 'Hey Ryna' wake-word." if has_oww else "Browser wake-word phrase listener active when continuous/wake-word mode enabled; native openwakeword optional.",
            ),
            VoiceBackendStatus(
                name="sounddevice",
                role="audio_io",
                available=has_sd,
                offline=True,
                detail="Native Python audio I/O installed." if has_sd else "Optional native Python audio I/O not installed (browser Web Audio API active).",
            ),
        ]
        return [b.to_dict() for b in backends]

    def get_state(self) -> str:
        with self._lock:
            return self._state

    def set_state(self, state: str) -> str:
        if state not in COMPANION_STATES:
            raise ValueError(f"Invalid companion/voice state '{state}'.")
        canonical = STATE_CANONICAL_MAP.get(state, state)
        with self._lock:
            self._state = canonical
            if canonical in ("speaking", "processing"):
                self._interrupted = False
            return self._state

    def begin_generation(self, generation_id: str) -> None:
        with self._lock:
            self._active_generation_id = generation_id
            self._interrupted = False
            self._state = "processing"

    def end_generation(self, generation_id: str) -> None:
        with self._lock:
            if self._active_generation_id == generation_id:
                self._active_generation_id = None
                if self._state == "processing":
                    self._state = "idle"

    def stop_native_speech(self) -> bool:
        """Immediately terminate any active Windows SAPI speech subprocess."""
        with self._lock:
            proc = self._speech_proc
            self._speech_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return True
        return False

    def speak_native(
        self,
        text: str,
        voice_name: str | None = None,
        speech_speed: float | None = None,
        on_finished: Any | None = None,
    ) -> dict[str, Any]:
        """Speak text aloud offline via Windows SAPI in a cancelable background subprocess."""
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Cannot speak empty text.")
        if sys.platform != "win32":
            raise RuntimeError("Native SAPI TTS is only supported on Windows.")

        voices = self.list_sapi_voices()
        if not voices:
            raise RuntimeError("No Windows SAPI voices are available on this host.")

        self.stop_native_speech()

        selected = (voice_name or str(self.db.get_setting("voice.selected_voice", ""))).strip()
        if selected not in voices:
            # Prefer Microsoft Zira Desktop for RYNA when present
            selected = next((v for v in voices if "zira" in v.lower()), voices[0])

        speed = float(speech_speed if speech_speed is not None else self.db.get_setting("voice.speech_speed", 1.0))
        # SAPI Rate ranges from -10 to +10 (0 is normal 1.0x)
        sapi_rate = max(-6, min(6, int(round((speed - 1.0) * 5))))

        ps_script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.SelectVoice($env:RYNA_TTS_VOICE); "
            "$s.Rate = [int]$env:RYNA_TTS_RATE; "
            "$s.Speak($env:RYNA_TTS_TEXT); "
            "$s.Dispose()"
        )
        import os

        env = dict(os.environ)
        env["RYNA_TTS_VOICE"] = selected
        env["RYNA_TTS_RATE"] = str(sapi_rate)
        env["RYNA_TTS_TEXT"] = cleaned[:3500]

        with self._lock:
            self._interrupted = False
            self._state = "speaking"
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps_script],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self._speech_proc = proc

        def _watch() -> None:
            proc.wait()
            with self._lock:
                if self._speech_proc is proc:
                    self._speech_proc = None
                    if self._state == "speaking":
                        self._state = "idle"
            if callable(on_finished):
                try:
                    on_finished()
                except Exception:
                    pass

        threading.Thread(target=_watch, daemon=True).start()
        return {"speaking": True, "engine": "windows-sapi", "voice": selected, "rate": sapi_rate}

    def interrupt(self, reason: str = "User interrupted speech/generation") -> dict[str, Any]:
        """Immediately halt active speech playback and generation, ready for next turn."""
        stopped_native = self.stop_native_speech()
        with self._lock:
            was_state = self._state
            self._interrupted = True
            self._active_generation_id = None
            voice_enabled = bool(self.db.get_setting("voice.enabled", False))
            self._state = "listening" if voice_enabled else "idle"
            return {
                "interrupted": True,
                "stopped_native_speech": stopped_native,
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
        sapi_voices = self.list_sapi_voices()
        with self._lock:
            native_speaking = self._speech_proc is not None and self._speech_proc.poll() is None
        return {
            "enabled": bool(s.get("voice.enabled", False)),
            "auto_speak": bool(s.get("voice.auto_speak", True)),
            "mode": str(s.get("voice.mode", "push_to_talk")),
            "wake_word": str(s.get("voice.wake_word", "Hey Ryna")),
            "sensitivity": float(s.get("voice.sensitivity", 0.65)),
            "speech_speed": float(s.get("voice.speech_speed", 1.0)),
            "selected_voice": str(s.get("voice.selected_voice", "Microsoft Zira Desktop")),
            "tts_engine": str(s.get("voice.tts_engine", "browser")),
            "microphone": str(s.get("voice.microphone", "default")),
            "speaker": str(s.get("voice.speaker", "default")),
            "state": self.get_state(),
            "native_speaking": native_speaking,
            "interrupted": self.is_interrupted(),
            "sapi_voices": sapi_voices,
            "offline_stt_ready": any(b["role"] == "stt" and b["offline"] and b["available"] for b in backends),
            "offline_tts_ready": any(b["role"] == "tts" and b["offline"] and b["available"] for b in backends),
            "wake_word_ready": True,
            "backends": backends,
        }
