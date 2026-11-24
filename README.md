# RYNA — Pure Voice-Controlled Walking Windows Desktop AI Robot

**RYNA** lives directly on your Windows desktop — no website, no HTTP server, no dropdown menus, and no bulky chat window. Just RYNA herself walking, sitting, standing, thinking, and sleeping on your desktop, configured purely by your voice.

## Key Features
- **Interchanging Walking Legs & Expressive Poses (`app/desktop/robot_window.py`)**:
  - Built directly from your isolated RYNA asset (`app/desktop/assets/ryna_robot.png`) via `scripts/build_ryna_poses.py`:
    - **Walking**: 4-phase alternating stride (`ryna_walk_a.png` → `ryna_walk_mid.png` → `ryna_walk_b.png` → `ryna_walk_mid.png`) facing right & left so her legs visibly interchange step-by-step.
    - **Thinking**: Stops walking, stands upright, looks **straight forward**, and raises her hand to touch her head (`ryna_think.png`).
    - **Sitting & Sleeping**: Relaxed seated pose (`ryna_sit.png`) and peaceful sleeping pose (`ryna_sleep.png`) with floating animated cyan `z Z`.
- **Autonomous Time-of-Day Routine**:
  - **Night (`22:00–05:59`)**: Sleeps peacefully on your desktop until you click her.
  - **Morning (`06:00–11:59`)**: Energetic walking patrols with brief standing pauses.
  - **Afternoon (`12:00–17:59`)**: Balanced mix of walking, sitting, and standing.
  - **Evening (`18:00–21:59`)**: Relaxed sitting with gentle strolls and brief naps.
- **Click-to-Talk + Live Dictation Box Above RYNA**:
  - Click RYNA → she stops/wakes, calmly says *"I'm here. What would you like me to help you with?"*, and automatically opens your microphone.
  - A dedicated **YOU SPOKE** box directly above RYNA shows a live audio level meter and your dictated words as you speak, keeping them visible while she thinks and responds plainly in a calm voice.
  - Click RYNA while she is speaking (or press `Esc`) for instant `<1ms` barge-in interruption.
- **Fast Physical Search-Bar App Launch & Windows Controls (`computer/controller.py`)**:
  - Say `"open word"`, `"open spotify"`, `"open chrome"`, or any installed Windows app → RYNA briskly walks down to your Windows Search bar, triggers Windows Search (`Win+S`), types the app name via native Win32 `SendInput`, and presses `Enter` to open it in under a second (with automatic StartApps / URI / web fallbacks).
  - Also supports polite app closing (`"close notepad"`), folders (`"open downloads"`, `"open this pc"`, `"open recycle bin"`), volume (`"volume up/down/mute"`, `"set volume to 50"`), brightness (`"brightness up/down"`), battery status, Windows Settings pages (`"open wifi settings"`), shortcuts (`"show desktop"`, `"switch window"`, `"lock screen"`), clipboard, and YouTube / Spotify / Maps searches.
- **Right-Click Monitor & Settings Panel (`app/desktop/settings_panel.py`)**:
  - **Left-click RYNA**: Greet, listen, display your dictation above her, think touching her head, and reply calmly (or interrupt mid-speech).
  - **Right-click RYNA**: Opens the live **RYNA Monitor & Settings** window showing real-time state, time-of-day routine, AI model, voice/mic status, battery, recent activity log, and live controls for poses, walking, physical Search-bar launching, size, speech speed, voice, and local model.
- **Voice-Controlled Settings & Poses**:
  - Poses: `"sit down"`, `"stand up"`, `"go to sleep"`, `"wake up"`, `"walk around"`, `"stop walking"`, `"resume schedule"`.
  - Voice & Size: `"speak faster"`, `"speak slower"`, `"normal speed"`, `"be bigger"`, `"be smaller"`, `"keep listening"`, `"push to talk"`.
  - AI & System: `"use fast model"`, `"use smarter model"`, `"use cloud"`, `"stay offline"`, `"hide yourself"`, `"goodbye ryna"`.
- **Fast Low-Latency Voice & Offline/Online AI**:
  - Persistent warm Windows SAPI speaker (`Microsoft Zira Desktop`) with compiled `.NET` `SpeakCompleted` callback (`0.1 ms` barge-in).
  - 100% offline `faster-whisper` (`tiny.en`) + adaptive noise-floor microphone capture.
  - Warm local Ollama (`qwen2.5:1.5b`, `keep_alive: "30m"`) with sentence-by-sentence speech streaming, plus optional cloud providers (Groq / OpenAI / Gemini).

## Run RYNA
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
```
- **Left-Click RYNA**: Greet, listen, display your dictation above her, think, and reply calmly (or interrupt mid-speech).
- **Right-Click RYNA**: Open the live **Monitor & Settings** panel.
- **Drag RYNA**: Move her anywhere on your desktop.
- **System Tray Icon**: `Show RYNA`, `Monitor & Settings`, `Exit RYNA`.

## Run Tests
```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```
