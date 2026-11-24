# RYNA — Clean Rebuild Architecture

## Directory Layout

```text
RYNA/
├── app/
│   ├── desktop/              # Native transparent Windows robot host & minimalist dark web UI
│   │   ├── robot_window.py   # Loads ONLY authoritative app/desktop/assets/ryna_robot.png
│   │   ├── single_instance.py# Win32 named Mutex single-instance lock
│   │   └── web/              # Clean dark cyan/blue conversation, memory, tasks, activity & settings UI
│   └── flutter_ui/           # Cross-platform Flutter UI foundation (ready for Flutter SDK)
├── core/
│   └── rust/                 # Rust core lifecycle supervisor crate (ready for Cargo)
├── ai/
│   ├── config.py             # Environment config, identity prompt & defaults
│   ├── local_models/         # CPU, RAM, disk, NVIDIA GPU/VRAM & connectivity detection
│   ├── providers/            # Ollama (streaming) + OpenAI-compatible (streaming) + Anthropic + Gemini
│   ├── routing/              # Automatic online/offline hybrid router & 5 operating modes
│   └── orchestration/        # Unified RynaCore, SSE EventBus, loopback HTTP server & CLI main
├── voice/                    # Windows SAPI offline TTS, instant barge-in, 13 robot states, backend probe
├── memory/                   # 9-category persistent memory with conflict resolution, dedup & secret guard
├── agents/                   # 8-state persistent task planner/executor, crash recovery & modular tools
├── computer/                 # Permission-gated Windows foreground inspection, allowlisted apps & vision boundary
├── integrations/             # Honest live integration registry (zero fake connected accounts)
├── database/                 # SQLite WAL store, versioned migrations, hot backup & restore
├── security/                 # 3-tier permissions, emergency stop, secret guard, untrusted-web isolation
├── scripts/                  # PowerShell setup/check/run/test/startup scripts + robot isolation helper
├── docs/                     # Permanent 37-category REQUIREMENTS.md + ARCHITECTURE.md
└── tests/                    # Automated unit, integration & live loopback verification
```
