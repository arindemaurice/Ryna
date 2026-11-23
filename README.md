# RYNA — Autonomous AI Assistant

**Version:** `0.2.0`

## 1. Project Name
**RYNA** — Personal AI Assistant, Expressive Desktop Companion, and Autonomous Task Agent.

## 2. Overview
RYNA is a fresh, modular, cross-platform personal AI assistant built from scratch with an **offline-first, privacy-respecting architecture**. RYNA stores conversations, persistent adaptive memory, task states, notes, settings, and security audit logs in a local SQLite database and communicates exclusively over local loopback (`127.0.0.1`).

## 3. Vision
**Same RYNA. Same identity. Same memory. Same assistant. Online or offline.**

Internet connectivity expands RYNA's intelligence and toolset, but never determines whether RYNA functions. When offline, RYNA continues operating with local AI models (such as Ollama), local memory, local file/math/diagnostic tools, and persistent task queues that resume safely once connectivity returns.

## 4. Features

### Working Today (v0.2.0)
- **Redesigned Futuristic Minimal Desktop UI**: Deep obsidian/charcoal interface (`assets/web/`) with 5 real functional pages (`Home`, `Memory`, `Tasks`, `Activity`, `Settings`), centralized reactive state (`store.js`), live Server-Sent Events sync (`/api/v1/events/stream`), and a draggable Floating Companion Dock sharing the exact same AI session.
- **Real Token-by-Token Streaming AI**: Native HTTP streaming for **Ollama** (local offline models) and **OpenAI-compatible** providers via `POST /api/v1/chat/stream`, plus Anthropic & Gemini support, mid-stream cancellation (`Stop` / `Esc`), **File Attachments**, and per-message **Copy**, **Speak**, and **Retry**.
- **Real End-to-End Voice Pipeline**:
  - Live microphone activation with real Web Audio API waveform metering driving the Companion visualization.
  - Real speech-to-text transcription (`SpeechRecognition` in Edge/Chrome), `"Hey Ryna"` wake-word mode, and continuous turn-taking.
  - Dual Text-to-Speech: Browser `SpeechSynthesis` (instant with word-boundary companion sync) AND **Windows Native Offline SAPI** (`System.Speech` — works 100% offline with installed Windows voices like Microsoft Zira & David).
  - Instant barge-in interruption stopping playback and generation immediately.
- **Real 5-State Operating Mode Indicator**: Live distinction between `Online · Cloud AI`, `Online · Local AI`, `Offline · Local AI`, `Offline · Limited`, and `AI Unavailable`, plus honest audio/microphone diagnostics.
- **Live Chronological Activity Feed**: SQLite-backed `/api/v1/activity` logging real AI requests, voice events, tool runs, file attachments, task transitions, memory changes, and errors (zero fake logs).
- **Persistent Memory, 3-Tier Permissions & Crash-Safe Task Queue**: Categorized memory with secret rejection, Level 1/2/3 permissions, Emergency Stop, task step progress bars, task Retry, offline queueing, and restart recovery.
- **Native Animated Windows Companion**: Click-to-interact Tkinter companion orb wired directly to the same local RYNA session and Windows SAPI speech, with Win32 single-instance mutex protection.

### Implemented Integration Boundaries (Awaiting Toolchain or Later Phase)
- **Flutter Cross-Platform App (`apps/desktop`)**: Real Dart/Flutter codebase wired to `/api/v1/*` — ready to build once Flutter SDK + Visual Studio C++ Build Tools are installed.
- **Rust Core Supervisor (`core/rust`)**: Real Rust crate implementing single-instance lock, Windows startup check, and engine supervision — ready to compile once `rustc`/`cargo` are installed.
- **Offline Voice Pipeline (`services/voice`)**: Mode/state machine, barge-in interruption handling, and live probe for `faster-whisper`, `piper`, `silero-vad`, `openwakeword`, and `sounddevice`.

## 5. Current Implementation Status

| Phase | Milestone | Status |
|---|---|---|
| Phase 1 | Integrated foundation (core engine, SQLite, UI, companion, scripts, tests, docs) | **Implemented & Tested (0.1.0)** |
| Phase 2 | AI provider abstraction, local Ollama & cloud HTTP clients, routing, fallback | **Implemented & Tested (0.1.0)** (streaming SSE planned next) |
| Phase 3 | Full live audio capture, offline STT/TTS, wake word ("Hey Ryna") | **Architecture & State Machine Ready** (audio runtime planned) |
| Phase 4 | Persistent memory, retrieval, Memory Center, privacy & export | **Implemented & Tested (0.1.0)** |
| Phase 5 | Computer intelligence (screen capture, OCR, GUI control, browser automation) | **Scoped File/Diag Tools Ready; GUI/OCR Planned** |
| Phase 6 | Autonomous multi-step agent planning, tool selection, persistent recovery | **Task Engine & Recovery Implemented; LLM Planner Planned** |
| Phase 7 | Cross-platform builds (Android, macOS, Linux, iOS) | **Flutter App Scaffolded; Builds Pending SDK** |
| Phase 8 | Production packaging, installer, auto-updates, full security hardening | **Planned** |

## 6. Architecture
See [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) for the full system diagram, offline/online state transition flow, and cross-platform capability matrix.

## 7. Technology Stack
- **Core Runtime & AI/Agent Layer**: Python 3.11+ (tested on Python 3.13.17, zero required third-party dependencies for core runtime)
- **Local Database**: SQLite 3 (WAL mode, foreign keys enabled)
- **Runnable Desktop UI & Companion**: Local HTML/CSS/JS UI served on `127.0.0.1` + Native Python Tkinter/Win32 Windows Companion
- **Cross-Platform Frontend**: Flutter & Dart (`apps/desktop`)
- **Native Core Supervisor**: Rust (`core/rust`)
- **API Protocol**: Local loopback JSON REST API (`packages/protocol/api_schema.json`)

## 8. Repository Structure

```text
RYNA/
├── apps/
│   ├── desktop/              # Cross-platform Flutter/Dart application
│   └── windows_companion/    # Runnable native Windows Tkinter companion & Win32 mutex
├── assets/
│   └── web/                  # Futuristic dark Navy/Cyan RYNA local UI
├── config/
│   └── ryna.default.json     # Default settings, identity prompt, hardware model tiers
├── core/
│   ├── python/               # Unified RYNA engine, SQLite DB, hardware monitor, HTTP server
│   └── rust/                 # Rust lifecycle supervisor, single-instance lock, startup check
├── docs/
│   ├── architecture/         # System overview & cross-platform matrix
│   ├── development/          # Windows setup & build instructions
│   ├── security/             # 3-tier permissions & secret filtering
│   └── testing/              # Automated testing strategy
├── packages/
│   └── protocol/             # Shared REST API schema
├── scripts/                  # Windows PowerShell setup, check, run, test, startup scripts
├── services/
│   ├── ai/                   # Ollama, OpenAI-compatible, Anthropic, Gemini & Router
│   ├── agents/               # 3-tier Permission Engine & Persistent Task Engine
│   ├── memory/               # Categorized adaptive memory with secret rejection
│   ├── tools/                # Safe local calculator, file search/read, notes, diagnostics
│   └── voice/                # Voice state machine, barge-in, offline backend probe
├── tests/                    # Automated unit and live server integration tests
├── .env.example
├── .gitignore
├── pyproject.toml
├── CHANGELOG.md
└── README.md
```

*Note on structure*: Unused empty placeholder folders were intentionally omitted until their code lands, keeping the repository clean and purposeful.

## 9. Prerequisites
- **Required now**:
  - Windows 10/11 (or macOS/Linux)
  - Python 3.11 or newer (`python --version`)
  - Git
- **Optional (for Offline AI)**:
  - [Ollama](https://ollama.com/download/windows) + at least one pulled local model
- **Optional (for Native Rust & Flutter Builds)**:
  - Rust (`rustup` / `cargo`) + Visual Studio C++ Build Tools
  - Flutter SDK 3.19+

## 10. Installation
1. Clone or open this repository on Windows.
2. Run the environment checker:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1
   ```
3. Run the setup script (creates `.env` if missing and initializes `data/ryna.db`):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```

## 11. Development Setup
Core RYNA runs directly with standard Python 3.11+ without needing to compile native extensions. Optional Python extras (such as `pytest`, `faster-whisper`, `sounddevice`, `pypdf`, `python-docx`) are declared in `pyproject.toml`. See [docs/development/SETUP.md](docs/development/SETUP.md).

## 12. Environment Configuration
Copy `.env.example` to `.env` (handled automatically by `scripts\setup.ps1`):
- `RYNA_HOST=127.0.0.1` — keep loopback for security
- `RYNA_PORT=8765` — local API & UI port
- `RYNA_OLLAMA_BASE_URL=http://127.0.0.1:11434` — local Ollama endpoint
- `RYNA_LOCAL_MODEL=` — optional default Ollama model name (auto-selects first installed model if blank)
- `RYNA_ALLOW_CLOUD=false` — set `true` (or toggle in Settings → Online AI) to allow cloud models
- `RYNA_OPENAI_API_KEY`, `RYNA_ANTHROPIC_API_KEY`, `RYNA_GEMINI_API_KEY` — optional cloud API keys (never stored in ordinary memory)

## 13. How to Run
```powershell
# Start RYNA local engine and open the full UI in your browser
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -OpenUI

# Start RYNA local engine + native animated Windows Desktop Companion + browser UI
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -OpenUI -Companion
```
Then open `http://127.0.0.1:8765` if `-OpenUI` was omitted.

## 14. How to Test
```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```
Runs all Python unit & integration tests (and automatically runs `cargo test` and `flutter test` when Rust/Flutter are installed).

## 15. How to Build
- **Python Engine**: Runs directly from source via `python -m core.python.main`.
- **Rust Core Engine** (requires Rust): `cd core\rust; cargo build --release`
- **Flutter Desktop App** (requires Flutter + VS C++ Build Tools): `cd apps\desktop; flutter build windows`

## 16. Windows Instructions
- **Single-Instance Behavior**: Launching RYNA a second time detects the active Win32 named mutex (`Local\RYNA_LOCAL_ENGINE_SINGLE_INSTANCE`) instead of starting duplicate engines.
- **Start on Windows Login**:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\startup_windows.ps1 -Action status
  powershell -ExecutionPolicy Bypass -File scripts\startup_windows.ps1 -Action enable -WithCompanion
  powershell -ExecutionPolicy Bypass -File scripts\startup_windows.ps1 -Action disable
  ```

## 17. Offline AI Setup
1. Install Ollama for Windows (if not already installed).
2. Start Ollama (launch the Ollama app or run `ollama serve`).
3. Pull a model sized for your hardware (check **Settings → Offline AI** for RYNA's hardware-aware recommendation):
   ```powershell
   ollama pull qwen2.5:3b
   ```
4. Refresh RYNA — Ollama and the installed model appear automatically under **Settings → Offline AI**.

## 18. Cloud AI Configuration
1. Add your API key (`RYNA_OPENAI_API_KEY`, `RYNA_ANTHROPIC_API_KEY`, or `RYNA_GEMINI_API_KEY`) to `.env`.
2. Restart RYNA, open **Settings → Online AI**, and enable **Allow Cloud AI Providers when Online**.
3. Prompts containing detected secret/credential patterns are still blocked from cloud transmission unless explicitly overridden.

## 19. Voice Configuration
Open **Settings → Voice** to configure mode (`push_to_talk`, `wake_word`, `continuous`), wake word (`Hey Ryna`), sensitivity, and speech speed, and inspect live availability of offline voice engines (`faster-whisper`, `piper`, `silero-vad`, `openwakeword`, `sounddevice`). Live microphone audio capture and neural TTS synthesis are scheduled for Phase 3.

## 20. Security and Permissions
See [docs/security/PERMISSIONS.md](docs/security/PERMISSIONS.md). RYNA enforces 3 permission levels, an instant Emergency Stop, sandboxed file access, secret rejection from memory, and loopback-only binding.

## 21. Troubleshooting
- **"AI Provider Unavailable" banner in chat**: This is honest behavior when neither Ollama (with a pulled model) nor an enabled Cloud AI key is reachable. Start Ollama and pull a model, or configure Cloud AI in `.env`. Offline local tools (`/diag`, `/calc`, `/find`, `/note`) still work immediately.
- **Port 8765 already in use**: Run `scripts\run.ps1 -Port 8766 -OpenUI` or set `RYNA_PORT` in `.env`.
- **Rust/Flutter skipped during tests**: Install Rust (`https://rustup.rs`) and Flutter (`https://docs.flutter.dev/get-started/install/windows`), then re-run `scripts\check_env.ps1`.

## 22. Known Limitations (v0.2.0)
- **Offline Speech-to-Text (`faster-whisper`)**: Browser `SpeechRecognition` in Chromium/Edge works live today, but Chromium's built-in STT requires internet connectivity; install optional `faster-whisper` + `sounddevice` (declared in `pyproject.toml`) for a future fully offline STT bridge. Offline **Text-to-Speech** works 100% offline today via Windows native SAPI (`Settings → Voice → Windows Native SAPI`).
- **Flutter & Rust Compilation**: `apps/desktop` (Flutter) and `core/rust` (Rust) are implemented and kept in sync with the `0.2.0` API, but require installing Flutter SDK, Rust/Cargo, and Visual Studio C++ Build Tools to compile native binaries.
- **Phase 5+ Capabilities**: Screen capture/OCR, desktop GUI automation, binary PDF/DOCX/XLSX parsers, browser automation, LLM-driven autonomous tool planning, native Windows system tray icon, and multi-device sync are not yet implemented.

## 23. Roadmap
1. **0.2.0**: Streaming AI responses (SSE), LLM-driven tool-calling agent loop, and Ollama model pull management.
2. **0.3.0**: Live offline voice pipeline (`sounddevice` + `faster-whisper` + `piper` + Silero VAD + barge-in audio cancellation).
3. **0.4.0**: Rich local document intelligence (PDF, DOCX, XLSX, PPTX, CSV) and safe Windows application launching / accessibility APIs.
4. **0.5.0**: Compile & ship unified Rust supervisor + Flutter Windows/Android/macOS/Linux/iOS clients.

## 24. Contribution Guidelines
- Keep core changes modular and covered by automated tests in `tests/`.
- Never add fake AI responses, hardcoded dashboard metrics, or non-functional buttons.
- Update `README.md` and `CHANGELOG.md` alongside every behavioral change.

## 25. License Status
License not yet selected (all rights reserved to the project owner until a formal `LICENSE` file is added).
