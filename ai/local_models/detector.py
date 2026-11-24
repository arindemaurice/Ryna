"""Real hardware detection (CPU, RAM, disk, NVIDIA GPU/VRAM) and hardware-aware model selection."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
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
    gpu_name: str
    gpu_vram_gb: float
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

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return round(st.ullTotalPhys / (1024**3), 2), round(st.ullAvailPhys / (1024**3), 2), float(st.dwMemoryLoad)
    return 0.0, 0.0, 0.0


def _detect_nvidia_gpu() -> tuple[str, float]:
    if not shutil.which("nvidia-smi"):
        return "Not detected (CPU/integrated GPU)", 0.0
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().splitlines()[0].split(",")]
            if len(parts) >= 2:
                return parts[0], round(float(parts[1]) / 1024.0, 2)
    except Exception:
        pass
    return "Not detected (CPU/integrated GPU)", 0.0


def select_hardware_tier(ram_total_gb: float) -> dict[str, Any]:
    ram = ram_total_gb if ram_total_gb > 0 else 8.0
    if ram <= 8.5:
        return {"tier": "low", "models": ["qwen2.5:1.5b", "llama3.2:1b"], "reduced_animations": True}
    if ram <= 18.0:
        return {"tier": "medium", "models": ["qwen2.5:3b", "qwen3.5:4b", "qwen2.5:7b"], "reduced_animations": False}
    if ram <= 64.0:
        return {"tier": "high", "models": ["qwen2.5:14b", "llama3.1:8b"], "reduced_animations": False}
    return {"tier": "workstation", "models": ["qwen2.5:32b", "llama3.3:70b"], "reduced_animations": False}


def detect_hardware(storage_path: Path | None = None) -> HardwareProfile:
    total_gb, avail_gb, used_pct = _detect_windows_memory() if sys.platform == "win32" else (0.0, 0.0, 0.0)
    gpu_name, vram_gb = _detect_nvidia_gpu()
    disk = shutil.disk_usage(str(storage_path or Path.cwd()))
    tier = select_hardware_tier(total_gb)
    return HardwareProfile(
        os_name=platform.system(),
        os_version=platform.version(),
        architecture=platform.machine(),
        cpu_logical_cores=os.cpu_count() or 1,
        ram_total_gb=total_gb,
        ram_available_gb=avail_gb,
        ram_used_percent=used_pct,
        gpu_name=gpu_name,
        gpu_vram_gb=vram_gb,
        disk_total_gb=round(disk.total / (1024**3), 2),
        disk_free_gb=round(disk.free / (1024**3), 2),
        python_version=platform.python_version(),
        sqlite_version=sqlite3.sqlite_version,
        recommended_tier=tier["tier"],
        recommended_local_models=tier["models"],
        reduced_animations_recommended=tier["reduced_animations"],
    )


def check_internet_connectivity(timeout: float = 1.5) -> bool:
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            return True
        except OSError:
            continue
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
    return False
