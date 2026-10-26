"""Preferences that survive restarts, stored as JSON in the app's config folder."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .llm import PRESETS, PROVIDERS

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
    provider: str = "claude"  # which model writes his lines: "claude" (Claude Code CLI) or an API from llm.PRESETS
    api: dict = field(default_factory=dict)  # per provider: {"key": ..., "model": ..., "base": ...}

    def __post_init__(self) -> None:
        if self.language not in LANGUAGES:
            self.language = system_language()
        if self.provider != "claude" and self.provider not in PROVIDERS:
            self.provider = "claude"

    def api_config(self, provider: str | None = None) -> dict:
        """What is saved for a provider, with the preset address and first suggested model filled in."""
        provider = provider or self.provider
        saved = self.api.get(provider, {})
        preset = PRESETS.get(provider)
        return {
            "base": saved.get("base") or (preset[1] if preset else ""),
            "key": saved.get("key", ""),
            "isolated": saved.get("isolated") == "1",
            "model": saved.get("model") or (preset[2][0] if preset else "claude-haiku-5-5" if provider == "claude" else ""),
        }

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
            elif key == "provider":
                if isinstance(value, str):
                    clean[key] = value
            elif key == "api":
                if isinstance(value, dict):
                    clean[key] = {
                        str(name): {str(k): v for k, v in conf.items() if isinstance(v, str)}
                        for name, conf in value.items()
                        if isinstance(conf, dict)
                    }
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
