"""Modular tool registry: AST calculator, sandboxed file ops, web fetch with prompt-injection isolation, notes, diagnostics, computer control."""

from __future__ import annotations

import ast
import html
import operator
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ai.config import RynaConfig
from ai.local_models.detector import check_internet_connectivity, detect_hardware
from computer.controller import ComputerController
from computer.system_manager import RynaSystemManager
from database.store import RynaDatabase
from security.privacy import wrap_untrusted_web_content

SAFE_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
SAFE_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_calculate(expression: str) -> float | int:
    node = ast.parse(expression.strip(), mode="eval")

    def _ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return _ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in SAFE_BIN:
            left, right = _ev(n.left), _ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 1000:
                raise ValueError("Exponent too large.")
            return SAFE_BIN[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and type(n.op) in SAFE_UNARY:
            return SAFE_UNARY[type(n.op)](_ev(n.operand))
        raise ValueError(f"Unsupported expression element: {type(n).__name__}")

    val = _ev(node)
    return int(val) if val.is_integer() else round(val, 8)


class ToolRegistry:
    def __init__(self, config: RynaConfig, db: RynaDatabase, computer: ComputerController, sys_mgr: RynaSystemManager | None = None) -> None:
        self.config = config
        self.db = db
        self.computer = computer
        self.sys_mgr = sys_mgr or RynaSystemManager(config.project_root)
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "calculate": self._calc,
            "current_time": self._current_time,
            "file_search": self._file_search,
            "file_read": self._file_read,
            "file_write": self._file_write,
            "create_note": self._create_note,
            "system_diagnostics": self._diagnostics,
            "system_info": self._system_info,
            "tech_doctor": self._tech_doctor,
            "active_window": self._active_window,
            "screen_capture": self._screen_capture,
            "ocr_screen": self._ocr_screen,
            "open_application": self._open_app,
            "close_application": self._close_app,
            "list_apps": self._list_apps,
            "open_folder": self._open_folder,
            "open_project": self._open_project,
            "user_file_list": self._user_file_list,
            "user_file_find": self._user_file_find,
            "user_file_read": self._user_file_read,
            "user_file_recycle": self._user_file_recycle,
            "volume_control": self._volume_control,
            "brightness_control": self._brightness_control,
            "battery_status": self._battery_status,
            "computer_shortcut": self._computer_shortcut,
            "open_settings_page": self._open_settings_page,
            "media_search": self._media_search,
            "weather": self._weather,
            "type_active_app": self._type_active_app,
            "power_control": self._power_control,
            "clipboard_get": self._clipboard_get,
            "clipboard_set": self._clipboard_set,
            "web_fetch": self._web_fetch,
            "web_search": self._web_search,
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "calculate", "level": 1, "offline": True, "description": "AST-safe mathematical evaluation"},
            {"name": "current_time", "level": 1, "offline": True, "description": "Real local system date and time"},
            {"name": "file_search", "level": 1, "offline": True, "description": "Discover files inside allowed workspace root"},
            {"name": "file_read", "level": 1, "offline": True, "description": "Read UTF-8 text/code/data files inside allowed root"},
            {"name": "file_write", "level": 2, "offline": True, "description": "Create or save a text file inside allowed root (Level 2 approval)"},
            {"name": "create_note", "level": 1, "offline": True, "description": "Save a local note or reminder in SQLite"},
            {"name": "system_diagnostics", "level": 1, "offline": True, "description": "Live CPU, RAM, GPU, disk, and database telemetry"},
            {"name": "active_window", "level": 1, "offline": True, "description": "Inspect currently focused Windows desktop window"},
            {"name": "screen_capture", "level": 1, "offline": True, "description": "Capture current Windows screen via mss (when screen vision enabled)"},
            {"name": "open_application", "level": 1, "offline": True, "description": "Launch any Windows app via physical Search bar or StartApps"},
            {"name": "close_application", "level": 1, "offline": True, "description": "Politely close an open non-system Windows app"},
            {"name": "list_apps", "level": 1, "offline": True, "description": "List installed Windows Start menu applications"},
            {"name": "open_folder", "level": 1, "offline": True, "description": "Open Desktop, Documents, Downloads, Pictures, This PC, or Recycle Bin"},
            {"name": "volume_control", "level": 1, "offline": True, "description": "Turn Windows volume up, down, mute, or set to a percentage"},
            {"name": "brightness_control", "level": 1, "offline": True, "description": "Adjust screen brightness up, down, or to a percentage"},
            {"name": "battery_status", "level": 1, "offline": True, "description": "Report live laptop battery percentage and charging status"},
            {"name": "computer_shortcut", "level": 1, "offline": True, "description": "Show desktop, switch windows, or lock screen"},
            {"name": "open_settings_page", "level": 1, "offline": True, "description": "Open a specific Windows Settings page (Wi-Fi, Bluetooth, Display, Sound, etc.)"},
            {"name": "media_search", "level": 1, "offline": False, "description": "Search Spotify, YouTube, or Google Maps directly"},
            {"name": "clipboard_get", "level": 1, "offline": True, "description": "Read text currently on the Windows clipboard"},
            {"name": "clipboard_set", "level": 1, "offline": True, "description": "Copy text to the Windows clipboard"},
            {"name": "web_fetch", "level": 1, "offline": False, "description": "Fetch public HTTP(S) webpage text with untrusted-content isolation"},
            {"name": "web_search", "level": 1, "offline": False, "description": "Search the live web with real URLs and isolated snippets"},
        ]

    def _allowed_root(self) -> Path:
        raw = Path(str(self.db.get_setting("computer.allowed_root", ".")))
        return raw.resolve() if raw.is_absolute() else (self.config.project_root / raw).resolve()

    def _safe_path(self, rel_or_abs: str) -> Path:
        root = self._allowed_root()
        p = Path(rel_or_abs)
        target = p.resolve() if p.is_absolute() else (root / p).resolve()
        if root != target and root not in target.parents:
            raise PermissionError(f"Path '{rel_or_abs}' is outside allowed root '{root}'.")
        return target

    def execute(self, name: str, args: dict[str, Any] | None = None) -> Any:
        if name not in self._handlers:
            raise ValueError(f"Unknown tool '{name}'.")
        return self._handlers[name](args or {})

    def _calc(self, args: dict[str, Any]) -> dict[str, Any]:
        expr = str(args.get("expression", "")).strip()
        if not expr:
            raise ValueError("Missing expression.")
        return {"expression": expr, "result": safe_calculate(expr)}

    def _file_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip().lower()
        limit = min(100, max(1, int(args.get("limit", 30))))
        root = self._allowed_root()
        skip = {".git", "__pycache__", ".venv", "node_modules", "target", "build", ".dart_tool"}
        matches: list[str] = []
        for p in root.rglob("*"):
            if any(part in skip for part in p.parts):
                continue
            if p.is_file() and (not query or query in p.name.lower()):
                matches.append(str(p.relative_to(root)))
                if len(matches) >= limit:
                    break
        return {"root": str(root), "query": query, "count": len(matches), "matches": matches}

    def _file_read(self, args: dict[str, Any]) -> dict[str, Any]:
        path_arg = str(args.get("path", "")).strip()
        if not path_arg:
            raise ValueError("Missing path.")
        target = self._safe_path(path_arg)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {path_arg}")
        text = target.read_text(encoding="utf-8", errors="replace")
        max_chars = min(20000, max(100, int(args.get("max_chars", 6000))))
        return {"path": str(target), "size_bytes": target.stat().st_size, "truncated": len(text) > max_chars, "content": text[:max_chars]}

    def _file_write(self, args: dict[str, Any]) -> dict[str, Any]:
        path_arg = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        if not path_arg:
            raise ValueError("Missing path.")
        target = self._safe_path(path_arg)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"written": True, "path": str(target), "bytes": len(content.encode("utf-8"))}

    def _create_note(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.db.create_note(str(args.get("title", "RYNA Note")), str(args.get("content", "")), args.get("remind_at"))

    def _diagnostics(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {
            "hardware": detect_hardware(self.config.project_root).to_dict(),
            "online": check_internet_connectivity(),
            "database": self.db.stats(),
            "computer": self.computer.capabilities(),
        }

    def _active_window(self, _args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.active_window_title()

    def _current_time(self, _args: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now()
        return {"time": now.strftime("%I:%M %p").lstrip("0"), "date": now.strftime("%A, %B %d, %Y"), "iso": now.isoformat()}

    def _screen_capture(self, _args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.capture_screen()

    def _open_app(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.launch_application(
            str(args.get("app", "")),
            prefer_search_bar=args.get("prefer_search_bar"),
        )

    def _close_app(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.close_application(str(args.get("app", "")))

    def _list_apps(self, _args: dict[str, Any]) -> dict[str, Any]:
        apps = self.computer.list_installed_apps()
        return {"count": len(apps), "apps": [a["name"] for a in apps[:60]]}

    def _open_folder(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.open_folder(str(args.get("folder", "")))

    def _volume_control(self, args: dict[str, Any]) -> dict[str, Any]:
        if "level" in args and args["level"] is not None:
            return self.computer.set_volume(int(args["level"]))
        return self.computer.volume_control(str(args.get("action", "up")), int(args.get("steps", 5)))

    def _brightness_control(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.brightness_control(str(args.get("mode", "up")), int(args.get("value", 0)))

    def _battery_status(self, _args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.battery_status()

    def _computer_shortcut(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.computer_shortcut(str(args.get("action", "")))

    def _open_settings_page(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.open_settings_page(str(args.get("page", "")))

    def _media_search(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.open_media_search(str(args.get("platform", "youtube")), str(args.get("query", "")))

    def _clipboard_get(self, _args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.clipboard_get()

    def _clipboard_set(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.clipboard_set(str(args.get("text", "")))

    def _system_info(self, _args: dict[str, Any]) -> dict[str, Any]:
        bat = self.computer.battery_status()
        telemetry = self.sys_mgr.sample_telemetry(battery_info=bat)
        telemetry["summary"] = self.sys_mgr.system_info_summary(battery_info=bat)
        return telemetry

    def _tech_doctor(self, args: dict[str, Any]) -> dict[str, Any]:
        recent = self.db.list_activity(limit=30)
        return self.sys_mgr.diagnose_issues(str(args.get("text", "")), recent_activity=recent)

    def _ocr_screen(self, args: dict[str, Any]) -> dict[str, Any]:
        src = str(args.get("source", "screen")).lower()
        if src == "file" and args.get("path"):
            img_path = self.sys_mgr.resolve_user_path(str(args["path"]))
            return self.sys_mgr.ocr_image(img_path)
        cap = self.computer.capture_screen()
        res = self.sys_mgr.ocr_image(Path(cap["path"]))
        res["active_window"] = cap.get("active_window", "")
        return res

    def _open_project(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.sys_mgr.find_and_open_project(str(args.get("name", "")))

    def _user_file_list(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.sys_mgr.list_user_folder(str(args.get("folder", "documents")))

    def _user_file_find(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.sys_mgr.find_user_files(str(args.get("query", "")), args.get("folder"))

    def _user_file_read(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.sys_mgr.read_user_file(str(args.get("path", "")))

    def _user_file_recycle(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.sys_mgr.recycle_user_file(str(args.get("path", "")))

    def _weather(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.sys_mgr.get_weather(str(args.get("city", "")))

    def _type_active_app(self, args: dict[str, Any]) -> dict[str, Any]:
        win = self.computer.active_window_title().get("title", "")
        return self.sys_mgr.type_in_active_app(str(args.get("text", "")), active_window_title=win)

    def _power_control(self, args: dict[str, Any]) -> dict[str, Any]:
        mode = str(args.get("mode", "request")).lower()
        action = str(args.get("action", "shutdown")).lower()
        if mode == "cancel":
            return self.sys_mgr.cancel_power_action()
        if mode == "confirm":
            return self.sys_mgr.confirm_power_action(action)
        return self.sys_mgr.request_power_action(action)

    def _web_search(self, args: dict[str, Any]) -> dict[str, Any]:
        q = str(args.get("query", "")).strip()
        if not q:
            raise ValueError("Missing search query.")
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(q)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RYNA/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                page = resp.read(250_000).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Web search unavailable: {exc}") from exc

        links = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', page)
        snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)</a>|<div[^>]+class="result__snippet"[^>]*>([\s\S]*?)</div>', page)
        results = []
        for idx, (href, raw_title) in enumerate(links[:5]):
            title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
            snip = ""
            if idx < len(snippets):
                raw_s = snippets[idx][0] or snippets[idx][1]
                snip = html.unescape(re.sub(r"<[^>]+>", "", raw_s)).strip()
            results.append({"title": title, "url": href, "snippet": snip})
        summary_block = "\n".join(f"- {r['title']}: {r['snippet']} ({r['url']})" for r in results)
        return {"query": q, "count": len(results), "results": results, "isolated": wrap_untrusted_web_content(url, summary_block)}

    def _web_fetch(self, args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("Only http:// and https:// URLs are permitted.")
        req = urllib.request.Request(url, headers={"User-Agent": "RYNA-Assistant/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                raw = resp.read(200_000).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc
        cleaned = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", " ", raw, flags=re.IGNORECASE)
        cleaned = html.unescape(re.sub(r"<[^>]+>", " ", cleaned))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return {"url": url, "isolated_content": wrap_untrusted_web_content(url, cleaned)}
