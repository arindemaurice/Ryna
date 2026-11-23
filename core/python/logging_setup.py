"""Structured logging configuration for RYNA."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    """Configure console + rotating file logger for RYNA."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ryna.log"

    logger = logging.getLogger("ryna")
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(numeric_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(numeric_level)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger
