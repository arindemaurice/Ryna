"""Native Windows microphone capture (sounddevice + numpy VAD) and 100% offline STT (faster-whisper)."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import numpy as np


class NativeSpeechRecognizer:
    """Records speech from the real Windows microphone with automatic silence turn-detection
    and transcribes locally using faster-whisper (100% offline once model is cached).
    """

    def __init__(self, model_size: str = "tiny.en") -> None:
        self.model_size = model_size
        self._model: Any = None
        self._model_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._recording = False

    def is_available(self) -> tuple[bool, str]:
        try:
            import faster_whisper  # noqa: F401
            import sounddevice as sd

            devs = [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
            if not devs:
                return False, "No microphone input device detected."
            return True, f"Native offline STT ready ({len(devs)} mic(s), faster-whisper {self.model_size})."
        except Exception as exc:
            return False, f"Native STT unavailable: {exc}"

    def _get_model(self) -> Any:
        with self._model_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            return self._model

    def prewarm(self) -> bool:
        try:
            self._get_model()
            return True
        except Exception:
            return False

    def stop_recording(self) -> None:
        self._stop_event.set()

    def listen_once(
        self,
        on_level: Callable[[float], None] | None = None,
        max_seconds: float = 12.0,
        silence_seconds: float = 1.15,
        energy_threshold: float = 0.014,
        sample_rate: int = 16000,
    ) -> str:
        """Listen to real microphone until user finishes speaking, then transcribe locally."""
        import sounddevice as sd

        self._stop_event.clear()
        self._recording = True
        frames: list[np.ndarray] = []
        speech_started = False
        last_voice_ts = time.monotonic()
        start_ts = last_voice_ts

        def _callback(indata: np.ndarray, _frame_count: int, _time_info: Any, _status: Any) -> None:
            nonlocal speech_started, last_voice_ts
            mono = indata[:, 0].copy()
            rms = float(np.sqrt(np.mean(mono * mono)))
            if on_level:
                try:
                    on_level(min(1.0, rms * 18.0))
                except Exception:
                    pass
            frames.append(mono)
            now = time.monotonic()
            if rms >= energy_threshold:
                speech_started = True
                last_voice_ts = now

        try:
            with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=1600, callback=_callback):
                while not self._stop_event.is_set():
                    time.sleep(0.06)
                    now = time.monotonic()
                    if now - start_ts >= max_seconds:
                        break
                    if speech_started and (now - last_voice_ts) >= silence_seconds:
                        break
                    if not speech_started and (now - start_ts) >= 5.0:
                        break
        finally:
            self._recording = False
            if on_level:
                try:
                    on_level(0.0)
                except Exception:
                    pass

        if not frames or not speech_started:
            return ""

        audio = np.concatenate(frames).astype(np.float32)
        model = self._get_model()
        segments, _info = model.transcribe(audio, language="en", vad_filter=True, beam_size=1)
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
        return text

    def transcribe_array(self, audio_16k_f32: np.ndarray) -> str:
        model = self._get_model()
        segments, _info = model.transcribe(audio_16k_f32.astype(np.float32), language="en", vad_filter=True, beam_size=1)
        return " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
