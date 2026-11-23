"""Hardware-aware local model detection and connectivity probing."""

from .detector import HardwareProfile, check_internet_connectivity, detect_hardware, select_hardware_tier

__all__ = ["HardwareProfile", "check_internet_connectivity", "detect_hardware", "select_hardware_tier"]
