# RYNA Security & Permission Model (v0.1.0)

## 1. Loopback Isolation

- The local HTTP REST API binds strictly to `127.0.0.1` by default (`RYNA_HOST=127.0.0.1`).
- CORS headers are permitted only for `http://127.0.0.1` and `http://localhost`.
- No remote telemetry is sent.

## 2. Three-Level Permission System

| Level | Policy | Examples |
|---|---|---|
| **Level 1 — Automatic** | Permitted without repeated confirmation (configurable) | Safe math (`/calc`), workspace file search/read, notes, system diagnostics |
| **Level 2 — Confirmation Required** | Blocked until user clicks **Approve & Execute** | Writing files, opening apps, sending emails/messages, submitting forms |
| **Level 3 — Restricted** | Blocked by default security safeguards | Permanent deletion, destructive shell commands, security/credential modifications |

## 3. Emergency Stop

- Triggering **Emergency Stop** immediately:
  1. Marks the permission engine halted so all actions are blocked.
  2. Pauses all `running` and `planning` tasks in SQLite.
  3. Interrupts voice/response state and sets the companion to `alert`.
  4. Writes an audit entry to `action_audit`.

## 4. Secret & Privacy Safeguards

- API keys are loaded exclusively from environment variables (`.env`), never stored in ordinary memory.
- `MemoryStore` scans content for common password, token, API key, and private-key patterns and rejects them with `SecretRejectedError`.
- `AIRouter` blocks sending prompts containing detected sensitive patterns to cloud providers unless `ai.send_sensitive_to_cloud` is explicitly enabled.
- `LocalToolRegistry` enforces path containment within `computer_control.allowed_root` so path traversal (`../../`) is denied.
