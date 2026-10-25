"""Clawd's "brain": what's going on around him, and a stash of lines from Claude Haiku.
The situation is described to Haiku in English; the Haiku object tells it which language to answer in."""
from __future__ import annotations

import threading
import time
from datetime import datetime

from . import claude
from .claude import Haiku, HaikuError

CACHE_TTL = 150
REFILL_EVERY = 30
CACHE_LOW = 4
WINDOW_COMMENT_EVERY = 45
APP_FORGET_AFTER = 15 * 60

STATE_NAMES = {
    "idle": "just standing around",
    "walk": "strolling along the taskbar",
    "read": "reading a book",
    "code": "coding on a laptop",
    "think": "thinking about something",
    "dance": "dancing",
    "sleep": "sleeping",
    "fly": "flying through the air, someone threw him",
    "hang": "hanging from the edge of the screen",
    "drag": "being dragged by the mouse",
    "pet": "being petted",
}

DAY_PARTS = [(5, "morning"), (12, "afternoon"), (18, "evening"), (23, "night")]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class Brain:
    def __init__(self, haiku: Haiku, post) -> None:
        """`post(fn)` runs `fn` on the thread that owns the pet; Haiku calls finish elsewhere."""
        self.haiku = haiku
        self.post = post
        self.cache: list[tuple[str, float]] = []  # (line, time it arrived)
        self.last_request = -float("inf")
        self.pending = False
        self.available = True
        self.window: dict | None = None
        self.kpm = 0
        self.app_seen: dict[str, float] = {}
        self.last_window_comment = -float("inf")

    def forget(self) -> None:
        """Drops lines in the old language and asks again right away."""
        self.cache = []
        self.available = True
        self.last_request = -float("inf")

    def on_context(self, event: dict, now: float) -> str | None:
        """Returns an app name worth commenting on when the user switches to a new app."""
        if event["kind"] == "typing":
            self.kpm = event["kpm"]
            return None
        app, title = event.get("app", ""), event.get("title", "")
        if not app and not title:
            self.window = None
            return None
        self.window = {"app": app, "title": title}
        key = app or title
        seen = self.app_seen.get(key)
        self.app_seen[key] = now
        fresh = seen is None or now - seen > APP_FORGET_AFTER
        if not fresh or now - self.last_window_comment < WINDOW_COMMENT_EVERY:
            return None
        self.last_window_comment = now
        return key

    def describe(self, pet, extra: str = "") -> str:
        date = datetime.now()
        part = next((name for hour, name in reversed(DAY_PARTS) if date.hour >= hour), "night")
        lines = [f"- It is {date:%H:%M}, {part}, {WEEKDAYS[date.weekday()]}."]
        if self.window:
            app = f" ({self.window['app']})" if self.window["app"] else ""
            lines.append(f'- The user has this window open: "{self.window["title"]}"{app}.')
        if self.kpm > 0:
            lines.append(f"- The user is typing, about {self.kpm} key presses a minute.")
        else:
            idle = round((time.monotonic() - pet.last_user_input) / 60)
            if idle >= 3:
                lines.append(
                    f"- The user hasn't touched the mouse or keyboard for about {idle} min; they seem away or idle."
                )
        if pet.music:
            lines.append("- Music is playing.")
        lines.append(f"- Clawd is {STATE_NAMES.get(pet.state, 'busy with his own things')}.")
        lines.append(f"- {pet.mood.describe()}")
        lines.extend(f"- {fact}" for fact in pet.memory.summary())
        projects = [s["project"] for s in pet.claude_sessions.values() if s.get("project")]
        if projects:
            lines.append(f"- Claude Code is working on a task in {', '.join(projects)}.")
        if extra:
            lines.append(f"- {extra}")
        return "\n".join(lines)

    def _ask(self, context: str, count: int, done) -> None:
        """Asks Haiku on a worker thread; `done(lines)` runs back on the pet's thread."""
        self.pending = True
        self.last_request = time.monotonic()

        def work() -> None:
            lines: list[str] = []
            available: bool | None = None  # None: leave as it was
            try:
                out = self.haiku.run(lambda lang: claude.idle_prompt(lang, context, max(1, min(count, 10))))
                lines = claude.phrases(out, 50)
                available = True
            except HaikuError as e:
                # Not installed or switched off: stop asking for a while.
                if "not found" in str(e) or "disabled" in str(e):
                    available = False
            except Exception:
                pass

            def finish() -> None:
                self.pending = False
                if available is not None:
                    self.available = available
                done(lines)

            self.post(finish)

        threading.Thread(target=work, name="clawd-haiku", daemon=True).start()

    def maybe_refill(self, pet, now: float) -> None:
        self.cache = [(text, at) for text, at in self.cache if now - at < CACHE_TTL]
        interval = REFILL_EVERY if self.available else REFILL_EVERY * 6
        if self.pending or len(self.cache) >= CACHE_LOW or now - self.last_request < interval:
            return

        def done(lines: list[str]) -> None:
            at = time.monotonic()
            self.cache.extend((text, at) for text in lines)

        self._ask(self.describe(pet), 8, done)

    def take(self) -> str | None:
        return self.cache.pop(0)[0] if self.cache else None

    def comment(self, pet, app: str, done) -> None:
        """One line about the window the user just switched to; `done(line or None)`."""
        if self.pending or not self.available:
            done(None)
            return
        context = self.describe(pet, f"The user just switched to {app}.")
        self._ask(context, 1, lambda lines: done(lines[0] if lines else None))
