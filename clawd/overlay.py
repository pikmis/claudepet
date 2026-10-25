"""The transparent, always-on-top window Clawd lives in. It covers the work area of the
primary monitor and lets every click through except those that land on Clawd himself."""
from __future__ import annotations

import math
import random
import re
import sys
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QWidget

from . import claude
from .brain import Brain
from .memory import Memory
from .pet import Pet
from .phrases import PHRASES, TOOL_PHRASES
from .sprite import GRID_H, GRID_W, Frame, render_ball, render_particle, render_pet

UNIT = 5
MAX_PARTICLES = 14
REFIT_EVERY = 120  # ticks
FRAME_MS = 16

BUBBLE_MAX_TEXT_WIDTH = 240
BUBBLE_PAD_X = 10
BUBBLE_PAD_Y = 6
BUBBLE_RADIUS = 12
BUBBLE_FADE = 0.15
BUTTON_H = 24
BUTTON_GAP = 6
BUTTON_COLORS = {"allow": "#2e9e5b", "deny": "#d64545", None: "#8a8f98"}
BUTTON_LABELS = {
    "ru": (("Разрешить", "allow"), ("Отклонить", "deny"), ("В терминале", None)),
    "en": (("Allow", "allow"), ("Deny", "deny"), ("In terminal", None)),
}

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def format_gap(seconds: float, lang: str) -> str:
    hours = max(1, int(seconds // 3600))
    if hours < 48:
        return f"{hours} ч" if lang == "ru" else f"{hours} h"
    return f"{hours // 24} дн." if lang == "ru" else f"{hours // 24} d"


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 2


class Particle:
    __slots__ = ("kind", "start", "duration", "x", "y", "drift", "rise", "rot", "pixmap", "w", "h")


class Overlay(QWidget):
    def __init__(self, haiku: claude.Haiku, post, memory: Memory | None = None) -> None:
        flags = (
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
        )
        super().__init__(None, flags)
        self.setWindowTitle("Clawd")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setMouseTracking(True)

        self.haiku = haiku
        self.lang = haiku.lang
        self.brain = Brain(haiku, post)
        self.memory = memory or Memory()
        self.pet = Pet(UNIT, self, self.memory)
        self.on_game_started = None  # called with the game's name when he starts one himself
        self.ball_pixmap = None
        self.perms: list[dict] = []  # permission questions waiting for an answer
        self.on_decision = None  # called with (request id, "allow" | "deny" | None)
        self.buttons: list[tuple[QRectF, str, str | None]] = []
        self.button_specs: list[tuple[str, str | None, float]] = []
        self.dpr = 0.0
        self.cell = 0
        self.pixmaps: dict[Frame, object] = {}
        self.particle_pixmaps: dict[tuple[str, int], object] = {}
        self.particles: list[Particle] = []
        self.last_phrase: str | None = None
        self.bubble_layout: QTextLayout | None = None
        self.bubble_until = 0.0
        self.bubble_important = False
        self.bubble_opacity = 0.0
        self.bubble_visible = False
        self.bubble_rect = QRectF()
        self.bubble_font = QFont()
        self.bubble_font.setFamilies(["Segoe UI", "Noto Sans", "Sans Serif"])
        self.bubble_font.setPixelSize(13)
        self.bubble_font.setWeight(QFont.DemiBold)

        self.passthrough: bool | None = None
        self.ticks = 0
        self.next_refill = 0.0
        self.last_render = time.monotonic()
        self.dirty: list[QRect] = []

        self.fit_to_work_area()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self.tick)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.show()
        self.set_click_through(True)
        self.fit_canvas()
        self.timer.start()

    def fit_to_work_area(self) -> None:
        """Stretch the overlay over the primary monitor, minus the taskbar/panels,
        so Clawd walks on top of the taskbar."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        if self.geometry() != area:
            self.setGeometry(area)

    def fit_canvas(self) -> None:
        dpr = self.devicePixelRatioF()
        if dpr == self.dpr:
            return
        self.dpr = dpr
        # Whole device pixels per grid cell keep the pixel art crisp at fractional scales.
        self.cell = max(1, round(UNIT * dpr))
        self.pet.set_unit(self.cell / dpr)
        self.pixmaps.clear()
        self.particle_pixmaps.clear()
        self.ball_pixmap = None

    def raise_on_top(self) -> None:
        """The taskbar is topmost too and wins whenever it was clicked last."""
        if sys.platform != "win32" or not self.isVisible():
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 5
            # HWND_TOPMOST, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            user32.SetWindowPos(ctypes.c_void_p(int(self.winId())), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0013)
        except Exception:
            pass

    # ------------------------------------------------------------------ speech (the pet's `fx`)

    def lines_for(self, category: str, values: dict) -> list[str]:
        if category.startswith("tool:"):
            tools = TOOL_PHRASES[self.lang]
            return (values.get("detail") and tools.get(category[5:])) or tools["default"]
        return PHRASES[self.lang].get(category) or [category]

    def say(self, category: str, force: bool = False, values: dict | None = None) -> None:
        """`values` fill {placeholders}; lines that need a missing one are skipped."""
        values = values or {}
        if isinstance(values.get("gap"), (int, float)):
            values = {**values, "gap": format_gap(values["gap"], self.lang)}
        if category == "idle":
            if random.random() < 0.75:
                generated = self.brain.take()
                if generated:
                    self.say_text(generated, force)
                    return
            mood_lines = f"idle_{self.pet.mood.label}"
            if mood_lines in PHRASES[self.lang] and random.random() < 0.5:
                category = mood_lines
        everything = self.lines_for(category, values)
        usable = [p for p in everything if all(values.get(k) for k in PLACEHOLDER.findall(p))]
        phrases = usable or everything
        options = [p for p in phrases if p != self.last_phrase]
        text = random.choice(options or phrases)
        self.say_text(PLACEHOLDER.sub(lambda m: str(values.get(m[1]) or ""), text), force)

    def say_text(self, text: str, force: bool = False) -> None:
        """Forced lines (Claude's thoughts, reports) aren't talked over by idle chatter."""
        now = time.monotonic()
        if self.perms or (not force and self.bubble_important and now < self.bubble_until):
            return
        self.last_phrase = text
        self.bubble_important = force
        self.bubble_visible = True
        self.bubble_until = now + 3.5 + len(text) * 0.08
        self.bubble_layout, width, height = self.layout_text(text)
        self.bubble_rect = QRectF(0, 0, width + 2 * BUBBLE_PAD_X, height + 2 * BUBBLE_PAD_Y)

    def layout_text(self, text: str) -> tuple[QTextLayout, float, float]:
        """Wraps text at word boundaries (or anywhere, for very long words) to the bubble's width."""
        layout = QTextLayout(text, self.bubble_font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        width = height = 0.0
        layout.beginLayout()
        while (line := layout.createLine()).isValid():
            line.setLineWidth(BUBBLE_MAX_TEXT_WIDTH)
            line.setPosition(QPointF(0, height))
            height += line.height()
            width = max(width, line.naturalTextWidth())
        layout.endLayout()
        return layout, math.ceil(width), height

    def hide_bubble(self) -> None:
        if self.perms:
            return
        self.bubble_visible = False
        self.bubble_until = 0.0
        self.bubble_important = False

    def spawn(self, kind: str) -> None:
        if len(self.particles) >= MAX_PARTICLES:
            return
        pet = self.pet
        s = max(2, round(pet.unit * 0.75 * self.dpr))
        pixmap = self.particle_pixmaps.get((kind, s))
        if pixmap is None:
            pixmap = self.particle_pixmaps[(kind, s)] = render_particle(kind, s, self.dpr)
        p = Particle()
        p.kind, p.pixmap = kind, pixmap
        p.w, p.h = pixmap.width() / self.dpr, pixmap.height() / self.dpr
        head_y = pet.y + pet.hop_off + pet.unit * 2 - p.h
        if kind == "zzz":
            p.x = pet.x + pet.w * 0.72
            p.y = head_y + pet.unit * 2
            p.drift = 18.0
        else:
            p.x = pet.x + pet.w / 2 - p.w / 2 + (random.random() - 0.5) * pet.w * 0.5
            p.y = head_y
            p.drift = (random.random() - 0.5) * 32
        p.rot = (random.random() - 0.5) * 30
        p.rise = 40 + random.random() * 18
        p.duration = 1.6 if kind == "zzz" else 1.1
        p.start = time.monotonic()
        self.particles.append(p)

    # ------------------------------------------------------------------ events from the rest of the app

    def on_audio(self, event: dict) -> None:
        self.pet.on_audio(event, time.monotonic())

    def on_claude(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "permission":
            self.pet.on_permission(time.monotonic())
            self.perms.append(event)
            self.show_permission()
        elif kind == "permission_done":
            self.drop_permission(event["id"])
        else:
            self.pet.on_claude(event, time.monotonic())

    def show_permission(self) -> None:
        """The bubble turns into a question with buttons until somebody answers it."""
        perm = self.perms[0]
        ru = self.lang == "ru"
        text = ("Claude просит: " if ru else "Claude asks: ") + f"{perm['tool']} {perm['detail']}".strip()
        if len(self.perms) > 1:
            text += f" (+{len(self.perms) - 1})"
        self.bubble_layout, width, height = self.layout_text(text)
        metrics = QFontMetricsF(self.bubble_font)
        self.button_specs = [
            (label, behavior, metrics.horizontalAdvance(label) + 18) for label, behavior in BUTTON_LABELS[self.lang]
        ]
        buttons_width = sum(w for _, _, w in self.button_specs) + BUTTON_GAP * (len(self.button_specs) - 1)
        self.bubble_rect = QRectF(
            0, 0, max(width, buttons_width) + 2 * BUBBLE_PAD_X, height + 8 + BUTTON_H + 2 * BUBBLE_PAD_Y
        )
        self.bubble_important = True
        self.bubble_visible = True
        self.bubble_until = math.inf

    def decide(self, behavior: str | None) -> None:
        perm = self.perms.pop(0)
        if self.on_decision:
            self.on_decision(perm["id"], behavior)
        self.after_permission(behavior)

    def drop_permission(self, request_id: str) -> None:
        """Nobody answered in time (or Claude Code moved on): Claude Code's own dialog takes over."""
        first = bool(self.perms) and self.perms[0]["id"] == request_id
        self.perms = [p for p in self.perms if p["id"] != request_id]
        if first:
            self.after_permission(None, quiet=True)

    def after_permission(self, behavior: str | None, quiet: bool = False) -> None:
        if self.perms:
            self.show_permission()
            return
        self.buttons = []
        self.bubble_important = False
        self.bubble_visible = False
        self.bubble_until = 0.0
        if not quiet and behavior in ("allow", "deny"):
            self.say("perm_allow" if behavior == "allow" else "perm_deny")

    def on_notice(self, phrase: str) -> None:
        if phrase == "claude_found":
            self.brain.forget()
        self.say(phrase, True)

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self.brain.forget()
        self.say("language", True)

    def start_game(self, name: str) -> None:
        """He asked for a game himself."""
        self.set_game(name, True)
        if self.on_game_started:
            self.on_game_started(name)

    def set_game(self, name: str, on: bool) -> None:
        now = time.monotonic()
        if name == "ball":
            if on and self.pet.ball is None:
                s = max(2, round(self.pet.unit * 0.6 * self.dpr))
                self.ball_pixmap = render_ball(s, self.dpr)
                self.pet.start_ball(now, 4 * s / self.dpr)
            elif not on:
                self.pet.stop_ball("ball_end")
        elif on:
            self.pet.start_tag(now)
        else:
            self.pet.stop_tag("tag_end")

    def recall(self) -> None:
        self.pet.recall(time.monotonic())

    def on_context(self, event: dict) -> None:
        now = time.monotonic()
        if event["kind"] == "window" and event.get("app"):
            self.memory.note_window(event["app"])
        self.pet.on_typing(event["kpm"] if event["kind"] == "typing" else None, now)
        app = self.brain.on_context(event, now)
        if not app or not self.pet.can_chat():
            return

        def done(line: str | None) -> None:
            if line:
                self.say_text(line)
            elif random.random() < 0.3:
                self.say("window", False, {"app": app})

        self.brain.comment(self.pet, app, done)

    # ------------------------------------------------------------------ frame loop

    def tick(self) -> None:
        now = time.monotonic()
        self.ticks += 1
        if now >= self.next_refill:
            self.next_refill = now + 1
            self.brain.maybe_refill(self.pet, now)
        if self.ticks % REFIT_EVERY == 0:
            self.fit_to_work_area()
            self.raise_on_top()
        self.fit_canvas()
        pet = self.pet
        pet.set_area(self.width(), self.height())
        if self.isVisible() and not (pet.held or (pet.ball is not None and pet.ball.held)):
            pos = QCursor.pos() - self.geometry().topLeft()
            pet.set_cursor(pos.x(), pos.y(), now)
        pet.step(now)
        self.update_passthrough()
        self.schedule_repaint(now)

    def set_click_through(self, enabled: bool) -> None:
        if not (sys.platform == "win32" and self._set_ex_transparent(enabled)) and self.windowHandle() is not None:
            self.windowHandle().setFlag(Qt.WindowTransparentForInput, enabled)
        self.passthrough = enabled

    def _set_ex_transparent(self, enabled: bool) -> bool:
        """Flips WS_EX_TRANSPARENT directly. Qt's own flag also refreshes the window frame,
        which made patches of the layered window flash black while the cursor crossed Clawd."""
        try:
            import ctypes

            user32 = ctypes.windll.user32
            user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
            hwnd = ctypes.c_void_p(int(self.winId()))
            style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            wanted = (style | 0x20) if enabled else (style & ~0x20)  # WS_EX_TRANSPARENT
            if wanted != style:
                user32.SetWindowLongW(hwnd, -20, wanted)
            return True
        except Exception:
            return False

    def update_passthrough(self) -> None:
        """The window covers the whole work area, so it must let clicks through
        everywhere except Clawd himself."""
        pet = self.pet
        c = pet.cursor
        ball = pet.ball
        over = bool(c) and (
            pet.hit_test(c[0], c[1])
            or (ball is not None and ball.hit(c[0], c[1]))
            or (bool(self.perms) and self.bubble_rect.contains(c[0], c[1]))
        )
        held = pet.held or (ball is not None and ball.held)
        want = not (over or held)
        self.setCursor(Qt.ClosedHandCursor if held else Qt.OpenHandCursor if over else Qt.ArrowCursor)
        if want != self.passthrough:
            self.set_click_through(want)

    def pet_rect(self) -> QRectF:
        pet = self.pet
        margin = max(pet.w, pet.h) * 0.4 + 8
        return QRectF(pet.x, pet.y + pet.hop_off, pet.w, pet.h).adjusted(-margin, -margin, margin, margin)

    def layout_bubble(self, now: float) -> None:
        pet = self.pet
        if self.bubble_until and now >= self.bubble_until:
            self.hide_bubble()
        step = (now - self.last_render) / BUBBLE_FADE
        if self.bubble_visible:
            self.bubble_opacity = min(1.0, self.bubble_opacity + step)
        else:
            self.bubble_opacity = max(0.0, self.bubble_opacity - step)
        if self.bubble_opacity <= 0:
            return
        y = pet.y + pet.hop_off
        bw, bh = self.bubble_rect.width(), self.bubble_rect.height()
        margin = 4
        bx = min(max(pet.x + pet.w / 2 - bw / 2, margin), self.width() - bw - margin)
        by = y + pet.unit - bh - margin
        if by < 0:
            by = y + pet.h + margin
        self.bubble_rect = QRectF(bx, by, bw, bh)
        self.buttons = []
        if self.perms:
            bx += BUBBLE_PAD_X
            by += bh - BUBBLE_PAD_Y - BUTTON_H
            for label, behavior, width in self.button_specs:
                self.buttons.append((QRectF(bx, by, width, BUTTON_H), label, behavior))
                bx += width + BUTTON_GAP

    def schedule_repaint(self, now: float) -> None:
        self.layout_bubble(now)
        self.last_render = now
        rects = [self.pet_rect()]
        ball = self.pet.ball
        if ball is not None:
            r = ball.r + 6
            rects.append(QRectF(ball.x - r, ball.y - r, 2 * r, 2 * r))
        if self.bubble_opacity > 0:
            rects.append(self.bubble_rect.adjusted(-8, -8, 8, 12))
        for p in self.particles:
            rects.append(QRectF(p.x - 20, p.y - 80, p.w + 60, p.h + 100))
        current = [r.toAlignedRect() for r in rects]
        for rect in self.dirty + current:
            self.update(rect)
        self.dirty = current

    # ------------------------------------------------------------------ painting

    def paintEvent(self, event) -> None:
        now = time.monotonic()
        pet = self.pet
        painter = QPainter(self)
        # A layered window keeps whatever was drawn before, so wipe the area being repainted.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(event.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing, False)
        if not pet.ready:
            painter.end()  # before the first step he is still at the corner
            return

        frame = pet.frame(now)
        pixmap = self.pixmaps.get(frame)
        if pixmap is None:
            if len(self.pixmaps) > 400:
                self.pixmaps.clear()
            pixmap = self.pixmaps[frame] = render_pet(frame, self.cell, self.dpr)
        # Rotation happens around 50% / 60% of the sprite, like the CSS transform-origin it replaces.
        painter.save()
        painter.translate(pet.x + pet.w / 2, pet.y + pet.hop_off + pet.h * 0.6)
        painter.rotate(pet.rotation(now))
        painter.drawPixmap(QPointF(-pet.w / 2, -pet.h * 0.6), pixmap)
        painter.restore()

        ball = pet.ball
        if ball is not None and self.ball_pixmap is not None:
            painter.drawPixmap(QPointF(ball.x - ball.r, ball.y - ball.r), self.ball_pixmap)
        if self.bubble_opacity > 0:
            self.paint_bubble(painter)
        self.paint_particles(painter, now)
        painter.end()

    def paint_particles(self, painter: QPainter, now: float) -> None:
        alive = []
        for p in self.particles:
            t = (now - p.start) / p.duration
            if t >= 1:
                continue
            alive.append(p)
            e = ease_out(max(0.0, t))
            painter.save()
            painter.setOpacity(1 - e)
            painter.translate(p.x + p.drift * e + p.w / 2, p.y - p.rise * e + p.h / 2)
            painter.rotate(p.rot)
            painter.scale(0.5 + 0.5 * e, 0.5 + 0.5 * e)
            painter.drawPixmap(QPointF(-p.w / 2, -p.h / 2), p.pixmap)
            painter.restore()
        self.particles = alive

    def paint_bubble(self, painter: QPainter) -> None:
        painter.save()
        painter.setOpacity(self.bubble_opacity)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.bubble_rect
        painter.setPen(Qt.NoPen)
        for grow, alpha in ((3, 10), (2, 14), (1, 18)):  # a soft drop shadow
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(rect.translated(0, 2).adjusted(-grow, -grow, grow, grow), BUBBLE_RADIUS + grow, BUBBLE_RADIUS + grow)
        painter.setPen(QPen(QColor(0, 0, 0, 31), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), BUBBLE_RADIUS, BUBBLE_RADIUS)
        painter.setPen(QColor("#1a1a1a"))
        self.bubble_layout.draw(painter, QPointF(rect.x() + BUBBLE_PAD_X, rect.y() + BUBBLE_PAD_Y))
        cursor = self.pet.cursor
        painter.setRenderHint(QPainter.Antialiasing, True)
        for button, label, behavior in self.buttons:
            hover = bool(cursor) and button.contains(cursor[0], cursor[1])
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(BUTTON_COLORS[behavior]).lighter(115 if hover else 100))
            painter.drawRoundedRect(button, 6, 6)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(button, int(Qt.AlignCenter), label)
        painter.restore()

    # ------------------------------------------------------------------ mouse

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if event.button() != Qt.LeftButton:
            return
        for button, _, behavior in self.buttons:
            if self.perms and button.contains(pos):
                self.decide(behavior)
                self.update_passthrough()
                return
        ball = self.pet.ball
        if ball is not None and ball.hit(pos.x(), pos.y()) and not self.pet.hit_test(pos.x(), pos.y()):
            ball.grab(pos.x(), pos.y(), time.monotonic())
            self.update_passthrough()
            return
        if not self.pet.hit_test(pos.x(), pos.y()):
            return
        self.pet.pointer_down(pos.x(), pos.y(), time.monotonic())
        self.update_passthrough()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        now = time.monotonic()
        self.pet.set_cursor(pos.x(), pos.y(), now)
        if self.pet.ball is not None and self.pet.ball.held:
            self.pet.ball.drag(pos.x(), pos.y(), now, self.width(), self.height())
        self.pet.pointer_move(pos.x(), pos.y(), now)
        self.update_passthrough()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            now = time.monotonic()
            if self.pet.ball is not None and self.pet.ball.held:
                self.pet.ball.release(now)
            self.pet.pointer_up(now)

    def contextMenuEvent(self, event) -> None:
        event.ignore()
