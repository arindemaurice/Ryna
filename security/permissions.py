"""Three-tier autonomy, confirmation, destructive-action safeguard, and emergency stop engine."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any

from database.store import RynaDatabase


class PermissionLevel(IntEnum):
    AUTOMATIC = 1             # Low-risk, read-only, calculations, drafting, local diagnostics
    CONFIRMATION_REQUIRED = 2 # Sending emails/messages, file writes, app launch, external actions
    RESTRICTED = 3            # Permanent deletion, destructive commands, security modifications


ACTION_LEVELS: dict[str, PermissionLevel] = {
    "calculate": PermissionLevel.AUTOMATIC,
    "current_time": PermissionLevel.AUTOMATIC,
    "file_search": PermissionLevel.AUTOMATIC,
    "file_read": PermissionLevel.AUTOMATIC,
    "system_diagnostics": PermissionLevel.AUTOMATIC,
    "create_note": PermissionLevel.AUTOMATIC,
    "web_fetch": PermissionLevel.AUTOMATIC,
    "web_search": PermissionLevel.AUTOMATIC,
    "active_window": PermissionLevel.AUTOMATIC,
    "screen_capture": PermissionLevel.AUTOMATIC,
    "open_application": PermissionLevel.AUTOMATIC,
    "close_application": PermissionLevel.AUTOMATIC,
    "list_apps": PermissionLevel.AUTOMATIC,
    "open_folder": PermissionLevel.AUTOMATIC,
    "volume_control": PermissionLevel.AUTOMATIC,
    "brightness_control": PermissionLevel.AUTOMATIC,
    "battery_status": PermissionLevel.AUTOMATIC,
    "computer_shortcut": PermissionLevel.AUTOMATIC,
    "open_settings_page": PermissionLevel.AUTOMATIC,
    "media_search": PermissionLevel.AUTOMATIC,
    "clipboard_get": PermissionLevel.AUTOMATIC,
    "clipboard_set": PermissionLevel.AUTOMATIC,
    "system_info": PermissionLevel.AUTOMATIC,
    "tech_doctor": PermissionLevel.AUTOMATIC,
    "ocr_screen": PermissionLevel.AUTOMATIC,
    "user_file_list": PermissionLevel.AUTOMATIC,
    "user_file_find": PermissionLevel.AUTOMATIC,
    "user_file_read": PermissionLevel.AUTOMATIC,
    "open_project": PermissionLevel.AUTOMATIC,
    "weather": PermissionLevel.AUTOMATIC,
    "type_active_app": PermissionLevel.AUTOMATIC,
    "power_control": PermissionLevel.AUTOMATIC,
    "user_file_recycle": PermissionLevel.AUTOMATIC,
    "execute_command": PermissionLevel.AUTOMATIC,
    "run_command": PermissionLevel.AUTOMATIC,
    "run_powershell": PermissionLevel.AUTOMATIC,
    "mouse_click": PermissionLevel.AUTOMATIC,
    "mouse_move": PermissionLevel.AUTOMATIC,
    "mouse_scroll": PermissionLevel.AUTOMATIC,
    "type_text": PermissionLevel.AUTOMATIC,
    "press_key": PermissionLevel.AUTOMATIC,
    "hotkey": PermissionLevel.AUTOMATIC,
    "list_processes": PermissionLevel.AUTOMATIC,
    "create_file": PermissionLevel.AUTOMATIC,
    "file_write": PermissionLevel.CONFIRMATION_REQUIRED,
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
        return self.db.log_action("emergency_stop", 3, "triggered", "engine", {"reason": reason})

    def reset_emergency_stop(self) -> dict[str, Any]:
        with self._lock:
            self._emergency_stopped = False
        return self.db.log_action("emergency_stop_reset", 2, "reset", "engine")

    def level_for_action(self, action_type: str, override_level: int | None = None) -> PermissionLevel:
        if override_level is not None:
            return PermissionLevel(max(1, min(3, int(override_level))))
        full_access = bool(self.db.get_setting("permissions.full_device_access", True))
        if full_access and action_type in (
            "shell_command", "execute_command", "run_command", "run_powershell",
            "file_write", "create_file", "delete_file", "mouse_click", "mouse_move",
            "mouse_scroll", "type_text", "press_key", "hotkey", "list_processes",
        ):
            return PermissionLevel.AUTOMATIC
        return ACTION_LEVELS.get(action_type, PermissionLevel.CONFIRMATION_REQUIRED)

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
            dec = PermissionDecision(False, False, True, int(level), "Emergency stop is active.")
            self.db.log_action(action_type, int(level), "blocked_emergency_stop", target, dec.to_dict(), task_id)
            return dec

        if level == PermissionLevel.RESTRICTED:
            if bool(self.db.get_setting("permissions.level3_block_destructive", True)) or not explicitly_approved:
                dec = PermissionDecision(False, False, True, int(level), "Level 3 destructive/restricted action blocked by RYNA safeguards.")
                self.db.log_action(action_type, int(level), "blocked_restricted", target, dec.to_dict(), task_id)
                return dec

        if level == PermissionLevel.CONFIRMATION_REQUIRED:
            if bool(self.db.get_setting("permissions.level2_require_confirm", True)) and not explicitly_approved:
                dec = PermissionDecision(False, True, False, int(level), f"Action '{action_type}' requires explicit user confirmation (Level 2).")
                self.db.log_action(action_type, int(level), "awaiting_approval", target, dec.to_dict(), task_id)
                return dec

        if level == PermissionLevel.AUTOMATIC:
            if not bool(self.db.get_setting("permissions.level1_auto", True)) and not explicitly_approved:
                dec = PermissionDecision(False, True, False, int(level), "Automatic Level 1 actions disabled; confirmation required.")
                self.db.log_action(action_type, int(level), "awaiting_approval", target, dec.to_dict(), task_id)
                return dec

        dec = PermissionDecision(True, False, False, int(level), "Permitted.")
        self.db.log_action(action_type, int(level), "permitted", target, dec.to_dict(), task_id)
        return dec
