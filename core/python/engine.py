"""Unified RYNA core engine integrating database, AI, memory, tasks, permissions, tools, and voice."""

from __future__ import annotations

import logging
from typing import Any, Callable

from core.python.config import RynaConfig
from core.python.database import RynaDatabase, new_id
from core.python.hardware import check_internet_connectivity, detect_hardware
from core.python.logging_setup import setup_logging
from core.python.version import APP_NAME, PROTOCOL_VERSION, VERSION
from services.agents.permissions import PermissionEngine
from services.agents.task_engine import TaskEngine
from services.ai.base import ChatMessage, ProviderError
from services.ai.router import AIRouter
from services.memory.store import MemoryStore
from services.tools.local_tools import LocalToolRegistry
from services.voice.pipeline import VoicePipeline


class RynaEngine:
    """Single source of truth for RYNA runtime state across online and offline operation."""

    def __init__(
        self,
        config: RynaConfig | None = None,
        connectivity_checker: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config or RynaConfig.load()
        self.logger: logging.Logger = setup_logging(self.config.log_dir, self.config.log_level)
        self._check_online = connectivity_checker or check_internet_connectivity

        self.db = RynaDatabase(self.config.db_path, self.config.default_settings)
        self.memory = MemoryStore(self.db)
        self.permissions = PermissionEngine(self.db)
        self.tools = LocalToolRegistry(self.config, self.db)
        self.tasks = TaskEngine(self.db, self.permissions, self.tools, connectivity_checker=self._check_online)
        self.ai = AIRouter(self.config, self.db, connectivity_checker=self._check_online)
        self.voice = VoicePipeline(self.db)

        self.recovered_tasks = self.tasks.recover_after_restart()
        if self.recovered_tasks:
            self.logger.warning("Recovered %d interrupted task(s) on startup.", len(self.recovered_tasks))
        self.logger.info("%s %s engine initialized (db=%s)", APP_NAME, VERSION, self.config.db_path)

    def close(self) -> None:
        self.logger.info("Shutting down %s %s engine.", APP_NAME, VERSION)
        self.db.close()
        for handler in list(self.logger.handlers):
            try:
                handler.flush()
                handler.close()
            finally:
                self.logger.removeHandler(handler)

    def status_summary(self) -> dict[str, Any]:
        online = self._check_online()
        ai_status = self.ai.provider_statuses(online=online)
        hw = detect_hardware(self.config.project_root, self.config.hardware_model_tiers)
        return {
            "app": APP_NAME,
            "version": VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "online": online,
            "mode": "online" if online else "offline",
            "companion_state": self.voice.get_state(),
            "emergency_stopped": self.permissions.emergency_stopped,
            "recovered_tasks_on_boot": self.recovered_tasks,
            "ai": ai_status,
            "voice": self.voice.status(),
            "hardware": hw.to_dict(),
            "database": self.db.stats(),
        }

    def emergency_stop(self, reason: str = "User triggered emergency stop") -> dict[str, Any]:
        audit = self.permissions.trigger_emergency_stop(reason)
        halted = self.tasks.halt_all_active()
        self.voice.interrupt(reason="Emergency stop activated")
        self.voice.set_state("alert")
        return {"emergency_stopped": True, "halted_tasks": halted, "audit": audit}

    def reset_emergency_stop(self) -> dict[str, Any]:
        audit = self.permissions.reset_emergency_stop()
        self.voice.set_state("idle")
        return {"emergency_stopped": False, "audit": audit}

    def send_chat(
        self,
        conversation_id: str | None,
        user_text: str,
        requested_provider: str | None = None,
        requested_model: str | None = None,
    ) -> dict[str, Any]:
        text = user_text.strip()
        if not text:
            raise ValueError("Message content cannot be empty.")

        if not conversation_id or not self.db.get_conversation(conversation_id):
            conv = self.db.create_conversation()
            conversation_id = conv["id"]

        online = self._check_online()
        mode = "online" if online else "offline"

        user_msg = self.db.add_message(
            conversation_id=conversation_id,
            role="user",
            content=text,
            mode=mode,
        )

        # Support explicit local slash commands executed by real local tools (clearly labeled as tools, never fake AI)
        if text.startswith("/"):
            tool_reply = self._try_local_command(text, conversation_id, mode)
            if tool_reply is not None:
                return {
                    "conversation_id": conversation_id,
                    "user_message": user_msg,
                    "assistant_message": tool_reply,
                    "ai_available": True,
                    "used_local_tool": True,
                }

        # Build prompt context with real persistent memories + recent conversation turns
        relevant_memories = self.memory.retrieve_relevant(text, limit=6)
        system_content = self.config.system_prompt
        if relevant_memories:
            mem_lines = [
                f"- [{m['category']}/{m['memory_type']}] {m['content']}"
                for m in relevant_memories
            ]
            system_content += "\n\nRelevant persistent memories about the user and workspace:\n" + "\n".join(mem_lines)

        history = self.db.list_messages(conversation_id, limit=24)
        chat_messages = [ChatMessage(role="system", content=system_content)]
        for msg in history:
            if msg["role"] in ("user", "assistant"):
                chat_messages.append(ChatMessage(role=msg["role"], content=msg["content"]))

        gen_id = new_id("gen")
        self.voice.begin_generation(gen_id)
        try:
            ai_resp = self.ai.chat(
                messages=chat_messages,
                requested_provider=requested_provider,
                requested_model=requested_model,
                online_override=online,
            )
            if self.voice.is_interrupted():
                interrupted_msg = self.db.add_message(
                    conversation_id=conversation_id,
                    role="system",
                    content="Response generation was interrupted by the user.",
                    mode=mode,
                    metadata={"interrupted": True},
                )
                return {
                    "conversation_id": conversation_id,
                    "user_message": user_msg,
                    "assistant_message": interrupted_msg,
                    "ai_available": True,
                    "interrupted": True,
                }

            assistant_msg = self.db.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=ai_resp.content,
                provider=ai_resp.provider,
                model=ai_resp.model,
                mode="offline" if ai_resp.is_local else "online",
                metadata={
                    "latency_ms": ai_resp.latency_ms,
                    "usage": ai_resp.usage,
                    "memories_used": [m["id"] for m in relevant_memories],
                },
            )
            return {
                "conversation_id": conversation_id,
                "user_message": user_msg,
                "assistant_message": assistant_msg,
                "ai_available": True,
                "provider": ai_resp.provider,
                "model": ai_resp.model,
                "memories_used": len(relevant_memories),
            }
        except ProviderError as exc:
            # Strict Rule 2 & 5: NEVER fake an AI response. Report honest status and setup guidance.
            notice = (
                "AI Provider Unavailable\n\n"
                f"Reason: {exc}\n\n"
                "Your message has been saved locally in SQLite. To enable real AI responses:\n"
                "• Offline AI: Start Ollama and pull a local model (e.g. `ollama pull qwen2.5:3b`), then select it in Settings → Offline AI.\n"
                "• Cloud AI: Set an API key in `.env` (OpenAI, Anthropic, or Gemini) and enable Cloud AI in Settings → Online AI.\n"
                "• Local Tools (work offline right now): try `/calc 24 * 7`, `/diag`, `/find README`, or `/note Remember to pull Ollama model`."
            )
            sys_msg = self.db.add_message(
                conversation_id=conversation_id,
                role="system",
                content=notice,
                provider=None,
                model=None,
                mode=mode,
                metadata={"ai_unavailable": True, "error": str(exc)},
            )
            return {
                "conversation_id": conversation_id,
                "user_message": user_msg,
                "assistant_message": sys_msg,
                "ai_available": False,
                "error": str(exc),
            }
        finally:
            self.voice.end_generation(gen_id)

    def _try_local_command(self, text: str, conversation_id: str, mode: str) -> dict[str, Any] | None:
        parts = text.split(" ", 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        mapping = {
            "/calc": ("calculate", {"expression": arg}),
            "/diag": ("system_diagnostics", {}),
            "/find": ("file_search", {"query": arg}),
            "/read": ("file_read", {"path": arg}),
            "/note": ("create_note", {"title": arg[:48] or "Quick Note", "content": arg}),
        }
        if cmd not in mapping:
            return None

        tool_name, tool_args = mapping[cmd]
        decision = self.permissions.evaluate(action_type=tool_name, target=arg or cmd)
        if not decision.allowed:
            return self.db.add_message(
                conversation_id=conversation_id,
                role="system",
                content=f"Local tool `{tool_name}` blocked: {decision.reason}",
                provider="local_tool",
                model=tool_name,
                mode="offline",
                metadata={"tool_blocked": True},
            )

        self.voice.set_state("working")
        try:
            res = self.tools.execute(tool_name, tool_args)
            import json

            formatted = f"[Local Tool: {tool_name}]\n```json\n{json.dumps(res, indent=2)}\n```"
            return self.db.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=formatted,
                provider="local_tool",
                model=tool_name,
                mode="offline",
                metadata={"tool_name": tool_name, "tool_result": res},
            )
        except Exception as exc:
            return self.db.add_message(
                conversation_id=conversation_id,
                role="system",
                content=f"[Local Tool Error: {tool_name}] {exc}",
                provider="local_tool",
                model=tool_name,
                mode="offline",
                metadata={"tool_error": str(exc)},
            )
        finally:
            self.voice.set_state("idle")
