"""Helper utility to install/crop the user's supplied RYNA robot reference screenshot
into the authoritative transparent asset path: app/desktop/assets/ryna_robot.png.

Usage:
    python scripts/isolate_robot.py <source_image_path> [--crop x1,y1,x2,y2]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "app" / "desktop" / "assets" / "ryna_robot.png"


def main() -> int:
    parser = argparse.ArgumentParser(description="Install or crop authoritative RYNA robot PNG asset.")
    parser.add_argument("source", help="Path to user-supplied robot image/screenshot")
    parser.add_argument("--crop", default=None, help="Optional crop box x1,y1,x2,y2 (requires Pillow)")
    args = parser.parse_args()

    src = Path(args.source).resolve()
    if not src.is_file():
        print(f"Error: source image not found: {src}")
        return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if args.crop:
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError:
            print("Error: Pillow is required for --crop (pip install Pillow).")
            return 1
        box = tuple(int(v.strip()) for v in args.crop.split(","))
        if len(box) != 4:
            print("Error: --crop must be 4 integers: x1,y1,x2,y2")
            return 1
        img = Image.open(src).convert("RGBA").crop(box)
        img.save(TARGET, format="PNG")
    else:
        shutil.copy2(src, TARGET)

    print(f"Installed authoritative RYNA robot asset -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
