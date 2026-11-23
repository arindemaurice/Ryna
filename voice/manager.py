"""Unified voice pipeline: real Windows SAPI sentence-streaming TTS, native offline STT (faster-whisper + sounddevice),
instant barge-in interruption, and all 13 canonical RYNA robot states.
"""

from __future__ import annotations

import importlib.util
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from typing import Any, Callable

from database.store import RynaDatabase
from voice.native_voice import NativeSpeechRecognizer

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


def clean_for_speech(text: str) -> str:
    txt = re.sub(r"```[\s\S]*?```", " Code block omitted. ", str(text or ""))
    txt = re.sub(r"[`*_#\[\]]", "", txt)
    txt = re.sub(r"https?://\S+", "", txt)
    return re.sub(r"\s+", " ", txt).strip()


def split_sentences(text: str) -> list[str]:
    cleaned = clean_for_speech(text)
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [p.strip() for p in parts if p.strip()]


class VoiceManager:
    def __init__(self, db: RynaDatabase) -> None:
        self.db = db
        self._lock = threading.RLock()
        self._robot_state = "idle"
        self._interrupted = False
        self._active_gen_id: str | None = None
        self._sapi_proc: subprocess.Popen[str] | None = None
        self._sapi_voices_cache: list[str] | None = None
        self._speech_queue: queue.Queue[str | None] = queue.Queue()
        self._speech_worker_running = False
        self._on_speech_done: Callable[[], None] | None = None
        self.recognizer = NativeSpeechRecognizer(model_size="tiny.en")

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
        stt_ok, stt_detail = self.recognizer.is_available()
        return [
            {"name": "windows-sapi", "role": "synthesis", "offline": True, "available": bool(sapi), "detail": f"Offline Windows SAPI voices: {', '.join(sapi)}" if sapi else "SAPI voices unavailable."},
            {"name": "faster-whisper", "role": "recognition", "offline": True, "available": stt_ok, "detail": stt_detail},
            {"name": "sounddevice", "role": "audio_io", "offline": True, "available": _has_mod("sounddevice"), "detail": "Native Windows microphone & audio I/O active." if _has_mod("sounddevice") else "Not installed."},
            {"name": "piper", "role": "synthesis", "offline": True, "available": bool(shutil.which("piper") or _has_mod("piper")), "detail": "Installed." if (shutil.which("piper") or _has_mod("piper")) else "Optional neural TTS not installed (Windows SAPI active)."},
            {"name": "openwakeword", "role": "wake_word", "offline": True, "available": _has_mod("openwakeword"), "detail": "Installed." if _has_mod("openwakeword") else "VAD + faster-whisper phrase wake-word active."},
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

    def _clear_speech_queue(self) -> None:
        while True:
            try:
                self._speech_queue.get_nowait()
            except queue.Empty:
                break

    def stop_native_speech(self) -> bool:
        self._clear_speech_queue()
        with self._lock:
            proc = self._sapi_proc
            self._sapi_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=0.8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return True
        return False

    def _run_sapi_chunk(self, text: str, voice_name: str, sapi_rate: int) -> None:
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.SelectVoice($env:RYNA_TTS_VOICE); "
            "$s.Rate = [int]$env:RYNA_TTS_RATE; "
            "$s.Speak($env:RYNA_TTS_TEXT); "
            "$s.Dispose()"
        )
        env = dict(os.environ, RYNA_TTS_VOICE=voice_name, RYNA_TTS_RATE=str(sapi_rate), RYNA_TTS_TEXT=text[:1800])
        with self._lock:
            if self._interrupted:
                return
            self._robot_state = "speaking"
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self._sapi_proc = proc
        proc.wait()
        with self._lock:
            if self._sapi_proc is proc:
                self._sapi_proc = None

    def speak_native(
        self,
        text: str,
        voice_name: str | None = None,
        speed: float | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        sentences = split_sentences(text)
        if not sentences:
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

        with self._lock:
            self._interrupted = False
            self._robot_state = "speaking"
            self._on_speech_done = on_finished

        for sent in sentences:
            self._speech_queue.put(sent)
        self._speech_queue.put(None)

        def _worker() -> None:
            try:
                while True:
                    chunk = self._speech_queue.get()
                    if chunk is None or self.is_interrupted():
                        break
                    self._run_sapi_chunk(chunk, chosen, sapi_rate)
            finally:
                with self._lock:
                    if self._robot_state == "speaking":
                        self._robot_state = "idle"
                    cb = self._on_speech_done
                if callable(cb):
                    try:
                        cb()
                    except Exception:
                        pass

        threading.Thread(target=_worker, daemon=True).start()
        return {"speaking": True, "engine": "windows-sapi", "voice": chosen, "rate": sapi_rate, "sentences": len(sentences)}

    def listen_native(self, on_level: Callable[[float], None] | None = None) -> str:
        """Record from real Windows mic and transcribe offline with faster-whisper."""
        self.set_state("listening")
        try:
            text = self.recognizer.listen_once(on_level=on_level)
            return text
        finally:
            if self.get_state() == "listening":
                self.set_state("idle")

    def interrupt(self, reason: str = "User interrupted") -> dict[str, Any]:
        self.recognizer.stop_recording()
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
        stt_ok, _ = self.recognizer.is_available()
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
            "tts_engine": str(s.get("voice.tts_engine", "windows-sapi")),
            "microphone": str(s.get("voice.microphone", "default")),
            "speaker": str(s.get("voice.speaker", "default")),
            "vad_enabled": bool(s.get("voice.vad_enabled", True)),
            "robot_state": self.get_state(),
            "native_speaking": native_speaking,
            "native_stt_ready": stt_ok,
            "sapi_voices": sapi,
            "backends": backends,
        }
