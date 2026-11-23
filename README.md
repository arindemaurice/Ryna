# RYNA — Pure Walking Windows Desktop AI Robot

**RYNA** lives directly on your Windows desktop — no website, no HTTP web server, no bulky chat window. Just RYNA herself walking across your desktop, listening through your microphone, speaking back aloud with instant barge-in interruption, and executing real local and online actions.

## Key Features
- **Pure Walking Desktop Robot (`app/desktop/robot_window.py`)**: Uses your isolated transparent RYNA asset (`app/desktop/assets/ryna_robot.png`) with directional left/right stride animation, subtle tilt & footstep bounce, glowing cyan speed trails, edge-turning patrol, drag/reposition, and a floating cyan speech bubble that fades cleanly when idle.
- **Fast Low-Latency Voice & AI**:
  - Auto-selects your fastest installed local model (`qwen2.5:1.5b` kept warm in RAM with `keep_alive: "30m"`) and streams replies **sentence-by-sentence directly into speech** so RYNA begins talking in ~1–2 seconds offline.
  - **Persistent Warm Windows SAPI Speaker** (`Microsoft Zira Desktop` — <20ms speech start, <15ms instant barge-in cancellation).
  - **100% Offline Speech Recognition** (`sounddevice` + `faster-whisper` `tiny.en` pre-warmed in background with `0.65s` silence turn detection).
  - **Ultra-Fast Online Alternatives (<0.5s)**: Set `RYNA_ALLOW_CLOUD=true` plus a **Groq** (`RYNA_OPENAI_BASE_URL=https://api.groq.com/openai/v1`, `RYNA_OPENAI_MODEL=llama-3.1-8b-instant`) or **Gemini** (`RYNA_GEMINI_API_KEY`) key in `.env`.
- **Real Local & Online Actions**: Speak naturally — `"Open Spotify"`, `"Open Chrome"`, `"What time is it?"`, `"Look at my screen"`, `"Search the web for ..."`, `"Remember that ..."`.

## Run RYNA
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
```
- **Click RYNA**: Speak to her (or interrupt her mid-sentence if she is talking).
- **Drag RYNA**: Move her anywhere on your desktop.
- **Right-click RYNA (or System Tray icon)**: Toggle Walking / Stay Put, Toggle Continuous Voice, Switch Local Model (`qwen2.5:1.5b` / `3b` / `4b`), Quick Type Command, Resize, Hide, or Exit.

## Run Tests
```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```
