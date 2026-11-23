"""Unified voice pipeline manager (Windows SAPI offline TTS, instant barge-in, 13 robot states, backend probes)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from typing import Any

from database.store import RynaDatabase

ROBOT_STATES = (
    "idle",
    "listening",
    "thinking",
    "speaking",
    "working",
    "waiting",
    "curious",
    "happy",
    "confused",
    "concerned",
    "sleeping",
    "interrupted",
    "error",
)


def _has_mod(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


class VoiceManager:
    def __init__(self, db: RynaDatabase) -> None:
        self.db = db
        self._lock = threading.RLock()
        self._robot_state = "idle"
        self._interrupted = False
        self._active_gen_id: str | None = None
        self._sapi_proc: subprocess.Popen[str] | None = None
        self._sapi_voices_cache: list[str] | None = None

    def list_sapi_voices(self, refresh: bool = False) -> list[str]:
        if sys.platform != "win32":
            return []
        with self._lock:
            if self._sapi_voices_cache is not None and not refresh:
                return list(self._sapi_voices_cache)
        try:
            cmd = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }; "
                "$s.Dispose()"
            )
            res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=4.0)
            voices = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()] if res.returncode == 0 else []
        except Exception:
            voices = []
        with self._lock:
            self._sapi_voices_cache = voices
        return list(voices)

    def detect_backends(self) -> list[dict[str, Any]]:
        sapi = self.list_sapi_voices()
        return [
            {"name": "windows-sapi", "role": "synthesis", "offline": True, "available": bool(sapi), "detail": f"Offline Windows SAPI voices: {', '.join(sapi)}" if sapi else "SAPI voices unavailable in current context."},
            {"name": "browser-web-speech", "role": "recognition", "offline": False, "available": True, "detail": "Live microphone capture, waveform, STT, wake-word, and TTS in Edge/Chrome."},
            {"name": "faster-whisper", "role": "recognition", "offline": True, "available": _has_mod("faster_whisper"), "detail": "Installed." if _has_mod("faster_whisper") else "Optional offline STT not installed (pip install faster-whisper)."},
            {"name": "piper", "role": "synthesis", "offline": True, "available": bool(shutil.which("piper") or _has_mod("piper")), "detail": "Installed." if (shutil.which("piper") or _has_mod("piper")) else "Optional offline neural TTS not installed."},
            {"name": "openwakeword", "role": "wake_word", "offline": True, "available": _has_mod("openwakeword"), "detail": "Installed." if _has_mod("openwakeword") else "Browser wake-word active; optional OS-level openwakeword not installed."},
            {"name": "sounddevice", "role": "audio_io", "offline": True, "available": _has_mod("sounddevice"), "detail": "Installed." if _has_mod("sounddevice") else "Browser Web Audio API active; optional native sounddevice not installed."},
        ]

    def get_state(self) -> str:
        with self._lock:
            return self._robot_state

    def set_state(self, state: str) -> str:
        if state not in ROBOT_STATES:
            raise ValueError(f"Invalid robot state '{state}'. Expected one of {ROBOT_STATES}.")
        with self._lock:
            self._robot_state = state
            if state in ("thinking", "speaking"):
                self._interrupted = False
            return self._robot_state

    def begin_generation(self, gen_id: str) -> None:
        with self._lock:
            self._active_gen_id = gen_id
            self._interrupted = False
            self._robot_state = "thinking"

    def end_generation(self, gen_id: str) -> None:
        with self._lock:
            if self._active_gen_id == gen_id:
                self._active_gen_id = None
                if self._robot_state == "thinking":
                    self._robot_state = "idle"

    def stop_native_speech(self) -> bool:
        with self._lock:
            proc = self._sapi_proc
            self._sapi_proc = None
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
        speed: float | None = None,
        on_finished: Any | None = None,
    ) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Cannot speak empty text.")
        if sys.platform != "win32":
            raise RuntimeError("Native SAPI TTS is supported on Windows only.")
        voices = self.list_sapi_voices()
        if not voices:
            raise RuntimeError("No Windows SAPI voices detected.")

        self.stop_native_speech()
        chosen = (voice_name or str(self.db.get_setting("voice.selected_voice", ""))).strip()
        if chosen not in voices:
            chosen = next((v for v in voices if "zira" in v.lower()), voices[0])

        spd = float(speed if speed is not None else self.db.get_setting("voice.speech_speed", 1.0))
        sapi_rate = max(-6, min(6, int(round((spd - 1.0) * 5))))

        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.SelectVoice($env:RYNA_TTS_VOICE); "
            "$s.Rate = [int]$env:RYNA_TTS_RATE; "
            "$s.Speak($env:RYNA_TTS_TEXT); "
            "$s.Dispose()"
        )
        env = dict(os.environ, RYNA_TTS_VOICE=chosen, RYNA_TTS_RATE=str(sapi_rate), RYNA_TTS_TEXT=cleaned[:3500])

        with self._lock:
            self._interrupted = False
            self._robot_state = "speaking"
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self._sapi_proc = proc

        def _watch() -> None:
            proc.wait()
            with self._lock:
                if self._sapi_proc is proc:
                    self._sapi_proc = None
                    if self._robot_state == "speaking":
                        self._robot_state = "idle"
            if callable(on_finished):
                try:
                    on_finished()
                except Exception:
                    pass

        threading.Thread(target=_watch, daemon=True).start()
        return {"speaking": True, "engine": "windows-sapi", "voice": chosen, "rate": sapi_rate}

    def interrupt(self, reason: str = "User interrupted") -> dict[str, Any]:
        stopped_native = self.stop_native_speech()
        with self._lock:
            prev = self._robot_state
            self._interrupted = True
            self._active_gen_id = None
            self._robot_state = "interrupted"
            return {
                "interrupted": True,
                "stopped_native_speech": stopped_native,
                "previous_state": prev,
                "new_state": self._robot_state,
                "reason": reason,
            }

    def is_interrupted(self) -> bool:
        with self._lock:
            return self._interrupted

    def status(self) -> dict[str, Any]:
        s = self.db.get_all_settings()
        backends = self.detect_backends()
        sapi = self.list_sapi_voices()
        with self._lock:
            native_speaking = self._sapi_proc is not None and self._sapi_proc.poll() is None
        return {
            "enabled": bool(s.get("voice.enabled", True)),
            "auto_speak": bool(s.get("voice.auto_speak", True)),
            "mode": str(s.get("voice.mode", "push_to_talk")),
            "wake_word": str(s.get("voice.wake_word", "Hey Ryna")),
            "wake_sensitivity": float(s.get("voice.wake_sensitivity", 0.65)),
            "speech_speed": float(s.get("voice.speech_speed", 1.0)),
            "selected_voice": str(s.get("voice.selected_voice", "Microsoft Zira Desktop")),
            "tts_engine": str(s.get("voice.tts_engine", "browser")),
            "microphone": str(s.get("voice.microphone", "default")),
            "speaker": str(s.get("voice.speaker", "default")),
            "vad_enabled": bool(s.get("voice.vad_enabled", True)),
            "robot_state": self.get_state(),
            "native_speaking": native_speaking,
            "sapi_voices": sapi,
            "backends": backends,
        }
