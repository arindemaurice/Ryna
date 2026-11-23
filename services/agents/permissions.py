"""Three-level permission, safety guard, audit logging, and emergency stop system."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any

from core.python.database import RynaDatabase


class PermissionLevel(IntEnum):
    AUTOMATIC = 1             # Safe, reversible, read-only or routine drafting actions
    CONFIRMATION_REQUIRED = 2 # External side effects, sending messages, overwriting files, launching apps
    RESTRICTED = 3            # Permanent deletion, destructive commands, security/credential changes


ACTION_DEFAULT_LEVELS: dict[str, PermissionLevel] = {
    "file_search": PermissionLevel.AUTOMATIC,
    "file_read": PermissionLevel.AUTOMATIC,
    "calculate": PermissionLevel.AUTOMATIC,
    "system_diagnostics": PermissionLevel.AUTOMATIC,
    "create_note": PermissionLevel.AUTOMATIC,
    "web_research": PermissionLevel.AUTOMATIC,
    "file_write": PermissionLevel.CONFIRMATION_REQUIRED,
    "open_application": PermissionLevel.CONFIRMATION_REQUIRED,
    "send_email": PermissionLevel.CONFIRMATION_REQUIRED,
    "send_message": PermissionLevel.CONFIRMATION_REQUIRED,
    "submit_form": PermissionLevel.CONFIRMATION_REQUIRED,
    "delete_permanent": PermissionLevel.RESTRICTED,
    "shell_command": PermissionLevel.RESTRICTED,
    "system_security_change": PermissionLevel.RESTRICTED,
}


@dataclass
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool
    blocked: bool
    level: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PermissionEngine:
    """Evaluates action safety, enforces emergency stop, and writes audit logs."""

    def __init__(self, db: RynaDatabase) -> None:
        self.db = db
        self._lock = threading.RLock()
        self._emergency_stopped = False

    @property
    def emergency_stopped(self) -> bool:
        with self._lock:
            return self._emergency_stopped

    def trigger_emergency_stop(self, reason: str = "User triggered emergency stop") -> dict[str, Any]:
        with self._lock:
            self._emergency_stopped = True
        return self.db.log_action(
            action_type="emergency_stop",
            permission_level=PermissionLevel.RESTRICTED,
            status="triggered",
            target="engine",
            details={"reason": reason},
        )

    def reset_emergency_stop(self) -> dict[str, Any]:
        with self._lock:
            self._emergency_stopped = False
        return self.db.log_action(
            action_type="emergency_stop_reset",
            permission_level=PermissionLevel.CONFIRMATION_REQUIRED,
            status="reset",
            target="engine",
        )

    def level_for_action(self, action_type: str, override_level: int | None = None) -> PermissionLevel:
        if override_level is not None:
            return PermissionLevel(max(1, min(3, int(override_level))))
        return ACTION_DEFAULT_LEVELS.get(action_type, PermissionLevel.CONFIRMATION_REQUIRED)

    def evaluate(
        self,
        action_type: str,
        target: str = "",
        explicitly_approved: bool = False,
        override_level: int | None = None,
        task_id: str | None = None,
    ) -> PermissionDecision:
        level = self.level_for_action(action_type, override_level)

        if self.emergency_stopped:
            decision = PermissionDecision(
                allowed=False,
                requires_confirmation=False,
                blocked=True,
                level=int(level),
                reason="Emergency stop is active. Reset emergency stop before executing actions.",
            )
            self.db.log_action(action_type, int(level), "blocked_emergency_stop", target, decision.to_dict(), task_id)
            return decision

        if level == PermissionLevel.RESTRICTED:
            block_destructive = bool(self.db.get_setting("permissions.level3_block_destructive", True))
            if block_destructive or not explicitly_approved:
                decision = PermissionDecision(
                    allowed=False,
                    requires_confirmation=False,
                    blocked=True,
                    level=int(level),
                    reason="Level 3 restricted action is blocked by RYNA security safeguards.",
                )
                self.db.log_action(action_type, int(level), "blocked_restricted", target, decision.to_dict(), task_id)
                return decision

        if level == PermissionLevel.CONFIRMATION_REQUIRED:
            require_confirm = bool(self.db.get_setting("permissions.level2_require_confirm", True))
            if require_confirm and not explicitly_approved:
                decision = PermissionDecision(
                    allowed=False,
                    requires_confirmation=True,
                    blocked=False,
                    level=int(level),
                    reason=f"Action '{action_type}' requires explicit user confirmation (Level 2).",
                )
                self.db.log_action(action_type, int(level), "awaiting_approval", target, decision.to_dict(), task_id)
                return decision

        if level == PermissionLevel.AUTOMATIC:
            auto_ok = bool(self.db.get_setting("permissions.level1_auto", True))
            if not auto_ok and not explicitly_approved:
                decision = PermissionDecision(
                    allowed=False,
                    requires_confirmation=True,
                    blocked=False,
                    level=int(level),
                    reason="Automatic Level 1 actions are disabled in Settings; user approval required.",
                )
                self.db.log_action(action_type, int(level), "awaiting_approval", target, decision.to_dict(), task_id)
                return decision

        decision = PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            blocked=False,
            level=int(level),
            reason="Permitted.",
        )
        self.db.log_action(action_type, int(level), "permitted", target, decision.to_dict(), task_id)
        return decision
