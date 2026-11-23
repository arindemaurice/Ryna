"""Persistent task & agent execution engine with crash recovery and offline queueing."""

from __future__ import annotations

import json
from typing import Any, Callable

from core.python.database import RynaDatabase, new_id, utc_now
from core.python.hardware import check_internet_connectivity
from services.tools.local_tools import LocalToolRegistry

from .permissions import PermissionEngine

TASK_STATUSES = (
    "pending",
    "planning",
    "running",
    "waiting_for_approval",
    "paused",
    "failed",
    "completed",
    "cancelled",
)

OUTCOME_STATUSES = ("pending", "succeeded", "partially_completed", "failed", "uncertain")


class TaskEngine:
    """Manages persistent multi-step tasks, approvals, crash recovery, and offline transitions."""

    def __init__(
        self,
        db: RynaDatabase,
        permissions: PermissionEngine,
        tools: LocalToolRegistry,
        connectivity_checker: Callable[[], bool] | None = None,
    ) -> None:
        self.db = db
        self.permissions = permissions
        self.tools = tools
        self._check_online = connectivity_checker or check_internet_connectivity

    def recover_after_restart(self) -> list[dict[str, Any]]:
        """Safely recover any tasks left in 'running' or 'planning' state after a crash/restart.

        Never blindly repeats external actions: marks interrupted tasks as 'paused'
        with outcome_status='uncertain' so the user can inspect and safely resume.
        """
        recovered: list[dict[str, Any]] = []
        now = utc_now()
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id, title, status FROM tasks WHERE status IN ('running', 'planning')"
            ).fetchall()
            for r in rows:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'paused', outcome_status = 'uncertain',
                        error_message = 'Engine restarted while task was active. Inspect step state before resuming.',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, r["id"]),
                )
                conn.execute(
                    """
                    UPDATE task_steps
                    SET status = 'uncertain', updated_at = ?
                    WHERE task_id = ? AND status = 'running'
                    """,
                    (now, r["id"]),
                )
                recovered.append({"id": r["id"], "title": r["title"], "previous_status": r["status"]})
        if recovered:
            self.db.log_action(
                action_type="task_crash_recovery",
                permission_level=1,
                status="recovered",
                target=f"{len(recovered)} task(s)",
                details={"tasks": recovered},
            )
        return recovered

    def create_task(
        self,
        title: str,
        goal: str,
        steps: list[dict[str, Any]] | None = None,
        requires_internet: bool = False,
        permission_level: int = 1,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        task_id = new_id("task")
        now = utc_now()
        planned_steps = steps if steps is not None else self._build_default_plan(goal, requires_internet)
        max_perm = max(
            [int(permission_level)] + [int(s.get("permission_level", 1)) for s in planned_steps]
        )
        any_internet = bool(requires_internet) or any(bool(s.get("requires_internet", False)) for s in planned_steps)

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks(
                    id, title, goal, status, outcome_status, requires_internet,
                    permission_level, plan_json, result_summary, error_message,
                    conversation_id, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', 'pending', ?, ?, ?, '', '', ?, ?, ?)
                """,
                (
                    task_id,
                    title.strip() or goal.strip()[:60] or "Untitled Task",
                    goal.strip(),
                    1 if any_internet else 0,
                    max_perm,
                    json.dumps(planned_steps),
                    conversation_id,
                    now,
                    now,
                ),
            )
            for idx, step in enumerate(planned_steps):
                effective_perm = max(int(permission_level), int(step.get("permission_level", 1)))
                effective_net = bool(step.get("requires_internet", False)) or (
                    bool(requires_internet) and steps is None
                )
                conn.execute(
                    """
                    INSERT INTO task_steps(
                        id, task_id, step_index, description, tool_name, tool_args_json,
                        permission_level, requires_internet, status, output, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?)
                    """,
                    (
                        new_id("step"),
                        task_id,
                        idx,
                        str(step.get("description", f"Step {idx + 1}")),
                        step.get("tool_name"),
                        json.dumps(step.get("tool_args", {})),
                        effective_perm,
                        1 if effective_net else 0,
                        now,
                        now,
                    ),
                )
        return self.get_task(task_id)  # type: ignore[return-value]

    def _build_default_plan(self, goal: str, requires_internet: bool) -> list[dict[str, Any]]:
        lower = goal.lower()
        if any(k in lower for k in ("diagnostic", "hardware", "system status", "system info")):
            return [
                {
                    "description": "Collect local system diagnostics",
                    "tool_name": "system_diagnostics",
                    "tool_args": {},
                    "permission_level": 1,
                    "requires_internet": False,
                }
            ]
        if lower.startswith("calc:") or lower.startswith("calculate "):
            expr = goal.split(":", 1)[1] if ":" in goal else goal[len("calculate ") :]
            return [
                {
                    "description": f"Evaluate mathematical expression: {expr.strip()}",
                    "tool_name": "calculate",
                    "tool_args": {"expression": expr.strip()},
                    "permission_level": 1,
                    "requires_internet": False,
                }
            ]
        if lower.startswith("search files ") or lower.startswith("find files "):
            query = goal.split(" ", 2)[2].strip()
            return [
                {
                    "description": f"Search workspace files matching '{query}'",
                    "tool_name": "file_search",
                    "tool_args": {"query": query},
                    "permission_level": 1,
                    "requires_internet": False,
                }
            ]
        if lower.startswith("note:"):
            body = goal.split(":", 1)[1].strip()
            return [
                {
                    "description": "Save local note to SQLite",
                    "tool_name": "create_note",
                    "tool_args": {"title": body[:48] or "Task Note", "content": body},
                    "permission_level": 1,
                    "requires_internet": False,
                }
            ]
        return [
            {
                "description": f"Record goal note for manual or tool-assisted completion: {goal[:100]}",
                "tool_name": "create_note",
                "tool_args": {"title": f"Task: {goal[:42]}", "content": goal},
                "permission_level": 1,
                "requires_internet": requires_internet,
            }
        ]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.db._lock:
            row = self.db._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return None
            steps = self.db._conn.execute(
                "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_index ASC", (task_id,)
            ).fetchall()
            task = dict(row)
            task["requires_internet"] = bool(task["requires_internet"])
            task["plan"] = json.loads(task.pop("plan_json") or "[]")
            parsed_steps = []
            for s in steps:
                sd = dict(s)
                sd["requires_internet"] = bool(sd["requires_internet"])
                sd["tool_args"] = json.loads(sd.pop("tool_args_json") or "{}")
                parsed_steps.append(sd)
            task["steps"] = parsed_steps
            return task

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.db._lock:
            if status and status in TASK_STATUSES:
                rows = self.db._conn.execute(
                    "SELECT id FROM tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = self.db._conn.execute(
                    "SELECT id FROM tasks ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [t for r in rows if (t := self.get_task(r["id"])) is not None]

    def run_task(
        self,
        task_id: str,
        approved: bool = False,
        online_override: bool | None = None,
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")
        if task["status"] in ("completed", "cancelled"):
            return task

        if self.permissions.emergency_stopped:
            self._set_task_state(task_id, "paused", "partially_completed", error="Emergency stop is active.")
            return self.get_task(task_id)  # type: ignore[return-value]

        is_online = self._check_online() if online_override is None else online_override
        self._set_task_state(task_id, "running", task["outcome_status"], error="")

        outputs: list[str] = []
        completed_any = False

        for step in task["steps"]:
            if step["status"] == "completed":
                completed_any = True
                if step["output"]:
                    outputs.append(step["output"])
                continue
            if step["status"] == "uncertain" and not approved:
                self._set_task_state(
                    task_id,
                    "waiting_for_approval",
                    "uncertain",
                    error="Step outcome is uncertain after previous interruption. Explicit approval required to retry.",
                )
                return self.get_task(task_id)  # type: ignore[return-value]

            if step["requires_internet"] and not is_online:
                self._set_task_state(
                    task_id,
                    "paused",
                    "partially_completed" if completed_any else "pending",
                    summary="\n".join(outputs),
                    error="Queued pending internet connectivity. Offline steps preserved.",
                )
                return self.get_task(task_id)  # type: ignore[return-value]

            action_name = step["tool_name"] or "task_step"
            decision = self.permissions.evaluate(
                action_type=action_name,
                target=step["description"],
                explicitly_approved=approved,
                override_level=step["permission_level"],
                task_id=task_id,
            )
            if decision.blocked:
                self._set_step_state(step["id"], "failed", decision.reason)
                self._set_task_state(
                    task_id,
                    "failed",
                    "partially_completed" if completed_any else "failed",
                    summary="\n".join(outputs),
                    error=decision.reason,
                )
                return self.get_task(task_id)  # type: ignore[return-value]

            if decision.requires_confirmation:
                self._set_task_state(
                    task_id,
                    "waiting_for_approval",
                    "partially_completed" if completed_any else "pending",
                    summary="\n".join(outputs),
                    error=decision.reason,
                )
                return self.get_task(task_id)  # type: ignore[return-value]

            # Execute step
            self._set_step_state(step["id"], "running", "")
            try:
                if step["tool_name"]:
                    res = self.tools.execute(step["tool_name"], step["tool_args"])
                    out_text = json.dumps(res, ensure_ascii=False) if not isinstance(res, str) else res
                else:
                    out_text = f"Step recorded: {step['description']}"
                self._set_step_state(step["id"], "completed", out_text)
                outputs.append(out_text)
                completed_any = True
            except Exception as exc:
                self._set_step_state(step["id"], "failed", str(exc))
                self._set_task_state(
                    task_id,
                    "failed",
                    "partially_completed" if completed_any else "failed",
                    summary="\n".join(outputs),
                    error=str(exc),
                )
                return self.get_task(task_id)  # type: ignore[return-value]

        self._set_task_state(
            task_id,
            "completed",
            "succeeded",
            summary="\n".join(outputs),
            error="",
        )
        return self.get_task(task_id)  # type: ignore[return-value]

    def pause_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")
        if task["status"] not in ("completed", "cancelled"):
            self._set_task_state(task_id, "paused", task["outcome_status"])
        return self.get_task(task_id)  # type: ignore[return-value]

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")
        self._set_task_state(task_id, "cancelled", task["outcome_status"], error="Cancelled by user.")
        self.db.log_action("task_cancel", 1, "cancelled", task_id, task_id=task_id)
        return self.get_task(task_id)  # type: ignore[return-value]

    def retry_task(self, task_id: str, approved: bool = False) -> dict[str, Any]:
        """Reset non-completed steps cleanly to pending and re-execute through permissions."""
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE task_steps SET status = 'pending', output = '', updated_at = ? WHERE task_id = ? AND status != 'completed'",
                (now, task_id),
            )
            conn.execute(
                "UPDATE tasks SET status = 'pending', outcome_status = 'pending', error_message = '', updated_at = ? WHERE id = ?",
                (now, task_id),
            )
        self.db.log_action("task_retry", 1, "retrying", task_id, task_id=task_id)
        return self.run_task(task_id, approved=approved)

    def halt_all_active(self) -> int:
        now = utc_now()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE tasks
                SET status = 'paused', error_message = 'Halted by Emergency Stop.', updated_at = ?
                WHERE status IN ('running', 'planning')
                """,
                (now,),
            )
            return cur.rowcount

    def _set_task_state(
        self,
        task_id: str,
        status: str,
        outcome_status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self.db.transaction() as conn:
            if summary is not None and error is not None:
                conn.execute(
                    "UPDATE tasks SET status = ?, outcome_status = ?, result_summary = ?, error_message = ?, updated_at = ? WHERE id = ?",
                    (status, outcome_status, summary, error, now, task_id),
                )
            elif error is not None:
                conn.execute(
                    "UPDATE tasks SET status = ?, outcome_status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                    (status, outcome_status, error, now, task_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET status = ?, outcome_status = ?, updated_at = ? WHERE id = ?",
                    (status, outcome_status, now, task_id),
                )

    def _set_step_state(self, step_id: str, status: str, output: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE task_steps SET status = ?, output = ?, updated_at = ? WHERE id = ?",
                (status, output, utc_now(), step_id),
            )
