"""Persistent 8-state autonomous task planner, executor, and startup crash recovery engine."""

from __future__ import annotations

import json
from typing import Any, Callable

from ai.local_models.detector import check_internet_connectivity
from database.store import RynaDatabase, new_id, utc_now
from security.permissions import PermissionEngine

from .tools import ToolRegistry

# All 8 Section 16 canonical task states
TASK_STATUSES = ("planned", "running", "waiting", "completed", "failed", "paused", "cancelled", "recovering")


class AgentTaskEngine:
    def __init__(
        self,
        db: RynaDatabase,
        permissions: PermissionEngine,
        tools: ToolRegistry,
        connectivity_checker: Callable[[], bool] | None = None,
    ) -> None:
        self.db = db
        self.permissions = permissions
        self.tools = tools
        self._check_online = connectivity_checker or check_internet_connectivity

    def recover_after_restart(self) -> list[dict[str, Any]]:
        """Safely transition interrupted running/recovering tasks to paused/uncertain without blindly repeating actions."""
        recovered: list[dict[str, Any]] = []
        now = utc_now()
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT id, title, status FROM tasks WHERE status IN ('running', 'recovering')").fetchall()
            for r in rows:
                conn.execute("UPDATE tasks SET status = 'recovering', updated_at = ? WHERE id = ?", (now, r["id"]))
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'paused', outcome_status = 'uncertain',
                        error_message = 'Recovered after unexpected restart. Review uncertain step before approving resume.',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, r["id"]),
                )
                conn.execute("UPDATE task_steps SET status = 'uncertain', updated_at = ? WHERE task_id = ? AND status = 'running'", (now, r["id"]))
                recovered.append({"id": r["id"], "title": r["title"], "previous_status": r["status"]})
        if recovered:
            self.db.log_action("task_recovery", 1, "recovered", f"{len(recovered)} task(s)", {"tasks": recovered})
        return recovered

    def plan_steps(self, goal: str, requires_internet: bool = False) -> list[dict[str, Any]]:
        low = goal.lower().strip()
        if any(k in low for k in ("diagnostic", "hardware", "system status", "system info")):
            return [{"description": "Collect live hardware and system diagnostics", "tool_name": "system_diagnostics", "tool_args": {}, "permission_level": 1, "requires_internet": False}]
        if low.startswith("calc:") or low.startswith("calculate "):
            expr = goal.split(":", 1)[1] if ":" in goal else goal[len("calculate ") :]
            return [{"description": f"Evaluate expression: {expr.strip()}", "tool_name": "calculate", "tool_args": {"expression": expr.strip()}, "permission_level": 1, "requires_internet": False}]
        if low.startswith("find files ") or low.startswith("search files "):
            q = goal.split(" ", 2)[2].strip()
            return [{"description": f"Search workspace files for '{q}'", "tool_name": "file_search", "tool_args": {"query": q}, "permission_level": 1, "requires_internet": False}]
        if low.startswith("fetch http://") or low.startswith("fetch https://"):
            url = goal.split(" ", 1)[1].strip()
            return [{"description": f"Fetch webpage content from {url}", "tool_name": "web_fetch", "tool_args": {"url": url}, "permission_level": 1, "requires_internet": True}]
        if low.startswith("open app "):
            app = goal.split(" ", 2)[2].strip()
            return [{"description": f"Launch allowlisted app '{app}'", "tool_name": "open_application", "tool_args": {"app": app}, "permission_level": 2, "requires_internet": False}]
        return [{"description": f"Record structured task note: {goal[:100]}", "tool_name": "create_note", "tool_args": {"title": f"Task: {goal[:44]}", "content": goal}, "permission_level": 1, "requires_internet": requires_internet}]

    def create_task(
        self,
        title: str,
        goal: str,
        steps: list[dict[str, Any]] | None = None,
        requires_internet: bool = False,
        permission_level: int = 1,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        tid = new_id("task")
        now = utc_now()
        plan = steps if steps is not None else self.plan_steps(goal, requires_internet)
        max_perm = max([int(permission_level)] + [int(s.get("permission_level", 1)) for s in plan])
        any_net = bool(requires_internet) or any(bool(s.get("requires_internet", False)) for s in plan)

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks(id, title, goal, status, outcome_status, requires_internet, permission_level, result_summary, error_message, conversation_id, created_at, updated_at)
                VALUES (?, ?, ?, 'planned', 'pending', ?, ?, '', '', ?, ?, ?)
                """,
                (tid, title.strip() or goal.strip()[:60] or "Task", goal.strip(), 1 if any_net else 0, max_perm, conversation_id, now, now),
            )
            for idx, st in enumerate(plan):
                eff_perm = max(int(permission_level), int(st.get("permission_level", 1)))
                eff_net = bool(st.get("requires_internet", False)) or (bool(requires_internet) and steps is None)
                conn.execute(
                    """
                    INSERT INTO task_steps(id, task_id, step_index, description, tool_name, tool_args_json, permission_level, requires_internet, status, output, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', '', ?, ?)
                    """,
                    (new_id("step"), tid, idx, str(st.get("description", f"Step {idx + 1}")), st.get("tool_name"), json.dumps(st.get("tool_args", {})), eff_perm, 1 if eff_net else 0, now, now),
                )
        return self.get_task(tid)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.db._lock:
            row = self.db._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return None
            steps = self.db._conn.execute("SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_index ASC", (task_id,)).fetchall()
            t = dict(row)
            t["requires_internet"] = bool(t["requires_internet"])
            t["steps"] = [{**dict(s), "requires_internet": bool(s["requires_internet"]), "tool_args": json.loads(s["tool_args_json"] or "{}")} for s in steps]
            for s in t["steps"]:
                s.pop("tool_args_json", None)
            return t

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.db._lock:
            if status and status in TASK_STATUSES:
                rows = self.db._conn.execute("SELECT id FROM tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = self.db._conn.execute("SELECT id FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [t for r in rows if (t := self.get_task(r["id"])) is not None]

    def run_task(self, task_id: str, approved: bool = False, online_override: bool | None = None) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")
        if task["status"] in ("completed", "cancelled"):
            return task
        if self.permissions.emergency_stopped:
            self._set_task(task_id, "paused", "partially_completed", error="Emergency stop is active.")
            return self.get_task(task_id)  # type: ignore[return-value]

        is_online = self._check_online() if online_override is None else online_override
        self._set_task(task_id, "running", task["outcome_status"], error="")
        outputs: list[str] = []
        done_any = False

        for step in task["steps"]:
            if step["status"] == "completed":
                done_any = True
                if step["output"]:
                    outputs.append(step["output"])
                continue
            if step["status"] == "uncertain" and not approved:
                self._set_task(task_id, "waiting", "uncertain", error="Step state uncertain after restart. Explicit approval required to retry.")
                return self.get_task(task_id)  # type: ignore[return-value]
            if step["requires_internet"] and not is_online:
                self._set_task(task_id, "paused", "partially_completed" if done_any else "pending", summary="\n".join(outputs), error="Queued offline; awaiting internet restoration.")
                return self.get_task(task_id)  # type: ignore[return-value]

            dec = self.permissions.evaluate(step["tool_name"] or "task_step", step["description"], explicitly_approved=approved, override_level=step["permission_level"], task_id=task_id)
            if dec.blocked:
                self._set_step(step["id"], "failed", dec.reason)
                self._set_task(task_id, "failed", "partially_completed" if done_any else "failed", summary="\n".join(outputs), error=dec.reason)
                return self.get_task(task_id)  # type: ignore[return-value]
            if dec.requires_confirmation:
                self._set_task(task_id, "waiting", "partially_completed" if done_any else "pending", summary="\n".join(outputs), error=dec.reason)
                return self.get_task(task_id)  # type: ignore[return-value]

            self._set_step(step["id"], "running", "")
            try:
                res = self.tools.execute(step["tool_name"], step["tool_args"]) if step["tool_name"] else f"Step recorded: {step['description']}"
                out = json.dumps(res, ensure_ascii=False) if not isinstance(res, str) else res
                self._set_step(step["id"], "completed", out)
                outputs.append(out)
                done_any = True
            except Exception as exc:
                self._set_step(step["id"], "failed", str(exc))
                self._set_task(task_id, "failed", "partially_completed" if done_any else "failed", summary="\n".join(outputs), error=str(exc))
                return self.get_task(task_id)  # type: ignore[return-value]

        self._set_task(task_id, "completed", "succeeded", summary="\n".join(outputs), error="")
        return self.get_task(task_id)  # type: ignore[return-value]

    def pause_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")
        if task["status"] not in ("completed", "cancelled"):
            self._set_task(task_id, "paused", task["outcome_status"])
        return self.get_task(task_id)  # type: ignore[return-value]

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")
        self._set_task(task_id, "cancelled", task["outcome_status"], error="Cancelled by user.")
        self.db.log_action("task_cancel", 1, "cancelled", task_id, task_id=task_id)
        return self.get_task(task_id)  # type: ignore[return-value]

    def retry_task(self, task_id: str, approved: bool = False) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute("UPDATE task_steps SET status='planned', output='', updated_at=? WHERE task_id=? AND status!='completed'", (now, task_id))
            conn.execute("UPDATE tasks SET status='planned', outcome_status='pending', error_message='', updated_at=? WHERE id=?", (now, task_id))
        return self.run_task(task_id, approved=approved)

    def halt_active(self) -> int:
        with self.db.transaction() as conn:
            return conn.execute(
                "UPDATE tasks SET status='paused', error_message='Halted by Emergency Stop.', updated_at=? WHERE status IN ('running','recovering')",
                (utc_now(),),
            ).rowcount

    def _set_task(self, tid: str, status: str, outcome: str, summary: str | None = None, error: str | None = None) -> None:
        now = utc_now()
        with self.db.transaction() as conn:
            if summary is not None and error is not None:
                conn.execute("UPDATE tasks SET status=?, outcome_status=?, result_summary=?, error_message=?, updated_at=? WHERE id=?", (status, outcome, summary, error, now, tid))
            elif error is not None:
                conn.execute("UPDATE tasks SET status=?, outcome_status=?, error_message=?, updated_at=? WHERE id=?", (status, outcome, error, now, tid))
            else:
                conn.execute("UPDATE tasks SET status=?, outcome_status=?, updated_at=? WHERE id=?", (status, outcome, now, tid))

    def _set_step(self, sid: str, status: str, output: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE task_steps SET status=?, output=?, updated_at=? WHERE id=?", (status, output, utc_now(), sid))
