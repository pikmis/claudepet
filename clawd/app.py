"""Wires everything together: the overlay, the tray menu, Haiku, hooks and the sensors."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QFileDialog

from . import audio, claude, context, hooks
from .overlay import Overlay
from .memory import Memory, SAVE_EVERY
from .settings import Settings, config_dir, data_dir
from .tray import TrayMenu, app_icon, text

DEBUG = bool(os.environ.get("CLAWD_DEBUG"))


class Dispatcher(QObject):
    """Runs callables on the Qt thread; `post` is safe to call from any thread."""

    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(lambda fn: fn(), Qt.QueuedConnection)

    def post(self, fn) -> None:
        self.call.emit(fn)


def log(*args) -> None:
    print(*args, file=sys.stderr, flush=True)


class Clawd:
    def __init__(self) -> None:
        self.settings_path = config_dir() / "settings.json"
        self.settings = Settings.load(self.settings_path)
        self._settings_lock = threading.Lock()
        directory = data_dir()
        workdir = directory / "haiku"
        workdir.mkdir(parents=True, exist_ok=True)
        # Claude Code reports cwd with symlinks resolved.
        workdir = workdir.resolve()

        self.dispatcher = Dispatcher()
        self.post = self.dispatcher.post
        self.haiku = claude.Haiku(workdir, self.settings.language, self.settings.llm)
        self.listening = threading.Event()
        self.watching = threading.Event()
        if self.settings.listen:
            self.listening.set()
        if self.settings.watch:
            self.watching.set()
        self.token = hooks.load_token(directory)

        self.memory = Memory(directory / "memory.json")
        self.overlay = Overlay(self.haiku, self.post, self.memory)
        self.tray = TrayMenu(self)

        # Looking through the login shell can take a few seconds; don't hold up startup.
        threading.Thread(target=self._find_cli, name="clawd-find-claude", daemon=True).start()

        audio.spawn(self.listening, lambda e: self._deliver("audio", e, self.overlay.on_audio))
        context.spawn(self.watching, lambda e: self._deliver("context", e, self.overlay.on_context))
        hub = hooks.Hub(
            self.haiku,
            lambda e: self._deliver("claude", e, self.overlay.on_claude),
            permissions_enabled=lambda: self.settings.ask,
        )
        self.overlay.on_decision = hub.resolve
        try:
            self.server = hooks.serve(self.token, hub)
        except OSError as e:
            self.server = None
            log(f"Claude Code hook endpoint unavailable: {e}")

        self.overlay.on_game_started = lambda name: self.tray.set_checked(getattr(self.tray, name), True)
        self.save_timer = QTimer()
        self.save_timer.timeout.connect(lambda: self.memory.save())
        self.save_timer.start(int(SAVE_EVERY * 1000))
        self.overlay.pet.on_game_end = lambda name: self.tray.set_checked(getattr(self.tray, name), False)
        self.overlay.start()

    # ------------------------------------------------------------------ plumbing

    def _deliver(self, name: str, event: dict, handler) -> None:
        """Forwards a sensor event to the overlay; with CLAWD_DEBUG set, also logs it."""
        if DEBUG:
            log(f"[{name}] {event}")
        self.post(lambda: handler(event))

    def update(self, change) -> None:
        """Changes the saved preferences and writes them to disk."""
        with self._settings_lock:
            change(self.settings)
            self.settings.save(self.settings_path)

    def notice(self, phrase: str) -> None:
        self.post(lambda: self.overlay.on_notice(phrase))

    def _find_cli(self) -> None:
        found = claude.find_cli(self.settings.claude_path)
        log(f"claude: {found if found else 'not found'}")
        self.haiku.cli = found

    # ------------------------------------------------------------------ what the tray menu calls

    def recall(self) -> None:
        self.set_pet_visible(True)
        self.overlay.recall()

    def pet_visible(self) -> bool:
        return self.overlay.isVisible()

    def set_pet_visible(self, visible: bool) -> None:
        self.overlay.setVisible(visible)
        if visible:
            self.overlay.set_click_through(True)
        self.tray.relabel(self.haiku.lang, visible)

    def set_listen(self, on: bool) -> None:
        if on:
            self.listening.set()
        else:
            self.listening.clear()
        self.update(lambda s: setattr(s, "listen", on))

    def set_watch(self, on: bool) -> None:
        if on:
            self.watching.set()
        else:
            self.watching.clear()
        self.update(lambda s: setattr(s, "watch", on))
        if not on:
            # Make the pet forget what it saw.
            self.overlay.on_context({"kind": "window", "app": "", "title": ""})
            self.overlay.on_context({"kind": "typing", "kpm": 0})

    def set_llm(self, on: bool) -> None:
        self.update(lambda s: setattr(s, "llm", on))
        if not on:
            self.haiku.enabled = False
        elif self.haiku.cli is not None:
            self.haiku.enabled = True
        else:
            threading.Thread(target=self._find_or_ask, name="clawd-find-claude", daemon=True).start()

    def set_ask(self, on: bool) -> None:
        self.update(lambda s: setattr(s, "ask", on))

    def forget(self) -> None:
        self.memory.forget()
        self.overlay.pet.mood.affection = self.memory.affection
        self.overlay.say("forgot", True)

    def hooks_installed(self) -> bool:
        path = hooks.settings_path()
        return bool(path) and hooks.is_installed(path)

    def set_hooks(self, on: bool) -> bool:
        """Adds or removes Clawd's hooks in Claude Code's settings. Returns False on failure."""
        try:
            path = hooks.settings_path()
            if path is None:
                raise ValueError("home directory not found")
            hooks.set_installed(path, self.token if on else None)
        except (OSError, ValueError) as e:
            log(f"Claude Code hooks: {e}")
            self.notice("hooks_error")
            return False
        self.notice("hooks_on" if on else "hooks_off")
        return True

    def set_game(self, name: str, on: bool) -> None:
        self.set_pet_visible(True)
        self.overlay.set_game(name, on)

    def set_language(self, lang: str) -> None:
        self.haiku.lang = lang
        self.update(lambda s: setattr(s, "language", lang))
        self.tray.relabel(lang, self.pet_visible())
        self.overlay.set_language(lang)

    def quit(self) -> None:
        self.memory.affection = self.overlay.pet.mood.affection
        self.memory.save(force=True)
        self.tray.tray.hide()
        if self.server is not None:
            self.server.shutdown()
        QApplication.quit()

    # ------------------------------------------------------------------ finding claude

    def _adopt(self, path: Path) -> bool:
        """Uses `path` for Haiku if it really is Claude Code. Runs on a worker thread."""
        try:
            version = claude.verify(path)
        except claude.HaikuError as e:
            log(f"claude: {path} rejected: {e}")
            self.notice("claude_invalid")
            return False
        log(f"claude: using {path} ({version})")
        self.haiku.cli = path
        self.haiku.enabled = True
        self.update(lambda s: (setattr(s, "claude_path", str(path)), setattr(s, "llm", True)))
        self.post(lambda: self.tray.set_checked(self.tray.llm, True))
        return True

    def _find_or_ask(self) -> None:
        """Searches for `claude` off the UI thread; if it's nowhere to be found, asks the user."""
        found = claude.find_cli(self.settings.claude_path)
        if found is not None and self._adopt(found):
            self.notice("claude_found")
            return
        self.notice("claude_missing")
        self.post(self.pick_claude)

    def pick_claude(self) -> None:
        lang = self.haiku.lang
        home = claude.home_dir()
        title = "Где находится claude?" if lang == "ru" else "Where is claude?"
        file_filter = "claude (*.exe *.cmd);;*" if sys.platform == "win32" else ""
        picked, _ = QFileDialog.getOpenFileName(None, title, str(home or ""), file_filter)

        def work() -> None:
            if picked and self._adopt(Path(picked)):
                self.notice("claude_found")
            elif self.haiku.cli is None:
                # Still nothing to talk through: leave Haiku off.
                self.haiku.enabled = False
                self.update(lambda s: setattr(s, "llm", False))
                self.post(lambda: self.tray.set_checked(self.tray.llm, False))

        threading.Thread(target=work, name="clawd-pick-claude", daemon=True).start()


def main() -> int:
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
        # Wayland doesn't let clients position windows or read the global cursor,
        # so run through XWayland unless the user picked a backend explicitly.
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication(sys.argv)
    app.setApplicationName("Clawd")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    clawd = Clawd()  # noqa: F841 - keeps everything alive
    return app.exec()
