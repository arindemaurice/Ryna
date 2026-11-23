"""Cross-platform single-instance guard (Win32 named Mutex on Windows, lock file fallback elsewhere)."""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
from pathlib import Path

ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    """Prevents duplicate RYNA engine instances."""

    def __init__(self, name: str = "RYNA_SINGLE_INSTANCE") -> None:
        self.name = name
        self._handle: int | None = None
        self._lock_path = Path(tempfile.gettempdir()) / f"{name}.lock"

    def acquire(self) -> bool:
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
            if not handle:
                return False
            last_err = kernel32.GetLastError()
            if last_err == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            self._handle = int(handle)
            return True

        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            self._handle = 1
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        if sys.platform == "win32" and self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        elif self._handle:
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._handle = None
