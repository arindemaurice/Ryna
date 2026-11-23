"""Local loopback HTTP REST + SSE API & Web UI server for RYNA 0.2.0.

Binds strictly to 127.0.0.1 by default so no local services are exposed to the network.
"""

from __future__ import annotations

import json
import mimetypes
import queue
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.python.engine import RynaEngine
from services.memory.store import SecretRejectedError


class RynaHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], engine: RynaEngine, web_dir: Path) -> None:
        self.engine = engine
        self.web_dir = web_dir
        super().__init__(server_address, RynaRequestHandler)


class RynaRequestHandler(BaseHTTPRequestHandler):
    server: RynaHTTPServer  # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        self.server.engine.logger.debug("HTTP %s - " + format, self.address_string(), *args)

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin.startswith(("http://127.0.0.1", "http://localhost")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(raw)

    def _begin_sse(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._cors_headers()
        self.end_headers()

    def _write_sse(self, event: str, data: dict[str, Any]) -> bool:
        try:
            payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
            self.wfile.write(payload)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return False

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        if length > 4 * 1024 * 1024:
            raise ValueError("Request payload exceeds 4 MB limit.")
        raw = self.rfile.read(length).decode("utf-8")
        parsed = json.loads(raw) if raw.strip() else {}
        if not isinstance(parsed, dict):
            raise ValueError("JSON request body must be an object.")
        return parsed

    def _serve_static(self, rel_path: str) -> None:
        clean = rel_path.lstrip("/") or "index.html"
        target = (self.server.web_dir / clean).resolve()
        web_root = self.server.web_dir.resolve()
        if web_root != target and web_root not in target.parents:
            self._send_json({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or not target.is_file():
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(str(target))
        content = target.read_bytes()
        ctype = mime or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype = f"{ctype}; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        eng = self.server.engine

        try:
            if not path.startswith("/api/"):
                self._serve_static("index.html" if path == "/" else path)
                return

            if path == "/api/v1/health":
                self._send_json({"status": "ok", "app": "RYNA", "version": eng.status_summary()["version"]})
            elif path == "/api/v1/status":
                self._send_json(eng.status_summary())
            elif path == "/api/v1/events/stream":
                self._handle_event_stream()
            elif path == "/api/v1/settings":
                self._send_json({"settings": eng.db.get_all_settings()})
            elif path == "/api/v1/conversations":
                self._send_json({"conversations": eng.db.list_conversations()})
            elif path.startswith("/api/v1/conversations/") and path.endswith("/messages"):
                conv_id = path.split("/")[4]
                if not eng.db.get_conversation(conv_id):
                    self._send_json({"error": "Conversation not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"conversation_id": conv_id, "messages": eng.db.list_messages(conv_id)})
            elif path == "/api/v1/memories":
                cat = qs.get("category", [None])[0]
                q = qs.get("q", [None])[0]
                self._send_json({"memories": eng.memory.list(category=cat, query=q)})
            elif path == "/api/v1/memories/export":
                self._send_json(eng.memory.export_all())
            elif path == "/api/v1/tasks":
                st = qs.get("status", [None])[0]
                self._send_json({"tasks": eng.tasks.list_tasks(status=st)})
            elif path.startswith("/api/v1/tasks/"):
                task_id = path.split("/")[4]
                task = eng.tasks.get_task(task_id)
                if not task:
                    self._send_json({"error": "Task not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"task": task})
            elif path == "/api/v1/activity":
                cat = qs.get("category", [None])[0]
                self._send_json({"events": eng.db.list_activity(category=cat)})
            elif path == "/api/v1/notes":
                self._send_json({"notes": eng.db.list_notes()})
            elif path == "/api/v1/audit":
                self._send_json({"entries": eng.db.list_audit_log()})
            elif path == "/api/v1/tools":
                self._send_json({"tools": eng.tools.list_tools()})
            elif path == "/api/v1/voice/status":
                self._send_json(eng.voice.status())
            elif path == "/api/v1/ai/providers":
                self._send_json(eng.ai.provider_statuses())
            else:
                self._send_json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            eng.logger.exception("GET %s failed", path)
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_event_stream(self) -> None:
        eng = self.server.engine
        self._begin_sse()
        sub = eng.events.subscribe()
        try:
            if not self._write_sse("connected", {"companion_state": eng.voice.get_state()}):
                return
            while True:
                try:
                    msg = sub.get(timeout=15.0)
                    if not self._write_sse(msg["type"], msg["payload"]):
                        break
                except queue.Empty:
                    if not self._write_sse("ping", {"ok": True}):
                        break
        finally:
            eng.events.unsubscribe(sub)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        eng = self.server.engine

        try:
            body = self._read_json()

            if path == "/api/v1/conversations":
                conv = eng.db.create_conversation(title=str(body.get("title", "New Conversation")))
                eng.record_activity("system", "conversation_created", f"Created conversation '{conv['title']}'.", status="info")
                self._send_json({"conversation": conv}, HTTPStatus.CREATED)
            elif path == "/api/v1/chat":
                res = eng.send_chat(
                    conversation_id=body.get("conversation_id"),
                    user_text=str(body.get("message", "")),
                    requested_provider=body.get("provider"),
                    requested_model=body.get("model"),
                    attachments=body.get("attachments"),
                )
                self._send_json(res)
            elif path == "/api/v1/chat/stream":
                self._begin_sse()
                for evt in eng.stream_chat(
                    conversation_id=body.get("conversation_id"),
                    user_text=str(body.get("message", "")),
                    requested_provider=body.get("provider"),
                    requested_model=body.get("model"),
                    attachments=body.get("attachments"),
                    skip_user_insert=bool(body.get("skip_user_insert", False)),
                ):
                    if not self._write_sse(evt["event"], evt["data"]):
                        eng.voice.interrupt("Client disconnected from stream")
                        break
                self.close_connection = True
            elif path == "/api/v1/chat/retry":
                conv_id = str(body.get("conversation_id", "")).strip()
                if not conv_id:
                    raise ValueError("Missing conversation_id.")
                res = eng.retry_message(
                    conversation_id=conv_id,
                    target_message_id=body.get("message_id"),
                    requested_provider=body.get("provider"),
                    requested_model=body.get("model"),
                )
                self._send_json(res)
            elif path == "/api/v1/memories":
                mem = eng.memory.add(
                    category=str(body.get("category", "personal")),
                    content=str(body.get("content", "")),
                    memory_type=str(body.get("memory_type", "confirmed_fact")),
                    confidence=float(body.get("confidence", 0.85)),
                    source=str(body.get("source", "user")),
                    tags=list(body.get("tags", [])),
                    expires_in_days=body.get("expires_in_days"),
                )
                eng.record_activity("memory", "memory_saved", f"Saved {mem['category']} memory.", status="ok")
                self._send_json({"memory": mem}, HTTPStatus.CREATED)
            elif path == "/api/v1/tasks":
                task = eng.tasks.create_task(
                    title=str(body.get("title", "")),
                    goal=str(body.get("goal", "")),
                    steps=body.get("steps"),
                    requires_internet=bool(body.get("requires_internet", False)),
                    permission_level=int(body.get("permission_level", 1)),
                    conversation_id=body.get("conversation_id"),
                )
                eng.record_activity("task", "task_created", f"Created task '{task['title']}'.", status="info")
                if body.get("auto_run", False):
                    eng.set_companion_state("executing")
                    task = eng.tasks.run_task(task["id"], approved=bool(body.get("approved", False)))
                    eng.set_companion_state("waiting" if task["status"] == "waiting_for_approval" else "idle")
                    eng.record_activity("task", f"task_{task['status']}", f"Task '{task['title']}' -> {task['status']}.", status="ok" if task["status"] == "completed" else "warn")
                eng.events.publish("task_updated", task)
                self._send_json({"task": task}, HTTPStatus.CREATED)
            elif path.startswith("/api/v1/tasks/") and path.endswith("/run"):
                task_id = path.split("/")[4]
                eng.set_companion_state("executing")
                task = eng.tasks.run_task(task_id, approved=bool(body.get("approved", False)))
                eng.set_companion_state("waiting" if task["status"] == "waiting_for_approval" else "idle")
                eng.record_activity("task", f"task_{task['status']}", f"Task '{task['title']}' -> {task['status']}.", status="ok" if task["status"] == "completed" else "warn")
                eng.events.publish("task_updated", task)
                self._send_json({"task": task})
            elif path.startswith("/api/v1/tasks/") and path.endswith("/retry"):
                task_id = path.split("/")[4]
                eng.set_companion_state("executing")
                task = eng.tasks.retry_task(task_id, approved=bool(body.get("approved", False)))
                eng.set_companion_state("waiting" if task["status"] == "waiting_for_approval" else "idle")
                eng.record_activity("task", "task_retried", f"Retried task '{task['title']}' -> {task['status']}.", status="info")
                eng.events.publish("task_updated", task)
                self._send_json({"task": task})
            elif path.startswith("/api/v1/tasks/") and path.endswith("/pause"):
                task_id = path.split("/")[4]
                task = eng.tasks.pause_task(task_id)
                eng.record_activity("task", "task_paused", f"Paused task '{task['title']}'.", status="warn")
                eng.events.publish("task_updated", task)
                self._send_json({"task": task})
            elif path.startswith("/api/v1/tasks/") and path.endswith("/cancel"):
                task_id = path.split("/")[4]
                task = eng.tasks.cancel_task(task_id)
                eng.record_activity("task", "task_cancelled", f"Cancelled task '{task['title']}'.", status="warn")
                eng.events.publish("task_updated", task)
                self._send_json({"task": task})
            elif path == "/api/v1/notes":
                note = eng.db.create_note(
                    title=str(body.get("title", "Note")),
                    content=str(body.get("content", "")),
                    remind_at=body.get("remind_at"),
                )
                eng.record_activity("system", "note_created", f"Saved note '{note['title']}'.", status="ok")
                self._send_json({"note": note}, HTTPStatus.CREATED)
            elif path == "/api/v1/voice/state":
                new_state = eng.set_companion_state(str(body.get("state", "idle")))
                self._send_json({"state": new_state})
            elif path == "/api/v1/voice/speak":
                res = eng.voice.speak_native(
                    text=str(body.get("text", "")),
                    voice_name=body.get("voice"),
                    speech_speed=body.get("speed"),
                    on_finished=lambda: eng.events.publish("companion_state", {"state": eng.voice.get_state()}),
                )
                eng.events.publish("companion_state", {"state": "speaking"})
                eng.record_activity("voice", "voice_speak", f"Speaking aloud via Windows SAPI ({res['voice']}).", status="ok")
                self._send_json(res)
            elif path == "/api/v1/voice/event":
                ev_type = str(body.get("event_type", "voice_event"))
                summary = str(body.get("summary", "Voice interaction event"))
                status = str(body.get("status", "ok"))
                evt = eng.record_activity("voice", ev_type, summary, status=status, details=body.get("details") or {})
                if body.get("state"):
                    eng.set_companion_state(str(body["state"]))
                self._send_json({"event": evt})
            elif path == "/api/v1/voice/interrupt":
                res = eng.voice.interrupt(reason=str(body.get("reason", "User pressed interrupt")))
                eng.events.publish("companion_state", {"state": res["new_state"]})
                eng.record_activity("voice", "voice_interrupt", f"Interrupted ({res['previous_state']} -> {res['new_state']}).", status="warn")
                self._send_json(res)
            elif path == "/api/v1/emergency-stop":
                self._send_json(eng.emergency_stop(reason=str(body.get("reason", "User triggered emergency stop"))))
            elif path == "/api/v1/emergency-stop/reset":
                self._send_json(eng.reset_emergency_stop())
            else:
                self._send_json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except SecretRejectedError as exc:
            eng.record_activity("memory", "secret_rejected", str(exc), status="warn")
            self._send_json({"error": str(exc), "code": "SECRET_REJECTED"}, HTTPStatus.BAD_REQUEST)
        except (ValueError, KeyError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except Exception as exc:
            eng.logger.exception("POST %s failed", path)
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:
        self._handle_update()

    def do_PATCH(self) -> None:
        self._handle_update()

    def _handle_update(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        eng = self.server.engine
        try:
            body = self._read_json()
            if path == "/api/v1/settings":
                updates = body.get("settings", body)
                if not isinstance(updates, dict):
                    raise ValueError("Settings update must be a JSON object.")
                saved = eng.db.update_settings(updates)
                eng.record_activity("system", "settings_updated", f"Updated {len(updates)} setting(s).", status="ok", details={"keys": sorted(updates.keys())})
                eng.events.publish("settings_updated", {"keys": sorted(updates.keys())})
                self._send_json({"settings": saved})
            elif path.startswith("/api/v1/memories/"):
                mem_id = path.split("/")[4]
                updated = eng.memory.update(
                    memory_id=mem_id,
                    content=body.get("content"),
                    category=body.get("category"),
                    memory_type=body.get("memory_type"),
                    confidence=body.get("confidence"),
                    tags=body.get("tags"),
                )
                if not updated:
                    self._send_json({"error": "Memory not found"}, HTTPStatus.NOT_FOUND)
                    return
                eng.record_activity("memory", "memory_updated", f"Updated memory {mem_id}.", status="ok")
                self._send_json({"memory": updated})
            else:
                self._send_json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except SecretRejectedError as exc:
            self._send_json({"error": str(exc), "code": "SECRET_REJECTED"}, HTTPStatus.BAD_REQUEST)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            eng.logger.exception("UPDATE %s failed", path)
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        eng = self.server.engine
        try:
            if path.startswith("/api/v1/conversations/"):
                conv_id = path.split("/")[4]
                deleted = eng.db.delete_conversation(conv_id)
                self._send_json({"deleted": deleted})
            elif path == "/api/v1/memories":
                cat = qs.get("category", [None])[0]
                count = eng.memory.delete_category(cat) if cat else eng.memory.clear_all()
                eng.record_activity("memory", "memory_cleared", f"Deleted {count} memory item(s).", status="warn")
                self._send_json({"deleted_count": count})
            elif path.startswith("/api/v1/memories/"):
                mem_id = path.split("/")[4]
                deleted = eng.memory.delete(mem_id)
                self._send_json({"deleted": deleted})
            elif path == "/api/v1/activity":
                count = eng.db.clear_activity()
                self._send_json({"deleted_count": count})
            else:
                self._send_json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            eng.logger.exception("DELETE %s failed", path)
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def create_server(engine: RynaEngine, host: str | None = None, port: int | None = None) -> RynaHTTPServer:
    bind_host = host or engine.config.host
    bind_port = engine.config.port if port is None else port
    web_dir = engine.config.project_root / "assets" / "web"
    return RynaHTTPServer((bind_host, bind_port), engine, web_dir)
