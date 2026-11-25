"""Unified RYNA Core orchestrator connecting identity, AI, voice, memory, tasks, computer control, and SSE events."""

from __future__ import annotations

import json
import queue
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from agents.engine import AgentTaskEngine
from agents.tools import ToolRegistry
from ai.config import APP_NAME, VERSION, RynaConfig, setup_logging
from ai.local_models.detector import check_internet_connectivity, detect_hardware
from ai.providers.base import AIResponse, ChatMessage, ProviderError
from ai.routing.router import AIRouter
from computer.controller import ComputerController
from computer.system_manager import RynaSystemManager
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
        self.sys_mgr = RynaSystemManager(self.config.project_root)
        self.tools = ToolRegistry(self.config, self.db, self.computer, sys_mgr=self.sys_mgr)
        self.tasks = AgentTaskEngine(self.db, self.permissions, self.tools, connectivity_checker=self._check_online)
        self.ai = AIRouter(self.config, self.db, connectivity_checker=self._check_online)
        self.voice = VoiceManager(self.db)
        # Optional UI hook: when running as the desktop robot, RYNA physically walks to the
        # Windows Search bar, searches the app name, and opens it rapidly.
        self.physical_app_launcher: Callable[[str], dict[str, Any]] | None = None

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

    def check_due_reminders(self) -> list[dict[str, Any]]:
        """Check SQLite notes with remind_at timestamps, mark due items completed, and return them."""
        now_iso = datetime.now(timezone.utc).isoformat()
        due: list[dict[str, Any]] = []
        for note in self.db.list_notes(limit=100):
            rem = note.get("remind_at")
            if not note.get("completed") and rem and str(rem) <= now_iso:
                self.db.complete_note(note["id"])
                due.append(note)
                self.record_activity("system", "reminder_due", f"Reminder triggered: {note['title']}", "ok")
        return due

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

        # Inject concise Windows Device Access & Control schema (optimized for fast local Ollama models)
        sys_prompt += (
            "\n\n[Windows Device Control]\n"
            "You have full access to control this Windows PC. For normal questions/chat, answer naturally in 1-2 sentences with NO action tag.\n"
            "ONLY when the user asks you to perform a computer action, emit ONE action line:\n"
            "[ACTION: tool_name({\"arg\": \"val\"})]\n"
            "Tools: execute_command(command), open_application(app), close_application(app), "
            "file_write(path, content), file_read(path), file_search(query), delete_file(path), "
            "type_text(text), press_key(key), hotkey(combo), mouse_click(x, y, button), mouse_move(x, y), "
            "screen_capture(), active_window(), volume_control(action, level), brightness_control(mode, value), "
            "battery_status(), system_info(), list_processes(), web_search(query)."
        )

        hist = self.db.list_messages(conversation_id, limit=10)
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
        """Execute verified real local/online system-management actions (including fast physical
        Search-bar app opening, files, OCR, power, timers, and telemetry) for RYNA.
        """
        raw = text.strip()
        # Strip polite conversational lead-ins ("please", "can you", "could you", "go and")
        stripped = re.sub(r"^(?:(?:hey\s+ryna|ryna|please|can you|could you|i want you to|go and|go ahead and)\s*,?\s*)+", "", raw, flags=re.IGNORECASE).strip()
        low = stripped.lower().rstrip(".!?")
        mode = "online" if online else "offline"

        def _exec_action(tool_name: str, args: dict[str, Any], target: str, reply_fn: Callable[[dict[str, Any]], str], title: str) -> dict[str, Any]:
            dec = self.permissions.evaluate(tool_name, target)
            if not dec.allowed:
                return self.db.add_message(conv_id, "system", f"Action blocked: {dec.reason}", mode=mode)
            self.set_robot_state("working")
            try:
                res = self.tools.execute(tool_name, args)
                reply = reply_fn(res)
                self.record_activity("tool", tool_name, reply[:120], "ok", res)
                return self.db.add_message(
                    conv_id,
                    "assistant",
                    reply,
                    provider="ryna_computer",
                    model=tool_name,
                    mode="offline" if tool_name not in ("web_search", "media_search") else mode,
                    metadata={"action_card": {"title": title, "subtitle": target, "value": "Done"}},
                )
            except Exception as exc:
                self.record_activity("tool", f"{tool_name}_error", str(exc), "error")
                return self.db.add_message(conv_id, "system", f"I couldn't complete that: {exc}", mode=mode)
            finally:
                self.set_robot_state("idle")

        # 1. Time / Date check (100% offline)
        if re.search(r"\b(what time is it|current time|check time|what('s| is) the time|what('s| is) today'?s? date|tell me the time)\b", low):
            res = self.tools.execute("current_time", {})
            reply = f"It's {res['time']} on {res['date']}."
            self.record_activity("tool", "current_time", reply, "ok")
            return self.db.add_message(
                conv_id, "assistant", reply, provider="ryna_tool", model="current_time", mode="offline",
                metadata={"action_card": {"title": "Local Time", "subtitle": res["date"], "value": res["time"]}},
            )

        # 2. Battery status (instant Win32 offline)
        if re.match(r"^(?:what(?:'s| is) )?(?:my |the )?battery(?: level| status| percentage| life)?$|^how much battery(?: do i have| is left)?$", low):
            return _exec_action("battery_status", {}, "battery", lambda r: str(r.get("detail", "Checked battery.")), "Battery")

        # 2b. System health / specs / RYNA Tech Doctor (100% offline)
        if re.match(r"^(?:show |tell me |what(?:'s| is) )?(?:my |the )?(?:system|computer|pc|laptop) (?:info|information|specs|details|health|status)$|^how much (?:ram|memory|disk) do i have$|^run (?:system )?(?:diagnostics|health check)$", low):
            return _exec_action("system_info", {}, "system_info", lambda r: str(r.get("summary", "System looks healthy.")), "System Health")
        if re.match(r"^(?:diagnose|analyze|check)\s+(?:errors?|logs?|issues?|problems?)$|^what went wrong$|^system doctor$", low):
            return _exec_action("tech_doctor", {}, "tech_doctor", lambda r: str(r.get("summary", "No errors found.")), "RYNA Doctor")

        # 2c. Offline Windows Screen OCR ("read my screen", "read text on my screen")
        if re.match(r"^(?:read|scan|ocr)(?: all)?(?: the)?(?: text)? (?:on|from|in) (?:my |the )?screen$|^(?:read|scan|ocr) (?:my |the )?screen$", low):
            return _exec_action(
                "ocr_screen", {"source": "screen"}, "screen_ocr",
                lambda r: "I couldn't find readable text on your screen." if r.get("empty") else f"On your screen I see: {str(r.get('text', ''))[:280]}",
                "Screen OCR",
            )

        # 2d. Timers & Reminders ("set a 5 minute timer", "remind me to ... in 10 minutes", "list my reminders")
        m_timer = re.match(r"^(?:set|start)\s+(?:a\s+)?(?:(\d+)\s*(seconds?|minutes?|hours?)\s+timer|timer\s+(?:for\s+)?(\d+)\s*(seconds?|minutes?|hours?))$", low)
        if m_timer:
            amt = int(m_timer.group(1) or m_timer.group(3))
            unit = (m_timer.group(2) or m_timer.group(4)).lower()
            if 1 <= amt <= 1000:
                sec = amt * (1 if unit.startswith("second") else 60 if unit.startswith("minute") else 3600)
                due_iso = (datetime.now(timezone.utc) + timedelta(seconds=sec)).isoformat()
                label = f"{amt}-{unit.rstrip('s')} timer"
                self.db.create_note(f"Timer: {label}", f"Your {label} is done.", remind_at=due_iso)
                reply = f"Timer set for {amt} {unit}."
                self.record_activity("system", "timer_set", reply, "ok")
                return self.db.add_message(conv_id, "assistant", reply, provider="ryna_system", model="timer", mode="offline")

        m_rem_in = re.match(r"^remind me (?:to\s+)?(.+?)\s+in\s+(\d+)\s*(seconds?|minutes?|hours?)$", stripped, re.IGNORECASE)
        if m_rem_in:
            what, amt, unit = m_rem_in.group(1).strip(), int(m_rem_in.group(2)), m_rem_in.group(3).lower()
            if 1 <= amt <= 1000:
                sec = amt * (1 if unit.startswith("second") else 60 if unit.startswith("minute") else 3600)
                due_iso = (datetime.now(timezone.utc) + timedelta(seconds=sec)).isoformat()
                self.db.create_note(what[:60], what, remind_at=due_iso)
                reply = f"Okay, I'll remind you to {what} in {amt} {unit}."
                self.record_activity("system", "reminder_set", reply, "ok")
                return self.db.add_message(conv_id, "assistant", reply, provider="ryna_system", model="reminder", mode="offline")

        if re.match(r"^(?:list|show|what are)(?: all)?(?: my)? reminders$|^what reminders do i have$", low):
            active = [n for n in self.db.list_notes(limit=50) if n.get("remind_at") and not n.get("completed")]
            reply = "You have no active reminders." if not active else "Your active reminders are: " + "; ".join(n["title"] for n in active[:6]) + "."
            return self.db.add_message(conv_id, "assistant", reply, provider="ryna_system", model="reminder", mode="offline")

        # 2e. Power control with strict two-step confirmation ("shut down computer", "confirm shutdown", "cancel shutdown")
        if re.match(r"^(?:cancel|stop|abort) (?:the )?(?:shutdown|restart|reboot|sign out)$", low):
            return _exec_action("power_control", {"mode": "cancel"}, "cancel_power", lambda r: str(r["message"]), "Power")
        if re.match(r"^(?:confirm|yes,? confirm) (?:the )?shutdown$", low):
            return _exec_action("power_control", {"mode": "confirm", "action": "shutdown"}, "confirm_shutdown", lambda r: str(r["message"]), "Shutdown")
        if re.match(r"^(?:confirm|yes,? confirm) (?:the )?(?:restart|reboot)$", low):
            return _exec_action("power_control", {"mode": "confirm", "action": "restart"}, "confirm_restart", lambda r: str(r["message"]), "Restart")
        if re.match(r"^(?:confirm|yes,? confirm) (?:the )?(?:sign out|log out|log off)$", low):
            return _exec_action("power_control", {"mode": "confirm", "action": "signout"}, "confirm_signout", lambda r: str(r["message"]), "Sign Out")
        if re.match(r"^(?:turn off|shut down|shutdown|power off)(?: my| the)? (?:computer|pc|laptop|windows)(?: now)?$", low):
            return _exec_action("power_control", {"mode": "request", "action": "shutdown"}, "request_shutdown", lambda r: str(r["message"]), "Confirm Shutdown")
        if re.match(r"^(?:restart|reboot)(?: my| the)? (?:computer|pc|laptop|windows)(?: now)?$", low):
            return _exec_action("power_control", {"mode": "request", "action": "restart"}, "request_restart", lambda r: str(r["message"]), "Confirm Restart")
        if re.match(r"^(?:sign out|log out|log off)(?: of windows| my computer| now)?$", low):
            return _exec_action("power_control", {"mode": "request", "action": "signout"}, "request_signout", lambda r: str(r["message"]), "Confirm Sign Out")

        # 2f. Active-app typing ("type hello world", "dictate ...")
        m_type = re.match(r"^(?:type|dictate)\s+(.+)$", stripped, re.IGNORECASE)
        if m_type and len(m_type.group(1).strip()) >= 2:
            txt_to_type = m_type.group(1).strip()
            return _exec_action("type_active_app", {"text": txt_to_type}, "active_app", lambda r: f"Typed that into {r.get('target_window', 'your active app')}.", "Dictate")

        # 2g. Live Weather ("weather in Kampala", "what's the weather in London")
        m_wx = re.match(r"^(?:what(?:'s| is) )?(?:the )?(?:weather|forecast)(?: like)?(?: today| now)?\s+(?:in|for|at)\s+(.+)$", stripped, re.IGNORECASE)
        if m_wx:
            city = m_wx.group(1).strip().rstrip(".!?")
            if not online:
                return self.db.add_message(conv_id, "system", f"I'm offline right now, so I can't check live weather for {city}.", mode="offline")
            return _exec_action("weather", {"city": city}, city, lambda r: str(r.get("summary", "Checked weather.")), f"Weather: {city}")

        # 2h. Safe User-Profile File Management (list, find, open project, read, recycle to Recycle Bin)
        m_flist = re.match(r"^(?:list|show)(?: me)?(?: the)?(?: files(?: in| on)?)?(?: my| the)?\s+(desktop|documents|downloads|pictures|workspace)(?: folder| files)?$", low)
        if m_flist:
            fld = m_flist.group(1)
            return _exec_action(
                "user_file_list", {"folder": fld}, fld,
                lambda r: f"{fld.title()} has {r['total']} item(s), including {', '.join(i['name'] for i in r['items'][:6])}." if r["total"] else f"Your {fld.title()} folder is empty.",
                f"Folder: {fld.title()}",
            )
        m_proj = re.match(r"^(?:open|find|show|go to)\s+(?:my |the )?(.{2,40}?)\s+(?:project|project folder)$", stripped, re.IGNORECASE)
        if m_proj:
            pname = m_proj.group(1).strip()
            return _exec_action("open_project", {"name": pname}, pname, lambda r: f"Opened project folder {r['project']}.", "Project Folder")
        m_ffind = re.match(r"^(?:find|locate|search for)\s+(?:my\s+|the\s+|a\s+)?(?:file|document|folder)?\s*(?:named|called)?\s*([\w .\-]{2,60})$", stripped, re.IGNORECASE)
        if m_ffind and not re.search(r"\b(web|online|youtube|spotify|weather|flights?)\b", low):
            q = m_ffind.group(1).strip()
            if q.lower() not in ("desktop", "documents", "downloads", "pictures"):
                return _exec_action(
                    "user_file_find", {"query": q}, q,
                    lambda r: f"Found {r['count']} match(es) for {q}: {', '.join(Path(p).name for p in r['matches'][:5])}." if r["count"] else f"I couldn't find any file named like {q} in your user folders.",
                    "File Search",
                )
        m_frec = re.match(r"^(?:recycle|trash|move to recycle bin)\s+(?:the\s+)?(?:file\s+)?(.+)$", stripped, re.IGNORECASE)
        if m_frec:
            fpath = m_frec.group(1).strip()
            return _exec_action("user_file_recycle", {"path": fpath}, fpath, lambda r: f"Moved {Path(r['path']).name} to the Recycle Bin.", "Recycle Bin")

        # 3. Remember fact/preference into persistent memory
        m_rem = re.match(r"^(?:remember|don'?t forget|keep in mind)\s+(?:that\s+)?(.+)$", stripped, re.IGNORECASE)
        if m_rem:
            fact = m_rem.group(1).strip().rstrip(".!")
            try:
                saved = self.memory.add("personal", fact, "confirmed_fact", source="conversation")
                reply = f"Got it. I'll remember that: {saved['content']}."
                self.record_activity("memory", "memory_saved", f"Remembered: {fact[:80]}", "ok")
                return self.db.add_message(
                    conv_id, "assistant", reply, provider="ryna_memory", model="memory", mode="offline",
                    metadata={"action_card": {"title": "Memory Saved", "subtitle": saved["category"], "value": saved["content"]}},
                )
            except Exception as exc:
                return self.db.add_message(conv_id, "system", f"Could not save memory: {exc}", mode=mode)

        # 4. Volume controls ("volume up", "volume down", "mute", "set volume to 50")
        m_vol_set = re.match(r"^(?:set |change |put )?(?:the )?(?:volume|sound)(?: level)?(?: to| at)?\s+(\d{1,3})\s*(?:%|percent)?$", low)
        if m_vol_set:
            lvl = max(0, min(100, int(m_vol_set.group(1))))
            return _exec_action("volume_control", {"level": lvl}, f"volume:{lvl}", lambda _r: f"Volume set to about {lvl} percent.", "Volume")
        m_vol = re.match(r"^(?:turn |make )?(?:the )?(?:volume|sound)\s+(up|down)(?:\s+a\s+(?:bit|little))?$", low) or re.match(r"^(?:turn )?(?:it )?(up|down)\s+(?:the )?(?:volume|sound)$", low)
        if m_vol:
            act = m_vol.group(1)
            return _exec_action("volume_control", {"action": act, "steps": 5}, f"volume:{act}", lambda _r: f"Turned the volume {act}.", "Volume")
        if re.match(r"^(?:mute|unmute)(?: (?:the|my))?(?: volume| sound| computer| pc| laptop)?$", low):
            return _exec_action("volume_control", {"action": "mute"}, "volume:mute", lambda _r: "Toggled mute.", "Volume")

        # 5. Brightness controls ("brightness up/down", "set brightness to 70")
        m_br_set = re.match(r"^(?:set |change |put )?(?:the )?(?:screen )?brightness(?: to| at)?\s+(\d{1,3})\s*(?:%|percent)?$", low)
        if m_br_set:
            lvl = max(0, min(100, int(m_br_set.group(1))))
            return _exec_action("brightness_control", {"mode": "set", "value": lvl}, f"brightness:{lvl}", lambda _r: f"Screen brightness set to {lvl} percent.", "Brightness")
        m_br = re.match(r"^(?:turn |make )?(?:the )?(?:screen )?brightness\s+(up|down)$", low) or re.match(r"^(increase|raise|decrease|lower|reduce|dim)\s+(?:the\s+)?(?:screen\s+)?brightness$", low)
        if m_br:
            act = "up" if m_br.group(1) in ("up", "increase", "raise") else "down"
            return _exec_action("brightness_control", {"mode": act}, f"brightness:{act}", lambda _r: f"Turned screen brightness {act}.", "Brightness")

        # 6. Desktop shortcuts ("show desktop", "switch window", "lock screen")
        if re.match(r"^(?:show (?:the )?desktop|minimi[sz]e (?:all )?(?:the )?(?:windows|everything))$", low):
            return _exec_action("computer_shortcut", {"action": "show_desktop"}, "show_desktop", lambda _r: "Showing your desktop.", "Desktop")
        if re.match(r"^(?:switch|change) (?:the )?(?:app|window|windows)$", low):
            return _exec_action("computer_shortcut", {"action": "switch_window"}, "switch_window", lambda _r: "Switching windows.", "Window Switch")
        if re.match(r"^lock(?: my| the)?\s+(?:computer|pc|laptop|screen|windows|workstation)$", low):
            return _exec_action("computer_shortcut", {"action": "lock_screen"}, "lock_screen", lambda _r: "Locking your screen.", "Lock Screen")

        # 7. Open Windows Settings pages ("open wifi settings", "open bluetooth settings", etc.)
        m_set = re.match(r"^open(?: the)?\s+([a-z -]{3,20}?)\s+settings$", low)
        if m_set:
            page = m_set.group(1).strip()
            return _exec_action("open_settings_page", {"page": page}, page, lambda r: f"Opened {r.get('page', page)} settings.", "Windows Settings")

        # 8. Open common folders ("open downloads", "open documents", "open this pc", "open recycle bin")
        m_fold = re.match(r"^(?:open|show|go to)\s+(?:my |the )?(this pc|my pc|my computer|recycle bin|trash|desktop|documents|downloads|pictures|workspace)(?: folder)?$", low)
        if m_fold:
            f = m_fold.group(1)
            return _exec_action("open_folder", {"folder": f}, f, lambda r: f"Opened {r.get('folder', f)}.", "Folder")

        # 9. Clipboard read / copy
        if re.match(r"^(?:what(?:'s| is) (?:on|in) (?:my |the )?clipboard|read (?:my |the )?clipboard)$", low):
            return _exec_action(
                "clipboard_get", {}, "clipboard",
                lambda r: "Your clipboard is empty." if r.get("empty") else f"Your clipboard says: {str(r.get('text', ''))[:180]}",
                "Clipboard",
            )
        m_clip = re.match(r"^copy\s+(.+?)\s+to (?:the |my )?clipboard$", stripped, re.IGNORECASE)
        if m_clip:
            clip_txt = m_clip.group(1).strip().strip("\"'")
            return _exec_action("clipboard_set", {"text": clip_txt}, "clipboard", lambda _r: "Copied that to your clipboard.", "Clipboard")

        # 10. YouTube / Spotify / Maps search
        m_yt = re.match(r"^(?:play|search|find|watch|open|show me)\s+(.+?)\s+on youtube$", stripped, re.IGNORECASE) or re.match(r"^(?:search )?youtube(?: for)?\s+(.+)$", stripped, re.IGNORECASE)
        if m_yt:
            q = m_yt.group(1).strip()
            return _exec_action("media_search", {"platform": "youtube", "query": q}, q, lambda _r: f"Opened YouTube search for {q}.", "YouTube")
        m_sp = re.match(r"^(?:(?:open|launch) spotify and )?(?:play|search spotify for)\s+(?:me\s+)?(.+?)(?:\s+on spotify)?$", stripped, re.IGNORECASE)
        if m_sp and len(m_sp.group(1).strip()) <= 100 and not re.search(r"\b(game|video)\b", low):
            q = m_sp.group(1).strip()
            return _exec_action("media_search", {"platform": "spotify", "query": q}, q, lambda _r: f"Opened Spotify search for {q}.", "Spotify")
        m_map = re.match(r"^(?:show me |get |give me )?(?:directions to|a map of|map of|navigate to)\s+(.+)$", stripped, re.IGNORECASE)
        if m_map:
            q = m_map.group(1).strip()
            return _exec_action("media_search", {"platform": "maps", "query": q}, q, lambda _r: f"Opened directions to {q}.", "Maps")

        # 11. List installed apps
        if re.match(r"^(?:list|show|what are)\s+(?:my |the |installed )?(?:installed )?apps?$|^what apps can you open$", low):
            return _exec_action(
                "list_apps", {}, "installed_apps",
                lambda r: f"I found {r.get('count', 0)} installed apps, including {', '.join(r.get('apps', [])[:8])}.",
                "Installed Apps",
            )

        # 12. Open ANY Windows application (uses RYNA's fast physical Search-bar walk-and-open when active!)
        m_open = re.match(r"^(?:open|launch|start)\s+(?:my\s+|the\s+)?([\w .&()'+\-]{1,50}?)(?:\s+app|\s+application)?$", low)
        if m_open:
            app_name = m_open.group(1).strip()
            looks_like_sentence = bool(re.search(r"\b(?:and|then)\s+(?:tell|check|show|read|let|see|search|play|find|open|send)\b|\b(?:if|whether)\s+(?:i|there|you)\b|\bunread\b", app_name))
            is_non_app = app_name in ("up", "it", "that", "this", "a", "the", "walking", "listening")
            if not looks_like_sentence and not is_non_app:
                dec = self.permissions.evaluate("open_application", app_name)
                if not dec.allowed:
                    return self.db.add_message(conv_id, "system", f"Launching {app_name} blocked: {dec.reason}", mode=mode)
                self.set_robot_state("working")
                try:
                    use_physical = bool(self.db.get_setting("desktop.physical_app_search", True)) and callable(self.physical_app_launcher)
                    if use_physical:
                        res = self.physical_app_launcher(app_name)  # type: ignore[misc]
                    else:
                        res = self.tools.execute("open_application", {"app": app_name})
                    shown_name = str(res.get("app") or app_name).strip()
                    reply = f"Opening {shown_name} now."
                    self.record_activity("tool", "open_application", f"Launched {shown_name} ({res.get('method', 'app')}).", "ok", res)
                    return self.db.add_message(
                        conv_id, "assistant", reply, provider="ryna_computer", model="open_application", mode="offline",
                        metadata={"action_card": {"title": f"Opened {shown_name}", "subtitle": res.get("method", "Windows App"), "value": "Launched"}},
                    )
                except Exception as exc:
                    self.record_activity("tool", "open_application_error", str(exc), "error")
                    return self.db.add_message(conv_id, "system", f"I couldn't open {app_name}: {exc}", mode=mode)
                finally:
                    self.set_robot_state("idle")

        # 13. Close an open Windows app politely ("close notepad", "close word", "close chrome")
        m_close = re.match(r"^(?:close|quit|exit)\s+(?:my\s+|the\s+)?([\w .&()'+\-]{2,50}?)(?:\s+(?:app|application|program|window))?$", low)
        if m_close:
            target_app = m_close.group(1).strip()
            skip_close = {"it", "that", "this", "everything", "all", "ryna", "listening", "voice", "talking", "speaking", "settings", "chat"}
            if target_app not in skip_close:
                return _exec_action("close_application", {"app": target_app}, target_app, lambda r: f"Closed {r.get('app', target_app)}.", "Closed App")

        # 14. Screen capture / inspect screen ("take a screenshot", "look at my screen")
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

        # 15. Live web search ("search the web for ...", "google ...", "look up ...")
        m_search = re.match(r"^(?:search(?:\s+the)?\s+(?:web|internet|google)\s+for|web\s+search|search\s+for|google|look\s+up)\s+(.+)$", stripped, re.IGNORECASE)
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

        # 16. Direct PowerShell / Terminal command execution ("run command ...", "powershell ...", "terminal ...")
        m_cmd = re.match(r"^(?:run\s+(?:command|powershell|cmd|script)|execute|powershell|cmd|terminal)\s+(.+)$", stripped, re.IGNORECASE)
        if m_cmd:
            cmd_to_run = m_cmd.group(1).strip()
            return _exec_action(
                "execute_command", {"command": cmd_to_run}, cmd_to_run,
                lambda r: f"Command executed:\n```\n{str(r.get('output', 'Success')).strip()[:1800]}\n```" if r.get("output") else "Command executed successfully.",
                "PowerShell Command",
            )

        # 17. Direct keyboard hotkeys and key presses ("press enter", "press alt f4", "press ctrl c")
        m_press = re.match(r"^(?:press|hit)\s+(?:key\s+|keys\s+)?([a-z0-9\+\- ]+)$", stripped, re.IGNORECASE)
        if m_press:
            key_expr = m_press.group(1).strip().lower()
            if any(k in key_expr for k in ("+", "ctrl", "alt", "win", "shift")):
                return _exec_action("hotkey", {"combo": key_expr}, key_expr, lambda r: f"Pressed hotkey {r.get('hotkey', key_expr)}.", "Hotkey")
            return _exec_action("press_key", {"key": key_expr}, key_expr, lambda r: f"Pressed {r.get('key', key_expr)}.", "Keyboard")

        # 18. Direct mouse click and move ("click at 500, 400", "move mouse to 100, 200", "double click")
        m_click_xy = re.match(r"^(?:mouse\s+)?click(?:\s+at)?\s+(\d+)\s*,\s*(\d+)$", stripped, re.IGNORECASE)
        if m_click_xy:
            cx, cy = int(m_click_xy.group(1)), int(m_click_xy.group(2))
            return _exec_action("mouse_click", {"x": cx, "y": cy}, f"({cx}, {cy})", lambda r: f"Clicked at ({r['x']}, {r['y']}).", "Mouse Click")
        m_move_xy = re.match(r"^(?:move\s+mouse\s+to|cursor\s+to)\s+(\d+)\s*,\s*(\d+)$", stripped, re.IGNORECASE)
        if m_move_xy:
            mx, my = int(m_move_xy.group(1)), int(m_move_xy.group(2))
            return _exec_action("mouse_move", {"x": mx, "y": my}, f"({mx}, {my})", lambda r: f"Moved cursor to ({r['x']}, {r['y']}).", "Mouse Move")
        if re.match(r"^(?:double\s+click|right\s+click|left\s+click)$", low):
            b_type = "double" if "double" in low else ("right" if "right" in low else "left")
            return _exec_action("mouse_click", {"button": b_type}, b_type, lambda r: f"Performed {b_type} click.", "Mouse Click")

        # 19. Direct file creation ("create file hello.txt with content world")
        m_fcreate = re.match(r"^(?:create|make|write)\s+(?:a\s+)?(?:new\s+)?file\s+(?:named\s+|called\s+|at\s+)?([^\s]+)\s+with\s+content\s+(.+)$", stripped, re.IGNORECASE)
        if m_fcreate:
            f_path, f_content = m_fcreate.group(1).strip(), m_fcreate.group(2).strip()
            return _exec_action("file_write", {"path": f_path, "content": f_content}, f_path, lambda r: f"Created file {Path(r.get('path', f_path)).name} successfully.", "Create File")

        # 20. Running processes ("list running processes", "what processes are running")
        if re.match(r"^(?:list|show|what are)(?: the)?(?: running)? (?:processes|tasks|programs|running apps)$", low):
            return _exec_action(
                "list_processes", {"limit": 10}, "processes",
                lambda r: f"Running processes include: " + ", ".join(f"{p['name']} ({p['memory_mb']}MB)" for p in r.get("processes", [])[:7]) + ".",
                "Running Processes",
            )

        return None

    def _execute_ai_device_actions(self, content: str, conv_id: str, mode: str) -> tuple[str, list[dict[str, Any]]]:
        """Detect and execute Windows device actions invoked by Ollama or Cloud AI models."""
        executed_actions: list[dict[str, Any]] = []
        text = content

        action_pattern = re.compile(r"\[ACTION:\s*([a-zA-Z0-9_-]+)\s*\(([\s\S]*?)\)\]", re.IGNORECASE)
        json_pattern = re.compile(r"```(?:json)?\s*\{\s*\"action\"\s*:\s*\"([a-zA-Z0-9_-]+)\"\s*,\s*\"args\"\s*:\s*(\{[\s\S]*?\})\s*\}\s*```", re.IGNORECASE)

        matches = list(action_pattern.finditer(text))
        if not matches:
            matches = list(json_pattern.finditer(text))

        if not matches:
            return text, executed_actions

        self.set_robot_state("working")
        try:
            for m in matches:
                full_tag = m.group(0)
                tool_name = m.group(1).strip()
                raw_args = m.group(2).strip()
                args: dict[str, Any] = {}
                if raw_args:
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        try:
                            import ast
                            args = ast.literal_eval(raw_args)
                        except Exception:
                            if tool_name in ("execute_command", "run_command", "run_powershell", "shell_command"):
                                args = {"command": raw_args.strip("\"'")}
                            elif tool_name in ("open_application", "close_application"):
                                args = {"app": raw_args.strip("\"'")}
                            elif tool_name in ("file_read", "file_search"):
                                args = {"path": raw_args.strip("\"'")}
                            else:
                                args = {"param": raw_args.strip("\"'")}

                if not isinstance(args, dict):
                    args = {"input": args}

                target_desc = str(args.get("command") or args.get("app") or args.get("path") or args.get("text") or tool_name)
                dec = self.permissions.evaluate(tool_name, target_desc)
                if not dec.allowed:
                    block_msg = f"\n[Action `{tool_name}` blocked by permission policy: {dec.reason}]"
                    text = text.replace(full_tag, block_msg)
                    self.record_activity("tool", f"{tool_name}_blocked", dec.reason, "warn")
                    executed_actions.append({"tool": tool_name, "success": False, "reason": dec.reason})
                    continue

                try:
                    res = self.tools.execute(tool_name, args)
                    executed_actions.append({"tool": tool_name, "success": True, "result": res})
                    self.record_activity("tool", tool_name, f"AI executed '{tool_name}' on Windows device.", "ok", res)

                    replacement = ""
                    if tool_name in ("execute_command", "run_command", "run_powershell", "shell_command"):
                        output_str = str(res.get("output", "")).strip()
                        cmd_str = str(res.get("command", "")).strip()
                        replacement = f"\n`Command executed: {cmd_str}`\n```\n{output_str[:2500]}\n```" if output_str else "\n`Command executed successfully.`"
                    elif tool_name == "open_application":
                        app_name = res.get("app", args.get("app", "app"))
                        replacement = f"\n`Launched application: {app_name}`"
                    elif tool_name == "close_application":
                        app_name = res.get("app", args.get("app", "app"))
                        replacement = f"\n`Closed application: {app_name}`"
                    elif tool_name in ("file_write", "create_file"):
                        path_str = res.get("path", args.get("path", "file"))
                        replacement = f"\n`File created: {path_str}`"
                    elif tool_name == "file_read":
                        content_str = str(res.get("content", ""))[:2000]
                        replacement = f"\n`File contents ({res.get('path')}):`\n```\n{content_str}\n```"
                    elif tool_name in ("mouse_click", "mouse_move", "press_key", "hotkey", "type_text"):
                        replacement = f"\n`Action performed: {tool_name}`"
                    else:
                        replacement = f"\n[Tool: {tool_name}]\n```json\n{json.dumps(res, indent=2)[:1500]}\n```"

                    text = text.replace(full_tag, replacement)
                except Exception as exc:
                    err_msg = f"\n[Action `{tool_name}` failed: {exc}]"
                    text = text.replace(full_tag, err_msg)
                    self.record_activity("tool", f"{tool_name}_error", str(exc), "error")
                    executed_actions.append({"tool": tool_name, "success": False, "error": str(exc)})

            clean_check = re.sub(r"`[^`]*`|\[[^\]]*\]|```[\s\S]*?```", "", text).strip()
            if not clean_check:
                summaries = []
                for act in executed_actions:
                    if act["success"]:
                        t = act["tool"]
                        if t in ("execute_command", "run_command", "run_powershell"):
                            summaries.append("I ran the command on your computer.")
                        elif t == "open_application":
                            summaries.append("I opened that application for you.")
                        elif t in ("file_write", "create_file"):
                            summaries.append("I created the file on your device.")
                        elif t in ("mouse_click", "press_key", "hotkey", "type_text"):
                            summaries.append("I completed the computer action.")
                        else:
                            summaries.append(f"Completed {t}.")
                if summaries:
                    text = " ".join(summaries) + "\n" + text
        finally:
            self.set_robot_state("idle")

        return text.strip(), executed_actions

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

            # Process device actions emitted by Ollama or Cloud AI models
            processed_content, executed_actions = self._execute_ai_device_actions(final_resp.content, conv_id, mode)
            meta: dict[str, Any] = {
                "latency_ms": final_resp.latency_ms,
                "memories_used": [m["id"] for m in mems],
                "device_actions": executed_actions,
            }
            if executed_actions:
                top_act = executed_actions[0]
                meta["action_card"] = {
                    "title": f"Device Action: {top_act['tool']}",
                    "subtitle": "Success" if top_act.get("success") else "Blocked/Failed",
                    "value": "Executed",
                }

            saved = self.db.add_message(conv_id, "assistant", processed_content, provider=final_resp.provider, model=final_resp.model, mode="offline" if final_resp.is_local else "online", metadata=meta)
            self.record_activity("ai", "ai_response", f"{final_resp.provider}/{final_resp.model} responded in {final_resp.latency_ms} ms" + (f" ({len(executed_actions)} action(s) executed)." if executed_actions else "."), "ok")
            yield {"event": "done", "data": {"conversation_id": conv_id, "assistant_message": saved, "ai_available": True, "provider": final_resp.provider, "model": final_resp.model, "latency_ms": final_resp.latency_ms, "device_actions": executed_actions}}
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
