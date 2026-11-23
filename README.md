# RYNA — Native Windows Desktop Robot & Autonomous Personal AI

**Same RYNA. Same identity. Same memory. Same context. Online or offline.**

RYNA lives directly on your Windows desktop as the isolated futuristic mini humanoid robot from your authoritative design (`app/desktop/assets/ryna_robot.png`) — with a floating cyan speech bubble, a translucent dark-navy glass HUD panel, a Windows System Tray icon, real offline & online voice conversation with instant barge-in interruption, local Ollama + Cloud AI routing, persistent 9-category memory, 8-state crash-recoverable tasks, live web search, screen capture, and permission-gated Windows app control.

---

## What Runs Natively on Windows (No Browser Website Required)

- **Authoritative Isolated Robot Asset**: `app/desktop/assets/ryna_robot.png` (385×512 transparent RGBA), `ryna_head.png`, and `ryna_icon.ico`.
- **Desktop Presence (`app/desktop/robot_window.py`)**:
  - Transparent borderless floating RYNA robot with smooth hover & 13-state energy halo, drag/reposition with persisted coordinates, edge-awareness, resize, always-on-top, and `pystray` Windows System Tray integration.
  - Floating cyan-bordered speech bubble directly beside RYNA showing live listening/speaking/status updates.
  - Floating dark-navy glass HUD panel (click RYNA to toggle) with live streaming AI chat, Action Cards, quick action pills (`Open Browser`, `Search Web`, `Check Time`, `Screen`, `More`), native **Mic (`🎤`)**, **Interrupt (`■`)**, and native **Control Center** (Memory, Tasks, Settings, DB Backup).
- **100% Offline + Online Native Voice (`voice/native_voice.py` + `voice/manager.py`)**:
  - Real Windows microphone capture (`sounddevice` + `numpy` RMS VAD + automatic silence turn-detection).
  - Real **100% offline speech recognition** (`faster-whisper` `tiny.en` pre-cached locally).
  - Real **100% offline sentence-streaming speech synthesis** via Windows SAPI (`Microsoft Zira Desktop` / `Microsoft David Desktop`).
  - Instant barge-in interruption (clicking Mic while she speaks, clicking Interrupt `■`, clicking the speech bubble, or pressing `Esc` immediately stops speech and listens for your new input).
- **Hybrid AI Brain (`ai/`)**:
  - Real local Ollama token-by-token streaming (`qwen3.5:4b`, `qwen2.5:3b`, `qwen2.5:1.5b`) + Cloud OpenAI-compatible, Anthropic, and Gemini APIs with `.env`-only credentials and privacy filtering.
  - Automatic 5-state operating mode detection (`Online · Cloud AI`, `Online · Local AI`, `Offline · Local AI`, `Offline · Limited`, `AI Unavailable`).
- **Real Computer Control, Vision & Online Search**:
  - Natural spoken or typed actions: `"Open Spotify"`, `"Open Chrome"`, `"Open VS Code"`, `"What time is it?"`, `"Look at my screen"` (`mss` capture + active window title), `"Search the web for ..."` (live DuckDuckGo search with untrusted-content isolation), `"Remember that ..."`.

---

## How to Run RYNA

```powershell
# Launch RYNA directly onto your Windows desktop (robot + speech bubble + glass HUD + system tray)
powershell -ExecutionPolicy Bypass -File scripts\run.ps1

# Run all automated tests (Python + Rust + Flutter when available)
powershell -ExecutionPolicy Bypass -File scripts\test.ps1

# Optional: Enable / disable Windows login auto-start
powershell -ExecutionPolicy Bypass -File scripts\startup_windows.ps1 -Action status
powershell -ExecutionPolicy Bypass -File scripts\startup_windows.ps1 -Action enable

# Optional: Run headless engine only or open secondary browser UI
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Headless
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -WebUI
```

See [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) for the complete 37-domain requirements & verification tracker, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system structure.
