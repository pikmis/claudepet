"""What Clawd remembers about the user between launches, kept in a small JSON file.

Only counts and names are stored: which apps were in focus and for how long, which hours the
user is usually around, which Claude Code projects they work on, how much they pet Clawd. Window
titles, text and keystrokes are never kept. "Forget everything" in the tray wipes the file."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

MAX_APPS = 60
MAX_PROJECTS = 30
MAX_APP_GAP = 600  # seconds of one window's focus that count; longer means the user walked away
IGNORED_APPS = {"python", "pythonw", "clawd"}
SAVE_EVERY = 60.0
AFFECTION_START = 0.5


def now_dt() -> datetime:
    return datetime.now()


class Memory:
    def __init__(self, path: Path | None = None, clock=now_dt) -> None:
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()
        self.dirty = False
        self.last_save = 0.0
        self.reset()
        if path is not None:
            self.load()
        # How long they were apart before this launch; the session starts now.
        self.gap_seconds = self._gap()
        self.session_started = self.clock()
        self.launches += 1
        self.touch()

    def reset(self) -> None:
        stamp = self.clock().isoformat(timespec="seconds")
        self.first_seen = stamp
        self.last_seen = stamp
        self.launches = 0
        self.apps: dict[str, float] = {}
        self.hours = [0] * 24
        self.projects: dict[str, int] = {}
        self.pets = 0
        self.affection = AFFECTION_START
        self._focus: tuple[str, datetime] | None = None
        self.dirty = True

    def _gap(self) -> float:
        try:
            return max(0.0, (self.clock() - datetime.fromisoformat(self.last_seen)).total_seconds())
        except ValueError:
            return 0.0

    # ------------------------------------------------------------------ noting things

    def touch(self) -> None:
        self.last_seen = self.clock().isoformat(timespec="seconds")
        self.dirty = True

    def note_window(self, app: str) -> None:
        """The focus moved to `app`; the one it left gets the time it was in front."""
        now = self.clock()
        with self._lock:
            if self._focus is not None:
                name, since = self._focus
                seconds = min((now - since).total_seconds(), MAX_APP_GAP)
                if name and seconds > 0:
                    self.apps[name] = self.apps.get(name, 0.0) + seconds
            key = app.strip().lower()
            self._focus = (key, now) if key and key not in IGNORED_APPS else None
            if len(self.apps) > MAX_APPS:
                for name in sorted(self.apps, key=self.apps.get)[: len(self.apps) - MAX_APPS]:
                    del self.apps[name]
            self.dirty = True

    def note_activity(self) -> None:
        """The user is at the computer right now."""
        self.hours[self.clock().hour] += 1
        self.dirty = True

    def note_project(self, project: str) -> None:
        if not project:
            return
        self.projects[project] = self.projects.get(project, 0) + 1
        if len(self.projects) > MAX_PROJECTS:
            del self.projects[min(self.projects, key=self.projects.get)]
        self.dirty = True

    def note_pet(self) -> None:
        self.pets += 1
        self.dirty = True

    # ------------------------------------------------------------------ what Haiku is told

    def summary(self) -> list[str]:
        """Facts about the user for the model, in English."""
        now = self.clock()
        lines: list[str] = []
        try:
            days = (now - datetime.fromisoformat(self.first_seen)).days
        except ValueError:
            days = 0
        if days >= 1:
            lines.append(f"Clawd has known the user for {days} days and been started {self.launches} times.")
        if (now - self.session_started).total_seconds() < 15 * 60 and self.gap_seconds >= 2 * 3600:
            lines.append(f"The user has just come back after {describe_gap(self.gap_seconds)} away.")
        favorites = [
            name for name, seconds in sorted(self.apps.items(), key=lambda kv: -kv[1])[:3] if seconds >= 600
        ]
        if favorites:
            lines.append(f"The apps the user spends most time in: {', '.join(favorites)}.")
        total = sum(self.hours)
        if total >= 100:
            night = sum(self.hours[0:5]) / total
            busiest = max(range(24), key=lambda h: self.hours[h])
            lines.append(f"The user is most active around {busiest:02d}:00.")
            if night > 0.12:
                lines.append("The user often stays up past midnight.")
        if self.projects:
            top = max(self.projects, key=self.projects.get)
            lines.append(f'The user\'s main Claude Code project is "{top}".')
        if self.pets >= 30:
            lines.append("The user pets Clawd a lot.")
        elif self.launches >= 5 and self.pets < 3:
            lines.append("The user has hardly ever petted Clawd.")
        return lines

    # ------------------------------------------------------------------ files

    def to_dict(self) -> dict:
        return {
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "launches": self.launches,
            "apps": {k: round(v) for k, v in self.apps.items()},
            "hours": self.hours,
            "projects": self.projects,
            "pets": self.pets,
            "affection": round(self.affection, 3),
        }

    def load(self) -> None:
        try:
            raw = json.loads(Path(self.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return

        def number(key, default):
            value = raw.get(key)
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default

        for key in ("first_seen", "last_seen"):
            if isinstance(raw.get(key), str):
                try:
                    datetime.fromisoformat(raw[key])
                    setattr(self, key, raw[key])
                except ValueError:
                    pass
        self.launches = int(number("launches", 0))
        self.pets = int(number("pets", 0))
        self.affection = min(1.0, max(0.0, float(number("affection", AFFECTION_START))))
        apps = raw.get("apps")
        if isinstance(apps, dict):
            self.apps = {str(k): float(v) for k, v in apps.items() if isinstance(v, (int, float))}
        projects = raw.get("projects")
        if isinstance(projects, dict):
            self.projects = {str(k): int(v) for k, v in projects.items() if isinstance(v, (int, float))}
        hours = raw.get("hours")
        if isinstance(hours, list) and len(hours) == 24 and all(isinstance(h, (int, float)) for h in hours):
            self.hours = [int(h) for h in hours]

    def save(self, force: bool = False) -> None:
        if self.path is None or not (self.dirty or force):
            return
        self.touch()
        data = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass

    def forget(self) -> None:
        """Starts over as if they had just met."""
        with self._lock:
            self.reset()
            self.launches = 1
            self.gap_seconds = 0.0
            self.session_started = self.clock()
        self.save(force=True)


def describe_gap(seconds: float) -> str:
    hours = int(seconds // 3600)
    if hours < 48:
        return f"{max(hours, 1)} hours"
    return f"{hours // 24} days"
