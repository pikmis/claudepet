"""The tray menu: switches, language and finding `claude` for Haiku lines."""
from __future__ import annotations

from PySide6.QtGui import QAction, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .sprite import GRID_W, Frame, render_pet

TEXT = {
    "recall": ("Позвать Clawd", "Call Clawd"),
    "hide": ("Спрятать", "Hide"),
    "show": ("Показать", "Show"),
    "listen": ("Слушать звук", "Listen to sound"),
    "watch": ("Видеть окна и печать", "See windows and typing"),
    "llm": ("Реплики через Claude (Haiku)", "Lines from Claude (Haiku)"),
    "pick": ("Указать путь к claude…", "Locate claude…"),
    "hooks": ("Связать с Claude Code", "Connect to Claude Code"),
    "ask": ("Разрешения из пузыря", "Permissions from the bubble"),
    "forget": ("Забыть всё обо мне", "Forget everything about me"),
    "games": ("Игры", "Games"),
    "ball": ("Мячик", "Ball"),
    "tag": ("Догонялки", "Tag"),
    "quit": ("Выход", "Quit"),
}


def text(lang: str, key: str) -> str:
    return TEXT[key][0 if lang == "ru" else 1]


def app_icon() -> QIcon:
    """Clawd's idle frame on a square canvas."""
    cell = 8
    side = GRID_W * cell
    pixmap = QPixmap(side, side)
    pixmap.fill(0)
    painter = QPainter(pixmap)
    painter.drawPixmap(0, 2 * cell, render_pet(Frame(), cell))
    painter.end()
    return QIcon(pixmap)


class TrayMenu:
    def __init__(self, controller) -> None:
        """`controller` provides: settings, lang, recall(), set_pet_visible(on), pet_visible(),
        set_listen(on), set_watch(on), set_llm(on), pick_claude(), set_hooks(on) -> bool,
        set_language(lang) and quit()."""
        self.controller = controller
        settings = controller.settings
        lang = settings.language
        self.menu = QMenu()

        def action(key: str, checked: bool | None = None, slot=None) -> QAction:
            act = QAction(text(lang, key), self.menu)
            if checked is not None:
                act.setCheckable(True)
                act.setChecked(checked)
                if slot:
                    act.toggled.connect(slot)
            elif slot:
                act.triggered.connect(slot)
            self.menu.addAction(act)
            return act

        self.recall = action("recall", slot=lambda: controller.recall())
        self.toggle = action("hide", slot=self._toggle)
        self.menu.addSeparator()
        self.listen = action("listen", settings.listen, lambda on: controller.set_listen(on))
        self.watch = action("watch", settings.watch, lambda on: controller.set_watch(on))
        self.llm = action("llm", settings.llm, lambda on: controller.set_llm(on))
        self.ask = action("ask", settings.ask, lambda on: controller.set_ask(on))
        self.forget = action("forget", slot=lambda: controller.forget())
        self.pick = action("pick", slot=lambda: controller.pick_claude())
        self.link = action("hooks", controller.hooks_installed(), self._link)

        self.language = self.menu.addMenu("Язык / Language")
        self.ru = QAction("Русский", self.language)
        self.en = QAction("English", self.language)
        for act, code in ((self.ru, "ru"), (self.en, "en")):
            act.setCheckable(True)
            act.setChecked(lang == code)
            act.triggered.connect(lambda _=False, code=code: controller.set_language(code))
            self.language.addAction(act)
        self.games = self.menu.addMenu(text(lang, "games"))
        self.ball = QAction(text(lang, "ball"), self.games)
        self.tag = QAction(text(lang, "tag"), self.games)
        for act, name in ((self.ball, "ball"), (self.tag, "tag")):
            act.setCheckable(True)
            act.toggled.connect(lambda on, name=name: controller.set_game(name, on))
            self.games.addAction(act)
        self.menu.addSeparator()
        self.quit = action("quit", slot=lambda: controller.quit())

        self.tray = QSystemTrayIcon(app_icon())
        self.tray.setToolTip("Clawd")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()

    def _activated(self, reason) -> None:
        # Right click already opens the context menu; open it on left click too.
        if reason == QSystemTrayIcon.Trigger:
            self.menu.popup(QCursor.pos())

    def _toggle(self) -> None:
        self.controller.set_pet_visible(not self.controller.pet_visible())

    def _link(self, on: bool) -> None:
        if not self.controller.set_hooks(on):
            self.set_checked(self.link, not on)

    @staticmethod
    def set_checked(act: QAction, on: bool) -> None:
        """Changes a check mark without triggering the action's handler."""
        act.blockSignals(True)
        act.setChecked(on)
        act.blockSignals(False)

    def relabel(self, lang: str, visible: bool) -> None:
        for act, key in (
            (self.recall, "recall"),
            (self.toggle, "hide" if visible else "show"),
            (self.listen, "listen"),
            (self.watch, "watch"),
            (self.llm, "llm"),
            (self.ask, "ask"),
            (self.forget, "forget"),
            (self.pick, "pick"),
            (self.link, "hooks"),
            (self.quit, "quit"),
        ):
            act.setText(text(lang, key))
        self.games.setTitle(text(lang, "games"))
        self.ball.setText(text(lang, "ball"))
        self.tag.setText(text(lang, "tag"))
        self.set_checked(self.ru, lang == "ru")
        self.set_checked(self.en, lang == "en")
