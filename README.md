# RYNA — Pure Voice-Controlled Walking Windows Desktop AI Robot

**RYNA** lives directly on your Windows desktop — no website, no HTTP server, no dropdown menus, and no bulky chat window. Just RYNA herself walking, sitting, standing, thinking, and sleeping on your desktop, configured purely by your voice.

## Key Features
- **Interchanging Walking Legs & Expressive Poses (`app/desktop/robot_window.py`)**:
  - Built directly from your isolated RYNA asset (`app/desktop/assets/ryna_robot.png`) via `scripts/build_ryna_poses.py`:
    - **Walking**: 4-phase alternating stride (`ryna_walk_a.png` → `ryna_walk_mid.png` → `ryna_walk_b.png` → `ryna_walk_mid.png`) facing right & left so her legs visibly interchange step-by-step.
    - **Thinking**: Stops walking, stands upright, looks **straight forward**, and raises her hand to touch her head (`ryna_think.png`).
    - **Sitting & Sleeping**: Relaxed seated pose (`ryna_sit.png`) and peaceful sleeping pose (`ryna_sleep.png`) with floating animated cyan `z Z`.
- **Autonomous Time-of-Day Routine (Sitting-First)**:
  - **Night (`22:00–05:59`)**: Sleeps peacefully on your desktop (80%) with quiet sitting (20%); wakes immediately when clicked.
  - **Morning (`06:00–11:59`)**: Mostly sitting comfortably (62%), with occasional short walks (26%) and standing pauses (12%).
  - **Afternoon (`12:00–17:59`)**: Mostly sitting (68%), with occasional walks (20%) and standing (12%).
  - **Evening (`18:00–21:59`)**: Mostly relaxed sitting (70%), brief naps (15%), and occasional strolls (10%).
- **Click-to-Talk + Live Dictation Box Above RYNA**:
  - Click RYNA → she stops/wakes, calmly says *"I'm here. What would you like me to help you with?"*, and automatically opens your microphone.
  - A dedicated **YOU SPOKE** box directly above RYNA shows a live audio level meter and your dictated words as you speak, keeping them visible while she thinks and responds plainly in a calm voice.
  - Click RYNA while she is speaking (or press `Esc`) for instant `<1ms` barge-in interruption.
- **Full Windows System Management (`computer/controller.py` & `computer/system_manager.py`)**:
  - **Fast Physical Search-Bar App Launch**: Say `"open word"`, `"open spotify"`, `"open chrome"`, or any installed Windows app → RYNA briskly runs down to your Windows Search bar, triggers Windows Search (`Win+S`), types the app name via native Win32 `SendInput`, and presses `Enter` to open it in under a second (with automatic StartApps / URI / web fallbacks).
  - **Safe User-Profile File Management**: List/find/open/read/copy/move files inside `Desktop`, `Documents`, `Downloads`, `Pictures`, and `RYNA-Workspace`, open project folders, and safely send files to the Recycle Bin (blocks executable writes and path escapes).
  - **Offline Windows Screen OCR & Active-App Typing**: Say `"read my screen"` (built-in Windows WinRT OCR, 100% offline) or `"type ..."` to dictate directly into your active app.
  - **Live System Health, Power & Reminders**: Say `"system info"`, `"system health"`, `"diagnose errors"`, `"battery status"`, `"volume up/down/mute"`, `"brightness up/down"`, `"show desktop"`, `"lock screen"`, `"open wifi settings"`, `"set a 5 minute timer"`, `"remind me to ... in 10 minutes"`, `"weather in ..."`, or two-step confirmed `"shut down computer"` / `"restart computer"` / `"sign out"` + `"cancel shutdown"`.
- **Right-Click Monitor & Settings Panel (`app/desktop/settings_panel.py`)**:
  - **Left-click RYNA**: Greet, listen, display your dictation above her, think touching her head, and reply calmly (or interrupt mid-speech).
  - **Right-click RYNA**: Opens the live **RYNA Monitor & Settings** window showing real-time state, time-of-day routine, live CPU/RAM/Disk health telemetry, AI model, voice/mic status, battery, recent activity log, one-click system buttons (`Health Check`, `Desktop`, `Mute`, `Lock PC`, `Cancel Shutdown`), and live robot controls.
- **Voice-Controlled Settings & Poses**:
  - Poses: `"sit down"`, `"stand up"`, `"go to sleep"`, `"wake up"`, `"walk around"`, `"stop walking"`, `"resume schedule"`.
  - Voice & Size: `"speak faster"`, `"speak slower"`, `"normal speed"`, `"be bigger"`, `"be smaller"`, `"keep listening"`, `"push to talk"`.
  - AI & System: `"use fast model"`, `"use smarter model"`, `"use cloud"`, `"stay offline"`, `"hide yourself"`, `"goodbye ryna"`.
- **Fast Low-Latency Voice & Offline/Online AI**:
  - Persistent warm Windows SAPI speaker (`Microsoft Zira Desktop`) with compiled `.NET` `SpeakCompleted` callback (`0.1 ms` barge-in).
  - 100% offline `faster-whisper` (`tiny.en`) + adaptive noise-floor microphone capture.
  - Warm local Ollama (`qwen2.5:1.5b`, `keep_alive: "30m"`) with sentence-by-sentence speech streaming, plus optional cloud providers (Groq / OpenAI / Gemini).

## Run RYNA (Always-On, Detached — Survives Terminal Close & Offline)
RYNA runs completely standalone via windowless `pythonw.exe` with zero HTTP server or cloud server dependency. Closing your terminal will **never** close RYNA:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
```
Or simply **double-click `Start-RYNA.vbs`** (no terminal window at all).

### Start RYNA Automatically With Windows (+ Desktop Shortcut)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1 -Shortcut
```
*(You can also toggle **Start with Windows** anytime by right-clicking RYNA → **Monitor & Settings**, or by saying `"start with windows"`.)*
To remove auto-start later:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1 -Remove
```
- **Left-Click RYNA**: Greet, listen, display your dictation above her, think, and reply calmly (or interrupt mid-speech).
- **Right-Click RYNA**: Open the live **Monitor & Settings** panel.
- **Drag RYNA**: Move her anywhere on your desktop.
- **System Tray Icon**: `Show RYNA`, `Monitor & Settings`, `Exit RYNA`.

## Run Tests
```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```
