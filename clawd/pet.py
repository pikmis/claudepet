"""Clawd's behaviour: state machine and physics. Units are logical pixels and seconds."""
from __future__ import annotations

import math
import random
import time
from datetime import datetime

from .games import Ball
from .memory import Memory
from .mood import Mood
from .pet_math import clamp
from .sprite import GRID_H, GRID_W, SPINNER_LENGTH, Frame

GRAVITY = 2600
WALK_SPEED = 42
SLEEP_AFTER = 180
# Dozing off on his own lasts this long (minutes) at most.
DEEP_SLEEP = (20, 40)
# Coming back to the computer after this long wakes him up.
AWAY_AFTER = 300
HOVER_WAKE = 1.5
ATTENTION_RADIUS = 360
ATTENTION_TIME = 2.5

# Calm activities Clawd picks by himself: (state, min seconds, max seconds).
ACTIVITIES = [
    ("read", 25, 60),
    ("code", 25, 60),
    ("think", 8, 15),
    ("sleep", 25, 50),
]
ACTIVITY_STATES = {"read", "code", "think"}
CALM_STATES = ("idle", "walk", "read", "code", "think", "dance")
SESSION_TIMEOUT = 10 * 60
TOOL_SAY_EVERY = 8
FAST_TYPING_KPM = 250

# Games
RUN_SPEED = 190
FLEE_SPEED = 240
TAG_RADIUS = 210
LATE_NIGHT_EVERY = 45 * 60
OFFER_EVERY = 20 * 60
GAME_LENGTH = 180  # the ball goes away after this many seconds nobody touches it


rand = random.uniform
pick = random.choice


class Bounds:
    __slots__ = ("min_x", "max_x", "min_y", "max_y", "width", "height")

    def __init__(self) -> None:
        self.min_x = self.max_x = self.min_y = self.max_y = 0.0
        self.width = self.height = 0.0


class Pet:
    def __init__(self, unit: float, fx, memory: Memory | None = None, mood: Mood | None = None, hour=None) -> None:
        """`fx` receives side effects: say(category, force, vars), say_text(text, force),
        spawn(kind), hide_bubble() and, optionally, start_game(name)."""
        self.fx = fx
        self.memory = memory or Memory()
        self.mood = mood or Mood(self.memory.affection)
        self.hour = hour or (lambda: datetime.now().hour)
        self.greet_at = 0.0
        self.next_offer = 0.0
        self.last_late_night = -math.inf
        self.next_memory_sync = 0.0
        self.mood_quiet_until = 0.0  # moods speak up only now and then
        self.w = self.h = 0.0
        self.x = self.y = 0.0
        self.unit = unit
        self.set_unit(unit)
        now = time.monotonic()
        self.state = "idle"
        self.vx = 0.0
        self.vy = 0.0
        self.ready = False
        self.last_tick = now
        self.last_interaction = now
        self.next_action = now + 4
        self.next_blink = now + 2
        self.blink_until = 0.0
        self.look = 0
        self.expr: str | None = None
        self.expr_until = 0.0
        self.squash_until = 0.0
        self.wave_until = 0.0
        self.hop_active = False
        self.hop_off = 0.0
        self.hop_vy = 0.0
        self.walk_target = 0.0
        self.walk_in = False
        self.touch: dict | None = None
        self.last_tap = 0.0
        self.recent_taps: list[float] = []
        self.next_heart = 0.0
        self.next_z = 0.0
        self.hang_start = 0.0
        self.hang_until = 0.0
        self.activity_start = 0.0
        self.activity_until = 0.0
        self.next_activity = now + 15
        self.next_activity_event = 0.0
        self.flip_until = 0.0
        self.typing_pause = 0.0
        self.nap_until = 0.0
        self.last_user_input = now
        self.music = False
        self.music_reacted = False
        self.phones = False
        self.next_music_switch = 0.0
        self.beat_at = -10.0
        self.beat_count = 0
        self.next_note = 0.0
        self.last_startle = 0.0
        self.claude_sessions: dict[str, dict] = {}
        self.claude_work = False
        self.next_tool_say = 0.0
        self.pending_report: str | None = None
        self.report_at = 0.0
        self.next_session_sweep = 0.0
        self.fast_typing_since = 0.0
        self.last_typing_comment = -math.inf
        self.cursor: tuple[float, float, float] | None = None  # x, y, time it last moved
        self.hover_since = 0.0
        self.bounds = Bounds()
        self.ball: Ball | None = None
        self.ball_radius = 12.0
        self.tag = False
        self.tag_score = 0
        self.tag_grace = 0.0
        self.next_kick = 0.0
        self.game_until = 0.0
        self.on_game_end = None  # called with "ball" or "tag" when a game stops

    def set_unit(self, unit: float) -> None:
        old_w, old_h = self.w, self.h
        self.unit = unit
        self.w = GRID_W * unit
        self.h = GRID_H * unit
        if old_w:
            self.x += (old_w - self.w) / 2
            self.y += old_h - self.h

    def set_area(self, width: float, height: float) -> None:
        b = self.bounds
        b.min_x = 0
        b.max_x = max(0, width - self.w)
        b.min_y = 0
        b.max_y = max(0, height - self.h)
        b.width = width
        b.height = height

    @property
    def held(self) -> bool:
        return self.state in ("press", "drag", "pet")

    def hit_test(self, px: float, py: float) -> bool:
        u = self.unit
        top = self.y + self.hop_off
        return self.x + u <= px <= self.x + self.w - u and top + u * 2 <= py <= top + self.h

    def set_cursor(self, x: float, y: float, now: float) -> None:
        c = self.cursor
        if c and abs(c[0] - x) < 0.5 and abs(c[1] - y) < 0.5:
            return
        self.cursor = (x, y, now)
        self.note_user_input(now)

    def note_user_input(self, now: float) -> None:
        """The user moved the mouse or typed; after a long absence that wakes Clawd up."""
        if self.state == "sleep" and now - self.last_user_input > AWAY_AFTER:
            self.wake_up(now, "welcome_back")
        self.last_user_input = now

    def wake_up(self, now: float, phrase: str) -> None:
        self.nap_until = 0.0
        self.state = "idle"
        self.last_interaction = now
        self.next_action = now + rand(2, 4)
        self.hover_since = 0.0
        self.hop(300)
        self.fx.say(phrase, True)

    # ------------------------------------------------------------------ stepping

    def step(self, now: float) -> None:
        dt = clamp(now - self.last_tick, 0, 0.05)
        self.last_tick = now
        b = self.bounds

        if not self.ready:
            self.x = b.min_x + (b.max_x - b.min_x) * 0.8
            self.y = b.max_y
            self.ready = True
            self.hop(420)
            self.fx.say("hello")
            self.greet_at = now + 4

        ignored = now - self.last_interaction
        attended = ignored < 20 or self.held or self.ball is not None or self.tag
        self.mood.update(dt, self.hour(), self.state == "sleep", attended, ignored)
        if now >= self.next_memory_sync:
            self.next_memory_sync = now + 30
            self.memory.affection = self.mood.affection
        if self.greet_at and now >= self.greet_at:
            self.greet_at = 0.0
            self.greet()

        if self.ball is not None:
            self.ball.step(dt, b.width, b.height)
            if not self.ball.held and now - self.ball.last_touched > GAME_LENGTH:
                self.stop_ball("ball_end")
        if self.tag and now > self.game_until:
            self.stop_tag("tag_end")

        if self.state == "press" and self.touch and now - self.touch["t0"] >= 0.4:
            self.start_pet(now)
        if self.pending_report and now >= self.report_at:
            self.fx.say_text(self.pending_report, True)
            self.pending_report = None
        if now >= self.next_session_sweep:
            self.next_session_sweep = now + 5
            for sid in [i for i, s in self.claude_sessions.items() if now - s["last"] > SESSION_TIMEOUT]:
                del self.claude_sessions[sid]
            if not self.claude_sessions and self.claude_work and self.state == "code":
                self.finish_activity(now)

        state = self.state
        if state == "pet":
            if now >= self.next_heart:
                self.next_heart = now + 0.45
                self.fx.spawn("heart")
        elif state == "fly":
            self.step_fly(dt, now)
        elif state == "hang":
            self.step_hang(now)
        elif state in ("idle", "walk", "sleep"):
            self.step_ground(dt, now)
        elif state in ("read", "code", "think"):
            self.step_activity(now)
        elif state == "dance":
            self.step_dance(now)

        if self.hop_active:
            self.hop_off += self.hop_vy * dt
            self.hop_vy += GRAVITY * dt
            if self.hop_off >= 0:
                self.hop_off = 0.0
                self.hop_vy = 0.0
                self.hop_active = False
                self.squash_until = now + 0.1

    def step_ground(self, dt: float, now: float) -> None:
        b = self.bounds
        self.walk_in = False
        if self.state != "walk":
            # Walk back on screen if the work area shrank under us.
            if self.x < b.min_x - 0.5 or self.x > b.max_x + 0.5:
                step = 60 * dt
                if self.x < b.min_x:
                    self.x = min(b.min_x, self.x + step)
                    self.look = 1
                else:
                    self.x = max(b.max_x, self.x - step)
                    self.look = -1
                self.walk_in = True
            else:
                self.x = clamp(self.x, b.min_x, b.max_x)

        if self.y < b.max_y - 0.5 and not self.hop_active:
            self.fall(self.walk_speed() * self.look if self.state == "walk" else 0)
            return
        self.y = min(self.y, b.max_y)

        if self.state in ("idle", "walk") and not self.claude_sessions and self.play_step(dt, now):
            return

        if self.state == "walk":
            direction = 1 if self.walk_target > self.x else -1
            self.look = direction
            self.x += direction * self.walk_speed() * dt
            reached = (direction > 0 and self.x >= self.walk_target) or (direction < 0 and self.x <= self.walk_target)
            hit_edge = (direction < 0 and self.x <= b.min_x) or (direction > 0 and self.x >= b.max_x)
            if reached or hit_edge:
                self.x = clamp(self.x, b.min_x, b.max_x)
                self.state = "idle"
                self.next_action = now + rand(2.5, 6)
            return

        if self.state == "sleep":
            if self.nap_until and now >= self.nap_until:
                self.wake_up(now, "stretch")
                return
            if self.music:
                self.wake_up(now, "music_wake")
                return
            c = self.cursor
            if c and self.hit_test(c[0], c[1]):
                if not self.hover_since:
                    self.hover_since = now
                if now - self.hover_since > HOVER_WAKE:
                    self.wake_up(now, "wake")
                    return
            else:
                self.hover_since = 0.0
            if now >= self.next_z:
                self.next_z = now + 1.6
                self.fx.spawn("zzz")
            return

        if self.update_music(now):
            return
        settled = now - self.last_interaction > 3 and not self.hop_active and not self.walk_in
        if self.claude_sessions and self.state == "idle" and settled:
            self.start_work(now, False)
            return
        if self.music and self.phones and now >= self.next_note:
            self.next_note = now + rand(1.2, 2)
            self.fx.spawn("note")

        if now >= self.next_blink:
            self.blink_until = now + 0.13
            self.next_blink = now + (0.35 if random.random() < 0.15 else rand(2, 5.5))

        if self.update_hover(now):
            return

        attention = self.attention(now)
        if attention is not None:
            self.look = attention

        busy = self.music or bool(self.claude_sessions)
        sleep_after = SLEEP_AFTER * (0.5 if self.mood.label == "sleepy" else 1)
        if now - self.last_interaction > sleep_after and not busy and not self.hop_active and not self.walk_in:
            self.go_to_sleep(now, now + rand(*DEEP_SLEEP) * 60)
            self.fx.hide_bubble()
            return

        if now < self.next_action or self.hop_active or self.walk_in:
            return
        self.next_action = now + rand(3.5, 9)
        if self.act_on_mood(now):
            return
        if now >= self.next_activity and now - self.last_interaction > 8 and random.random() < 0.3:
            self.start_activity(now)
            return
        roll = random.random()
        if roll < 0.38:
            distance = rand(40, 160) * pick([-1, 1])
            target = clamp(self.x + distance, b.min_x, b.max_x)
            if abs(target - self.x) > 12:
                self.walk_target = target
                self.state = "walk"
        elif roll < 0.58:
            if attention is None:
                self.look = pick([-1, 0, 1])
        elif roll < 0.72:
            self.fx.say("idle")
        elif roll < 0.84:
            self.hop(360)
        else:
            self.wave_until = now + 1.6

    def walk_speed(self) -> float:
        return WALK_SPEED * (0.75 + 0.5 * self.mood.energy)

    def greet(self) -> None:
        """Right after he appears: say he missed the user, or greet them for the time of day."""
        if self.memory.launches > 1 and self.memory.gap_seconds >= 8 * 3600:
            self.fx.say("reunion", True, {"gap": self.memory.gap_seconds})
            return
        hour = self.hour()
        part = (
            "morning" if 5 <= hour < 11 else "lunch" if 12 <= hour < 14 else "evening" if 18 <= hour < 22
            else "night" if hour >= 23 or hour < 4 else None
        )
        if part:
            self.fx.say(f"greet_{part}")

    def act_on_mood(self, now: float) -> bool:
        """Moods nudge what he does with a quiet moment. True when it used this one up."""
        label = self.mood.label
        talk = now >= self.mood_quiet_until

        def speak(category: str) -> None:
            self.mood_quiet_until = now + rand(60, 150)
            self.fx.say(category)

        if label == "sulking":
            self.look = random.choice((-1, 1))
            if talk and random.random() < 0.5:
                speak("sulk")
            return True
        if label == "sleepy" and talk and random.random() < 0.4:
            speak("yawn")
            return True
        if label == "bored" and talk and random.random() < 0.6:
            return self.seek_attention(now)
        if label == "happy" and random.random() < 0.15:
            self.hop(400)
            self.fx.spawn("heart")
            return True
        return False

    def seek_attention(self, now: float) -> bool:
        """Bored: go and stand by the cursor, and every so often bring a ball."""
        if self.claude_sessions or self.music or self.ball is not None or self.tag:
            return False
        start_game = getattr(self.fx, "start_game", None)
        if self.mood.boredom > 0.9 and now >= self.next_offer and start_game:
            self.next_offer = now + OFFER_EVERY
            self.fx.say("ball_offer", True)
            start_game("ball")
            return True
        c, b = self.cursor, self.bounds
        if c and now - c[2] < 180:
            target = clamp(c[0] - self.w / 2, b.min_x, b.max_x)
            if abs(target - self.x) > 60:
                self.walk_target = target
                self.state = "walk"
                self.mood_quiet_until = now + rand(60, 150)
                self.fx.say("bored_poke")
                return True
        self.mood_quiet_until = now + rand(60, 150)
        self.fx.say("bored_poke")
        return True

    def start_activity(self, now: float) -> None:
        # No naps while music is playing.
        choices = [a for a in ACTIVITIES if a[0] != "sleep"] if self.music else ACTIVITIES
        state, lo, hi = pick(choices)
        self.look = 0
        if state == "sleep":
            self.go_to_sleep(now, now + rand(lo, hi))
            self.fx.say("nap")
            return
        self.state = state
        self.activity_start = now
        self.activity_until = now + rand(lo, hi)
        self.next_activity_event = now + rand(3, 6)
        self.typing_pause = 0.0
        self.fx.say(f"{state}_start")

    def go_to_sleep(self, now: float, nap_until: float) -> None:
        self.state = "sleep"
        self.phones = False
        self.music_reacted = False
        self.look = 0
        self.nap_until = nap_until
        self.next_z = now + 0.6

    def step_activity(self, now: float) -> None:
        max_y = self.bounds.max_y
        if self.y < max_y - 0.5:
            self.fall(0)
            return
        self.y = min(self.y, max_y)
        self.update_hover(now)
        self.update_music(now)

        if now >= self.next_blink:
            self.blink_until = now + 0.13
            self.next_blink = now + rand(2.5, 6)

        if now >= self.activity_until:
            self.finish_activity(now)
            return
        if now < self.next_activity_event:
            return

        if self.state == "read":
            self.next_activity_event = now + rand(4, 8)
            self.flip_until = now + 0.35
            if random.random() < 0.3:
                self.fx.say("read")
        elif self.state == "code":
            self.next_activity_event = now + rand(1.2, 2.6)
            if random.random() < 0.25:
                self.typing_pause = now + rand(1, 2.5)
            if self.claude_work:
                if now >= self.typing_pause:
                    self.fx.spawn("code")
            elif random.random() < 0.12:
                if random.random() < 0.35:
                    self.set_expr("angry", 1.6, now)
                    self.fx.say("bug")
                else:
                    self.fx.say("code")
            elif now >= self.typing_pause:
                self.fx.spawn("code")
        elif self.state == "think":
            self.next_activity_event = now + rand(3, 5)
            if random.random() < 0.4:
                self.fx.say("think")

    def finish_activity(self, now: float) -> None:
        state = self.state
        quiet = self.claude_work
        self.claude_work = False
        self.state = "idle"
        self.next_action = now + rand(2, 5)
        self.next_activity = now + rand(20, 45)
        if state == "think":
            self.hop(420)
            self.set_expr("happy", 1.5, now)
            for _ in range(3):
                self.fx.spawn("spark")
            self.fx.say("idea")
        elif not quiet:
            self.fx.say(f"{state}_end")

    def can_chat(self) -> bool:
        """Clawd is free to chat when he isn't held, flying or asleep."""
        return self.state in CALM_STATES

    def start_work(self, now: float, announce: bool) -> None:
        if self.state == "sleep":
            self.nap_until = 0.0
            self.set_expr("surprised", 0.8, now)
        if self.held or self.state in ("fly", "hang"):
            return
        self.state = "code"
        self.claude_work = True
        self.look = 0
        self.activity_start = now
        self.activity_until = math.inf
        self.next_activity_event = now + 0.5
        self.typing_pause = 0.0
        if self.music:
            self.phones = True
        if announce:
            self.fx.say("claude_start", True)

    # ------------------------------------------------------------------ games

    def start_ball(self, now: float, radius: float) -> None:
        b = self.bounds
        self.ball_radius = radius
        x = clamp(self.x + self.w / 2 + random.choice((-1, 1)) * rand(80, 200), radius, max(radius, b.width - radius))
        self.ball = Ball(x, radius, radius)
        self.ball.last_touched = now
        self.last_interaction = now
        self.mood.cheer(0.4, 0.02)
        self.fx.say("ball_start", True)

    def stop_ball(self, phrase: str | None = None) -> None:
        if self.ball is None:
            return
        self.ball = None
        if phrase:
            self.fx.say(phrase, True)
        if self.on_game_end:
            self.on_game_end("ball")

    def start_tag(self, now: float) -> None:
        self.tag = True
        self.tag_score = 0
        self.tag_grace = now + 1.0
        self.game_until = now + 120
        self.last_interaction = now
        self.mood.cheer(0.4, 0.02)
        self.fx.say("tag_start", True)

    def stop_tag(self, phrase: str | None = None) -> None:
        if not self.tag:
            return
        self.tag = False
        if phrase:
            self.fx.say(phrase, True, {"score": self.tag_score})
        if self.on_game_end:
            self.on_game_end("tag")

    def play_step(self, dt: float, now: float) -> bool:
        """Runs after the ball or away from the cursor. True when it moved him this tick."""
        if self.ball is not None and self.chase_ball(dt, now):
            return True
        return self.tag and self.flee_cursor(dt, now)

    def chase_ball(self, dt: float, now: float) -> bool:
        ball, b = self.ball, self.bounds
        self.last_interaction = now
        target = clamp(ball.x - self.w / 2, b.min_x, b.max_x)
        dx = target - self.x
        reach = self.w * 0.45
        if abs(dx) > reach:
            direction = 1 if dx > 0 else -1
            self.state = "walk"
            self.look = direction
            self.x = clamp(self.x + direction * RUN_SPEED * dt, b.min_x, b.max_x)
            return True
        # He is under the ball.
        self.state = "idle"
        top = self.y + self.hop_off
        reachable = top - 6 <= ball.y <= top + self.h and not ball.held
        if reachable and now >= self.next_kick and not self.hop_active:
            self.kick_ball(now)
        else:
            self.look = 0
        return True

    def kick_ball(self, now: float) -> None:
        ball = self.ball
        c = self.cursor
        centre = self.x + self.w / 2
        if c and abs(c[0] - centre) > 150:
            direction = 1 if c[0] > centre else -1  # towards the player
        else:
            direction = 1 if ball.x >= centre else -1
        if (direction > 0 and self.x >= self.bounds.max_x - 40) or (direction < 0 and self.x <= self.bounds.min_x + 40):
            direction = -direction
        ball.kick(direction * rand(380, 760), -rand(650, 1000), now)
        self.next_kick = now + 0.6
        self.mood.cheer(0.0, 0.002)
        self.look = direction
        self.hop(300)
        if random.random() < 0.3:
            self.fx.say("kick")

    def flee_cursor(self, dt: float, now: float) -> bool:
        c, b = self.cursor, self.bounds
        if not c or now - c[2] > 3 or now < self.tag_grace:
            return False
        cx, cy = self.x + self.w / 2, self.y + self.h / 2
        dx = cx - c[0]
        dist = math.hypot(dx, cy - c[1])
        if dist > TAG_RADIUS:
            return False
        direction = (1 if dx > 0 else -1) if abs(dx) > 1 else random.choice((-1, 1))
        nx = self.x + direction * FLEE_SPEED * dt
        if nx < b.min_x or nx > b.max_x:
            # Cornered: jump over the cursor.
            self.x = clamp(nx, b.min_x, b.max_x)
            if dist < 130:
                self.state = "fly"
                self.vx = -direction * rand(420, 560)
                self.vy = -820.0
                self.tag_grace = now + 0.8
                self.fx.say("tag_flee")
            else:
                self.state = "idle"
            return True
        self.x = nx
        self.look = direction
        self.state = "walk"
        self.last_interaction = now
        return True

    # ------------------------------------------------------------------ outside events

    def on_claude(self, event: dict, now: float) -> None:
        sessions = self.claude_sessions
        sid = event.get("session")
        kind = event.get("kind")

        def touch() -> None:
            session = sessions.get(sid) or {"project": event.get("project")}
            session["last"] = now
            if event.get("project"):
                session["project"] = event["project"]
            sessions[sid] = session

        if kind == "start":
            self.memory.note_project(event.get("project") or "")
            touch()
            self.last_interaction = now
            self.start_work(now, True)
        elif kind == "tool":
            touch()
            if not self.claude_work:
                self.start_work(now, False)
            if now < self.next_tool_say:
                return
            self.next_tool_say = now + TOOL_SAY_EVERY
            self.fx.say(f"tool:{event.get('tool')}", True, {"detail": event.get("detail"), "tool": event.get("tool")})
        elif kind == "thought":
            if sid in sessions:
                touch()
            self.next_tool_say = now + TOOL_SAY_EVERY
            self.fx.say_text(event["text"], True)
        elif kind == "attention":
            touch()
            self.wave_until = now + 3
            if self.state == "sleep":
                self.startle(now)
            self.hop(400)
            self.fx.say("claude_wait", True, {"project": event.get("project")})
        elif kind == "stop":
            sessions.pop(sid, None)
            if sessions:
                return
            if self.claude_work and self.state == "code":
                self.finish_activity(now)
            self.claude_work = False
            self.hop(520)
            self.wave_until = now + 2.5
            for _ in range(3):
                self.fx.spawn("spark")
            self.mood.cheer(0.0, 0.02)
            self.fx.say("claude_done", True, {"project": event.get("project")})
            self.report_at = now + 3
        elif kind == "report":
            # Haiku's summary follows the "done!" line instead of replacing it.
            if now < self.report_at:
                self.pending_report = event["text"]
            else:
                self.fx.say_text(event["text"], True)
        elif kind == "end":
            sessions.pop(sid, None)
            if not sessions and self.claude_work and self.state == "code":
                self.finish_activity(now)

    def on_permission(self, now: float) -> None:
        """Claude Code asks for an answer: get his attention."""
        self.wave_until = now + 3
        self.last_interaction = now
        if self.state == "sleep":
            self.wake_up(now, "wake")
        else:
            self.hop(400)

    def on_typing(self, kpm: int | None, now: float) -> None:
        if kpm is None:
            return
        if kpm > 0:
            self.note_user_input(now)
            self.memory.note_activity()
            hour = self.hour()
            if 1 <= hour < 5 and now - self.last_late_night > LATE_NIGHT_EVERY and self.can_chat():
                self.last_late_night = now
                self.fx.say("late_night", True)
        if kpm < FAST_TYPING_KPM:
            self.fast_typing_since = 0.0
            return
        if not self.fast_typing_since:
            self.fast_typing_since = now
        sustained = now - self.fast_typing_since > 6
        if sustained and now - self.last_typing_comment > 180 and self.can_chat():
            self.last_typing_comment = now
            self.fx.say("typing_fast")

    def on_audio(self, event: dict, now: float) -> None:
        kind = event.get("kind")
        if kind == "music":
            self.music = bool(event["playing"])
        elif kind == "beat":
            self.beat_at = now
            self.beat_count += 1
            if self.state == "dance" and event.get("strength", 0) > 4 and random.random() < 0.2:
                self.hop(300)
        elif kind == "loud":
            self.startle(now)

    def startle(self, now: float) -> None:
        if self.state == "sleep":
            self.nap_until = 0.0
            self.state = "idle"
            self.last_interaction = now
            self.next_action = now + rand(2, 4)
            self.set_expr("surprised", 1.4, now)
            self.hop(520)
            self.fx.say("loud_wake", True)
            return
        if self.state not in CALM_STATES or now - self.last_startle < 8:
            return
        self.last_startle = now
        self.set_expr("surprised", 0.8, now)
        self.squash_until = now + 0.15
        self.hop(300)
        if random.random() < 0.5:
            self.fx.say("loud")

    def update_music(self, now: float) -> bool:
        """Reacts to music starting, switching between dancing and listening, and stopping.
        Returns True when it changed what Clawd is doing this tick."""
        if not self.music:
            if not self.music_reacted:
                return False
            self.music_reacted = False
            was_dancing = self.state == "dance"
            if was_dancing:
                self.state = "idle"
                self.next_action = now + rand(2, 4)
            if self.phones or was_dancing:
                self.fx.say("music_end")
            self.phones = False
            return was_dancing
        if not self.music_reacted:
            self.music_reacted = True
            self.next_music_switch = now + rand(25, 50)
            if self.state in ACTIVITY_STATES:
                self.phones = True
                self.fx.say("music_phones")
            elif random.random() < 0.5:
                self.start_dance()
            else:
                self.phones = True
                self.fx.say("music")
            return True
        if now >= self.next_music_switch and self.state in ("idle", "dance"):
            self.next_music_switch = now + rand(25, 50)
            if self.state == "dance":
                self.state = "idle"
                self.phones = True
                self.fx.say("music")
            else:
                self.start_dance()
            return True
        return False

    def start_dance(self) -> None:
        self.state = "dance"
        self.look = 0
        self.fx.say("music_dance")

    def step_dance(self, now: float) -> None:
        max_y = self.bounds.max_y
        if self.y < max_y - 0.5 and not self.hop_active:
            self.fall(0)
            return
        self.y = min(self.y, max_y)
        self.update_hover(now)
        if self.update_music(now):
            return
        if now >= self.next_note:
            self.next_note = now + rand(0.6, 1.1)
            self.fx.spawn("note")

    def beat(self, now: float) -> tuple[int, float]:
        """Real beats while the analyzer finds them, otherwise a steady 120 BPM. -> (count, since)"""
        if now - self.beat_at < 1.5:
            return self.beat_count, now - self.beat_at
        t = now * 2
        return math.floor(t), (t % 1) / 2

    def update_hover(self, now: float) -> bool:
        """Cursor resting on Clawd makes him happy and keeps him in place."""
        c = self.cursor
        if not c or not self.hit_test(c[0], c[1]):
            self.hover_since = 0.0
            return False
        if not self.hover_since:
            self.hover_since = now
        self.last_interaction = now
        self.look = 0
        self.next_action = max(self.next_action, now + 1)
        if self.state != "idle":
            return True
        busy = self.expr and self.expr != "happy" and now < self.expr_until
        if now - self.hover_since > 0.6 and not busy:
            self.set_expr("happy", 0.3, now)
        return True

    def attention(self, now: float) -> int | None:
        """Eyes follow a recently moved cursor: -1 left, 0 straight, 1 right."""
        c = self.cursor
        if not c or now - c[2] > ATTENTION_TIME:
            return None
        dx = c[0] - (self.x + self.w / 2)
        dy = c[1] - (self.y + self.h / 2)
        if math.hypot(dx, dy) > ATTENTION_RADIUS:
            return None
        if abs(dx) < self.w / 2:
            return 0
        return 1 if dx > 0 else -1

    # ------------------------------------------------------------------ flying and hanging

    def fall(self, vx: float) -> None:
        self.state = "fly"
        self.vx = vx
        self.vy = 0.0

    def step_fly(self, dt: float, now: float) -> None:
        b = self.bounds
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += GRAVITY * dt
        self.vx *= math.exp(-0.35 * dt)

        impact = 0.0
        if self.x < b.min_x:
            self.x = b.min_x
            impact = max(impact, abs(self.vx))
            self.vx = abs(self.vx) * 0.55
        elif self.x > b.max_x:
            self.x = b.max_x
            impact = max(impact, abs(self.vx))
            self.vx = -abs(self.vx) * 0.55

        if self.y < b.min_y:
            if self.vy < -250:
                self.start_hang(now)
                return
            self.y = b.min_y
            impact = max(impact, abs(self.vy))
            self.vy = abs(self.vy) * 0.55
        elif self.y > b.max_y:
            self.y = b.max_y
            impact = max(impact, abs(self.vy))
            if self.vy < 380:
                self.land(now)
            else:
                self.vy = -abs(self.vy) * 0.42
                self.vx *= 0.75
                self.squash_until = now + 0.1

        if impact > 2300:
            self.set_expr("dizzy", 2.6, now)
            self.fx.say("dizzy")

    def land(self, now: float) -> None:
        self.state = "idle"
        self.vx = 0.0
        self.vy = 0.0
        self.squash_until = now + 0.14
        self.next_action = now + rand(2, 5)

    def start_hang(self, now: float) -> None:
        self.state = "hang"
        self.vx = 0.0
        self.vy = 0.0
        self.y = self.bounds.min_y - self.unit * 2
        self.hang_start = now
        self.hang_until = now + rand(2, 3.5)
        if random.random() < 0.7:
            self.fx.say("hang")

    def step_hang(self, now: float) -> None:
        b = self.bounds
        self.x = clamp(self.x, b.min_x, b.max_x)
        self.y = b.min_y - self.unit * 2
        if now < self.hang_until:
            return
        self.state = "fly"
        self.vx = 0.0
        self.vy = 150.0
        self.set_expr("surprised", 0.8, now)
        if random.random() < 0.6:
            self.fx.say("hang_fall")

    def recall(self, now: float) -> None:
        b = self.bounds
        self.x = (b.min_x + b.max_x) / 2
        self.y = (b.min_y + b.max_y) / 2
        self.touch = None
        self.hop_active = False
        self.hop_off = 0.0
        self.last_interaction = now
        self.fall(0)
        for _ in range(3):
            self.fx.spawn("spark")
        self.fx.say("hello")

    # ------------------------------------------------------------------ drawing

    def frame(self, now: float) -> Frame:
        eyes = "open"
        look = self.look
        arms = "normal"
        legs = "stand"
        squash = 0
        blush = False
        prop = None
        spinner = -1
        flip = False
        state = self.state

        def walk_legs() -> str:
            return "walk1" if math.floor(now * 7) % 2 else "walk2"

        if state in ("drag", "fly"):
            eyes, arms, legs = "surprised", "up", "dangle"
        elif state == "pet":
            eyes = "happy"
            blush = True
            squash = 1 if math.floor(now * 3) % 2 else 0
            look = 0
        elif state == "sleep":
            eyes, arms, legs, look = "sleep", "tuck", "tuck", 0
        elif state == "read":
            # Eyes sweep along the lines, then jump back to the start of the next one.
            eyes, arms, prop = "down", "hold", "book"
            look = (-1, 0, 1)[min(2, int(((now - self.activity_start) % 2.4) / 0.8))]
            flip = now < self.flip_until
        elif state == "code":
            prop = "laptop"
            typing = now >= self.typing_pause
            arms = "type1" if typing and math.floor(now * 8) % 2 else "type2"
            if not typing:
                eyes = "up"
            look = 0 if typing else 1
        elif state == "dance":
            count, since = self.beat(now)
            odd = count % 2 == 1
            eyes = "happy"
            arms = "wave1" if odd else "wave2"
            legs = "walk1" if odd else "walk2"
            squash = 1 if since < 0.12 else 0
            look = 1 if odd else -1
        elif state == "think":
            eyes = "up"
            look = 1 if math.floor(now * 0.8) % 2 else -1
            spinner = math.floor(now * 8) % SPINNER_LENGTH
        elif state == "walk":
            legs = walk_legs()
        elif state == "press":
            squash = 1
        elif state == "hang":
            eyes = "surprised" if now - self.hang_start < 0.5 else "open"
            arms, legs = "up", "dangle"
            look = -1 if math.floor(now * 1.5) % 2 else 1

        if self.walk_in and state == "idle":
            legs = walk_legs()
        if state == "idle" and self.phones and self.music:
            # Nodding along, eyes closed every few bars.
            count, since = self.beat(now)
            if since < 0.1:
                squash = 1
            if count % 8 >= 6:
                eyes = "happy"
        if state in ("idle", "walk") and now < self.wave_until:
            arms = "wave1" if math.floor(now * 5) % 2 else "wave2"
        if self.hop_active and state in ("idle", "walk"):
            legs = "dangle"
            if self.hop_vy < 0:
                squash = -1
                arms = "up"
        if state == "idle" and self.mood._label == "sulking" and not self.hop_active:
            eyes, arms, look = "sleep", "tuck", self.look or -1
        if now < self.expr_until and self.expr and state != "sleep":
            eyes = self.expr
            if self.expr == "happy":
                blush = True
        if now < self.squash_until and squash == 0:
            squash = 1
        can_blink = state in ("idle", "walk", "hang", "code", "think")
        if now < self.blink_until and eyes in ("open", "up") and can_blink:
            eyes = "blink"
        if legs == "tuck" and squash:
            squash = 0
        cap = state == "sleep"
        phones = self.phones and not cap
        return Frame(eyes, look, arms, legs, squash, blush, prop, cap, phones, spinner, flip)

    def rotation(self, now: float) -> float:
        if self.state == "fly":
            return clamp(self.vx / 70, -25, 25)
        if self.state == "dance":
            count, since = self.beat(now)
            return (1 if count % 2 else -1) * (4 + 4 * max(0.0, 1 - since * 4))
        if self.state == "hang":
            return math.sin((now - self.hang_start) * 3) * 6
        if self.expr == "dizzy" and now < self.expr_until:
            return math.sin(now * 10) * 7
        if self.state == "drag" and self.touch:
            last = self.touch["hist"][-1]
            if now - last[0] < 0.08:
                return clamp(self.touch["vx"] / 90, -18, 18)
        return 0.0

    # ------------------------------------------------------------------ pointer

    def pointer_down(self, x: float, y: float, now: float) -> None:
        was_sleeping = self.state == "sleep"
        interrupted = self.state if (self.state in ACTIVITY_STATES or self.state == "dance") else None
        self.last_interaction = now
        self.y += self.hop_off
        self.hop_active = False
        self.hop_off = 0.0
        self.hop_vy = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.touch = {
            "x0": x,
            "y0": y,
            "t0": now,
            "ox": x - self.x,
            "oy": y - self.y,
            "hist": [(now, x, y)],
            "anchor": x,
            "sulking": self.mood.label == "sulking",
            "dir": 0,
            "vx": 0.0,
        }
        self.state = "press"
        self.nap_until = 0.0
        if interrupted:
            self.next_activity = now + rand(20, 45)
        if interrupted == "dance":
            self.next_music_switch = now + rand(3, 6)
        if was_sleeping:
            self.set_expr("surprised", 0.9, now)
            self.fx.say("wake")
        elif interrupted:
            self.set_expr("surprised", 0.7, now)
            self.fx.say("work_stop" if self.claude_work else f"{interrupted}_stop", True)
        self.claude_work = False

    def pointer_move(self, x: float, y: float, now: float) -> None:
        touch = self.touch
        if not touch:
            return
        hist = touch["hist"]
        hist.append((now, x, y))
        while len(hist) > 2 and now - hist[0][0] > 0.1:
            hist.pop(0)

        if self.state == "press" and math.hypot(x - touch["x0"], y - touch["y0"]) > 8:
            self.state = "drag"
            if random.random() < 0.25:
                self.fx.say("drag")
        if self.state == "drag":
            b = self.bounds
            self.x = clamp(x - touch["ox"], -self.w * 0.3, b.width - self.w * 0.7)
            self.y = clamp(y - touch["oy"], 0, b.height - self.h * 0.5)
            t0, x0, _ = hist[0]
            if now - t0 > 0.005:
                touch["vx"] = (x - x0) / (now - t0)
        elif self.state == "pet":
            # Rubbing back and forth: every change of direction is a stroke.
            dx = x - touch["anchor"]
            if abs(dx) > 10:
                direction = 1 if dx > 0 else -1
                if touch["dir"] and direction != touch["dir"]:
                    self.fx.spawn("heart")
                    self.mood.cheer(0.08, 0.04)
                    self.memory.note_pet()
                touch["dir"] = direction
                touch["anchor"] = x

    def pointer_up(self, now: float, cancelled: bool = False) -> None:
        touch = self.touch
        if not touch:
            return
        self.touch = None
        self.last_interaction = now
        if self.state == "drag":
            self.release_drag(now, touch, cancelled)
        elif self.state == "pet":
            was_sulking = touch["sulking"]
            self.mood.cheer(0.4, 0.05)
            self.state = "idle"
            self.set_expr("happy", 2.2, now)
            self.next_action = now + rand(3, 6)
            if was_sulking and self.mood.label != "sulking":
                self.fx.say("forgive", True)
            elif random.random() < 0.6:
                self.fx.say("pet")
        elif self.state == "press":
            self.state = "idle"
            if not cancelled:
                self.tap(now)

    def release_drag(self, now: float, touch: dict, cancelled: bool) -> None:
        vx = vy = 0.0
        hist = touch["hist"]
        if not cancelled and len(hist) >= 2:
            t0, x0, y0 = hist[0]
            t1, x1, y1 = hist[-1]
            if t1 - t0 > 0.005 and now - t1 < 0.08:
                vx = (x1 - x0) / (t1 - t0)
                vy = (y1 - y0) / (t1 - t0)
        vx = clamp(vx, -6000, 6000)
        vy = clamp(vy, -6000, 6000)
        speed = math.hypot(vx, vy)
        if speed <= 700 and self.y <= self.bounds.min_y + self.unit * 2:
            self.start_hang(now)
            return
        self.state = "fly"
        self.vx = vx
        self.vy = vy
        if speed > 2600:
            self.fx.say("throw")

    def tap(self, now: float) -> None:
        if self.tag:
            self.tag_score += 1
            self.tag_grace = now + 1.5
            self.game_until = max(self.game_until, now + 60)
            self.hop(520)
            self.set_expr("happy", 1.2, now)
            self.fx.say("tag_caught", True, {"score": self.tag_score})
            return
        if now - self.last_tap < 0.32:
            self.last_tap = 0.0
            self.hop(720)
            self.set_expr("happy", 1.3, now)
            for _ in range(3):
                self.fx.spawn("spark")
            if random.random() < 0.4:
                self.fx.say("jump")
            return
        self.last_tap = now
        self.recent_taps = [t for t in self.recent_taps if now - t < 2.5]
        self.recent_taps.append(now)
        if len(self.recent_taps) >= 6:
            self.recent_taps = []
            self.mood.annoy(0.1)
            self.set_expr("angry", 2.5, now)
            self.fx.say("annoyed", True)
            return
        self.mood.cheer(0.1, 0.01)
        self.squash_until = now + 0.12
        self.set_expr("surprised", 0.45, now)
        self.hop(330)
        if random.random() < 0.35:
            self.fx.say("tap")

    def start_pet(self, now: float) -> None:
        self.state = "pet"
        self.next_heart = now + 0.1

    def set_expr(self, expr: str, duration: float, now: float) -> None:
        self.expr = expr
        self.expr_until = now + duration

    def hop(self, strength: float) -> None:
        if self.state not in ("idle", "walk", "dance"):
            return
        self.hop_active = True
        self.hop_vy = -strength
        if self.hop_off > 0:
            self.hop_off = 0.0
