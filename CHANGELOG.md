# Changelog

## [0.1.0] - 2026-11-23 (Clean-Slate Architecture Rebuild)

### Changed
- Performed a complete clean working-tree reset while preserving `.git` history and `.env`.
- Rebuilt RYNA from scratch under the modular hybrid architecture (`app/`, `core/`, `ai/`, `voice/`, `memory/`, `agents/`, `computer/`, `integrations/`, `database/`, `security/`, `tests/`, `docs/`, `scripts/`).

### Added
- Permanent 37-category requirements tracker in `docs/REQUIREMENTS.md`.
- SQLite WAL database with versioned migrations, hot backup (`sqlite3.backup`), and restore.
- 9-category persistent memory with subject-key conflict resolution, deduplication, expiration, export/import, and automatic secret rejection.
- Provider-independent AI layer (local streaming Ollama + cloud OpenAI-compatible/Anthropic/Gemini) with hardware/GPU/VRAM detection and 5-state automatic online/offline operating mode classification.
- Full voice pipeline: real microphone RMS meter, SpeechRecognition STT, `"Hey Ryna"` wake word, dual Browser + offline Windows SAPI TTS, instant barge-in interruption, and all 13 canonical robot states.
- 8-state persistent autonomous task engine (`planned`, `running`, `waiting`, `completed`, `failed`, `paused`, `cancelled`, `recovering`) with startup crash recovery, sandboxed file tools, AST calculator, safe webpage fetch with prompt-injection isolation, and permission-gated Windows computer control.
- Authoritative RYNA desktop robot host (`app/desktop/robot_window.py`) loading only `app/desktop/assets/ryna_robot.png` (zero fabricated sphere or replacement robot) with persisted screen coordinates and click-to-interact session sync.
