"""Preferences that survive restarts, stored as JSON in the app's config folder."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

LANGUAGES = ("ru", "en")
LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}


def system_language() -> str:
    """Russian for Russian-language systems, English otherwise."""
    if sys.platform == "win32":
        try:
            import ctypes

            # Primary language id 0x19 is Russian.
            return "ru" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x19 else "en"
        except Exception:
            return "en"
    name = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "ru" if name.lower().startswith("ru") else "en"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "clawd"


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "clawd"


@dataclass
class Settings:
    language: str = ""
    claude_path: str | None = None
    listen: bool = True
    watch: bool = True
    llm: bool = True
    ask: bool = True  # answer Claude Code's permission questions from the bubble

    def __post_init__(self) -> None:
        if self.language not in LANGUAGES:
            self.language = system_language()

    @classmethod
    def load(cls, path: Path) -> Settings:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f.name: f for f in fields(cls)}
        clean = {}
        for key, value in raw.items():
            field = known.get(key)
            if field is None:
                continue
            if key == "claude_path":
                if isinstance(value, str) or value is None:
                    clean[key] = value
            elif key == "language":
                clean[key] = value
            elif isinstance(value, bool):
                clean[key] = value
        return cls(**clean)

    def save(self, path: Path) -> None:
        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            print(f"settings: cannot save {path}: {e}", file=sys.stderr)
