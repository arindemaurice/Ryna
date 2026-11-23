"""Local loopback HTTP REST API & Web UI server for RYNA.

Binds strictly to 127.0.0.1 by default so no local services are exposed to the network.
"""

from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.python.engine import RynaEngine
from services.memory.store import SecretRejectedError


class RynaHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], engine: RynaEngine, web_dir: Path) -> None:
        self.engine = engine
        self.web_dir = web_dir
        super().__init__(server_address, RynaRequestHandler)


class RynaRequestHandler(BaseHTTPRequestHandler):
    server: RynaHTTPServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:
        self.server.engine.logger.debug("HTTP %s - " + format, self.address_string(), *args)

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        # Allow local Flutter / Web UI origins on loopback only
        origin = self.headers.get("Origin", "")
        if origin.startswith(("http://127.0.0.1", "http://localhost")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        if length > 2 * 1024 * 1024:
            raise ValueError("Request payload exceeds 2 MB limit.")
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
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8" if (mime or "").startswith("text/") or mime in ("application/javascript", "application/json") else (mime or "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        origin = self.headers.get("Origin", "")
        if origin.startswith(("http://127.0.0.1", "http://localhost")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
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

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        eng = self.server.engine

        try:
            body = self._read_json()

            if path == "/api/v1/conversations":
                conv = eng.db.create_conversation(title=str(body.get("title", "New Conversation")))
                self._send_json({"conversation": conv}, HTTPStatus.CREATED)
            elif path == "/api/v1/chat":
                res = eng.send_chat(
                    conversation_id=body.get("conversation_id"),
                    user_text=str(body.get("message", "")),
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
                if body.get("auto_run", False):
                    task = eng.tasks.run_task(task["id"], approved=bool(body.get("approved", False)))
                self._send_json({"task": task}, HTTPStatus.CREATED)
            elif path.startswith("/api/v1/tasks/") and path.endswith("/run"):
                task_id = path.split("/")[4]
                task = eng.tasks.run_task(task_id, approved=bool(body.get("approved", False)))
                self._send_json({"task": task})
            elif path.startswith("/api/v1/tasks/") and path.endswith("/pause"):
                task_id = path.split("/")[4]
                self._send_json({"task": eng.tasks.pause_task(task_id)})
            elif path.startswith("/api/v1/tasks/") and path.endswith("/cancel"):
                task_id = path.split("/")[4]
                self._send_json({"task": eng.tasks.cancel_task(task_id)})
            elif path == "/api/v1/notes":
                note = eng.db.create_note(
                    title=str(body.get("title", "Note")),
                    content=str(body.get("content", "")),
                    remind_at=body.get("remind_at"),
                )
                self._send_json({"note": note}, HTTPStatus.CREATED)
            elif path == "/api/v1/voice/state":
                new_state = eng.voice.set_state(str(body.get("state", "idle")))
                self._send_json({"state": new_state})
            elif path == "/api/v1/voice/interrupt":
                res = eng.voice.interrupt(reason=str(body.get("reason", "User pressed interrupt")))
                self._send_json(res)
            elif path == "/api/v1/emergency-stop":
                self._send_json(eng.emergency_stop(reason=str(body.get("reason", "User triggered emergency stop"))))
            elif path == "/api/v1/emergency-stop/reset":
                self._send_json(eng.reset_emergency_stop())
            else:
                self._send_json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except SecretRejectedError as exc:
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
                self._send_json({"deleted_count": count})
            elif path.startswith("/api/v1/memories/"):
                mem_id = path.split("/")[4]
                deleted = eng.memory.delete(mem_id)
                self._send_json({"deleted": deleted})
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
