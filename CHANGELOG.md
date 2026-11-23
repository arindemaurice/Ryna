# Changelog

All notable changes to **RYNA** will be documented in this file.

The format follows [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-23

### Added
- **Complete Futuristic Minimal UI Redesign (`assets/web/`)**:
  - Deep obsidian/charcoal/graphite aesthetic with refined cyan/ice-blue/violet accents and minimal 5-destination navigation (`Home`, `Memory`, `Tasks`, `Activity`, `Settings`).
  - Centralized reactive state store (`assets/web/store.js`) synchronized over real-time Server-Sent Events (`GET /api/v1/events/stream`).
  - Event-driven integrated **RYNA Companion** canvas (`assets/web/companion.js`) supporting all 8 real states (`idle`, `listening`, `processing`, `speaking`, `executing`, `waiting`, `offline`, `error`) with live microphone radial waveform and speaking pulse synchronization.
  - Draggable **Floating Companion Dock** connected to the exact same RYNA conversation and engine session.
- **Real Token-by-Token Streaming AI (`POST /api/v1/chat/stream`)**:
  - Added native streaming support to `OllamaProvider` and `OpenAICompatibleProvider` with automatic fallback and instant mid-stream cancellation (`Stop` / `Esc`).
  - Added real **File Attachments** in the chat composer, plus per-message **Copy**, **Speak**, and **Retry** (`POST /api/v1/chat/retry`).
- **Full End-to-End Voice Pipeline (`assets/web/voice.js` + `services/voice/pipeline.py`)**:
  - Real microphone device enumeration and Web Audio API `AnalyserNode` live amplitude metering.
  - Real speech-to-text transcription (`SpeechRecognition`), `"Hey Ryna"` wake-word mode, and continuous natural turn-taking.
  - Dual TTS playback: Browser `SpeechSynthesis` (instant with word-boundary animation sync) AND **Windows Native Offline SAPI** (`POST /api/v1/voice/speak`, verified with Microsoft Zira & David voices).
  - Instant barge-in interruption stopping browser TTS, terminating active Windows SAPI subprocesses, and aborting in-flight generation immediately.
- **Real Operating Mode & Activity Feed**:
  - Explicit 5-state live operating mode classification (`Online · Cloud AI`, `Online · Local AI`, `Offline · Local AI`, `Offline · Limited`, `AI Unavailable`).
  - Real SQLite-backed `activity_events` table and `/api/v1/activity` endpoint recording actual AI requests, tool executions, voice events, file attachments, task updates, and errors (zero fake logs).
  - Task retry endpoint (`POST /api/v1/tasks/{id}/retry`) and step progress indicators.
- **Native Windows Companion Upgrade (`apps/windows_companion/companion_window.py`)**:
  - Click-to-interact quick desktop prompt wired directly to the same SQLite conversation and Windows SAPI voice output.

## [0.1.0] - 2026-11-23

### Added
- Initial clean-slate RYNA monorepo structure (`apps/`, `core/`, `services/`, `packages/`, `assets/`, `config/`, `docs/`, `scripts/`, `tests/`).
- **Core Python Engine (`core/python`)**: configuration loader with `.env` support, rotating structured logging, SQLite WAL database (`conversations`, `messages`, `memories`, `tasks`, `task_steps`, `settings`, `notes`, `action_audit`), hardware & connectivity detector, unified `RynaEngine`, and loopback-only (`127.0.0.1`) HTTP REST API server.
- **AI Provider Abstraction & Router (`services/ai`)**: real HTTP clients for local Ollama, OpenAI-compatible endpoints, Anthropic Messages API, and Google Gemini API; hardware-aware model tier recommendations; privacy blocking of sensitive prompts to cloud; honest unavailable reporting with zero fake AI responses.
- **Persistent Adaptive Memory (`services/memory`)**: 4 categories (`personal`, `project`, `knowledge`, `activity`), 5 epistemic types (`confirmed_fact`, `user_preference`, `inference`, `temporary`, `outdated`), deduplication, expiration, relevance retrieval, export, and automatic secret rejection.
- **Permission & Task Engine (`services/agents`)**: 3-level permission model, emergency stop, persistent action audit log, persistent multi-step tasks, crash recovery marking interrupted tasks as `paused`/`uncertain`, and offline queueing for internet-dependent steps.
- **Local Tools (`services/tools`)**: AST-safe calculator, root-sandboxed file search & reader, persistent local notes, and live system diagnostics (`/calc`, `/diag`, `/find`, `/read`, `/note`).
- **Voice Pipeline Foundation (`services/voice`)**: configurable wake-word / push-to-talk / continuous modes, barge-in interruption state machine, and live runtime discovery of offline audio/STT/TTS backends.
- **Runnable User Interfaces**:
  - Futuristic dark Navy/Cyan local Web UI (`assets/web`) covering Assistant, Companion, Task Center, Memory Center, and all 16 Settings categories.
  - Native animated Windows Desktop Companion (`apps/windows_companion`) using Tkinter + Win32 single-instance mutex with 8 visual states, drag/edge-snapping, size & transparency controls.
- **Cross-Platform Targets**:
  - Flutter/Dart desktop & cross-platform app (`apps/desktop`) wired to the RYNA local API protocol.
  - Rust core supervisor crate (`core/rust`) implementing single-instance locking, Windows startup check, and engine supervision.
- **Windows Tooling & Docs**: PowerShell 5.1+ scripts (`check_env.ps1`, `setup.ps1`, `run.ps1`, `test.ps1`, `startup_windows.ps1`), comprehensive `README.md`, architecture/security/testing docs, and automated unit + integration test suites.
