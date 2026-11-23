"""End-to-end integration tests against the live loopback RYNA HTTP/SSE server."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ai.config import RynaConfig
from ai.orchestration.core import RynaCore
from ai.orchestration.server import create_server


class TestServerE2E(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = RynaConfig.load(
            overrides={
                "db_path": Path(self._tmp.name) / "e2e.db",
                "log_dir": Path(self._tmp.name) / "logs",
                "backup_dir": Path(self._tmp.name) / "backups",
                "openai_api_key": "",
                "anthropic_api_key": "",
                "gemini_api_key": "",
            }
        )
        self.core = RynaCore(self.cfg, connectivity_checker=lambda: False)
        self.core.db.set_setting("ai.ollama_base_url", "http://127.0.0.1:1")
        self.server = create_server(self.core, host="127.0.0.1", port=0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.core.close()
        self._tmp.cleanup()

    def _req(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, headers={"Content-Type": "application/json"} if data else {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5.0) as r:
                raw = r.read().decode("utf-8")
                return r.status, json.loads(raw) if "application/json" in r.headers.get("Content-Type", "") else raw
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_full_api_ui_assets_robot_boundary_and_backup(self) -> None:
        for path in ("/", "/styles.css", "/app.js", "/voice.js"):
            code, body = self._req("GET", path)
            self.assertEqual(code, 200)
            self.assertTrue(len(body) > 100)

        code, st = self._req("GET", "/api/v1/status")
        self.assertEqual(code, 200)
        self.assertEqual(st["operating_mode"]["id"], "offline_limited")
        self.assertTrue(st["robot_asset"]["available"])

        # Real natural time action works offline
        code, time_res = self._req("POST", "/api/v1/chat", {"message": "what time is it"})
        self.assertEqual(code, 200)
        self.assertTrue(time_res["ai_available"])
        self.assertIn("action_card", time_res["assistant_message"]["metadata"])

        # Tool slash command + SSE stream + attachment
        code, calc = self._req("POST", "/api/v1/chat", {"message": "/calc 11 * 9"})
        self.assertEqual(code, 200)
        self.assertIn("99", calc["assistant_message"]["content"])

        req = urllib.request.Request(
            f"{self.base}/api/v1/chat/stream",
            data=json.dumps({"message": "Read file", "attachments": [{"name": "a.txt", "content": "hello", "size": 5}]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        sse = urllib.request.urlopen(req, timeout=5.0).read().decode("utf-8")
        self.assertIn("event: meta", sse)
        self.assertIn("event: error", sse)

        # Backup + Emergency Stop + Activity
        code, bk = self._req("POST", "/api/v1/database/backup", {})
        self.assertEqual(code, 200)
        self.assertGreater(bk["size_bytes"], 0)

        code, stop = self._req("POST", "/api/v1/emergency-stop", {})
        self.assertTrue(stop["emergency_stopped"])
        code, reset = self._req("POST", "/api/v1/emergency-stop/reset", {})
        self.assertFalse(reset["emergency_stopped"])

        code, act = self._req("GET", "/api/v1/activity")
        types = {e["event_type"] for e in act["events"]}
        self.assertIn("file_attach", types)
        self.assertIn("db_backup", types)
        self.assertIn("emergency_stop", types)


if __name__ == "__main__":
    unittest.main()
