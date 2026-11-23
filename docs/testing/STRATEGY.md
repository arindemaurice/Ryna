# RYNA Testing Strategy (v0.1.0)

## Test Suites

All Python tests use standard `unittest` (and are compatible with `pytest`) with isolated temporary SQLite databases so running tests never mutates live user data.

1. `tests/test_database.py` — Schema initialization, WAL persistence, settings CRUD, conversations, auto-titling, deletion cascade, notes, audit log.
2. `tests/test_hardware.py` — Real OS/CPU/RAM/disk detection and hardware-aware model tier selection across low/medium/high/workstation profiles.
3. `tests/test_memory.py` — Categories, epistemic classifications, inference confidence cap, deduplication, expiration purge, search, relevance retrieval, export, and secret rejection.
4. `tests/test_permissions_and_tasks.py` — Level 1/2/3 enforcement, approval flow, emergency stop halting tasks, crash recovery (`running` → `paused`/`uncertain`), offline queueing and online resume.
5. `tests/test_ai_router.py` — Real HTTP communication against a local mock Ollama & OpenAI-compatible HTTP server, fallback when local fails, honest failure when no provider is available (zero fake responses), cloud privacy gate on sensitive text, and offline mode switching.
6. `tests/test_tools_and_voice.py` — Safe AST calculator (blocks code injection), sandboxed file search/read (blocks path traversal), voice mode status, backend detection, and barge-in interruption transitions.
7. `tests/test_server_integration.py` — Live loopback `RynaHTTPServer` startup, REST API round-trips (health, status, settings persistence, chat, local tools, memory CRUD, task approval, emergency stop), web UI serving, and graceful shutdown.

## Acceptance Verification

A feature is marked complete only when it is implemented, runs, passes automated verification, handles errors gracefully, and is accurately reflected in `README.md`.
