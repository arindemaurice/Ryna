# RYNA Developer Setup Guide (Windows)

## 1. Check Your Environment

Run the environment checker from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1
```

- **Required today**: Python 3.11+ and Git.
- **Recommended for Offline AI**: Ollama (`ollama pull qwen2.5:3b` or `llama3.2:3b`).
- **Optional for compiling native Rust & Flutter targets**:
  - Rust toolchain (`rustup` + `cargo`) + Visual Studio C++ Build Tools
  - Flutter SDK + Visual Studio Desktop Development with C++ workload

## 2. Initialize Configuration & Database

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

This copies `.env.example` to `.env` (without overwriting an existing `.env`) and initializes `data/ryna.db`.

## 3. Run RYNA Locally

```powershell
# Local engine + open browser UI
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -OpenUI

# Local engine + native Windows animated companion + browser UI
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -OpenUI -Companion
```

## 4. Run Automated Tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

## 5. Building Rust & Flutter (Once Toolchains Are Installed)

```powershell
# Rust core engine
cd core\rust
cargo test
cargo build --release

# Flutter desktop app
cd apps\desktop
flutter pub get
flutter test
flutter run -d windows
```
