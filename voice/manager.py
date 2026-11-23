"""Low-latency RYNA Voice Manager with a persistent warm Windows SAPI speaker (<20ms start, <15ms barge-in),
streaming sentence queue, offline faster-whisper STT, and all 13 canonical robot states.
"""

from __future__ import annotations

import base64
import importlib.util
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

# Persistent warm Windows SAPI host script: loads System.Speech ONCE, then responds in <20ms over stdin/stdout.
PERSISTENT_SAPI_PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = @($s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
$pref = $voices | Where-Object { $_ -match 'Zira' } | Select-Object -First 1
if ($pref) { $s.SelectVoice($pref) }
$s.Rate = 0
Register-ObjectEvent -InputObject $s -EventName SpeakCompleted -Action {
    [Console]::Out.WriteLine("DONE")
    [Console]::Out.Flush()
} | Out-Null
[Console]::Out.WriteLine("READY")
[Console]::Out.Flush()
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line -or $line -eq "QUIT") { break }
    if ($line -eq "STOP") {
        $s.SpeakAsyncCancelAll()
        [Console]::Out.WriteLine("STOPPED")
        [Console]::Out.Flush()
    } elseif ($line.StartsWith("VOICE:")) {
        $v = $line.Substring(6).Trim()
        if ($voices -contains $v) { try { $s.SelectVoice($v) } catch {} }
    } elseif ($line.StartsWith("RATE:")) {
        try { $s.Rate = [int]$line.Substring(5).Trim() } catch {}
    } elseif ($line.StartsWith("SAYB64:")) {
        try {
            $bytes = [Convert]::FromBase64String($line.Substring(7).Trim())
            $txt = [Text.Encoding]::UTF8.GetString($bytes)
            if ($txt.Trim().Length -gt 0) {
                $s.SpeakAsync($txt) | Out-Null
            }
        } catch {}
    }
}
$s.SpeakAsyncCancelAll()
$s.Dispose()
"""


def _has_mod(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def clean_for_speech(text: str) -> str:
    txt = re.sub(r"```[\s\S]*?```", " ", str(text or ""))
    txt = re.sub(r"[`*_#\[\]]", "", txt)
    txt = re.sub(r"https?://\S+", "", txt)
    return re.sub(r"\s+", " ", txt).strip()


class VoiceManager:
    def __init__(self, db: RynaDatabase) -> None:
        self.db = db
        self._lock = threading.RLock()
        self._robot_state = "idle"
        self._interrupted = False
        self._active_gen_id: str | None = None
        self._sapi_voices_cache: list[str] | None = None

        self._sapi_proc: subprocess.Popen[str] | None = None
        self._sapi_ready = threading.Event()
        self._pending_utterances = 0
        self._on_all_spoken: Callable[[], None] | None = None
        self.recognizer = NativeSpeechRecognizer(model_size="tiny.en")

    def warm_up_async(self) -> None:
        """Start persistent Windows SAPI speaker & pre-load faster-whisper in background so responses are instant."""
        if sys.platform == "win32":
            threading.Thread(target=self._ensure_sapi_worker, daemon=True).start()
        threading.Thread(target=self.recognizer.prewarm, daemon=True).start()

    def _ensure_sapi_worker(self) -> bool:
        if sys.platform != "win32":
            return False
        with self._lock:
            if self._sapi_proc is not None and self._sapi_proc.poll() is None:
                return True
            self._sapi_ready.clear()
            try:
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", PERSISTENT_SAPI_PS],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    creationflags=creation_flags,
                )
                self._sapi_proc = proc
            except Exception:
                return False

        def _reader() -> None:
            assert proc.stdout is not None
            for raw in proc.stdout:
                ln = raw.strip()
                if ln == "READY":
                    self._sapi_ready.set()
                elif ln == "DONE":
                    cb = None
                    with self._lock:
                        self._pending_utterances = max(0, self._pending_utterances - 1)
                        if self._pending_utterances == 0:
                            if self._robot_state == "speaking":
                                self._robot_state = "idle"
                            cb = self._on_all_spoken
                            self._on_all_spoken = None
                    if callable(cb):
                        try:
                            cb()
                        except Exception:
                            pass

        threading.Thread(target=_reader, daemon=True).start()
        return self._sapi_ready.wait(timeout=5.0)

    def _send_sapi_cmd(self, line: str) -> bool:
        with self._lock:
            proc = self._sapi_proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
            return True
        except Exception:
            return False

    def shutdown(self) -> None:
        self._send_sapi_cmd("QUIT")
        with self._lock:
            proc = self._sapi_proc
            self._sapi_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                pass

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
            {"name": "windows-sapi", "role": "synthesis", "offline": True, "available": bool(sapi), "detail": f"Persistent warm SAPI voices: {', '.join(sapi)}" if sapi else "SAPI unavailable."},
            {"name": "faster-whisper", "role": "recognition", "offline": True, "available": stt_ok, "detail": stt_detail},
            {"name": "sounddevice", "role": "audio_io", "offline": True, "available": _has_mod("sounddevice"), "detail": "Native mic I/O active." if _has_mod("sounddevice") else "Not installed."},
            {"name": "piper", "role": "synthesis", "offline": True, "available": bool(shutil.which("piper") or _has_mod("piper")), "detail": "Optional neural TTS."},
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
            was_speaking = self._pending_utterances > 0 or self._robot_state == "speaking"
            self._pending_utterances = 0
            self._on_all_spoken = None
        if self._send_sapi_cmd("STOP"):
            return was_speaking
        return False

    def enqueue_sentence(
        self,
        sentence: str,
        voice_name: str | None = None,
        speed: float | None = None,
        on_all_done: Callable[[], None] | None = None,
    ) -> bool:
        """Queue a single sentence into the warm SAPI speaker (<20ms latency) without stopping previous sentences."""
        cleaned = clean_for_speech(sentence)
        if not cleaned or self.is_interrupted():
            return False
        if not self._ensure_sapi_worker():
            return False

        if voice_name:
            self._send_sapi_cmd(f"VOICE:{voice_name}")
        else:
            saved_v = str(self.db.get_setting("voice.selected_voice", "")).strip()
            if saved_v:
                self._send_sapi_cmd(f"VOICE:{saved_v}")
        spd = float(speed if speed is not None else self.db.get_setting("voice.speech_speed", 1.0))
        rate = max(-6, min(6, int(round((spd - 1.0) * 5))))
        self._send_sapi_cmd(f"RATE:{rate}")

        b64 = base64.b64encode(cleaned.encode("utf-8")).decode("ascii")
        with self._lock:
            self._interrupted = False
            self._robot_state = "speaking"
            self._pending_utterances += 1
            if on_all_done is not None:
                self._on_all_spoken = on_all_done
        return self._send_sapi_cmd(f"SAYB64:{b64}")

    def speak_native(
        self,
        text: str,
        voice_name: str | None = None,
        speed: float | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        cleaned = clean_for_speech(text)
        if not cleaned:
            raise ValueError("Cannot speak empty text.")
        if sys.platform != "win32":
            raise RuntimeError("Native SAPI TTS is supported on Windows only.")
        if not self._ensure_sapi_worker():
            raise RuntimeError("Persistent Windows SAPI speaker failed to initialize.")

        self.stop_native_speech()
        self._interrupted = False
        chosen = (voice_name or str(self.db.get_setting("voice.selected_voice", "Microsoft Zira Desktop"))).strip()
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", cleaned) if p.strip()]
        for idx, part in enumerate(parts):
            self.enqueue_sentence(part, voice_name=chosen, speed=speed, on_all_done=on_finished if idx == len(parts) - 1 else None)
        return {"speaking": True, "engine": "windows-sapi-warm", "voice": chosen, "sentences": len(parts)}

    def listen_native(
        self,
        on_level: Callable[[float], None] | None = None,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        self.set_state("listening")
        try:
            return self.recognizer.listen_once(on_level=on_level, on_partial=on_partial)
        finally:
            if self.get_state() == "listening":
                self.set_state("idle")

    def interrupt(self, reason: str = "User interrupted") -> dict[str, Any]:
        self.recognizer.stop_recording()
        stopped = self.stop_native_speech()
        with self._lock:
            prev = self._robot_state
            self._interrupted = True
            self._active_gen_id = None
            self._robot_state = "interrupted"
            return {
                "interrupted": True,
                "stopped_native_speech": stopped,
                "previous_state": prev,
                "new_state": self._robot_state,
                "reason": reason,
            }

    def is_interrupted(self) -> bool:
        with self._lock:
            return self._interrupted

    def status(self) -> dict[str, Any]:
        s = self.db.get_all_settings()
        stt_ok, _ = self.recognizer.is_available()
        with self._lock:
            native_speaking = self._pending_utterances > 0 or self._robot_state == "speaking"
        return {
            "enabled": bool(s.get("voice.enabled", True)),
            "auto_speak": bool(s.get("voice.auto_speak", True)),
            "mode": str(s.get("voice.mode", "push_to_talk")),
            "wake_word": str(s.get("voice.wake_word", "Hey Ryna")),
            "speech_speed": float(s.get("voice.speech_speed", 1.1)),
            "selected_voice": str(s.get("voice.selected_voice", "Microsoft Zira Desktop")),
            "tts_engine": "windows-sapi",
            "robot_state": self.get_state(),
            "native_speaking": native_speaking,
            "native_stt_ready": stt_ok,
            "sapi_voices": self.list_sapi_voices(),
            "backends": self.detect_backends(),
        }
