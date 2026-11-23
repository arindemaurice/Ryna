# RYNA System Architecture (v0.1.0)

## Core Principle

**Same RYNA. Same identity. Same memory. Same assistant. Online or offline.**

Every interface (local Web UI, native Windows Tkinter companion, and Flutter cross-platform app) talks to one local source of truth — the RYNA local engine bound strictly to `127.0.0.1`.

## Component Overview

```
+-------------------------------------------------------------------+
| Interfaces                                                        |
|  - Local Futuristic Web UI (assets/web) — runs immediately today  |
|  - Native Windows Companion (apps/windows_companion) — runs today |
|  - Flutter Cross-Platform App (apps/desktop) — Windows/Mac/Linux/ |
|    Android/iOS (requires Flutter SDK)                             |
+-------------------------------+-----------------------------------+
                                | Local Loopback HTTP JSON (127.0.0.1:8765)
+-------------------------------v-----------------------------------+
| Core Lifecycle Supervisor (core/rust — optional until Cargo built)|
|  - Single-instance lock, health check, supervised engine launch   |
+-------------------------------+-----------------------------------+
                                |
+-------------------------------v-----------------------------------+
| RYNA Local Engine (core/python)                                   |
|  - RynaEngine + Loopback ThreadingHTTPServer                      |
|  - SQLite WAL Database (conversations, memories, tasks, settings, |
|    notes, permission audit log)                                   |
|  - Hardware & Connectivity Monitor (RAM/CPU/Disk/Tier/Online)     |
+--------+--------------+----------------+---------------+----------+
         |              |                |               |
   +-----v-----+  +-----v------+   +-----v------+  +-----v------+
   | services/ |  | services/  |   | services/  |  | services/  |
   | ai/       |  | memory/    |   | agents/ &  |  | voice/     |
   | Ollama +  |  | 4-cat store|   | tools/     |  | State mach.|
   | Cloud     |  | Secret     |   | 3-tier     |  | Barge-in & |
   | Router    |  | Guard      |   | Perms +    |  | Backend    |
   |           |  |            |   | Recovery   |  | Discovery  |
   +-----------+  +------------+   +------------+  +------------+
```

## Offline-to-Online Transition Behavior

1. Connectivity is probed cleanly without blocking local actions.
2. When offline:
   - Local Ollama models, SQLite conversations, memory search/storage, local tools (`/calc`, `/diag`, `/find`, `/read`, `/note`), and offline tasks continue normally.
   - Cloud AI providers are skipped cleanly with an explicit status message.
   - Task steps marked `requires_internet: true` pause cleanly (`status = "paused"`) while preserving completed offline step outputs.
3. On unexpected crash/restart:
   - Any task left in `running` or `planning` transitions to `paused` with `outcome_status = "uncertain"` and requires explicit user approval before retrying uncertain steps.

## Cross-Platform Capability Matrix

| Capability | Windows (Primary) | macOS / Linux | Android / iOS |
|---|---|---|---|
| Local Engine + SQLite + AI Router | Implemented & Tested | Supported (Python 3.11+) | Planned (mobile bridge / on-device) |
| Local Futuristic Web UI | Implemented & Tested | Supported | Responsive via browser / Flutter |
| Native Desktop Companion | Implemented (Tkinter + Win32 Mutex) | Tkinter fallback | N/A (mobile companion widget planned) |
| Flutter Cross-Platform UI | Implemented (requires Flutter SDK) | Ready for Flutter build | Ready for Flutter build |
| Windows Login Startup (HKCU Run) | Implemented (`scripts/startup_windows.ps1`) | launchd / systemd planned | OS-restricted (foreground/background tasks) |
| Desktop GUI Automation / OCR | Planned (Phase 5) | Planned (Phase 7) | Restricted by OS sandboxes |
