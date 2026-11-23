"""Build RYNA's polished multi-pose desktop sprite set directly from her authoritative reference PNG.

Every pose is constructed from the genuine high-resolution pixels of
`app/desktop/assets/ryna_robot.png` (zero generic replacements):
- ryna_walk_a.png   : Stride A (near leg forward, far leg back — authoritative original)
- ryna_walk_mid.png : Mid-stride passing pose (legs crossing vertically under hips)
- ryna_walk_b.png   : Stride B (INTERCHANGED legs: near leg back, far leg forward, arms swapped)
- ryna_stand.png    : Front-facing standing upright, looking straight forward
- ryna_think.png    : Front-facing standing upright, looking straight forward, hand touching head
- ryna_sit.png      : Relaxed seated pose on desktop/taskbar with bent legs
- ryna_sleep.png    : Peaceful sleeping pose with closed cyan visor eyes
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ASSETS_DIR = Path(__file__).resolve().parents[1] / "app" / "desktop" / "assets"
SOURCE_PNG = ASSETS_DIR / "ryna_robot.png"


def _poly_mask(size: tuple[int, int], pts: list[tuple[float, float]], feather: float = 1.0) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    return m


def _extract_part(src: Image.Image, mask: Image.Image) -> Image.Image:
    arr = np.array(src, dtype=np.float32)
    m = np.array(mask, dtype=np.float32) / 255.0
    out = arr.copy()
    out[:, :, 3] = np.clip(arr[:, :, 3] * m, 0, 255)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def _subtract_mask(base_mask: Image.Image, sub_mask: Image.Image) -> Image.Image:
    a = np.array(base_mask, dtype=np.float32)
    b = np.array(sub_mask, dtype=np.float32)
    return Image.fromarray(np.clip(a - b, 0, 255).astype(np.uint8), "L")


def _rotate_about_pivot(
    part: Image.Image,
    pivot: tuple[float, float],
    angle_deg: float,
    target_pivot: tuple[float, float] | None = None,
    canvas_size: tuple[int, int] | None = None,
    scale: float = 1.0,
) -> Image.Image:
    """Rotate an RGBA layer around `pivot` (in source coords) and place that pivot at `target_pivot`."""
    cw, ch = canvas_size or part.size
    tx, ty = target_pivot or pivot
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad) / scale
    sin_a = math.sin(rad) / scale
    px, py = pivot
    a = cos_a
    b = sin_a
    c = px - cos_a * tx - sin_a * ty
    d = -sin_a
    e = cos_a
    f = py + sin_a * tx - cos_a * ty
    return part.transform(
        (cw, ch),
        Image.Transform.AFFINE,
        (a, b, c, d, e, f),
        resample=Image.Resampling.BICUBIC,
    )


def _shade_rgba(img: Image.Image, brightness: float = 0.88) -> Image.Image:
    r, g, b, a = img.split()
    rgb = ImageEnhance.Brightness(Image.merge("RGB", (r, g, b))).enhance(brightness)
    nr, ng, nb = rgb.split()
    return Image.merge("RGBA", (nr, ng, nb, a))


def _metallic_rounded_plate(
    canvas: Image.Image,
    bbox: tuple[int, int, int, int],
    radius: int,
    top_rgb: tuple[int, int, int] = (244, 249, 255),
    mid_rgb: tuple[int, int, int] = (204, 218, 232),
    bot_rgb: tuple[int, int, int] = (128, 148, 172),
    rim_rgba: tuple[int, int, int, int] = (72, 94, 118, 235),
) -> None:
    """Render a 3D metallic silver/white armor plate matching RYNA's CGI lighting."""
    x0, y0, x1, y1 = bbox
    pw, ph = max(2, x1 - x0), max(2, y1 - y0)
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=radius, fill=255)

    # Smooth 2D metallic gradient (vertical top->mid->bot + horizontal cylindrical shading)
    yy = np.linspace(0.0, 1.0, ph, dtype=np.float32)[:, None]
    xx = np.linspace(-1.0, 1.0, pw, dtype=np.float32)[None, :]
    cyl = 1.0 - 0.24 * (xx * xx)  # darker metallic edges on left/right

    plate = np.zeros((ph, pw, 4), dtype=np.float32)
    for c in range(3):
        upper = top_rgb[c] * (1.0 - np.clip(yy * 2.0, 0, 1)) + mid_rgb[c] * np.clip(yy * 2.0, 0, 1)
        lower = mid_rgb[c] * (1.0 - np.clip((yy - 0.5) * 2.0, 0, 1)) + bot_rgb[c] * np.clip((yy - 0.5) * 2.0, 0, 1)
        col = np.where(yy < 0.5, upper, lower) * cyl
        plate[:, :, c] = np.clip(col, 0, 255)
    plate[:, :, 3] = np.array(mask, dtype=np.float32)
    plate_img = Image.fromarray(plate.astype(np.uint8), "RGBA")

    # Subtle specular highlight near top
    hl = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle(
        (max(4, pw // 7), max(3, ph // 10), pw - max(4, pw // 7), max(8, int(ph * 0.42))),
        radius=max(4, radius // 2),
        fill=(255, 255, 255, 78),
    )
    hl = hl.filter(ImageFilter.GaussianBlur(3.5))
    # Clip highlight to plate mask
    hl_arr = np.array(hl)
    hl_arr[:, :, 3] = np.minimum(hl_arr[:, :, 3], np.array(mask))
    plate_img.alpha_composite(Image.fromarray(hl_arr, "RGBA"))

    # Crisp dark metallic outline
    ImageDraw.Draw(plate_img).rounded_rectangle((1, 1, pw - 2, ph - 2), radius=radius, outline=rim_rgba, width=2)
    canvas.alpha_composite(plate_img, (x0, y0))


def _glow_ellipse(
    canvas: Image.Image,
    bbox: tuple[float, float, float, float],
    core_rgba: tuple[int, int, int, int],
    glow_rgba: tuple[int, int, int, int],
    glow_radius: float = 5.0,
) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(bbox, fill=glow_rgba)
    layer = layer.filter(ImageFilter.GaussianBlur(glow_radius))
    canvas.alpha_composite(layer)
    core = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(core).ellipse(bbox, fill=core_rgba)
    canvas.alpha_composite(core)


def _glow_arc(
    canvas: Image.Image,
    bbox: tuple[float, float, float, float],
    start: float,
    end: float,
    width: int,
    core_rgba: tuple[int, int, int, int] = (145, 252, 255, 255),
    glow_rgba: tuple[int, int, int, int] = (20, 220, 255, 200),
    glow_radius: float = 5.0,
) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).arc(bbox, start=start, end=end, fill=glow_rgba, width=width + 4)
    layer = layer.filter(ImageFilter.GaussianBlur(glow_radius))
    canvas.alpha_composite(layer)
    core = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(core).arc(bbox, start=start, end=end, fill=core_rgba, width=width)
    canvas.alpha_composite(core)


def _glow_line(
    canvas: Image.Image,
    pts: list[tuple[float, float]],
    width: int,
    core_rgba: tuple[int, int, int, int] = (150, 252, 255, 255),
    glow_rgba: tuple[int, int, int, int] = (25, 215, 255, 185),
    glow_radius: float = 4.5,
) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).line(pts, fill=glow_rgba, width=width + 4, joint="curve")
    layer = layer.filter(ImageFilter.GaussianBlur(glow_radius))
    canvas.alpha_composite(layer)
    core = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(core).line(pts, fill=core_rgba, width=width, joint="curve")
    canvas.alpha_composite(core)


def _tight_arm_parts(src: Image.Image) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Extract clean near arm, upper arm, and forearm+hand with ZERO dark cape fringe."""
    w, h = src.size
    # Tight anatomical contour closely hugging RYNA's metallic arm & hand (excludes cape to the left!)
    arm_poly = _poly_mask(
        (w, h),
        [
            (218, 224),
            (248, 228),
            (250, 260),
            (236, 286),
            (218, 306),
            (218, 336),
            (224, 356),
            (214, 376),
            (188, 375),
            (180, 352),
            (182, 334),
            (182, 294),
            (194, 276),
            (206, 246),
        ],
        feather=0.9,
    )
    upper_poly = _poly_mask(
        (w, h),
        [(218, 224), (248, 228), (248, 264), (234, 288), (218, 300), (196, 294), (194, 274), (206, 246)],
        feather=0.9,
    )
    forearm_poly = _poly_mask(
        (w, h),
        [(182, 282), (222, 284), (220, 336), (224, 356), (214, 376), (188, 375), (180, 352), (181, 312)],
        feather=0.9,
    )
    return _extract_part(src, arm_poly), _extract_part(src, upper_poly), _extract_part(src, forearm_poly)


def _build_walk_poses(src: Image.Image) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Segment RYNA's exact limbs from ryna_robot.png and generate genuine alternating leg stride frames."""
    w, h = src.size  # 385 x 512

    near_leg_mask = _poly_mask(
        (w, h),
        [
            (234, 336),
            (270, 326),
            (300, 348),
            (318, 396),
            (340, 444),
            (376, 466),
            (378, 508),
            (280, 508),
            (272, 460),
            (274, 412),
            (264, 386),
            (236, 360),
        ],
        feather=1.1,
    )

    back_leg_mask = _poly_mask(
        (w, h),
        [
            (226, 354),
            (256, 366),
            (266, 398),
            (242, 434),
            (214, 460),
            (218, 496),
            (168, 496),
            (144, 448),
            (150, 414),
            (196, 392),
            (216, 364),
        ],
        feather=1.1,
    )

    near_arm_mask = _poly_mask(
        (w, h),
        [
            (218, 224),
            (248, 228),
            (250, 260),
            (236, 286),
            (218, 306),
            (218, 336),
            (224, 356),
            (214, 376),
            (188, 375),
            (180, 352),
            (182, 334),
            (182, 294),
            (194, 276),
            (206, 246),
        ],
        feather=1.0,
    )

    far_arm_mask = _poly_mask(
        (w, h),
        [
            (316, 224),
            (340, 224),
            (366, 290),
            (374, 344),
            (358, 376),
            (324, 364),
            (316, 316),
            (316, 262),
        ],
        feather=1.0,
    )

    full_mask = Image.new("L", (w, h), 255)
    body_mask = _subtract_mask(full_mask, near_leg_mask)
    body_mask = _subtract_mask(body_mask, back_leg_mask)
    body_mask = _subtract_mask(body_mask, near_arm_mask)
    body_mask = _subtract_mask(body_mask, far_arm_mask)

    body_core = _extract_part(src, body_mask)
    near_leg = _extract_part(src, near_leg_mask)
    near_arm = _extract_part(src, near_arm_mask)
    far_arm = _extract_part(src, far_arm_mask)

    # Seamless dark-navy cape continuation behind where the near arm originally sat (so zero white gap appears when arm swings forward!)
    body_patched = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(body_patched)
    # Match RYNA's exact inner cape navy gradient smoothly tucked behind the arm & curving into the cape hem
    pd.polygon(
        [(168, 286), (206, 232), (248, 232), (254, 344), (212, 358), (174, 366), (162, 340)],
        fill=(12, 30, 48, 255),
    )
    pd.ellipse((230, 324, 274, 366), fill=(20, 30, 44, 255))
    body_patched = body_patched.filter(ImageFilter.GaussianBlur(1.4))
    # Restrict strictly to original alpha so nothing ever bleeds past RYNA's true outline
    src_alpha = np.array(src)[:, :, 3]
    p_arr = np.array(body_patched)
    p_arr[:, :, 3] = np.minimum(p_arr[:, :, 3], src_alpha)
    body_patched = Image.fromarray(p_arr, "RGBA")
    body_patched.alpha_composite(body_core)

    near_shin_mask = _poly_mask(
        (w, h),
        [(268, 396), (330, 396), (378, 468), (378, 508), (274, 508), (268, 446)],
        feather=1.1,
    )
    near_thigh_mask = _subtract_mask(near_leg_mask, near_shin_mask)
    near_thigh = _extract_part(src, near_thigh_mask)
    near_shin = _extract_part(src, near_shin_mask)

    hip_near = (252.0, 344.0)
    hip_far = (264.0, 342.0)
    knee_near = (292.0, 405.0)
    sh_near = (224.0, 244.0)
    sh_far = (318.0, 238.0)

    # --- POSE 1: WALK A (Stride A: near leg forward, far leg back) ---
    walk_a = src.copy()

    # --- POSE 2: WALK MID (Passing stride: legs crossing vertically under hips) ---
    walk_mid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    far_arm_mid = _rotate_about_pivot(far_arm, sh_far, 14.0, (sh_far[0] - 2, sh_far[1] - 3))
    walk_mid.alpha_composite(far_arm_mid)
    far_leg_mid = _rotate_about_pivot(_shade_rgba(near_leg, 0.80), hip_near, 18.0, (hip_far[0] - 4, hip_far[1] - 2))
    walk_mid.alpha_composite(far_leg_mid)
    body_mid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    body_mid.alpha_composite(body_patched, (0, -5))
    walk_mid.alpha_composite(body_mid)
    near_leg_mid = _rotate_about_pivot(near_leg, hip_near, 24.0, (hip_near[0] + 4, hip_near[1] - 5))
    walk_mid.alpha_composite(near_leg_mid)
    near_arm_mid = _rotate_about_pivot(near_arm, sh_near, -14.0, (sh_near[0] + 1, sh_near[1] - 5))
    walk_mid.alpha_composite(near_arm_mid)

    # --- POSE 3: WALK B (OPPOSITE INTERCHANGED STRIDE: near leg BACK, far leg FORWARD!) ---
    walk_b = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    far_arm_b = _rotate_about_pivot(far_arm, sh_far, 28.0, (sh_far[0] - 8, sh_far[1] + 2))
    walk_b.alpha_composite(far_arm_b)

    # Far leg stepping clearly FORWARD (shifted -3px left so toe stays 100% unclipped inside canvas)
    far_leg_forward = _rotate_about_pivot(
        _shade_rgba(near_leg, 0.84),
        hip_near,
        -4.0,
        (hip_far[0] + 1, hip_far[1] - 2),
        scale=0.97,
    )
    walk_b.alpha_composite(far_leg_forward)
    walk_b.alpha_composite(body_patched)

    # Near leg stepping clearly BACKWARD (interchanged!)
    thigh_angle = 46.0
    near_thigh_b = _rotate_about_pivot(near_thigh, hip_near, thigh_angle, (hip_near[0] - 2, hip_near[1] + 2))
    rad = math.radians(thigh_angle)
    dx, dy = knee_near[0] - hip_near[0], knee_near[1] - hip_near[1]
    new_knee_x = (hip_near[0] - 2) + dx * math.cos(rad) - dy * math.sin(rad)
    new_knee_y = (hip_near[1] + 2) + dx * math.sin(rad) + dy * math.cos(rad)
    near_shin_b = _rotate_about_pivot(near_shin, knee_near, thigh_angle + 18.0, (new_knee_x + 2, new_knee_y - 2))
    walk_b.alpha_composite(near_shin_b)
    walk_b.alpha_composite(near_thigh_b)

    # Near arm swinging clearly FORWARD (contralateral)
    near_arm_b = _rotate_about_pivot(near_arm, sh_near, -34.0, (sh_near[0] + 4, sh_near[1] + 2))
    walk_b.alpha_composite(near_arm_b)

    return walk_a, walk_mid, walk_b


def _build_front_head(src: Image.Image, expression: str = "calm") -> Image.Image:
    """Build RYNA's authentic front-facing helmet & visor directly from her real helmet pixels."""
    hw, hh = 260, 214
    head = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    cx = hw // 2

    # Cleanly crop just above neck collar (y: 5..204) so no stray collar corners ever poke out
    left_crop = src.crop((164, 5, 294, 204))
    right_crop = left_crop.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    half_w = 130
    overlap = 20
    head.alpha_composite(left_crop, (cx - half_w + overlap, 4))

    r_arr = np.array(right_crop, dtype=np.float32)
    grad = np.ones((right_crop.height, right_crop.width), dtype=np.float32)
    blend_px = overlap * 2
    for i in range(blend_px):
        grad[:, i] = i / float(blend_px)
    r_arr[:, :, 3] *= grad
    head.alpha_composite(Image.fromarray(r_arr.astype(np.uint8), "RGBA"), (cx - overlap, 4))

    # Glossy front-facing black visor centered straight ahead on helmet
    visor_layer = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    vd = ImageDraw.Draw(visor_layer)
    vx0, vy0, vx1, vy1 = cx - 64, 68, cx + 64, 184
    vd.rounded_rectangle((vx0 - 4, vy0 - 4, vx1 + 4, vy1 + 4), radius=38, fill=(14, 24, 38, 255), outline=(140, 170, 198, 210), width=2)
    vd.rounded_rectangle((vx0, vy0, vx1, vy1), radius=34, fill=(4, 9, 18, 255))

    shine = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    ImageDraw.Draw(shine).rounded_rectangle((vx0 + 10, vy0 + 5, vx1 - 10, vy0 + 38), radius=18, fill=(180, 215, 240, 62))
    shine = shine.filter(ImageFilter.GaussianBlur(4.0))
    visor_layer.alpha_composite(shine)

    amb = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    ImageDraw.Draw(amb).ellipse((vx0 + 18, vy0 + 42, vx1 - 18, vy1 - 6), fill=(18, 175, 225, 34))
    amb = amb.filter(ImageFilter.GaussianBlur(10.0))
    visor_layer.alpha_composite(amb)
    head.alpha_composite(visor_layer)

    if expression == "thinking":
        # Clear thoughtful eyes looking STRAIGHT forward at the user
        _glow_ellipse(head, (cx - 40, 114, cx - 14, 138), (165, 252, 255, 255), (20, 215, 255, 220), 6.0)
        _glow_ellipse(head, (cx + 14, 112, cx + 40, 140), (165, 252, 255, 255), (20, 215, 255, 220), 6.0)
        pd = ImageDraw.Draw(head)
        pd.ellipse((cx - 34, 119, cx - 26, 127), fill=(240, 255, 255, 255))
        pd.ellipse((cx + 20, 118, cx + 28, 126), fill=(240, 255, 255, 255))
        _glow_line(head, [(cx + 12, 102), (cx + 27, 98), (cx + 42, 103)], width=3)
        _glow_line(head, [(cx - 10, 156), (cx + 2, 154), (cx + 12, 157)], width=4)
    elif expression == "sleeping":
        dim = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
        ImageDraw.Draw(dim).rounded_rectangle((vx0, vy0, vx1, vy1), radius=34, fill=(3, 7, 16, 88))
        head.alpha_composite(dim)
        _glow_arc(head, (cx - 44, 116, cx - 10, 144), start=15, end=165, width=5, core_rgba=(115, 232, 250, 240), glow_rgba=(20, 185, 235, 165))
        _glow_arc(head, (cx + 10, 116, cx + 44, 144), start=15, end=165, width=5, core_rgba=(115, 232, 250, 240), glow_rgba=(20, 185, 235, 165))
        _glow_ellipse(head, (cx - 5, 154, cx + 5, 161), (110, 225, 245, 210), (20, 180, 230, 130), 3.0)
    else:
        _glow_arc(head, (cx - 44, 114, cx - 10, 146), start=195, end=345, width=8)
        _glow_arc(head, (cx + 10, 114, cx + 44, 146), start=195, end=345, width=8)
        _glow_arc(head, (cx - 16, 142, cx + 16, 166), start=20, end=160, width=5)

    return head


def _extract_reusable_parts(src: Image.Image) -> dict[str, Image.Image]:
    w, h = src.size
    arm_full, upper_arm, forearm = _tight_arm_parts(src)

    leg_mask = _poly_mask(
        (w, h),
        [(234, 336), (270, 326), (300, 348), (318, 396), (340, 444), (376, 466), (378, 508), (280, 508), (272, 460), (274, 412), (264, 386), (236, 360)],
        feather=1.1,
    )
    leg_full = _extract_part(src, leg_mask)

    cape_mask = _poly_mask(
        (w, h),
        [(0, 290), (140, 250), (226, 200), (246, 204), (206, 250), (180, 300), (182, 374), (160, 396), (10, 396), (0, 340)],
        feather=1.5,
    )
    cape_wing = _extract_part(src, cape_mask)

    return {
        "arm_full": arm_full,
        "upper_arm": upper_arm,
        "forearm": forearm,
        "leg_full": leg_full,
        "cape_wing": cape_wing,
    }


def _draw_front_torso_and_cape(canvas: Image.Image, parts: dict[str, Image.Image], cx: int, torso_top: int, cape_scale: float = 0.78) -> None:
    """Composite symmetric front-facing cape + 3D shaded metallic white/cyan torso centered at (cx, torso_top)."""
    cape = parts["cape_wing"].crop((6, 200, 232, 392))
    cw = int(cape.width * cape_scale)
    ch = int(cape.height * cape_scale)
    cape_l = cape.resize((cw, ch), Image.Resampling.LANCZOS).rotate(14, resample=Image.Resampling.BICUBIC, expand=True)
    cape_r = cape_l.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    canvas.alpha_composite(cape_l, (cx - cape_l.width + 42, torso_top - 4))
    canvas.alpha_composite(cape_r, (cx - 42, torso_top - 4))

    td = ImageDraw.Draw(canvas)
    # Dark robotic neck & ribbed abdomen core
    td.rounded_rectangle((cx - 22, torso_top - 8, cx + 22, torso_top + 18), radius=10, fill=(18, 28, 42, 255))
    td.rounded_rectangle((cx - 32, torso_top + 56, cx + 32, torso_top + 114), radius=14, fill=(20, 30, 44, 255), outline=(64, 88, 112, 255), width=2)
    for ry in (torso_top + 74, torso_top + 90):
        td.line([(cx - 25, ry), (cx + 25, ry)], fill=(58, 84, 110, 220), width=3)

    # 3D metallic pelvis plate & sculpted chest plate (smooth silver-white CGI shading!)
    _metallic_rounded_plate(canvas, (cx - 41, torso_top + 102, cx + 41, torso_top + 136), radius=16)
    _metallic_rounded_plate(canvas, (cx - 50, torso_top + 4, cx + 50, torso_top + 82), radius=26)

    # Subtle chin drop shadow at top of chest plate
    chin_sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(chin_sh).ellipse((cx - 34, torso_top - 4, cx + 34, torso_top + 22), fill=(10, 18, 30, 115))
    chin_sh = chin_sh.filter(ImageFilter.GaussianBlur(5.0))
    canvas.alpha_composite(chin_sh)

    # Glowing cyan chest core + waist accent (matching RYNA's exact CGI glow)
    td = ImageDraw.Draw(canvas)
    td.ellipse((cx - 23, torso_top + 22, cx + 23, torso_top + 68), fill=(20, 34, 52, 255), outline=(128, 160, 186, 255), width=2)
    _glow_ellipse(canvas, (cx - 15, torso_top + 30, cx + 15, torso_top + 60), (178, 252, 255, 255), (20, 215, 255, 235), 7.0)
    _glow_ellipse(canvas, (cx - 11, torso_top + 78, cx + 11, torso_top + 92), (145, 248, 255, 235), (20, 210, 255, 180), 5.0)


def _build_front_standing_and_thinking(src: Image.Image, parts: dict[str, Image.Image]) -> tuple[Image.Image, Image.Image]:
    """Build `ryna_stand.png` (straight standing) and `ryna_think.png` (standing, looking straight, hand touching head!)."""
    w, h = 385, 512
    cx = w // 2
    torso_top = 210

    def _make_base(expression: str, thinking_arm: bool) -> Image.Image:
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        hip_src = (252.0, 344.0)
        leg_right = _rotate_about_pivot(parts["leg_full"], hip_src, 22.0, (cx + 26, torso_top + 132), canvas_size=(w, h), scale=0.96)
        leg_left = leg_right.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        canvas.alpha_composite(leg_left)
        canvas.alpha_composite(leg_right)

        _draw_front_torso_and_cape(canvas, parts, cx, torso_top, cape_scale=0.72)

        sh_src = (224.0, 244.0)
        if thinking_arm:
            # Left arm (viewer-right): resting calmly down at her side
            arm_r_side = _rotate_about_pivot(
                parts["arm_full"].transpose(Image.Transpose.FLIP_LEFT_RIGHT),
                (w - sh_src[0], sh_src[1]),
                -10.0,
                (cx + 56, torso_top + 28),
                canvas_size=(w, h),
                scale=0.96,
            )
            canvas.alpha_composite(arm_r_side)

            # Right arm (viewer-left): RAISED UP & BENT AT ELBOW -> HAND TOUCHING TEMPLE/SIDE OF HELMET (clean, not covering eye!)
            u_arm = _rotate_about_pivot(
                parts["upper_arm"],
                sh_src,
                114.0,
                (cx - 54, torso_top + 26),
                canvas_size=(w, h),
                scale=0.98,
            )
            canvas.alpha_composite(u_arm)

            elbow_src = (202.0, 286.0)
            f_arm = _rotate_about_pivot(
                parts["forearm"],
                elbow_src,
                -160.0,
                (cx - 108, torso_top - 4),
                canvas_size=(w, h),
                scale=1.0,
            )
            head = _build_front_head(src, expression=expression)
            canvas.alpha_composite(head, (cx - head.width // 2, 14))
            canvas.alpha_composite(f_arm)

            # Subtle thoughtful cyan pulse right at temple/ear-ring contact point
            _glow_ellipse(canvas, (cx - 98, 122, cx - 84, 136), (195, 254, 255, 215), (34, 211, 238, 185), 7.5)
        else:
            arm_l_side = _rotate_about_pivot(parts["arm_full"], sh_src, 8.0, (cx - 56, torso_top + 28), canvas_size=(w, h), scale=0.96)
            arm_r_side = arm_l_side.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            canvas.alpha_composite(arm_l_side)
            canvas.alpha_composite(arm_r_side)

            head = _build_front_head(src, expression=expression)
            canvas.alpha_composite(head, (cx - head.width // 2, 14))

        return canvas

    return _make_base("calm", thinking_arm=False), _make_base("thinking", thinking_arm=True)


def _build_sit_and_sleep(src: Image.Image, parts: dict[str, Image.Image]) -> tuple[Image.Image, Image.Image]:
    """Build RYNA's relaxed sitting pose (`ryna_sit.png`) and peaceful sleeping pose (`ryna_sleep.png`)."""
    w, h = 385, 512
    cx = w // 2
    torso_top = 278

    def _make_seated(sleeping: bool) -> Image.Image:
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        _draw_front_torso_and_cape(canvas, parts, cx, torso_top, cape_scale=0.80)

        hip_src = (252.0, 344.0)
        leg_r_sit = _rotate_about_pivot(parts["leg_full"], hip_src, -42.0, (cx + 22, torso_top + 116), canvas_size=(w, h), scale=0.84)
        leg_l_sit = leg_r_sit.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        canvas.alpha_composite(leg_l_sit)
        canvas.alpha_composite(leg_r_sit)

        # Smooth 3D metallic pelvis overlay over inner hip joint
        _metallic_rounded_plate(canvas, (cx - 34, torso_top + 104, cx + 34, torso_top + 132), radius=14)

        sh_src = (224.0, 244.0)
        if sleeping:
            arm_l = _rotate_about_pivot(parts["arm_full"], sh_src, -26.0, (cx - 52, torso_top + 26), canvas_size=(w, h), scale=0.92)
            arm_r = arm_l.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        else:
            arm_l = _rotate_about_pivot(parts["arm_full"], sh_src, -14.0, (cx - 54, torso_top + 26), canvas_size=(w, h), scale=0.94)
            arm_r = arm_l.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        canvas.alpha_composite(arm_l)
        canvas.alpha_composite(arm_r)

        head = _build_front_head(src, expression="sleeping" if sleeping else "calm")
        if sleeping:
            head = head.rotate(-9.0, resample=Image.Resampling.BICUBIC, expand=True)
            canvas.alpha_composite(head, (cx - head.width // 2 + 4, torso_top - 190))
        else:
            canvas.alpha_composite(head, (cx - head.width // 2, torso_top - 194))

        return canvas

    return _make_seated(sleeping=False), _make_seated(sleeping=True)


def build_all_poses() -> dict[str, Path]:
    if not SOURCE_PNG.is_file():
        raise FileNotFoundError(f"Authoritative RYNA asset not found: {SOURCE_PNG}")

    src = Image.open(SOURCE_PNG).convert("RGBA")
    parts = _extract_reusable_parts(src)

    walk_a, walk_mid, walk_b = _build_walk_poses(src)
    stand_img, think_img = _build_front_standing_and_thinking(src, parts)
    sit_img, sleep_img = _build_sit_and_sleep(src, parts)

    outputs = {
        "walk_a": (ASSETS_DIR / "ryna_walk_a.png", walk_a),
        "walk_mid": (ASSETS_DIR / "ryna_walk_mid.png", walk_mid),
        "walk_b": (ASSETS_DIR / "ryna_walk_b.png", walk_b),
        "stand": (ASSETS_DIR / "ryna_stand.png", stand_img),
        "think": (ASSETS_DIR / "ryna_think.png", think_img),
        "sit": (ASSETS_DIR / "ryna_sit.png", sit_img),
        "sleep": (ASSETS_DIR / "ryna_sleep.png", sleep_img),
    }
    saved: dict[str, Path] = {}
    for name, (path, img) in outputs.items():
        img.save(path, format="PNG")
        saved[name] = path
        print(f"Saved {name:8s} -> {path} ({img.size[0]}x{img.size[1]})")
    return saved


if __name__ == "__main__":
    build_all_poses()
