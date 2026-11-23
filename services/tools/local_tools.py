"""Safe, real local tools for offline RYNA operation.

Includes:
- AST-safe mathematical calculator (no eval)
- Scoped workspace file search & text/CSV reader
- Local notes & reminders persistence
- Live hardware & system diagnostics
"""

from __future__ import annotations

import ast
import operator
from pathlib import Path
from typing import Any, Callable

from core.python.config import RynaConfig
from core.python.database import RynaDatabase
from core.python.hardware import check_internet_connectivity, detect_hardware

SAFE_BIN_OPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

SAFE_UNARY_OPS: dict[type, Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def safe_calculate(expression: str) -> float:
    """Safely evaluate basic arithmetic expressions via AST without exec/eval."""
    node = ast.parse(expression.strip(), mode="eval")

    def _eval(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return _eval(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in SAFE_BIN_OPS:
            left = _eval(n.left)
            right = _eval(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 1000:
                raise ValueError("Exponent too large.")
            return SAFE_BIN_OPS[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and type(n.op) in SAFE_UNARY_OPS:
            return SAFE_UNARY_OPS[type(n.op)](_eval(n.operand))
        raise ValueError(f"Unsupported expression element: {type(n).__name__}")

    result = _eval(node)
    return int(result) if result.is_integer() else round(result, 8)


class LocalToolRegistry:
    """Registry of real, sandbox-scoped local tools."""

    def __init__(self, config: RynaConfig, db: RynaDatabase) -> None:
        self.config = config
        self.db = db
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "calculate": self._tool_calculate,
            "file_search": self._tool_file_search,
            "file_read": self._tool_file_read,
            "create_note": self._tool_create_note,
            "system_diagnostics": self._tool_system_diagnostics,
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "calculate", "level": 1, "offline": True, "description": "Safe arithmetic calculator"},
            {"name": "file_search", "level": 1, "offline": True, "description": "Search filenames inside allowed workspace root"},
            {"name": "file_read", "level": 1, "offline": True, "description": "Read UTF-8 text/CSV/Markdown/JSON files inside allowed workspace root"},
            {"name": "create_note", "level": 1, "offline": True, "description": "Create persistent local note or reminder in SQLite"},
            {"name": "system_diagnostics", "level": 1, "offline": True, "description": "Collect real CPU, RAM, disk, SQLite, and connectivity metrics"},
        ]

    def _allowed_root(self) -> Path:
        configured = str(self.db.get_setting("computer_control.allowed_root", "."))
        root = Path(configured)
        if not root.is_absolute():
            root = (self.config.project_root / root).resolve()
        else:
            root = root.resolve()
        return root

    def _resolve_safe_path(self, rel_or_abs: str) -> Path:
        root = self._allowed_root()
        candidate = Path(rel_or_abs)
        target = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if root != target and root not in target.parents:
            raise PermissionError(f"Access denied: path '{rel_or_abs}' is outside allowed root '{root}'.")
        return target

    def execute(self, tool_name: str, args: dict[str, Any] | None = None) -> Any:
        if tool_name not in self._handlers:
            raise ValueError(f"Unknown tool '{tool_name}'. Available tools: {sorted(self._handlers)}")
        return self._handlers[tool_name](args or {})

    def _tool_calculate(self, args: dict[str, Any]) -> dict[str, Any]:
        expr = str(args.get("expression", "")).strip()
        if not expr:
            raise ValueError("Missing 'expression' argument.")
        return {"expression": expr, "result": safe_calculate(expr)}

    def _tool_file_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip().lower()
        limit = min(100, max(1, int(args.get("limit", 25))))
        root = self._allowed_root()
        matches: list[str] = []
        skip_dirs = {".git", "__pycache__", ".venv", "node_modules", "target", "build", ".dart_tool"}
        for path in root.rglob("*"):
            if any(part in skip_dirs for part in path.parts):
                continue
            if path.is_file() and (not query or query in path.name.lower()):
                matches.append(str(path.relative_to(root)))
                if len(matches) >= limit:
                    break
        return {"root": str(root), "query": query, "matches": matches, "count": len(matches)}

    def _tool_file_read(self, args: dict[str, Any]) -> dict[str, Any]:
        path_arg = str(args.get("path", "")).strip()
        if not path_arg:
            raise ValueError("Missing 'path' argument.")
        max_chars = min(20000, max(100, int(args.get("max_chars", 4000))))
        target = self._resolve_safe_path(path_arg)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"File not found: {path_arg}")
        text = target.read_text(encoding="utf-8", errors="replace")
        return {
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "truncated": len(text) > max_chars,
            "content": text[:max_chars],
        }

    def _tool_create_note(self, args: dict[str, Any]) -> dict[str, Any]:
        title = str(args.get("title", "RYNA Note"))
        content = str(args.get("content", ""))
        remind_at = args.get("remind_at")
        return self.db.create_note(title=title, content=content, remind_at=remind_at)

    def _tool_system_diagnostics(self, _args: dict[str, Any]) -> dict[str, Any]:
        hw = detect_hardware(self.config.project_root, self.config.hardware_model_tiers)
        return {
            "hardware": hw.to_dict(),
            "online": check_internet_connectivity(),
            "database": self.db.stats(),
        }
