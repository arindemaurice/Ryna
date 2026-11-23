"""Tests for AI routing, local mock HTTP Ollama/OpenAI providers, fallback, privacy guard, and honest errors."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from core.python.config import RynaConfig
from core.python.database import RynaDatabase
from services.ai.base import ChatMessage, ProviderError
from services.ai.router import AIRouter


class _MockProviderHandler(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            body = json.dumps({"models": [{"name": "qwen2.5:3b"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path == "/api/chat":
            if _MockProviderHandler.mode == "fail_ollama":
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"simulated ollama error")
                return
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                chunks = [
                    {"model": payload.get("model", "qwen2.5:3b"), "message": {"role": "assistant", "content": "Hello "}, "done": False},
                    {"model": payload.get("model", "qwen2.5:3b"), "message": {"role": "assistant", "content": "from stream!"}, "done": False},
                    {"model": payload.get("model", "qwen2.5:3b"), "message": {"role": "assistant", "content": ""}, "done": True, "eval_count": 6},
                ]
                for c in chunks:
                    self.wfile.write((json.dumps(c) + "\n").encode("utf-8"))
                    self.wfile.flush()
                return
            reply = {
                "model": payload.get("model", "qwen2.5:3b"),
                "message": {"role": "assistant", "content": "Real HTTP reply from mock local Ollama."},
                "eval_count": 10,
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(reply).encode("utf-8"))
            return

        if self.path == "/v1/chat/completions":
            reply = {
                "model": payload.get("model", "gpt-4o-mini"),
                "choices": [{"message": {"role": "assistant", "content": "Real HTTP reply from mock OpenAI-compatible."}}],
                "usage": {"total_tokens": 12},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(reply).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


class TestAIRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mock_server = HTTPServer(("127.0.0.1", 0), _MockProviderHandler)
        cls.mock_port = cls.mock_server.server_address[1]
        cls.thread = threading.Thread(target=cls.mock_server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.mock_server.shutdown()
        cls.mock_server.server_close()

    def setUp(self) -> None:
        _MockProviderHandler.mode = "ok"
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = RynaConfig.load(
            overrides={
                "db_path": Path(self._tmp.name) / "ai.db",
                "openai_api_key": "test-key-for-mock",
            }
        )
        self.db = RynaDatabase(self.cfg.db_path, self.cfg.default_settings)
        self.online = False
        self.router = AIRouter(self.cfg, self.db, connectivity_checker=lambda: self.online)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_offline_local_ollama_http_success(self) -> None:
        self.db.update_settings({"ai.ollama_base_url": f"http://127.0.0.1:{self.mock_port}", "ai.local_model": "qwen2.5:3b"})
        resp = self.router.chat([ChatMessage(role="user", content="Hello RYNA offline")])
        self.assertEqual(resp.provider, "ollama")
        self.assertTrue(resp.is_local)
        self.assertIn("mock local Ollama", resp.content)

    def test_honest_error_when_no_provider_reachable(self) -> None:
        self.db.update_settings({"ai.ollama_base_url": "http://127.0.0.1:1", "ai.allow_cloud": False})
        with self.assertRaises(ProviderError) as ctx:
            self.router.chat([ChatMessage(role="user", content="Hello")])
        self.assertIn("No real AI provider", str(ctx.exception))

    def test_fallback_from_failing_local_to_openai_when_online_and_allowed(self) -> None:
        _MockProviderHandler.mode = "fail_ollama"
        self.online = True
        self.db.update_settings(
            {
                "ai.ollama_base_url": f"http://127.0.0.1:{self.mock_port}",
                "ai.allow_cloud": True,
                "ai.openai_base_url": f"http://127.0.0.1:{self.mock_port}/v1",
                "ai.preferred_provider": "ollama",
                "ai.fallback_provider": "openai",
            }
        )
        resp = self.router.chat([ChatMessage(role="user", content="Explain quantum annealing")])
        self.assertEqual(resp.provider, "openai")
        self.assertIn("OpenAI-compatible", resp.content)

    def test_privacy_blocks_sensitive_prompt_from_cloud(self) -> None:
        self.online = True
        self.db.update_settings(
            {
                "ai.ollama_base_url": "http://127.0.0.1:1",
                "ai.allow_cloud": True,
                "ai.send_sensitive_to_cloud": False,
                "ai.openai_base_url": "https://api.openai.com/v1",
            }
        )
        with self.assertRaises(ProviderError) as ctx:
            self.router.chat([ChatMessage(role="user", content="My password is secret123")])
        self.assertIn("Sensitive content", str(ctx.exception))

    def test_streaming_ollama_and_operating_modes(self) -> None:
        self.online = False
        self.db.update_settings({"ai.ollama_base_url": f"http://127.0.0.1:{self.mock_port}", "ai.local_model": "qwen2.5:3b"})
        mode_off = self.router.operating_mode(online=False)
        self.assertEqual(mode_off["mode_id"], "offline_local_ai")

        deltas = []
        final = None
        for ev in self.router.chat_stream([ChatMessage(role="user", content="Stream test")], online_override=False):
            if "delta" in ev:
                deltas.append(ev["delta"])
            if ev.get("done"):
                final = ev["response"]
        self.assertEqual("".join(deltas), "Hello from stream!")
        self.assertIsNotNone(final)
        self.assertEqual(final.content, "Hello from stream!")

        mode_on_local = self.router.operating_mode(online=True)
        self.assertEqual(mode_on_local["mode_id"], "online_local_ai")

        self.db.update_settings({"ai.ollama_base_url": "http://127.0.0.1:1", "ai.allow_cloud": False})
        self.assertEqual(self.router.operating_mode(online=False)["mode_id"], "offline_limited")
        self.assertEqual(self.router.operating_mode(online=True)["mode_id"], "ai_unavailable")

        self.db.update_settings({"ai.allow_cloud": True})
        self.assertEqual(self.router.operating_mode(online=True)["mode_id"], "online_cloud_ai")


if __name__ == "__main__":
    unittest.main()
