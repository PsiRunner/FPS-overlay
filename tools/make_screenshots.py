"""Generates the README screenshots by simulating exactly what FpsOverlay
draws: a bold number with a thin black outline, in any installed font and
color - composited over a synthetic dark "game scene" backdrop so the
images read well on both light and dark GitHub themes.

Run from the project root:  python tools/make_screenshots.py
Output goes to docs/*.png. Only fonts actually installed on this machine
are used (checked via QFontDatabase), so nothing is faked.
"""
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QRect, QPointF
from PyQt6.QtGui import (
    QGuiApplication, QColor, QFont, QFontDatabase, QFontMetrics,
    QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"

W, H = 560, 200          # per-shot canvas
TILE_W, TILE_H = 340, 150  # showcase tile

# (file name, fps text, font family, point size, color)
SHOTS = [
    ("hero-neon-green",  "144", "Bahnschrift",            58, "#39FF14"),
    ("consolas-red",     "98",  "Consolas",              48, "#FF4136"),
    ("arial-black-cyan", "240", "Arial Black",           40, "#00E5FF"),
    ("impact-orange",    "75",  "Impact",                52, "#FF9F0A"),
    ("segoe-white",      "60",  "Segoe UI",              46, "#FFFFFF"),
    ("franklin-magenta", "165", "Franklin Gothic Medium",44, "#FF2D95"),
]

# Showcase banner: one tile per entry (font, size, color)
SHOWCASE = [
    ("Bahnschrift",             34, "#39FF14"),
    ("Consolas",                30, "#00E5FF"),
    ("Arial Black",             26, "#FF9F0A"),
    ("Impact",                  32, "#FF4136"),
]


def draw_scene(p: QPainter, w: int, h: int):
    """Synthetic dark game-ish backdrop: gradient sky, faint grid,
    vignette. Keeps the focus on the counter while giving it context."""
    grad = QLinearGradient(0, 0, 0, h)
    grad.setColorAt(0.0, QColor("#232a3d"))
    grad.setColorAt(0.55, QColor("#141a29"))
    grad.setColorAt(1.0, QColor("#0a0d16"))
    p.fillRect(0, 0, w, h, grad)

    # faint perspective grid on the lower half
    p.setPen(QPen(QColor(255, 255, 255, 14), 1))
    horizon = int(h * 0.62)
    for i in range(9):
        x = int(w * i / 8)
        p.drawLine(x, horizon, int(w * 0.5 + (x - w * 0.5) * 2.2), h)
    for i in range(5):
        y = horizon + int((h - horizon) * (i / 4) ** 1.7)
        p.drawLine(0, y, w, y)

    # vignette
    vig = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.75)
    vig.setColorAt(0.0, QColor(0, 0, 0, 0))
    vig.setColorAt(1.0, QColor(0, 0, 0, 110))
    p.fillRect(0, 0, w, h, vig)


def draw_counter(p: QPainter, cx: int, cy: int, text: str,
                 family: str, pt: int, color: str):
    """Same drawing recipe as FpsOverlay.paintEvent: bold font, black
    outline offset by 1px in four directions, colored fill on top."""
    font = QFont(family, pt, QFont.Weight.Bold)
    fm = QFontMetrics(font)
    path = QPainterPath()
    path.addText(cx - fm.horizontalAdvance(text) / 2,
                 cy + (fm.ascent() - fm.descent()) / 2, font, text)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 220))
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        p.drawPath(path.translated(dx, dy))
    p.setBrush(QColor(color))
    p.drawPath(path)


def render_shot(fname, text, family, pt, color):
    pm = QPixmap(W, H)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw_scene(p, W, H)
    draw_counter(p, W // 2, H // 2, text, family, pt, color)
    p.end()
    pm.save(str(OUT / f"{fname}.png"))


def render_showcase():
    n = len(SHOWCASE)
    pm = QPixmap(TILE_W * n, TILE_H)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    texts = ["144", "98", "240", "75"]
    for i, (family, pt, color) in enumerate(SHOWCASE):
        p.save()
        p.translate(i * TILE_W, 0)
        p.setClipRect(0, 0, TILE_W, TILE_H)
        draw_scene(p, TILE_W, TILE_H)
        draw_counter(p, TILE_W // 2, TILE_H // 2, texts[i], family, pt, color)
        p.restore()
        if i:  # subtle separator between tiles
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.drawLine(i * TILE_W, 0, i * TILE_W, TILE_H)
    p.end()
    pm.save(str(OUT / "showcase.png"))


def sanity_check():
    """I can't eyeball the images myself, so verify programmatically that
    each PNG exists, has the right dimensions, and actually contains
    bright pixels of roughly the expected hue (the counter text)."""
    import colorsys
    ok = True
    checks = {s[0]: s[4] for s in SHOTS}
    for fname, color in checks.items():
        img = QImage(str(OUT / f"{fname}.png"))
        if img.isNull() or img.width() != W or img.height() != H:
            print(f"FAIL {fname}: bad image/dimensions")
            ok = False
            continue
        r, g, b = (int(color[i:i+2], 16) for i in (1, 3, 5))
        tr, tg, tb = (v / 255 for v in (r, g, b))
        _, tdeg, tval = colorsys.rgb_to_hsv(tr, tg, tb)
        found = 0
        for y in range(0, H, 3):
            for x in range(0, W, 3):
                c = img.pixelColor(x, y)
                cr, cg, cb = (c.redF(), c.greenF(), c.blueF())
                _, deg, val = colorsys.rgb_to_hsv(cr, cg, cb)
                if val > 0.5 and abs(deg - tdeg) < 0.04:
                    found += 1
        status = "OK " if found > 40 else "FAIL"
        if found <= 40:
            ok = False
        print(f"{status} {fname}: {found} pixels near #{color[1:]}")
    return ok


def main():
    app = QGuiApplication(sys.argv)
    OUT.mkdir(exist_ok=True)
    installed = set(QFontDatabase.families())
    for _, _, family, _, _ in SHOTS + [(f, "", f, 0, "") for f, *_ in SHOWCASE]:
        if family not in installed:
            print(f"SKIP: '{family}' not installed on this machine")
            sys.exit(1)
    for shot in SHOTS:
        render_shot(*shot)
        print("wrote", shot[0])
    render_showcase()
    print("wrote showcase")
    sys.exit(0 if sanity_check() else 1)


if __name__ == "__main__":
    main()
