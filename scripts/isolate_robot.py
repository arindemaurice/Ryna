"""Extract clean transparent RYNA robot PNG, head avatar PNG, and Windows .ico
from a chroma-keyed (magenta #FF00FF) isolated robot image with edge despill.
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

ASSETS_DIR = Path(__file__).resolve().parents[1] / "app" / "desktop" / "assets"


def extract_magenta_to_rgba(src_path: Path, out_robot: Path, out_head: Path, out_ico: Path) -> None:
    img = Image.open(src_path).convert("RGBA")
    arr = np.array(img, dtype=np.float32)
    h, w, _ = arr.shape
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # Measure magenta-ness: high R and B, low G
    magenta_score = np.minimum(r, b) - g
    is_bg_candidate = (magenta_score > 55.0) & (r > 120.0) & (b > 120.0) & (g < 150.0)

    # Flood-fill from image border so we only remove exterior background
    visited = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        if is_bg_candidate[0, x]:
            visited[0, x] = True
            q.append((0, x))
        if is_bg_candidate[h - 1, x]:
            visited[h - 1, x] = True
            q.append((h - 1, x))
    for y in range(h):
        if is_bg_candidate[y, 0] and not visited[y, 0]:
            visited[y, 0] = True
            q.append((y, 0))
        if is_bg_candidate[y, w - 1] and not visited[y, w - 1]:
            visited[y, w - 1] = True
            q.append((y, w - 1))

    while q:
        cy, cx = q.popleft()
        for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and is_bg_candidate[ny, nx]:
                visited[ny, nx] = True
                q.append((ny, nx))

    alpha = np.where(visited, 0.0, 255.0)

    # Soften & despill boundary fringe pixels adjacent to transparent background
    padded = np.pad(visited, 1, mode="constant", constant_values=True)
    near_bg = (
        padded[:-2, 1:-1] | padded[2:, 1:-1] | padded[1:-1, :-2] | padded[1:-1, 2:]
        | padded[:-2, :-2] | padded[:-2, 2:] | padded[2:, :-2] | padded[2:, 2:]
    )
    fringe = (~visited) & near_bg & (magenta_score > 12.0)
    # Feather alpha on magenta-tinted fringe pixels
    alpha = np.where(fringe, np.clip(255.0 - magenta_score * 3.0, 0.0, 255.0), alpha)

    # Despill any remaining magenta cast along edges (clamp R & B toward G/neighbor luminance)
    edge_despill = (~visited) & (magenta_score > 18.0)
    new_r = np.where(edge_despill, np.minimum(r, g + 16.0), r)
    new_b = np.where(edge_despill, np.minimum(b, g + 28.0), b)

    out_arr = np.stack([new_r, g, new_b, alpha], axis=-1).clip(0, 255).astype(np.uint8)
    rgba = Image.fromarray(out_arr, mode="RGBA")

    # Tightly crop around non-transparent RYNA robot with small padding
    bbox = rgba.getbbox()
    if bbox:
        pad = 12
        x0, y0, x1, y1 = max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(w, bbox[2] + pad), min(h, bbox[3] + pad)
        rgba = rgba.crop((x0, y0, x1, y1))

    out_robot.parent.mkdir(parents=True, exist_ok=True)
    rgba.thumbnail((512, 512), Image.Resampling.LANCZOS)
    rgba.save(out_robot, format="PNG")

    # Create clean cropped head avatar for HUD header & system tray icon
    rw, rh = rgba.size
    head_box = (int(rw * 0.36), 0, rw, int(rh * 0.43))
    head = rgba.crop(head_box)
    hb = head.getbbox()
    if hb:
        head = head.crop(hb)
    # Center inside a square 128x128 transparent canvas
    side = max(head.size) + 12
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(head, ((side - head.width) // 2, (side - head.height) // 2), head)
    square_128 = square.resize((128, 128), Image.Resampling.LANCZOS)
    square_128.save(out_head, format="PNG")
    square_128.save(out_ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128)])

    print(f"Extracted RYNA robot PNG : {out_robot} ({rgba.size})")
    print(f"Extracted RYNA head PNG  : {out_head} ({square_128.size})")
    print(f"Extracted RYNA tray ICO  : {out_ico}")


def extract_single_pose(src_path: Path, out_png: Path, max_size: int = 512) -> None:
    """Extract a single chroma-keyed magenta (#FF00FF) robot pose into a clean transparent RGBA PNG."""
    img = Image.open(src_path).convert("RGBA")
    arr = np.array(img, dtype=np.float32)
    h, w, _ = arr.shape
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    magenta_score = np.minimum(r, b) - g
    is_bg = (magenta_score > 55.0) & (r > 120.0) & (b > 120.0) & (g < 150.0)

    visited = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        if is_bg[0, x]:
            visited[0, x] = True
            q.append((0, x))
        if is_bg[h - 1, x]:
            visited[h - 1, x] = True
            q.append((h - 1, x))
    for y in range(h):
        if is_bg[y, 0] and not visited[y, 0]:
            visited[y, 0] = True
            q.append((y, 0))
        if is_bg[y, w - 1] and not visited[y, w - 1]:
            visited[y, w - 1] = True
            q.append((y, w - 1))

    while q:
        cy, cx = q.popleft()
        for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and is_bg[ny, nx]:
                visited[ny, nx] = True
                q.append((ny, nx))

    alpha = np.where(visited, 0.0, 255.0)
    padded = np.pad(visited, 1, mode="constant", constant_values=True)
    near_bg = (
        padded[:-2, 1:-1] | padded[2:, 1:-1] | padded[1:-1, :-2] | padded[1:-1, 2:]
        | padded[:-2, :-2] | padded[:-2, 2:] | padded[2:, :-2] | padded[2:, 2:]
    )
    fringe = (~visited) & near_bg & (magenta_score > 12.0)
    alpha = np.where(fringe, np.clip(255.0 - magenta_score * 3.0, 0.0, 255.0), alpha)
    despill = (~visited) & (magenta_score > 18.0)
    new_r = np.where(despill, np.minimum(r, g + 16.0), r)
    new_b = np.where(despill, np.minimum(b, g + 28.0), b)

    rgba = Image.fromarray(np.stack([new_r, g, new_b, alpha], axis=-1).clip(0, 255).astype(np.uint8), mode="RGBA")
    bbox = rgba.getbbox()
    if bbox:
        pad = 12
        rgba = rgba.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(w, bbox[2] + pad), min(h, bbox[3] + pad)))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    rgba.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    rgba.save(out_png, format="PNG")
    print(f"Extracted pose -> {out_png} ({rgba.size})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract transparent RYNA robot assets from chroma-keyed image.")
    parser.add_argument("source", help="Path to source magenta-background robot image")
    parser.add_argument("--out", default=None, help="Optional single output PNG path (pose extraction mode)")
    args = parser.parse_args()
    if args.out:
        extract_single_pose(Path(args.source), Path(args.out))
        return 0
    extract_magenta_to_rgba(
        Path(args.source),
        ASSETS_DIR / "ryna_robot.png",
        ASSETS_DIR / "ryna_head.png",
        ASSETS_DIR / "ryna_icon.ico",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
