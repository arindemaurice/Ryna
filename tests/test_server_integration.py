"""End-to-end integration test against a live loopback RYNA HTTP server."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.python.config import RynaConfig
from core.python.engine import RynaEngine
from core.python.server import create_server


class TestServerIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = RynaConfig.load(
            overrides={
                "db_path": Path(self._tmp.name) / "int.db",
                "log_dir": Path(self._tmp.name) / "logs",
                "openai_api_key": "",
                "anthropic_api_key": "",
                "gemini_api_key": "",
            }
        )
        self.engine = RynaEngine(self.cfg, connectivity_checker=lambda: False)
        # Ensure Ollama points to closed port so AI-unavailable honesty is deterministic
        self.engine.db.set_setting("ai.ollama_base_url", "http://127.0.0.1:1")
        self.server = create_server(self.engine, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.engine.close()
        self._tmp.cleanup()

    def _req(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(f"{self.base}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                raw = resp.read().decode("utf-8")
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw) if "application/json" in ctype else raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return exc.code, json.loads(raw)

    def test_web_ui_and_health_endpoints(self) -> None:
        code, html = self._req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("RYNA 0.1.0", html)

        code, health = self._req("GET", "/api/v1/health")
        self.assertEqual(code, 200)
        self.assertEqual(health["status"], "ok")

        code, status = self._req("GET", "/api/v1/status")
        self.assertEqual(code, 200)
        self.assertEqual(status["mode"], "offline")
        self.assertFalse(status["ai"]["any_provider_available"])

    def test_chat_honest_unavailable_and_local_tool_command(self) -> None:
        code, res = self._req("POST", "/api/v1/chat", {"message": "Hello RYNA"})
        self.assertEqual(code, 200)
        self.assertFalse(res["ai_available"])
        self.assertIn("AI Provider Unavailable", res["assistant_message"]["content"])

        conv_id = res["conversation_id"]
        code, calc = self._req("POST", "/api/v1/chat", {"conversation_id": conv_id, "message": "/calc 19 * 4"})
        self.assertEqual(code, 200)
        self.assertTrue(calc["used_local_tool"])
        self.assertIn("76", calc["assistant_message"]["content"])

    def test_settings_memory_tasks_and_emergency_stop(self) -> None:
        code, _ = self._req("PUT", "/api/v1/settings", {"settings": {"voice.wake_word": "Hey Ryna Test"}})
        self.assertEqual(code, 200)
        code, s = self._req("GET", "/api/v1/settings")
        self.assertEqual(s["settings"]["voice.wake_word"], "Hey Ryna Test")

        # Secret rejection via REST API
        code, err = self._req("POST", "/api/v1/memories", {"category": "personal", "content": "api_key=sk-1234567890abcdefghij"})
        self.assertEqual(code, 400)
        self.assertEqual(err["code"], "SECRET_REJECTED")

        # Valid memory CRUD
        code, created = self._req("POST", "/api/v1/memories", {"category": "project", "content": "RYNA targets Windows first"})
        self.assertEqual(code, 201)
        mem_id = created["memory"]["id"]

        code, exported = self._req("GET", "/api/v1/memories/export")
        self.assertEqual(code, 200)
        self.assertEqual(exported["count"], 1)

        code, _ = self._req("DELETE", f"/api/v1/memories/{mem_id}")
        self.assertEqual(code, 200)

        # Level 2 task approval workflow
        code, task_resp = self._req(
            "POST",
            "/api/v1/tasks",
            {"title": "Confirm Calc", "goal": "calc: 5 * 5", "permission_level": 2, "auto_run": True},
        )
        self.assertEqual(code, 201)
        task_id = task_resp["task"]["id"]
        self.assertEqual(task_resp["task"]["status"], "waiting_for_approval")

        code, approved = self._req("POST", f"/api/v1/tasks/{task_id}/run", {"approved": True})
        self.assertEqual(code, 200)
        self.assertEqual(approved["task"]["status"], "completed")

        # Emergency stop + reset
        code, stop = self._req("POST", "/api/v1/emergency-stop", {"reason": "Integration test"})
        self.assertEqual(code, 200)
        self.assertTrue(stop["emergency_stopped"])

        code, reset = self._req("POST", "/api/v1/emergency-stop/reset", {})
        self.assertEqual(code, 200)
        self.assertFalse(reset["emergency_stopped"])


if __name__ == "__main__":
    unittest.main()
