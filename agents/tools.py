"""Modular tool registry: AST calculator, sandboxed file ops, web fetch with prompt-injection isolation, notes, diagnostics, computer control."""

from __future__ import annotations

import ast
import html
import operator
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ai.config import RynaConfig
from ai.local_models.detector import check_internet_connectivity, detect_hardware
from computer.controller import ComputerController
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
    def __init__(self, config: RynaConfig, db: RynaDatabase, computer: ComputerController) -> None:
        self.config = config
        self.db = db
        self.computer = computer
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "calculate": self._calc,
            "file_search": self._file_search,
            "file_read": self._file_read,
            "file_write": self._file_write,
            "create_note": self._create_note,
            "system_diagnostics": self._diagnostics,
            "active_window": self._active_window,
            "open_application": self._open_app,
            "web_fetch": self._web_fetch,
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "calculate", "level": 1, "offline": True, "description": "AST-safe mathematical evaluation"},
            {"name": "file_search", "level": 1, "offline": True, "description": "Discover files inside allowed workspace root"},
            {"name": "file_read", "level": 1, "offline": True, "description": "Read UTF-8 text/code/data files inside allowed root"},
            {"name": "file_write", "level": 2, "offline": True, "description": "Create or save a text file inside allowed root (requires Level 2 approval)"},
            {"name": "create_note", "level": 1, "offline": True, "description": "Save a local note or reminder in SQLite"},
            {"name": "system_diagnostics", "level": 1, "offline": True, "description": "Live CPU, RAM, GPU, disk, and database telemetry"},
            {"name": "active_window", "level": 1, "offline": True, "description": "Inspect currently focused Windows desktop window"},
            {"name": "open_application", "level": 2, "offline": True, "description": "Launch an allowlisted Windows application (requires opt-in & confirmation)"},
            {"name": "web_fetch", "level": 1, "offline": False, "description": "Fetch public HTTP(S) webpage text with untrusted-content isolation"},
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

    def _open_app(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.computer.launch_application(str(args.get("app", "")))

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
