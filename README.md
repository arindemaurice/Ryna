# RYNA — Autonomous Personal AI Assistant

**Same RYNA. Same identity. Same memory. Same context. Online or offline.**

RYNA is a unified personal AI assistant, desktop robot companion, voice assistant, and autonomous agent built on a clean, modular, offline-first hybrid architecture (Python AI/Agent core + SQLite + Windows Desktop Robot Host + Flutter UI & Rust Core foundations).

---

## Authoritative Robot Asset Status
Per Section 4 (*The Robot is RYNA*), RYNA **never** fabricates a replacement sphere or generic mascot. Place the isolated transparent PNG of your supplied RYNA robot at:

```text
app/desktop/assets/ryna_robot.png
```

(Or install/crop it from a screenshot using `python scripts/isolate_robot.py <path_to_image> [--crop x1,y1,x2,y2]`.)

---

## Key Capabilities Implemented & Verified

- **Complete Research Preservation**: All 37 specification domains are tracked honestly in [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).
- **Hybrid Online/Offline AI**: Local Ollama token-by-token streaming (`qwen3.5:4b`, `qwen2.5:3b`, `qwen2.5:1.5b` supported immediately) + Cloud OpenAI-compatible, Anthropic, and Gemini APIs with `.env`-only credentials and privacy filtering.
- **Voice & Instant Barge-In**: Live microphone waveform metering, browser `SpeechRecognition`, configurable `"Hey Ryna"` wake word, dual TTS (Browser `SpeechSynthesis` + **100% offline Windows SAPI**), and instant speech/generation interruption.
- **9-Category Persistent Memory**: Deduplication, subject-key conflict resolution, confidence/source/time tracking, expiration, export/import, and automatic secret rejection.
- **8-State Persistent Task Engine & Crash Recovery**: `planned`, `running`, `waiting`, `completed`, `failed`, `paused`, `cancelled`, `recovering` — pauses internet steps offline and safely recovers interrupted tasks on restart.
- **3-Tier Security & Computer Control**: Automatic / Confirmation Required / Restricted policy, Emergency Stop, loopback-only `127.0.0.1` binding, untrusted webpage prompt-injection isolation, Win32 foreground window inspection, and opt-in allowlisted app launching.
- **Hot SQLite Backup & Restore**: Built-in consistent `sqlite3.backup` endpoint (`POST /api/v1/database/backup`).

---

## Quick Start (Windows)

```powershell
# 1. Check prerequisites & robot asset status
powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1

# 2. Initialize .env and SQLite database
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# 3. Launch RYNA conversation UI
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -OpenUI

# 4. Launch RYNA with native transparent Windows Desktop Robot host
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -OpenUI -Robot

# 5. Run automated tests
powershell -ExecutionPolicy Bypass -File scripts\test.ps1

# 6. Optional: Manage Windows user-login startup
powershell -ExecutionPolicy Bypass -File scripts\startup_windows.ps1 -Action status
```

## Architecture & Limitations
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) for the exact implementation status, dependencies, and honest limitations of every subsystem.
