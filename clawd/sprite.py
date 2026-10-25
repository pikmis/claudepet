"""Pixel-art Clawd. Coordinates are in grid units; the idle frame matches the Clawd logo
from the Claude Code CLI banner (quadrant blocks mapped to 1x2 grid cells)."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

GRID_W = 18
GRID_H = 13

BODY_COLOR = "#d77757"
EYE_COLOR = "#000000"

BOOK_COVER = "#34558f"
BOOK_PAGE = "#f4efe3"
BOOK_FOLD = "#dcd5c4"
BOOK_TEXT = "#9a9282"
LAPTOP_LID = "#c3c9d0"
LAPTOP_EDGE = "#8d959e"
LAPTOP_LOGO = "#eef1f4"
CAP_COLOR = "#5b74c9"
POMPOM_COLOR = "#ffffff"
SPINNER_COLOR = "#eb8f6e"
PHONES_BAND = "#2b2b2b"
PHONES_CUP = "#3d3d3d"
PHONES_ACCENT = "#e6e6e6"

# Thinking spinner beside the head, a nod to Claude Code's "✻ Thinking…".
SPINNER_FRAMES = [
    ["...", ".X.", "..."],
    [".X.", "XXX", ".X."],
    ["X.X", ".X.", "X.X"],
    ["XXX", "X.X", "XXX"],
    ["X.X", ".X.", "X.X"],
    [".X.", "XXX", ".X."],
]
SPINNER_LENGTH = len(SPINNER_FRAMES)

PARTICLE_COLORS = {
    "heart": "#ff5c7a",
    "zzz": "#8fa3bf",
    "spark": "#ffc94d",
    "code": "#7fd38a",
    "note": "#6fb8ff",
}

PATTERNS = {
    "heart": [".XX.XX.", "XXXXXXX", "XXXXXXX", ".XXXXX.", "..XXX..", "...X..."],
    "zzz": ["XXXXX", "...X.", "..X..", ".X...", "XXXXX"],
    "spark": ["..X..", "..X..", "XX.XX", "..X..", "..X.."],
    "code": ["..X...X.X..", ".X....X..X.", "X....X....X", ".X..X....X.", "..X.X...X.."],
    "note": ["..XXXXX", "..X...X", "..X...X", "..X...X", "XXX.XXX", "XXX.XXX"],
}


def mix(a: str, b: str, t: float) -> str:
    pa, pb = int(a[1:], 16), int(b[1:], 16)
    out = 0
    for shift in (16, 8, 0):
        ca, cb = (pa >> shift) & 0xFF, (pb >> shift) & 0xFF
        out |= round(ca + (cb - ca) * t) << shift
    return f"#{out:06x}"


BLUSH_COLOR = mix(BODY_COLOR, "#ff4f79", 0.5)

LEG_HEIGHTS = {
    "stand": (2, 2, 2, 2),
    "walk1": (1, 2, 1, 2),
    "walk2": (2, 1, 2, 1),
    "dangle": (3, 3, 3, 3),
    "tuck": (1, 1, 1, 1),
}


@dataclass(frozen=True)
class Frame:
    eyes: str = "open"
    look: int = 0
    arms: str = "normal"
    legs: str = "stand"
    squash: int = 0
    blush: bool = False
    prop: str | None = None
    cap: bool = False
    phones: bool = False
    spinner: int = -1
    flip: bool = False


_colors: dict[str, QColor] = {}


def _color(name: str) -> QColor:
    c = _colors.get(name)
    if c is None:
        c = _colors[name] = QColor(name)
    return c


def _render(painter: QPainter, frame: Frame, s: int) -> None:
    def px(x0: float, y0: float, x1: float, y1: float, color: str) -> None:
        painter.fillRect(QRectF(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s), _color(color))

    left, right, top, bottom = 3, 15, 3, 11
    if frame.squash > 0:
        left, right, top = 2, 16, 5
    elif frame.squash < 0:
        left, right, top = 4, 14, 2
    if frame.legs == "tuck":
        top += 1
        bottom += 1

    heights = LEG_HEIGHTS.get(frame.legs, LEG_HEIGHTS["stand"])
    for col, h in zip((left + 1, left + 3, right - 4, right - 2), heights):
        px(col, bottom, col + 1, bottom + h, BODY_COLOR)

    def arm(side: int, style: str) -> None:
        if style == "up":
            if side < 0:
                px(left - 2, top - 1, left - 1, top + 3, BODY_COLOR)
                px(left - 1, top + 2, left, top + 3, BODY_COLOR)
            else:
                px(right + 1, top - 1, right + 2, top + 3, BODY_COLOR)
                px(right, top + 2, right + 1, top + 3, BODY_COLOR)
        elif style in ("type1", "type2"):
            # Hands bob up and down on the keyboard, alternating sides.
            up = 1 if (style == "type1") == (side < 0) else 0
            if side < 0:
                px(left - 1, bottom - 3 - up, left, bottom - 1 - up, BODY_COLOR)
            else:
                px(right, bottom - 3 - up, right + 1, bottom - 1 - up, BODY_COLOR)
        elif style == "hold":
            pass  # Hands are drawn on top of the book instead.
        elif style == "tuck":
            if side < 0:
                px(left - 1, bottom - 4, left, bottom - 2, BODY_COLOR)
            else:
                px(right, bottom - 4, right + 1, bottom - 2, BODY_COLOR)
        elif side < 0:
            px(left - 2, bottom - 4, left, bottom - 2, BODY_COLOR)
        else:
            px(right, bottom - 4, right + 2, bottom - 2, BODY_COLOR)

    left_arm = right_arm = frame.arms
    if frame.arms == "wave1":
        left_arm, right_arm = "up", "normal"
    elif frame.arms == "wave2":
        left_arm, right_arm = "normal", "up"
    arm(-1, left_arm)
    arm(1, right_arm)

    px(left, top, right, bottom, BODY_COLOR)

    ey = top + 2
    for side, cx in ((-1, left + 2 + frame.look), (1, right - 3 + frame.look)):
        eyes = frame.eyes
        if eyes == "blink":
            px(cx, ey + 1, cx + 1, ey + 2, EYE_COLOR)
        elif eyes == "happy":
            px(cx - 1, ey + 1, cx, ey + 2, EYE_COLOR)
            px(cx, ey, cx + 1, ey + 1, EYE_COLOR)
            px(cx + 1, ey + 1, cx + 2, ey + 2, EYE_COLOR)
        elif eyes == "surprised":
            px(cx, ey - 1, cx + 1, ey + 2, EYE_COLOR)
        elif eyes == "dizzy":
            for dx, dy in ((-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)):
                px(cx + dx, ey + dy, cx + dx + 1, ey + dy + 1, EYE_COLOR)
        elif eyes == "sleep":
            px(cx - 1, ey + 1, cx + 2, ey + 2, EYE_COLOR)
        elif eyes == "down":
            px(cx, ey + 1, cx + 1, ey + 3, EYE_COLOR)
        elif eyes == "up":
            px(cx, ey - 1, cx + 1, ey + 1, EYE_COLOR)
        elif eyes == "angry":
            px(cx, ey, cx + 1, ey + 2, EYE_COLOR)
            brow = cx - 1 if side < 0 else cx + 1
            px(brow, ey - 1, brow + 1, ey, EYE_COLOR)
        else:
            px(cx, ey, cx + 1, ey + 2, EYE_COLOR)
        if frame.blush:
            if side < 0:
                px(cx - 2, ey + 2, cx, ey + 3, BLUSH_COLOR)
            else:
                px(cx + 1, ey + 2, cx + 3, ey + 3, BLUSH_COLOR)

    if frame.phones:
        _phones(px, left, right, top)
    if frame.prop == "book":
        _book(px, left, right, bottom, frame.flip)
    elif frame.prop == "laptop":
        _laptop(px, left, right, bottom)
    if frame.cap:
        _cap(px, left, right, top)
    if frame.spinner >= 0:
        for y, row in enumerate(SPINNER_FRAMES[frame.spinner % SPINNER_LENGTH]):
            for x, ch in enumerate(row):
                if ch == "X":
                    px(GRID_W - 3 + x, y, GRID_W - 2 + x, y + 1, SPINNER_COLOR)


def _book(px, left, right, bottom, flip) -> None:
    """Open book held up in front of the body, pages towards the viewer."""
    x0, x1 = left + 1, right - 1
    mid = (x0 + x1) / 2
    y0 = bottom - 4
    px(x0, y0, x1, bottom, BOOK_COVER)
    px(x0 + 1, y0, x1 - 1, bottom - 1, BOOK_PAGE)
    px(mid - 1, y0 + 1, mid + 1, bottom - 1, BOOK_FOLD)
    px(mid - 1, y0, mid + 1, y0 + 1, BODY_COLOR)
    px(x0 + 1, y0 + 1, mid - 1, y0 + 2, BOOK_TEXT)
    px(mid + 1, y0 + 1, x1 - 1, y0 + 2, BOOK_TEXT)
    if flip:
        px(mid + 1, y0 - 1, x1 - 2, y0, BOOK_PAGE)
    px(x0, y0 + 1, x0 + 1, y0 + 3, BODY_COLOR)
    px(x1 - 1, y0 + 1, x1, y0 + 3, BODY_COLOR)


def _laptop(px, left, right, bottom) -> None:
    """Laptop on the ground in front of Clawd; we see the back of the lid."""
    px(left + 1, bottom - 4, right - 1, bottom + 1, LAPTOP_LID)
    px(left + 1, bottom - 4, right - 1, bottom - 3, LAPTOP_EDGE)
    mid = (left + right) / 2
    px(mid - 1, bottom - 2, mid + 1, bottom - 1, LAPTOP_LOGO)
    px(left, bottom + 1, right, bottom + 2, LAPTOP_EDGE)


def _phones(px, left, right, top) -> None:
    """Over-ear headphones: band across the top of the head, cups on the sides."""
    px(left + 1, top - 2, right - 1, top - 1, PHONES_BAND)
    px(left, top - 1, left + 1, top + 1, PHONES_BAND)
    px(right - 1, top - 1, right, top + 1, PHONES_BAND)
    px(left - 1, top + 1, left + 1, top + 4, PHONES_CUP)
    px(right - 1, top + 1, right + 1, top + 4, PHONES_CUP)
    px(left - 1, top + 2, left, top + 3, PHONES_ACCENT)
    px(right, top + 2, right + 1, top + 3, PHONES_ACCENT)


def _cap(px, left, right, top) -> None:
    """Nightcap leaning to the right, with a pompom."""
    px(left + 1, top - 1, right - 1, top, CAP_COLOR)
    px(left + 3, top - 2, right - 2, top - 1, CAP_COLOR)
    px(right - 4, top - 3, right - 1, top - 2, CAP_COLOR)
    px(right - 1, top - 3, right + 1, top - 1, POMPOM_COLOR)


def render_pet(frame: Frame, cell: int, dpr: float = 1.0) -> QPixmap:
    """Draws a frame. `cell` is the size of one grid cell in device pixels."""
    pixmap = QPixmap(GRID_W * cell, GRID_H * cell)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, False)
    _render(painter, frame, cell)
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def render_particle(kind: str, s: int, dpr: float = 1.0) -> QPixmap:
    rows = PATTERNS[kind]
    pixmap = QPixmap(len(rows[0]) * s, len(rows) * s)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    color = _color(PARTICLE_COLORS[kind])
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "X":
                painter.fillRect(x * s, y * s, s, s, color)
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap
