"""Native Windows microphone capture (adaptive VAD + live partial dictation + 100% offline faster-whisper STT)."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import numpy as np


class NativeSpeechRecognizer:
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

    def _clean_whisper_text(self, text: str) -> str:
        cleaned = text.strip()
        # Filter common Whisper silence hallucinations
        low = cleaned.lower().strip(".!?, ")
        if low in {"", "you", "thank you", "thanks for watching", "bye", "mbc", "uh", "um", "hmm"} and len(cleaned) < 12:
            return ""
        return cleaned

    def listen_once(
        self,
        on_level: Callable[[float], None] | None = None,
        on_partial: Callable[[str], None] | None = None,
        max_seconds: float = 14.0,
        silence_seconds: float = 0.95,
        sample_rate: int = 16000,
    ) -> str:
        """Listen to real Windows mic with adaptive noise floor, live partial dictation, and offline transcription."""
        import sounddevice as sd

        self._stop_event.clear()
        self._recording = True
        frames: list[np.ndarray] = []
        frames_lock = threading.Lock()
        noise_samples: list[float] = []
        threshold = 0.006
        peak_rms = 0.0
        speech_started = False
        last_voice_ts = time.monotonic()
        start_ts = last_voice_ts
        last_partial_ts = start_ts

        def _callback(indata: np.ndarray, _frame_count: int, _time_info: Any, _status: Any) -> None:
            nonlocal speech_started, last_voice_ts, threshold, peak_rms
            mono = indata[:, 0].copy()
            rms = float(np.sqrt(np.mean(mono * mono)))
            peak_rms = max(peak_rms, rms)
            if on_level:
                try:
                    on_level(min(1.0, rms * 45.0))
                except Exception:
                    pass
            with frames_lock:
                frames.append(mono)

            now = time.monotonic()
            # Calibrate ambient noise floor during first 0.25s
            if now - start_ts < 0.25:
                noise_samples.append(rms)
                if noise_samples:
                    floor = float(np.median(noise_samples))
                    threshold = max(0.0032, min(0.022, floor * 2.0 + 0.0016))
                return

            if rms >= threshold:
                speech_started = True
                last_voice_ts = now

        try:
            with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=1600, callback=_callback):
                while not self._stop_event.is_set():
                    time.sleep(0.05)
                    now = time.monotonic()
                    if on_partial and (speech_started or peak_rms >= 0.003) and (now - last_partial_ts) >= 0.95:
                        last_partial_ts = now
                        with frames_lock:
                            snap = np.concatenate(frames).astype(np.float32) if frames else None
                        if snap is not None and len(snap) >= sample_rate // 2:
                            try:
                                segs, _ = self._get_model().transcribe(snap, language="en", vad_filter=False, beam_size=1)
                                partial = self._clean_whisper_text(" ".join(s.text.strip() for s in segs if s.text.strip()))
                                if partial:
                                    on_partial(partial)
                            except Exception:
                                pass

                    if now - start_ts >= max_seconds:
                        break
                    if speech_started and (now - last_voice_ts) >= silence_seconds:
                        break
                    if not speech_started and (now - start_ts) >= 6.5:
                        break
        finally:
            self._recording = False
            if on_level:
                try:
                    on_level(0.0)
                except Exception:
                    pass

        with frames_lock:
            if not frames or (not speech_started and peak_rms < 0.0025):
                return ""
            audio = np.concatenate(frames).astype(np.float32)

        model = self._get_model()
        segments, _info = model.transcribe(audio, language="en", vad_filter=False, beam_size=1)
        raw = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
        return self._clean_whisper_text(raw)
