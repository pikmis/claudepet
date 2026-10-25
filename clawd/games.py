"""Little games to play with Clawd: a ball to throw around and tag."""
from __future__ import annotations

import math

from .pet_math import clamp

GRAVITY = 2600
BOUNCE_FLOOR = 0.62
BOUNCE_WALL = 0.7
HIT_SLACK = 4


class Ball:
    """A ball with simple physics. `x`, `y` is its centre in window pixels; `r` is the radius."""

    def __init__(self, x: float, y: float, r: float) -> None:
        self.x = x
        self.y = y
        self.r = r
        self.vx = 0.0
        self.vy = 0.0
        self.held = False
        self.on_floor = False
        self.last_touched = 0.0  # when the player last held it
        self._grab = (0.0, 0.0)
        self._hist: list[tuple[float, float, float]] = []

    def hit(self, px: float, py: float) -> bool:
        return math.hypot(px - self.x, py - self.y) <= self.r + HIT_SLACK

    def grab(self, px: float, py: float, now: float) -> None:
        self.held = True
        self._grab = (px - self.x, py - self.y)
        self._hist = [(now, px, py)]
        self.vx = self.vy = 0.0
        self.last_touched = now

    def drag(self, px: float, py: float, now: float, width: float, height: float) -> None:
        if not self.held:
            return
        self._hist.append((now, px, py))
        while len(self._hist) > 2 and now - self._hist[0][0] > 0.1:
            self._hist.pop(0)
        self.x = clamp(px - self._grab[0], self.r, width - self.r)
        self.y = clamp(py - self._grab[1], self.r, height - self.r)

    def release(self, now: float) -> None:
        if not self.held:
            return
        self.held = False
        vx = vy = 0.0
        if len(self._hist) >= 2:
            t0, x0, y0 = self._hist[0]
            t1, x1, y1 = self._hist[-1]
            if t1 - t0 > 0.005 and now - t1 < 0.08:
                vx = (x1 - x0) / (t1 - t0)
                vy = (y1 - y0) / (t1 - t0)
        self.vx = clamp(vx, -3500, 3500)
        self.vy = clamp(vy, -3500, 3500)
        self.last_touched = now

    def kick(self, vx: float, vy: float, now: float) -> None:
        self.vx, self.vy = vx, vy
        self.on_floor = False

    def step(self, dt: float, width: float, height: float) -> None:
        if self.held:
            return
        self.vy += GRAVITY * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        if self.x < self.r:
            self.x = self.r
            self.vx = abs(self.vx) * BOUNCE_WALL
        elif self.x > width - self.r:
            self.x = width - self.r
            self.vx = -abs(self.vx) * BOUNCE_WALL
        if self.y < self.r:
            self.y = self.r
            self.vy = abs(self.vy) * BOUNCE_FLOOR
        self.on_floor = False
        if self.y > height - self.r:
            self.y = height - self.r
            if self.vy > 140:
                self.vy = -self.vy * BOUNCE_FLOOR
            else:
                self.vy = 0.0
                self.on_floor = True
            self.vx *= math.exp(-2.0 * dt)
            if abs(self.vx) < 6:
                self.vx = 0.0
