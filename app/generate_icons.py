"""
Generate PNG icons for Shelly virtual components.
Run once during Docker build: python -m app.generate_icons
"""
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).parent / "static" / "icons"
OUT_DIR.mkdir(parents=True, exist_ok=True)

S = 128                          # canvas size
C = S // 2                       # centre
GOLD  = (201, 168, 76, 255)
WHITE = (240, 240, 240, 255)
GREEN = (76, 175, 125, 255)
DARK  = (20, 20, 20, 255)
BG    = (0, 0, 0, 0)            # transparent


def canvas() -> tuple["Image.Image", "ImageDraw.ImageDraw"]:
    img = Image.new("RGBA", (S, S), BG)
    return img, ImageDraw.Draw(img)


def save(img: "Image.Image", name: str) -> str:
    path = OUT_DIR / f"{name}.png"
    img.save(path, "PNG")
    print(f"  ✓ {path}")
    return str(path)


# ── 1. battery.png ────────────────────────────────────────────────────────────

def make_battery():
    img, d = canvas()
    # body
    bx, by, bw, bh = 14, 38, 88, 52
    d.rounded_rectangle([bx, by, bx+bw, by+bh], radius=8, outline=GOLD, width=5)
    # nub
    d.rounded_rectangle([bx+bw, by+14, bx+bw+12, by+bh-14], radius=4, fill=GOLD)
    # fill bar (~75 %)
    fill_w = int((bw - 14) * 0.75)
    d.rounded_rectangle([bx+7, by+8, bx+7+fill_w, by+bh-8], radius=5, fill=GOLD)
    # "%"
    d.text((bx+bw+14, by+bh+4), "%", fill=GOLD)
    save(img, "battery")


# ── 2. thermometer.png ───────────────────────────────────────────────────────

def make_thermometer():
    img, d = canvas()
    cx = C
    tube_top, tube_bot = 12, 82
    tube_r = 10
    bulb_r = 22
    # tube outline
    d.rounded_rectangle(
        [cx - tube_r, tube_top, cx + tube_r, tube_bot],
        radius=tube_r, outline=GOLD, width=5
    )
    # bulb outline
    d.ellipse([cx - bulb_r, tube_bot - bulb_r, cx + bulb_r, tube_bot + bulb_r],
              outline=GOLD, width=5)
    # mercury fill in tube
    fill_top = tube_top + 14
    d.rounded_rectangle(
        [cx - tube_r + 7, fill_top, cx + tube_r - 7, tube_bot],
        radius=3, fill=GOLD
    )
    # mercury fill in bulb
    d.ellipse([cx - bulb_r + 6, tube_bot - bulb_r + 6,
               cx + bulb_r - 6, tube_bot + bulb_r - 6], fill=GOLD)
    # tick marks
    for y in range(tube_top + 14, tube_bot, 14):
        d.line([cx + tube_r + 2, y, cx + tube_r + 10, y], fill=GOLD, width=3)
    save(img, "thermometer")


# ── 3. snowflake.png  (climate start button) ─────────────────────────────────

def make_snowflake(name="snowflake", color=GOLD):
    img, d = canvas()
    cx, cy = C, C
    r_outer = 50
    r_inner = 18
    r_cross  = 28
    arms = 6
    for i in range(arms):
        angle = math.radians(i * 360 / arms)
        # main arm
        x1 = cx + r_inner * math.cos(angle)
        y1 = cy + r_inner * math.sin(angle)
        x2 = cx + r_outer * math.cos(angle)
        y2 = cy + r_outer * math.sin(angle)
        d.line([x1, y1, x2, y2], fill=color, width=5)
        # cross branches at mid-point
        for side in (-1, 1):
            cross_angle = angle + side * math.radians(55)
            mx = cx + r_cross * math.cos(angle)
            my = cy + r_cross * math.sin(angle)
            bx = mx + 14 * math.cos(cross_angle)
            by = my + 14 * math.sin(cross_angle)
            d.line([mx, my, bx, by], fill=color, width=4)
    # centre dot
    d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=color)
    save(img, name)


# ── 4. locked.png ────────────────────────────────────────────────────────────

def make_locked():
    img, d = canvas()
    cx = C
    # shackle (arc)
    shackle_r = 22
    sx, sy = cx - shackle_r, 16
    d.arc([sx, sy, sx + shackle_r*2, sy + shackle_r*2 + 10],
          start=200, end=340, fill=GOLD, width=8)
    # shackle legs
    leg_top, leg_bot = sy + shackle_r + 2, 64
    d.line([cx - shackle_r, leg_top, cx - shackle_r, leg_bot], fill=GOLD, width=8)
    d.line([cx + shackle_r, leg_top, cx + shackle_r, leg_bot], fill=GOLD, width=8)
    # body
    body_x1, body_y1 = cx - 34, 62
    body_x2, body_y2 = cx + 34, 114
    d.rounded_rectangle([body_x1, body_y1, body_x2, body_y2], radius=8, fill=GOLD)
    # keyhole
    kx, ky = cx, 84
    d.ellipse([kx-8, ky-10, kx+8, ky+6], fill=DARK)
    d.polygon([(kx-5, ky+4), (kx+5, ky+4), (kx+3, ky+18), (kx-3, ky+18)], fill=DARK)
    save(img, "locked")


# ── 5. unlocked.png ──────────────────────────────────────────────────────────

def make_unlocked():
    img, d = canvas()
    cx = C
    shackle_r = 22
    sx, sy = cx - shackle_r - 12, 10
    # open shackle — only left leg goes down
    d.arc([sx, sy, sx + shackle_r*2, sy + shackle_r*2 + 10],
          start=200, end=340, fill=GOLD, width=8)
    d.line([sx, sy + shackle_r + 7, sx, 62], fill=GOLD, width=8)
    # body
    body_x1, body_y1 = cx - 34, 62
    body_x2, body_y2 = cx + 34, 114
    d.rounded_rectangle([body_x1, body_y1, body_x2, body_y2], radius=8, fill=GOLD)
    # keyhole
    kx, ky = cx, 84
    d.ellipse([kx-8, ky-10, kx+8, ky+6], fill=DARK)
    d.polygon([(kx-5, ky+4), (kx+5, ky+4), (kx+3, ky+18), (kx-3, ky+18)], fill=DARK)
    save(img, "unlocked")


# ── 6. doors.png  (car doors — top-down outline) ────────────────────────────

def make_doors():
    img, d = canvas()
    # simple car body top-view outline
    pts = [
        (C, 8),         # front nose
        (C+30, 22),
        (C+36, 50),
        (C+36, 80),
        (C+30, 106),
        (C, 118),
        (C-30, 106),
        (C-36, 80),
        (C-36, 50),
        (C-30, 22),
    ]
    d.polygon(pts, outline=GOLD, width=5)
    # door line left
    d.line([C-36, 58, C, 54], fill=GOLD, width=4)
    # door line right
    d.line([C+36, 58, C, 54], fill=GOLD, width=4)
    # window
    win_pts = [(C-20, 26), (C+20, 26), (C+28, 52), (C-28, 52)]
    d.polygon(win_pts, outline=GOLD, width=3)
    save(img, "doors")


# ── 7. charging.png  (lightning bolt) ────────────────────────────────────────

def make_charging():
    img, d = canvas()
    bolt = [
        (C+14, 6),
        (C-4,  52),
        (C+10, 52),
        (C-14, 122),
        (C+18, 64),
        (C+2,  64),
    ]
    d.polygon(bolt, fill=GOLD, outline=GOLD)
    save(img, "charging")


# ── 8. climate_on.png  (snowflake tinted green when on) ──────────────────────
# ── 9. climate_off.png (snowflake grey when off) ─────────────────────────────

def make_climate_on():
    make_snowflake("climate_on",  color=GREEN)


def make_climate_off():
    make_snowflake("climate_off", color=(100, 100, 100, 200))


# ── run all ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating icons →", OUT_DIR)
    make_battery()
    make_thermometer()
    make_snowflake("climate_button")
    make_climate_on()
    make_climate_off()
    make_locked()
    make_unlocked()
    make_doors()
    make_charging()
    print("Done.")
