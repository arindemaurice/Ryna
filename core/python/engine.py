"""Unified RYNA core engine with streaming AI, real-time SSE event bus, voice, memory, tasks, and activity logging."""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Callable, Iterator

from core.python.config import RynaConfig
from core.python.database import RynaDatabase, new_id
from core.python.hardware import check_internet_connectivity, detect_hardware
from core.python.logging_setup import setup_logging
from core.python.version import APP_NAME, PROTOCOL_VERSION, VERSION
from services.agents.permissions import PermissionEngine
from services.agents.task_engine import TaskEngine
from services.ai.base import AIResponse, ChatMessage, ProviderError
from services.ai.router import AIRouter
from services.memory.store import MemoryStore
from services.tools.local_tools import LocalToolRegistry
from services.voice.pipeline import VoicePipeline


class EventBus:
    """Thread-safe pub/sub event bus pushing real engine state updates to SSE subscribers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        msg = {"type": event_type, "payload": payload}
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass


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
        self.events = EventBus()

        self.db = RynaDatabase(self.config.db_path, self.config.default_settings)
        self.memory = MemoryStore(self.db)
        self.permissions = PermissionEngine(self.db)
        self.tools = LocalToolRegistry(self.config, self.db)
        self.tasks = TaskEngine(self.db, self.permissions, self.tools, connectivity_checker=self._check_online)
        self.ai = AIRouter(self.config, self.db, connectivity_checker=self._check_online)
        self.voice = VoicePipeline(self.db)

        self.recovered_tasks = self.tasks.recover_after_restart()
        if self.recovered_tasks:
            self.record_activity(
                "task",
                "task_recovery",
                f"Recovered {len(self.recovered_tasks)} interrupted task(s) on startup.",
                status="warn",
                details={"tasks": self.recovered_tasks},
            )
            self.logger.warning("Recovered %d interrupted task(s) on startup.", len(self.recovered_tasks))
        self.logger.info("%s %s engine initialized (db=%s)", APP_NAME, VERSION, self.config.db_path)

    def close(self) -> None:
        self.voice.stop_native_speech()
        self.logger.info("Shutting down %s %s engine.", APP_NAME, VERSION)
        self.db.close()
        for handler in list(self.logger.handlers):
            try:
                handler.flush()
                handler.close()
            finally:
                self.logger.removeHandler(handler)

    def record_activity(
        self,
        category: str,
        event_type: str,
        summary: str,
        status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evt = self.db.record_activity(category, event_type, summary, status=status, details=details)
        self.events.publish("activity", evt)
        return evt

    def set_companion_state(self, state: str) -> str:
        new_state = self.voice.set_state(state)
        self.events.publish("companion_state", {"state": new_state})
        return new_state

    def status_summary(self) -> dict[str, Any]:
        online = self._check_online()
        op_mode = self.ai.operating_mode(online=online)
        hw = detect_hardware(self.config.project_root, self.config.hardware_model_tiers)
        voice_status = self.voice.status()

        # Reflect offline state unobtrusively when idle and disconnected with no local AI
        companion_state = voice_status["state"]
        if self.permissions.emergency_stopped:
            companion_state = "error"
        elif companion_state == "idle" and not online and not op_mode["local_ai_available"]:
            companion_state = "offline"

        return {
            "app": APP_NAME,
            "version": VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "online": online,
            "mode": "online" if online else "offline",
            "operating_mode": {
                "id": op_mode["mode_id"],
                "label": op_mode["label"],
                "local_ai_available": op_mode["local_ai_available"],
                "cloud_ai_available": op_mode["cloud_ai_available"],
                "active_provider": op_mode["active_provider"],
                "active_model": op_mode["active_model"],
            },
            "companion_state": companion_state,
            "emergency_stopped": self.permissions.emergency_stopped,
            "recovered_tasks_on_boot": self.recovered_tasks,
            "ai": op_mode["status"],
            "voice": voice_status,
            "hardware": hw.to_dict(),
            "database": self.db.stats(),
        }

    def emergency_stop(self, reason: str = "User triggered emergency stop") -> dict[str, Any]:
        audit = self.permissions.trigger_emergency_stop(reason)
        halted = self.tasks.halt_all_active()
        self.voice.interrupt(reason="Emergency stop activated")
        self.set_companion_state("error")
        self.record_activity("system", "emergency_stop", f"Emergency stop triggered ({halted} task(s) paused).", status="error", details={"reason": reason})
        self.events.publish("emergency_stop", {"emergency_stopped": True, "halted_tasks": halted})
        return {"emergency_stopped": True, "halted_tasks": halted, "audit": audit}

    def reset_emergency_stop(self) -> dict[str, Any]:
        audit = self.permissions.reset_emergency_stop()
        self.set_companion_state("idle")
        self.record_activity("system", "emergency_stop_reset", "Emergency stop cleared by user.", status="ok")
        self.events.publish("emergency_stop", {"emergency_stopped": False})
        return {"emergency_stopped": False, "audit": audit}

    def _prepare_chat_context(
        self,
        conversation_id: str | None,
        user_text: str,
        attachments: list[dict[str, Any]] | None = None,
        skip_user_insert: bool = False,
    ) -> tuple[str, dict[str, Any] | None, list[ChatMessage], list[dict[str, Any]], bool]:
        text = user_text.strip()
        clean_attachments: list[dict[str, Any]] = []
        for att in (attachments or [])[:5]:
            name = str(att.get("name", "attachment.txt")).strip()[:120]
            content = str(att.get("content", ""))[:24000]
            size = int(att.get("size", len(content)))
            mime = str(att.get("mime", "text/plain"))
            if name and content:
                clean_attachments.append({"name": name, "content": content, "size": size, "mime": mime})

        if not text and not clean_attachments:
            raise ValueError("Message content cannot be empty.")

        if not conversation_id or not self.db.get_conversation(conversation_id):
            conv = self.db.create_conversation()
            conversation_id = conv["id"]

        online = self._check_online()
        mode = "online" if online else "offline"

        user_msg: dict[str, Any] | None = None
        if not skip_user_insert:
            att_meta = [{"name": a["name"], "size": a["size"], "mime": a["mime"]} for a in clean_attachments]
            display_content = text if text else f"[Attached {len(clean_attachments)} file(s)]"
            user_msg = self.db.add_message(
                conversation_id=conversation_id,
                role="user",
                content=display_content,
                mode=mode,
                metadata={"attachments": att_meta} if att_meta else {},
            )
            if clean_attachments:
                self.record_activity(
                    "file",
                    "file_attach",
                    f"Attached {len(clean_attachments)} file(s): {', '.join(a['name'] for a in clean_attachments)}",
                    status="ok",
                    details={"conversation_id": conversation_id, "files": att_meta},
                )

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
        for idx, msg in enumerate(history):
            if msg["role"] not in ("user", "assistant"):
                continue
            content_for_llm = msg["content"]
            # Append actual attachment text to the latest user turn
            if idx == len(history) - 1 and msg["role"] == "user" and clean_attachments:
                att_blocks = [
                    f"\n\n--- Attached File: {a['name']} ({a['size']} bytes) ---\n{a['content']}\n--- End of {a['name']} ---"
                    for a in clean_attachments
                ]
                content_for_llm += "".join(att_blocks)
            chat_messages.append(ChatMessage(role=msg["role"], content=content_for_llm))

        return conversation_id, user_msg, chat_messages, relevant_memories, online

    def send_chat(
        self,
        conversation_id: str | None,
        user_text: str,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        skip_user_insert: bool = False,
    ) -> dict[str, Any]:
        conversation_id, user_msg, chat_messages, relevant_memories, online = self._prepare_chat_context(
            conversation_id, user_text, attachments=attachments, skip_user_insert=skip_user_insert
        )
        mode = "online" if online else "offline"
        text = user_text.strip()

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

        gen_id = new_id("gen")
        self.voice.begin_generation(gen_id)
        self.events.publish("companion_state", {"state": "processing"})
        self.record_activity("ai", "ai_request", f"Sent request to AI router ({len(chat_messages) - 1} turn(s)).", status="info")

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
            self.record_activity(
                "ai",
                "ai_response",
                f"Response from {ai_resp.provider}/{ai_resp.model} in {ai_resp.latency_ms} ms.",
                status="ok",
                details={"provider": ai_resp.provider, "model": ai_resp.model, "latency_ms": ai_resp.latency_ms},
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
            self.record_activity("ai", "ai_error", str(exc), status="error")
            self.set_companion_state("error")
            sys_msg = self._store_unavailable_message(conversation_id, mode, str(exc))
            return {
                "conversation_id": conversation_id,
                "user_message": user_msg,
                "assistant_message": sys_msg,
                "ai_available": False,
                "error": str(exc),
            }
        finally:
            self.voice.end_generation(gen_id)
            self.events.publish("companion_state", {"state": self.voice.get_state()})

    def stream_chat(
        self,
        conversation_id: str | None,
        user_text: str,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        skip_user_insert: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Generator yielding real streaming SSE events: meta -> delta* -> done | error."""
        conversation_id, user_msg, chat_messages, relevant_memories, online = self._prepare_chat_context(
            conversation_id, user_text, attachments=attachments, skip_user_insert=skip_user_insert
        )
        mode = "online" if online else "offline"
        text = user_text.strip()

        yield {
            "event": "meta",
            "data": {
                "conversation_id": conversation_id,
                "user_message": user_msg,
                "memories_used": len(relevant_memories),
            },
        }

        if text.startswith("/"):
            tool_reply = self._try_local_command(text, conversation_id, mode)
            if tool_reply is not None:
                yield {
                    "event": "done",
                    "data": {
                        "conversation_id": conversation_id,
                        "assistant_message": tool_reply,
                        "ai_available": True,
                        "used_local_tool": True,
                    },
                }
                return

        gen_id = new_id("gen")
        self.voice.begin_generation(gen_id)
        self.events.publish("companion_state", {"state": "processing"})
        self.record_activity("ai", "ai_request", f"Streaming request ({len(chat_messages) - 1} turn(s)).", status="info")

        accumulated: list[str] = []
        final_resp: AIResponse | None = None
        last_provider = ""
        last_model = ""

        try:
            for chunk in self.ai.chat_stream(
                messages=chat_messages,
                requested_provider=requested_provider,
                requested_model=requested_model,
                online_override=online,
                should_cancel=self.voice.is_interrupted,
            ):
                if self.voice.is_interrupted():
                    break
                if chunk.get("done"):
                    final_resp = chunk["response"]
                    break
                delta = str(chunk.get("delta", ""))
                if delta:
                    accumulated.append(delta)
                    last_provider = str(chunk.get("provider", last_provider))
                    last_model = str(chunk.get("model", last_model))
                    yield {
                        "event": "delta",
                        "data": {
                            "delta": delta,
                            "provider": last_provider,
                            "model": last_model,
                        },
                    }

            if self.voice.is_interrupted():
                partial = "".join(accumulated).strip()
                saved_content = f"{partial}\n\n[Generation interrupted by user]" if partial else "Generation stopped by user."
                msg = self.db.add_message(
                    conversation_id=conversation_id,
                    role="assistant" if partial else "system",
                    content=saved_content,
                    provider=last_provider or None,
                    model=last_model or None,
                    mode=mode,
                    metadata={"interrupted": True},
                )
                self.record_activity("ai", "ai_interrupted", "Streaming generation interrupted by user.", status="warn")
                yield {
                    "event": "done",
                    "data": {
                        "conversation_id": conversation_id,
                        "assistant_message": msg,
                        "ai_available": True,
                        "interrupted": True,
                    },
                }
                return

            if final_resp is None:
                raise ProviderError("router", "AI stream ended unexpectedly without a completion.")

            assistant_msg = self.db.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=final_resp.content,
                provider=final_resp.provider,
                model=final_resp.model,
                mode="offline" if final_resp.is_local else "online",
                metadata={
                    "latency_ms": final_resp.latency_ms,
                    "usage": final_resp.usage,
                    "memories_used": [m["id"] for m in relevant_memories],
                },
            )
            self.record_activity(
                "ai",
                "ai_response",
                f"Streamed response from {final_resp.provider}/{final_resp.model} ({final_resp.latency_ms} ms).",
                status="ok",
                details={"provider": final_resp.provider, "model": final_resp.model, "latency_ms": final_resp.latency_ms},
            )
            yield {
                "event": "done",
                "data": {
                    "conversation_id": conversation_id,
                    "assistant_message": assistant_msg,
                    "ai_available": True,
                    "provider": final_resp.provider,
                    "model": final_resp.model,
                    "latency_ms": final_resp.latency_ms,
                },
            }
        except ProviderError as exc:
            self.record_activity("ai", "ai_error", str(exc), status="error")
            self.set_companion_state("error")
            sys_msg = self._store_unavailable_message(conversation_id, mode, str(exc))
            yield {
                "event": "error",
                "data": {
                    "conversation_id": conversation_id,
                    "assistant_message": sys_msg,
                    "ai_available": False,
                    "error": str(exc),
                },
            }
        finally:
            self.voice.end_generation(gen_id)
            self.events.publish("companion_state", {"state": self.voice.get_state()})

    def retry_message(
        self,
        conversation_id: str,
        target_message_id: str | None = None,
        requested_provider: str | None = None,
        requested_model: str | None = None,
    ) -> dict[str, Any]:
        """Retry generating an AI response for the last user message (optionally removing a failed reply)."""
        msgs = self.db.list_messages(conversation_id, limit=200)
        if not msgs:
            raise ValueError("Conversation has no messages to retry.")

        if target_message_id:
            target = next((m for m in msgs if m["id"] == target_message_id), None)
            if target and target["role"] in ("assistant", "system"):
                self.db.delete_message(target["id"])
                msgs = self.db.list_messages(conversation_id, limit=200)

        last_user = next((m for m in reversed(msgs) if m["role"] == "user"), None)
        if not last_user:
            raise ValueError("No user prompt found to retry.")

        self.record_activity("ai", "ai_retry", f"Retrying prompt in conversation {conversation_id}.", status="info")
        return self.send_chat(
            conversation_id=conversation_id,
            user_text=last_user["content"],
            requested_provider=requested_provider,
            requested_model=requested_model,
            skip_user_insert=True,
        )

    def _store_unavailable_message(self, conversation_id: str, mode: str, err: str) -> dict[str, Any]:
        notice = (
            "AI Provider Unavailable\n\n"
            f"Reason: {err}\n\n"
            "Your message was saved locally. To restore AI responses:\n"
            "• Offline AI: Ensure Ollama is running with a local model installed (Settings → Local AI).\n"
            "• Cloud AI: Configure an API key in `.env` and enable Cloud AI in Settings → Cloud AI.\n"
            "• Local Tools (always offline): `/calc 24 * 7`, `/diag`, `/find README`, `/note ...`."
        )
        return self.db.add_message(
            conversation_id=conversation_id,
            role="system",
            content=notice,
            provider=None,
            model=None,
            mode=mode,
            metadata={"ai_unavailable": True, "error": err},
        )

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
            self.record_activity("tool", "tool_blocked", f"Blocked {tool_name}: {decision.reason}", status="warn")
            return self.db.add_message(
                conversation_id=conversation_id,
                role="system",
                content=f"Local tool `{tool_name}` blocked: {decision.reason}",
                provider="local_tool",
                model=tool_name,
                mode="offline",
                metadata={"tool_blocked": True},
            )

        self.set_companion_state("executing")
        try:
            res = self.tools.execute(tool_name, tool_args)
            self.record_activity("tool", "tool_execution", f"Executed local tool '{tool_name}'.", status="ok", details={"args": tool_args})
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
            self.record_activity("tool", "tool_error", f"Local tool '{tool_name}' failed: {exc}", status="error")
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
            self.set_companion_state("idle")
