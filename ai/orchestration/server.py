"""Loopback-only (127.0.0.1) HTTP REST & SSE API server for RYNA."""

from __future__ import annotations

import json
import mimetypes
import queue
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ai.orchestration.core import RynaCore
from security.privacy import SecretRejectedError


class RynaHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], core: RynaCore, web_dir: Path) -> None:
        self.core = core
        self.web_dir = web_dir
        super().__init__(addr, RynaHandler)


class RynaHandler(BaseHTTPRequestHandler):
    server: RynaHTTPServer  # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        self.server.core.logger.debug("HTTP %s - " + fmt, self.address_string(), *args)

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin.startswith(("http://127.0.0.1", "http://localhost")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def _begin_sse(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()

    def _sse(self, event: str, data: dict[str, Any]) -> bool:
        try:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            return True
        except OSError:
            return False

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        if length > 4 * 1024 * 1024:
            raise ValueError("Payload exceeds 4 MB limit.")
        parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object.")
        return parsed

    def _serve_file(self, path: Path) -> None:
        if not path.is_file():
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(str(path))
        ctype = mime or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        core = self.server.core
        try:
            if not path.startswith("/api/"):
                rel = "index.html" if path == "/" else path.lstrip("/")
                target = (self.server.web_dir / rel).resolve()
                root = self.server.web_dir.resolve()
                if root != target and root not in target.parents:
                    self._json({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
                    return
                self._serve_file(target)
                return

            if path == "/api/v1/health":
                self._json({"status": "ok", "app": "RYNA", "version": core.status_summary()["version"]})
            elif path == "/api/v1/status":
                self._json(core.status_summary())
            elif path == "/api/v1/robot/asset":
                if core.config.robot_asset_path.is_file():
                    self._serve_file(core.config.robot_asset_path)
                else:
                    self._json(core.robot_asset_status(), HTTPStatus.NOT_FOUND)
            elif path == "/api/v1/events/stream":
                self._begin_sse()
                sub = core.events.subscribe()
                try:
                    if not self._sse("connected", {"robot_state": core.voice.get_state()}):
                        return
                    while True:
                        try:
                            msg = sub.get(timeout=15.0)
                            if not self._sse(msg["type"], msg["payload"]):
                                break
                        except queue.Empty:
                            if not self._sse("ping", {"ok": True}):
                                break
                finally:
                    core.events.unsubscribe(sub)
            elif path == "/api/v1/settings":
                self._json({"settings": core.db.get_all_settings()})
            elif path == "/api/v1/conversations":
                self._json({"conversations": core.db.list_conversations()})
            elif path.startswith("/api/v1/conversations/") and path.endswith("/messages"):
                cid = path.split("/")[4]
                if not core.db.get_conversation(cid):
                    self._json({"error": "Conversation not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json({"conversation_id": cid, "messages": core.db.list_messages(cid)})
            elif path == "/api/v1/memories":
                self._json({"memories": core.memory.list(qs.get("category", [None])[0], qs.get("q", [None])[0])})
            elif path == "/api/v1/memories/export":
                self._json(core.memory.export_all())
            elif path == "/api/v1/tasks":
                self._json({"tasks": core.tasks.list_tasks(qs.get("status", [None])[0])})
            elif path == "/api/v1/activity":
                self._json({"events": core.db.list_activity(qs.get("category", [None])[0])})
            elif path == "/api/v1/audit":
                self._json({"entries": core.db.list_audit_log()})
            elif path == "/api/v1/tools":
                self._json({"tools": core.tools.list_tools()})
            elif path == "/api/v1/voice/status":
                self._json(core.voice.status())
            else:
                self._json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            core.logger.exception("GET %s failed", path)
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        core = self.server.core
        try:
            body = self._body()
            if path == "/api/v1/conversations":
                self._json({"conversation": core.db.create_conversation(str(body.get("title", "New Conversation")))}, HTTPStatus.CREATED)
            elif path == "/api/v1/chat":
                self._json(core.send_chat(body.get("conversation_id"), str(body.get("message", "")), body.get("provider"), body.get("model"), body.get("attachments")))
            elif path == "/api/v1/chat/stream":
                self._begin_sse()
                for ev in core.stream_chat(body.get("conversation_id"), str(body.get("message", "")), body.get("provider"), body.get("model"), body.get("attachments"), bool(body.get("skip_user_insert", False))):
                    if not self._sse(ev["event"], ev["data"]):
                        core.voice.interrupt("Client closed stream")
                        break
                self.close_connection = True
            elif path == "/api/v1/chat/retry":
                self._json(core.retry_message(str(body.get("conversation_id", "")), body.get("message_id")))
            elif path == "/api/v1/memories":
                mem = core.memory.add(
                    str(body.get("category", "personal")),
                    str(body.get("content", "")),
                    str(body.get("memory_type", "confirmed_fact")),
                    float(body.get("confidence", 0.85)),
                    str(body.get("source", "user")),
                    list(body.get("tags", [])),
                    str(body.get("subject_key", "")),
                    body.get("expires_in_days"),
                )
                core.record_activity("memory", "memory_saved", f"Saved {mem['category']} memory.", "ok")
                self._json({"memory": mem}, HTTPStatus.CREATED)
            elif path == "/api/v1/tasks":
                t = core.tasks.create_task(str(body.get("title", "")), str(body.get("goal", "")), body.get("steps"), bool(body.get("requires_internet", False)), int(body.get("permission_level", 1)), body.get("conversation_id"))
                core.record_activity("task", "task_created", f"Planned task '{t['title']}'.", "info")
                if body.get("auto_run", False):
                    core.set_robot_state("working")
                    t = core.tasks.run_task(t["id"], approved=bool(body.get("approved", False)))
                    core.set_robot_state("waiting" if t["status"] == "waiting" else "idle")
                    core.record_activity("task", f"task_{t['status']}", f"Task '{t['title']}' -> {t['status']}.", "ok" if t["status"] == "completed" else "warn")
                core.events.publish("task_updated", t)
                self._json({"task": t}, HTTPStatus.CREATED)
            elif path.startswith("/api/v1/tasks/") and path.endswith("/run"):
                tid = path.split("/")[4]
                core.set_robot_state("working")
                t = core.tasks.run_task(tid, approved=bool(body.get("approved", False)))
                core.set_robot_state("waiting" if t["status"] == "waiting" else "idle")
                core.record_activity("task", f"task_{t['status']}", f"Task '{t['title']}' -> {t['status']}.", "ok" if t["status"] == "completed" else "warn")
                core.events.publish("task_updated", t)
                self._json({"task": t})
            elif path.startswith("/api/v1/tasks/") and path.endswith("/retry"):
                tid = path.split("/")[4]
                t = core.tasks.retry_task(tid, approved=bool(body.get("approved", False)))
                core.events.publish("task_updated", t)
                self._json({"task": t})
            elif path.startswith("/api/v1/tasks/") and path.endswith("/pause"):
                t = core.tasks.pause_task(path.split("/")[4])
                core.events.publish("task_updated", t)
                self._json({"task": t})
            elif path.startswith("/api/v1/tasks/") and path.endswith("/cancel"):
                t = core.tasks.cancel_task(path.split("/")[4])
                core.events.publish("task_updated", t)
                self._json({"task": t})
            elif path == "/api/v1/voice/state":
                self._json({"state": core.set_robot_state(str(body.get("state", "idle")))})
            elif path == "/api/v1/voice/speak":
                res = core.voice.speak_native(str(body.get("text", "")), body.get("voice"), body.get("speed"), on_finished=lambda: core.events.publish("robot_state", {"state": core.voice.get_state()}))
                core.events.publish("robot_state", {"state": "speaking"})
                core.record_activity("voice", "voice_speak", f"Spoke aloud via Windows SAPI ({res['voice']}).", "ok")
                self._json(res)
            elif path == "/api/v1/voice/event":
                evt = core.record_activity("voice", str(body.get("event_type", "voice_event")), str(body.get("summary", "Voice event")), str(body.get("status", "info")), body.get("details"))
                if body.get("state"):
                    core.set_robot_state(str(body["state"]))
                self._json({"event": evt})
            elif path == "/api/v1/voice/interrupt":
                res = core.voice.interrupt(str(body.get("reason", "User interrupted")))
                core.events.publish("robot_state", {"state": res["new_state"]})
                core.record_activity("voice", "voice_interrupt", f"Interrupted ({res['previous_state']} -> {res['new_state']}).", "warn")
                self._json(res)
            elif path == "/api/v1/database/backup":
                self._json(core.create_backup())
            elif path == "/api/v1/emergency-stop":
                self._json(core.emergency_stop(str(body.get("reason", "User triggered emergency stop"))))
            elif path == "/api/v1/emergency-stop/reset":
                self._json(core.reset_emergency_stop())
            else:
                self._json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except SecretRejectedError as exc:
            core.record_activity("memory", "secret_rejected", str(exc), "warn")
            self._json({"error": str(exc), "code": "SECRET_REJECTED"}, HTTPStatus.BAD_REQUEST)
        except (ValueError, KeyError, PermissionError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except Exception as exc:
            core.logger.exception("POST %s failed", path)
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:
        self._update()

    def do_PATCH(self) -> None:
        self._update()

    def _update(self) -> None:
        path = urlparse(self.path).path
        core = self.server.core
        try:
            body = self._body()
            if path == "/api/v1/settings":
                updates = body.get("settings", body)
                saved = core.db.update_settings(updates)
                core.record_activity("system", "settings_updated", f"Updated {len(updates)} setting(s).", "ok")
                core.events.publish("settings_updated", {"keys": sorted(updates.keys())})
                self._json({"settings": saved})
            elif path.startswith("/api/v1/memories/"):
                upd = core.memory.update(path.split("/")[4], body.get("content"), body.get("category"), body.get("memory_type"), body.get("confidence"), body.get("tags"))
                if not upd:
                    self._json({"error": "Memory not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json({"memory": upd})
            else:
                self._json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except SecretRejectedError as exc:
            self._json({"error": str(exc), "code": "SECRET_REJECTED"}, HTTPStatus.BAD_REQUEST)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        core = self.server.core
        try:
            if path.startswith("/api/v1/conversations/"):
                self._json({"deleted": core.db.delete_conversation(path.split("/")[4])})
            elif path == "/api/v1/memories":
                self._json({"deleted_count": core.memory.clear_all(qs.get("category", [None])[0])})
            elif path.startswith("/api/v1/memories/"):
                self._json({"deleted": core.memory.delete(path.split("/")[4])})
            elif path == "/api/v1/activity":
                self._json({"deleted_count": core.db.clear_activity()})
            else:
                self._json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def create_server(core: RynaCore, host: str | None = None, port: int | None = None) -> RynaHTTPServer:
    web_dir = core.config.project_root / "app" / "desktop" / "web"
    return RynaHTTPServer((host or core.config.host, core.config.port if port is None else port), core, web_dir)
