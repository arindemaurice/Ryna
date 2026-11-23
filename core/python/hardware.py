"""Hardware detection, connectivity checks, and hardware-aware model selection."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import socket
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class HardwareProfile:
    os_name: str
    os_version: str
    architecture: str
    cpu_logical_cores: int
    ram_total_gb: float
    ram_available_gb: float
    ram_used_percent: float
    disk_total_gb: float
    disk_free_gb: float
    python_version: str
    sqlite_version: str
    recommended_tier: str
    recommended_local_models: list[str]
    reduced_animations_recommended: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _detect_windows_memory() -> tuple[float, float, float]:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        total_gb = round(stat.ullTotalPhys / (1024**3), 2)
        avail_gb = round(stat.ullAvailPhys / (1024**3), 2)
        return total_gb, avail_gb, float(stat.dwMemoryLoad)
    return 0.0, 0.0, 0.0


def _detect_posix_memory() -> tuple[float, float, float]:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        values: dict[str, int] = {}
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].endswith(":"):
                try:
                    values[parts[0][:-1]] = int(parts[1])
                except ValueError:
                    pass
        total_kb = values.get("MemTotal", 0)
        avail_kb = values.get("MemAvailable", values.get("MemFree", 0))
        if total_kb > 0:
            total_gb = round(total_kb / (1024**2), 2)
            avail_gb = round(avail_kb / (1024**2), 2)
            used_pct = round(((total_kb - avail_kb) / total_kb) * 100.0, 1)
            return total_gb, avail_gb, used_pct
    return 0.0, 0.0, 0.0


def detect_memory() -> tuple[float, float, float]:
    try:
        if sys.platform == "win32":
            return _detect_windows_memory()
        return _detect_posix_memory()
    except Exception:
        return 0.0, 0.0, 0.0


def select_hardware_tier(
    ram_total_gb: float, tiers: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    default_tiers = tiers or [
        {
            "tier": "low",
            "max_ram_gb": 8,
            "recommended_local_models": ["qwen2.5:1.5b", "llama3.2:1b"],
            "reduced_animations": True,
        },
        {
            "tier": "medium",
            "max_ram_gb": 16,
            "recommended_local_models": ["qwen2.5:7b", "llama3.2:3b"],
            "reduced_animations": False,
        },
        {
            "tier": "high",
            "max_ram_gb": 64,
            "recommended_local_models": ["qwen2.5:14b", "llama3.1:8b"],
            "reduced_animations": False,
        },
        {
            "tier": "workstation",
            "max_ram_gb": 1024,
            "recommended_local_models": ["qwen2.5:32b", "llama3.3:70b"],
            "reduced_animations": False,
        },
    ]
    effective_ram = ram_total_gb if ram_total_gb > 0 else 8.0
    for tier in default_tiers:
        if effective_ram <= float(tier.get("max_ram_gb", 8)):
            return tier
    return default_tiers[-1]


def detect_hardware(
    storage_path: Path | None = None, tiers: list[dict[str, Any]] | None = None
) -> HardwareProfile:
    total_gb, avail_gb, used_pct = detect_memory()
    disk = shutil.disk_usage(str(storage_path or Path.cwd()))
    tier = select_hardware_tier(total_gb, tiers)
    return HardwareProfile(
        os_name=platform.system(),
        os_version=platform.version(),
        architecture=platform.machine(),
        cpu_logical_cores=os.cpu_count() or 1,
        ram_total_gb=total_gb,
        ram_available_gb=avail_gb,
        ram_used_percent=used_pct,
        disk_total_gb=round(disk.total / (1024**3), 2),
        disk_free_gb=round(disk.free / (1024**3), 2),
        python_version=platform.python_version(),
        sqlite_version=sqlite3.sqlite_version,
        recommended_tier=str(tier.get("tier", "low")),
        recommended_local_models=list(tier.get("recommended_local_models", [])),
        reduced_animations_recommended=bool(tier.get("reduced_animations", False)),
    )


def check_internet_connectivity(timeout: float = 1.5) -> bool:
    """Fast, non-intrusive TCP connectivity probe to detect online vs offline mode."""
    targets = [("1.1.1.1", 443), ("8.8.8.8", 53)]
    for host, port in targets:
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            return True
        except OSError:
            continue
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    return False
