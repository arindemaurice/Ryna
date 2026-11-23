"""Unified RYNA Core orchestrator connecting identity, AI, voice, memory, tasks, computer control, and SSE events."""

from __future__ import annotations

import json
import queue
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from agents.engine import AgentTaskEngine
from agents.tools import ToolRegistry
from ai.config import APP_NAME, VERSION, RynaConfig, setup_logging
from ai.local_models.detector import check_internet_connectivity, detect_hardware
from ai.providers.base import AIResponse, ChatMessage, ProviderError
from ai.routing.router import AIRouter
from computer.controller import ComputerController
from database.store import RynaDatabase, new_id
from integrations.registry import build_integration_report
from memory.engine import MemoryEngine
from security.permissions import PermissionEngine
from voice.manager import VoiceManager


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: list[queue.Queue[dict[str, Any]]] = []

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        msg = {"type": event_type, "payload": payload}
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass


class RynaCore:
    def __init__(self, config: RynaConfig | None = None, connectivity_checker: Callable[[], bool] | None = None) -> None:
        self.config = config or RynaConfig.load()
        self.logger = setup_logging(self.config.log_dir, self.config.log_level)
        self._check_online = connectivity_checker or check_internet_connectivity
        self.events = EventBus()

        self.db = RynaDatabase(self.config.db_path, self.config.default_settings)
        self.memory = MemoryEngine(self.db)
        self.permissions = PermissionEngine(self.db)
        self.computer = ComputerController(self.db, screenshots_dir=self.config.data_dir / "screenshots")
        self.tools = ToolRegistry(self.config, self.db, self.computer)
        self.tasks = AgentTaskEngine(self.db, self.permissions, self.tools, connectivity_checker=self._check_online)
        self.ai = AIRouter(self.config, self.db, connectivity_checker=self._check_online)
        self.voice = VoiceManager(self.db)

        self.recovered_tasks = self.tasks.recover_after_restart()
        if self.recovered_tasks:
            self.record_activity("task", "task_recovery", f"Recovered {len(self.recovered_tasks)} interrupted task(s) on boot.", "warn")
        self.logger.info("%s %s core initialized (db=%s)", APP_NAME, VERSION, self.config.db_path)

    def warm_up_async(self) -> None:
        """Warm up Windows SAPI, faster-whisper, and fast local Ollama model in parallel on startup."""
        self.voice.warm_up_async()

        def _warm_ollama() -> None:
            try:
                provs = self.ai.build_providers()
                ollama = provs.get("ollama")
                if ollama and hasattr(ollama, "warm_up"):
                    ollama.warm_up()  # type: ignore[attr-defined]
            except Exception:
                pass

        threading.Thread(target=_warm_ollama, daemon=True).start()

    def close(self) -> None:
        self.voice.shutdown()
        self.logger.info("Shutting down %s %s core.", APP_NAME, VERSION)
        self.db.close()
        for h in list(self.logger.handlers):
            try:
                h.flush()
                h.close()
            finally:
                self.logger.removeHandler(h)

    def record_activity(self, category: str, event_type: str, summary: str, status: str = "info", details: dict[str, Any] | None = None) -> dict[str, Any]:
        evt = self.db.record_activity(category, event_type, summary, status, details)
        self.events.publish("activity", evt)
        return evt

    def set_robot_state(self, state: str) -> str:
        st = self.voice.set_state(state)
        self.events.publish("robot_state", {"state": st})
        return st

    def robot_asset_status(self) -> dict[str, Any]:
        exists = self.config.robot_asset_path.is_file()
        return {
            "available": exists,
            "path": str(self.config.robot_asset_path),
            "url": "/api/v1/robot/asset" if exists else None,
            "detail": (
                "Authoritative isolated RYNA robot asset loaded."
                if exists
                else "Awaiting user-supplied RYNA robot reference image. Place isolated PNG at app/desktop/assets/ryna_robot.png or run scripts/isolate_robot.py."
            ),
        }

    def status_summary(self) -> dict[str, Any]:
        online = self._check_online()
        op = self.ai.operating_mode(online=online)
        hw = detect_hardware(self.config.project_root)
        voice_st = self.voice.status()
        comp_caps = self.computer.capabilities()
        robot_state = "error" if self.permissions.emergency_stopped else voice_st["robot_state"]

        return {
            "app": APP_NAME,
            "version": VERSION,
            "online": online,
            "operating_mode": {
                "id": op["mode_id"],
                "label": op["label"],
                "local_ai_available": op["local_ai_available"],
                "cloud_ai_available": op["cloud_ai_available"],
                "active_provider": op["active_provider"],
                "active_model": op["active_model"],
            },
            "robot_state": robot_state,
            "robot_asset": self.robot_asset_status(),
            "emergency_stopped": self.permissions.emergency_stopped,
            "recovered_tasks_on_boot": self.recovered_tasks,
            "ai": op["status"],
            "voice": voice_st,
            "computer": comp_caps,
            "integrations": build_integration_report(op["status"], voice_st, comp_caps),
            "hardware": hw.to_dict(),
            "database": self.db.stats(),
        }

    def create_backup(self) -> dict[str, Any]:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = self.config.backup_dir / f"ryna_backup_{ts}.db"
        self.db.backup_to(dest)
        self.record_activity("system", "db_backup", f"Created SQLite backup at {dest.name}.", "ok")
        return {"backup_path": str(dest), "size_bytes": dest.stat().st_size}

    def emergency_stop(self, reason: str = "User triggered emergency stop") -> dict[str, Any]:
        audit = self.permissions.trigger_emergency_stop(reason)
        halted = self.tasks.halt_active()
        self.voice.interrupt(reason="Emergency stop")
        self.set_robot_state("error")
        self.record_activity("system", "emergency_stop", f"Emergency stop active ({halted} task(s) paused).", "error")
        self.events.publish("emergency_stop", {"emergency_stopped": True, "halted_tasks": halted})
        return {"emergency_stopped": True, "halted_tasks": halted, "audit": audit}

    def reset_emergency_stop(self) -> dict[str, Any]:
        audit = self.permissions.reset_emergency_stop()
        self.set_robot_state("idle")
        self.record_activity("system", "emergency_stop_reset", "Emergency stop cleared.", "ok")
        self.events.publish("emergency_stop", {"emergency_stopped": False})
        return {"emergency_stopped": False, "audit": audit}

    def _prepare_context(
        self,
        conversation_id: str | None,
        user_text: str,
        attachments: list[dict[str, Any]] | None = None,
        skip_user_insert: bool = False,
    ) -> tuple[str, dict[str, Any] | None, list[ChatMessage], list[dict[str, Any]], bool]:
        text = user_text.strip()
        atts = [
            {
                "name": str(a.get("name", "file.txt"))[:120],
                "content": str(a.get("content", ""))[:24000],
                "size": int(a.get("size", len(str(a.get("content", ""))))),
                "mime": str(a.get("mime", "text/plain")),
            }
            for a in (attachments or [])[:5]
            if a.get("content")
        ]
        if not text and not atts:
            raise ValueError("Message content cannot be empty.")

        if not conversation_id or not self.db.get_conversation(conversation_id):
            conversation_id = self.db.create_conversation()["id"]

        online = self._check_online()
        mode = "online" if online else "offline"
        user_msg = None
        if not skip_user_insert:
            meta = {"attachments": [{"name": a["name"], "size": a["size"], "mime": a["mime"]} for a in atts]} if atts else {}
            user_msg = self.db.add_message(conversation_id, "user", text or f"[Attached {len(atts)} file(s)]", mode=mode, metadata=meta)
            if atts:
                self.record_activity("file", "file_attach", f"Attached {', '.join(a['name'] for a in atts)}.", "ok")

        mems = self.memory.retrieve_relevant(text, limit=6)
        sys_prompt = self.config.system_prompt
        if mems:
            sys_prompt += "\n\nRelevant persistent memories:\n" + "\n".join(f"- [{m['category']}/{m['memory_type']}] {m['content']}" for m in mems)

        hist = self.db.list_messages(conversation_id, limit=24)
        msgs = [ChatMessage("system", sys_prompt)]
        for i, m in enumerate(hist):
            if m["role"] not in ("user", "assistant"):
                continue
            body = m["content"]
            if i == len(hist) - 1 and m["role"] == "user" and atts:
                body += "".join(f"\n\n--- Attached File: {a['name']} ---\n{a['content']}\n--- End {a['name']} ---" for a in atts)
            msgs.append(ChatMessage(m["role"], body))
        return conversation_id, user_msg, msgs, mems, online

    def _run_slash_tool(self, text: str, conv_id: str, mode: str) -> dict[str, Any] | None:
        parts = text.split(" ", 1)
        cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
        mapping = {
            "/calc": ("calculate", {"expression": arg}),
            "/time": ("current_time", {}),
            "/diag": ("system_diagnostics", {}),
            "/find": ("file_search", {"query": arg}),
            "/read": ("file_read", {"path": arg}),
            "/note": ("create_note", {"title": arg[:48] or "Note", "content": arg}),
            "/window": ("active_window", {}),
            "/screen": ("screen_capture", {}),
            "/open": ("open_application", {"app": arg}),
            "/search": ("web_search", {"query": arg}),
            "/fetch": ("web_fetch", {"url": arg}),
        }
        if cmd not in mapping:
            return None
        tool_name, tool_args = mapping[cmd]
        dec = self.permissions.evaluate(tool_name, arg or cmd)
        if not dec.allowed:
            self.record_activity("tool", "tool_blocked", f"Blocked {tool_name}: {dec.reason}", "warn")
            return self.db.add_message(conv_id, "system", f"Tool `{tool_name}` blocked: {dec.reason}", provider="tool", model=tool_name, mode=mode)
        self.set_robot_state("working")
        try:
            res = self.tools.execute(tool_name, tool_args)
            self.record_activity("tool", "tool_execution", f"Executed '{tool_name}'.", "ok")
            return self.db.add_message(conv_id, "assistant", f"[Tool: {tool_name}]\n```json\n{json.dumps(res, indent=2)}\n```", provider="tool", model=tool_name, mode="offline" if tool_name != "web_fetch" else "online", metadata={"tool_name": tool_name})
        except Exception as exc:
            self.record_activity("tool", "tool_error", f"Tool '{tool_name}' failed: {exc}", "error")
            return self.db.add_message(conv_id, "system", f"[Tool Error: {tool_name}] {exc}", provider="tool", model=tool_name, mode=mode)
        finally:
            self.set_robot_state("idle")

    def _try_natural_action(self, text: str, conv_id: str, online: bool) -> dict[str, Any] | None:
        """Execute verified real local/online actions when user gives natural spoken or typed commands."""
        low = text.strip().lower().rstrip(".!?")
        mode = "online" if online else "offline"

        # 1. Time / Date check (works 100% offline)
        if re.search(r"\b(what time is it|current time|check time|what('s| is) the time|what('s| is) today'?s? date)\b", low):
            res = self.tools.execute("current_time", {})
            reply = f"It's {res['time']} on {res['date']}."
            self.record_activity("tool", "current_time", reply, "ok")
            return self.db.add_message(
                conv_id, "assistant", reply, provider="ryna_tool", model="current_time", mode="offline",
                metadata={"action_card": {"title": "Local Time", "subtitle": res["date"], "value": res["time"]}},
            )

        # 2. Remember fact/preference into persistent memory
        m_rem = re.match(r"^(?:please\s+)?remember\s+(?:that\s+)?(.+)$", text.strip(), re.IGNORECASE)
        if m_rem:
            fact = m_rem.group(1).strip()
            try:
                saved = self.memory.add("personal", fact, "confirmed_fact", source="conversation")
                reply = f"Got it — I've saved that to memory: \"{saved['content']}\""
                self.record_activity("memory", "memory_saved", f"Remembered: {fact[:80]}", "ok")
                return self.db.add_message(
                    conv_id, "assistant", reply, provider="ryna_memory", model="memory", mode="offline",
                    metadata={"action_card": {"title": "Memory Saved", "subtitle": saved["category"], "value": saved["content"]}},
                )
            except Exception as exc:
                return self.db.add_message(conv_id, "system", f"Could not save memory: {exc}", mode=mode)

        # 3. Open allowlisted Windows application ("open spotify", "open chrome", "open browser", "open vs code", etc.)
        m_open = re.match(r"^(?:please\s+)?(?:open|launch|start)\s+(spotify|chrome|google chrome|edge|microsoft edge|browser|vs code|vscode|notepad|calculator|explorer|file explorer|paint|settings|discord|whatsapp|word|excel)\b", low)
        if m_open:
            app_key = m_open.group(1).replace("google ", "").replace("microsoft ", "")
            dec = self.permissions.evaluate("open_application", app_key)
            if not dec.allowed:
                return self.db.add_message(conv_id, "system", f"Launching {app_key} blocked: {dec.reason}", mode=mode)
            self.set_robot_state("working")
            try:
                res = self.tools.execute("open_application", {"app": app_key})
                reply = f"Opening {app_key.title()} now."
                self.record_activity("tool", "open_application", f"Launched {app_key}.", "ok", res)
                return self.db.add_message(
                    conv_id, "assistant", reply, provider="ryna_computer", model="open_application", mode="offline",
                    metadata={"action_card": {"title": f"Opened {app_key.title()}", "subtitle": res.get("method", res.get("path", "Windows App")), "value": "Launched"}},
                )
            except Exception as exc:
                self.record_activity("tool", "open_application_error", str(exc), "error")
                return self.db.add_message(conv_id, "system", f"I couldn't open {app_key}: {exc}", mode=mode)
            finally:
                self.set_robot_state("idle")

        # 4. Screen capture / inspect screen ("take a screenshot", "look at my screen")
        if re.search(r"\b(take a screenshot|capture my screen|look at my screen|what'?s on my screen)\b", low):
            dec = self.permissions.evaluate("screen_capture", "primary_monitor")
            if not dec.allowed:
                return self.db.add_message(conv_id, "system", f"Screen capture blocked: {dec.reason}", mode=mode)
            self.set_robot_state("working")
            try:
                res = self.tools.execute("screen_capture", {})
                reply = f"I captured your screen ({res['width']}×{res['height']}). Your active window is \"{res['active_window'] or 'Desktop'}\", and the screenshot was saved locally."
                self.record_activity("tool", "screen_capture", f"Captured screen ({res['width']}x{res['height']}).", "ok", res)
                return self.db.add_message(
                    conv_id, "assistant", reply, provider="ryna_vision", model="screen_capture", mode="offline",
                    metadata={"action_card": {"title": "Screen Captured", "subtitle": res["active_window"] or "Desktop", "value": Path(res["path"]).name}},
                )
            except Exception as exc:
                return self.db.add_message(conv_id, "system", f"Screen capture failed: {exc}", mode=mode)
            finally:
                self.set_robot_state("idle")

        # 5. Live web search ("search the web for ...", "search web for ...", "google ...")
        m_search = re.match(r"^(?:please\s+)?(?:search(?:\s+the)?\s+web\s+for|web\s+search|search\s+for|look\s+up)\s+(.+)$", text.strip(), re.IGNORECASE)
        if m_search:
            query = m_search.group(1).strip()
            if not online:
                return self.db.add_message(conv_id, "system", f"I'm currently offline, so I can't search the web for \"{query}\" right now. I can still answer using local AI if you'd like.", mode="offline")
            self.set_robot_state("working")
            try:
                res = self.tools.execute("web_search", {"query": query})
                top = res.get("results", [])[:4]
                if not top:
                    return self.db.add_message(conv_id, "assistant", f"I searched the web for \"{query}\" but no results were returned.", provider="ryna_web", model="web_search", mode="online")
                lines = [f"Here's what I found for \"{query}\":"]
                for r in top:
                    lines.append(f"• {r['title']}: {r['snippet']}")
                reply = "\n".join(lines)
                self.record_activity("tool", "web_search", f"Searched web for '{query}' ({len(top)} results).", "ok")
                return self.db.add_message(
                    conv_id, "assistant", reply, provider="ryna_web", model="web_search", mode="online",
                    metadata={"action_card": {"title": f"Web Search: {query}", "subtitle": f"{len(top)} verified result(s)", "value": top[0]["title"]}},
                )
            except Exception as exc:
                return self.db.add_message(conv_id, "system", f"Web search failed: {exc}", mode=mode)
            finally:
                self.set_robot_state("idle")

        return None

    def stream_chat(
        self,
        conversation_id: str | None,
        user_text: str,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        skip_user_insert: bool = False,
    ) -> Iterator[dict[str, Any]]:
        conv_id, user_msg, msgs, mems, online = self._prepare_context(conversation_id, user_text, attachments, skip_user_insert)
        mode = "online" if online else "offline"
        yield {"event": "meta", "data": {"conversation_id": conv_id, "user_message": user_msg, "memories_used": len(mems)}}

        if user_text.strip().startswith("/"):
            tool_msg = self._run_slash_tool(user_text.strip(), conv_id, mode)
            if tool_msg is not None:
                yield {"event": "done", "data": {"conversation_id": conv_id, "assistant_message": tool_msg, "ai_available": True, "used_tool": True}}
                return

        natural_msg = self._try_natural_action(user_text, conv_id, online)
        if natural_msg is not None:
            yield {"event": "done", "data": {"conversation_id": conv_id, "assistant_message": natural_msg, "ai_available": True, "used_tool": True}}
            return

        gid = new_id("gen")
        self.voice.begin_generation(gid)
        self.events.publish("robot_state", {"state": "thinking"})
        self.record_activity("ai", "ai_request", f"Streaming request ({len(msgs) - 1} turn(s)).", "info")

        acc: list[str] = []
        final_resp: AIResponse | None = None
        prov, mod = "", ""
        try:
            for chunk in self.ai.chat_stream(msgs, requested_provider, requested_model, online_override=online, should_cancel=self.voice.is_interrupted):
                if self.voice.is_interrupted():
                    break
                if chunk.get("done"):
                    final_resp = chunk["response"]
                    break
                d = str(chunk.get("delta", ""))
                if d:
                    acc.append(d)
                    prov = str(chunk.get("provider", prov))
                    mod = str(chunk.get("model", mod))
                    yield {"event": "delta", "data": {"delta": d, "provider": prov, "model": mod}}

            if self.voice.is_interrupted():
                partial = "".join(acc).strip()
                msg = self.db.add_message(conv_id, "assistant" if partial else "system", f"{partial}\n\n[Interrupted by user]" if partial else "Generation stopped by user.", provider=prov or None, model=mod or None, mode=mode, metadata={"interrupted": True})
                self.record_activity("ai", "ai_interrupted", "Streaming response interrupted.", "warn")
                yield {"event": "done", "data": {"conversation_id": conv_id, "assistant_message": msg, "ai_available": True, "interrupted": True}}
                return

            if not final_resp:
                raise ProviderError("router", "AI stream ended without response.")
            saved = self.db.add_message(conv_id, "assistant", final_resp.content, provider=final_resp.provider, model=final_resp.model, mode="offline" if final_resp.is_local else "online", metadata={"latency_ms": final_resp.latency_ms, "memories_used": [m["id"] for m in mems]})
            self.record_activity("ai", "ai_response", f"{final_resp.provider}/{final_resp.model} responded in {final_resp.latency_ms} ms.", "ok")
            yield {"event": "done", "data": {"conversation_id": conv_id, "assistant_message": saved, "ai_available": True, "provider": final_resp.provider, "model": final_resp.model, "latency_ms": final_resp.latency_ms}}
        except ProviderError as exc:
            self.record_activity("ai", "ai_error", str(exc), "error")
            self.set_robot_state("error")
            sys_msg = self.db.add_message(conv_id, "system", f"AI Provider Unavailable\n\nReason: {exc}\n\nYour message is saved locally. Start Ollama with a local model or enable Cloud AI in Settings, or use offline tools (/diag, /calc, /find, /note).", mode=mode, metadata={"ai_unavailable": True})
            yield {"event": "error", "data": {"conversation_id": conv_id, "assistant_message": sys_msg, "ai_available": False, "error": str(exc)}}
        finally:
            self.voice.end_generation(gid)
            self.events.publish("robot_state", {"state": self.voice.get_state()})

    def send_chat(self, conversation_id: str | None, user_text: str, requested_provider: str | None = None, requested_model: str | None = None, attachments: list[dict[str, Any]] | None = None, skip_user_insert: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {}
        user_msg = None
        for ev in self.stream_chat(conversation_id, user_text, requested_provider, requested_model, attachments, skip_user_insert):
            if ev["event"] == "meta":
                user_msg = ev["data"].get("user_message")
            elif ev["event"] in ("done", "error"):
                result = {**ev["data"], "user_message": user_msg}
        return result

    def retry_message(self, conversation_id: str, message_id: str | None = None) -> dict[str, Any]:
        msgs = self.db.list_messages(conversation_id)
        if message_id:
            target = next((m for m in msgs if m["id"] == message_id), None)
            if target and target["role"] in ("assistant", "system"):
                self.db.delete_message(target["id"])
                msgs = self.db.list_messages(conversation_id)
        last_user = next((m for m in reversed(msgs) if m["role"] == "user"), None)
        if not last_user:
            raise ValueError("No user prompt found to retry.")
        return self.send_chat(conversation_id, last_user["content"], skip_user_insert=True)
