"""Talking to Clawd: a text box above him, answers from Haiku (which may search the web),
and a confirmation panel for the one shell command he may offer to run."""
from __future__ import annotations

import re
import sys
import threading
import time

from PySide6.QtCore import QObject, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from . import claude, shell

HISTORY_TURNS = 16
BOX_WIDTH = 270
GAP = 4

UI = {
    "ru": {
        "placeholder": "Напиши Clawd… (Esc — закрыть)",
        "run": "Выполнить",
        "cancel": "Отмена",
        "danger": "⚠ Осторожно: команда может что-то удалить или остановить",
        "running": "Выполняю…",
        "declined": "Ладно, не буду",
    },
    "en": {
        "placeholder": "Message Clawd… (Esc to close)",
        "run": "Run",
        "cancel": "Cancel",
        "danger": "⚠ Careful: this may delete or stop something",
        "running": "Running…",
        "declined": "Okay, I won't",
    },
}

# Commands that delete, kill, power off or rewrite disks get a red warning.
DANGER = re.compile(
    r"\b(rm|rmdir|del|erase|kill|killall|pkill|taskkill|stop-process|remove-item|shutdown|reboot|poweroff|halt"
    r"|mkfs|format|dd|chmod|chown|truncate|shred|wipefs|diskpart|reg\s+delete)\b|>\s*/dev/|:\(\)\s*\{",
    re.IGNORECASE,
)


def is_dangerous(command: str) -> bool:
    return DANGER.search(command) is not None


def force_foreground(widget: QWidget) -> None:
    """Clawd's overlay never takes focus, so Windows may refuse to hand it to the box;
    a tap on Alt lets the request through."""
    widget.show()
    widget.raise_()
    widget.activateWindow()
    if sys.platform == "win32":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            user32.keybd_event(0x12, 0, 0, 0)  # Alt down
            user32.SetForegroundWindow(ctypes.c_void_p(int(widget.winId())))
            user32.keybd_event(0x12, 0, 2, 0)  # Alt up
        except Exception:
            pass


class Panel(QWidget):
    """A frameless, rounded, always-on-top window in the style of Clawd's speech bubble."""

    def __init__(self, border: str = "#0000001f") -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.border = QColor(border)
        self.setWindowTitle("Clawd chat")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(Qt.NoPen)
        for grow, alpha in ((2, 12), (1, 18)):  # a soft drop shadow
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(rect.translated(0, 1).adjusted(-grow, -grow, grow, grow), 12 + grow, 12 + grow)
        painter.setPen(QPen(self.border, 1.5))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect, 12, 12)


class InputBox(Panel):
    def __init__(self) -> None:
        super().__init__()
        self.edit = QLineEdit(self)
        self.edit.setMaxLength(300)
        self.edit.setStyleSheet(
            "QLineEdit { background: transparent; border: none; color: #1a1a1a; selection-background-color: #d77757; }"
        )
        font = QFont()
        font.setFamilies(["Segoe UI", "Noto Sans", "Sans Serif"])
        font.setPixelSize(13)
        font.setWeight(QFont.DemiBold)
        self.edit.setFont(font)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.addWidget(self.edit)
        self.setFixedWidth(BOX_WIDTH)
        self.adjustSize()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.edit.clear()
            self.edit.returnPressed.emit()
        else:
            super().keyPressEvent(event)


class ConfirmBox(Panel):
    def __init__(self) -> None:
        super().__init__()
        self.on_cancel = None
        self.warning = QLabel(self)
        self.warning.setStyleSheet("color: #c0392b; font-weight: 600;")
        self.warning.setWordWrap(True)
        self.command = QLabel(self)
        self.command.setWordWrap(True)
        self.command.setTextInteractionFlags(Qt.NoTextInteraction)
        self.command.setStyleSheet(
            "background: #f3f1ec; border-radius: 6px; padding: 6px 8px; color: #1a1a1a;"
            "font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 12px;"
        )
        self.run = QPushButton(self)
        self.cancel = QPushButton(self)
        self.run.setStyleSheet(
            "QPushButton { background: #2e9e5b; color: white; border-radius: 6px; padding: 5px 14px; font-weight: 600; }"
            "QPushButton:hover { background: #38b36a; }"
        )
        self.cancel.setStyleSheet(
            "QPushButton { background: #8a8f98; color: white; border-radius: 6px; padding: 5px 14px; font-weight: 600; }"
            "QPushButton:hover { background: #9aa0a9; }"
            "QPushButton:focus { border: 2px solid #1a1a1a; }"
        )
        self.run.setMinimumWidth(110)
        self.cancel.setMinimumWidth(90)
        self.run.setAutoDefault(False)
        self.cancel.setAutoDefault(True)
        self.cancel.setDefault(True)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.run)
        row.addWidget(self.cancel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addWidget(self.warning)
        layout.addWidget(self.command)
        layout.addLayout(row)
        self.setFixedWidth(BOX_WIDTH + 70)
        self.setStyleSheet("QLabel, QPushButton { font-size: 13px; }")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.on_cancel:
            self.on_cancel()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.cancel.hasFocus():
            self.cancel.click()  # Cancel is the default: a stray Enter must never run anything
        else:
            super().keyPressEvent(event)

    def show_command(self, command: str, labels: dict) -> None:
        danger = is_dangerous(command)
        self.border = QColor("#d64545" if danger else "#0000001f")
        self.warning.setText(labels["danger"])
        self.warning.setVisible(danger)
        self.command.setText(command)
        self.run.setText(labels["run"])
        self.cancel.setText(labels["cancel"])
        self.adjustSize()


class Chat(QObject):
    def __init__(self, overlay, haiku, post) -> None:
        super().__init__()
        self.overlay = overlay
        self.haiku = haiku
        self.post = post
        self.history: list[list[str]] = []
        self.pending: str | None = None  # a command waiting for the user's yes or no
        self.is_open = False
        self.input = InputBox()
        self.confirm = ConfirmBox()
        self.input.edit.returnPressed.connect(self.submit)
        self.confirm.run.clicked.connect(self.run_pending)
        self.confirm.cancel.clicked.connect(self.cancel_pending)
        self.confirm.on_cancel = self.cancel_pending
        QGuiApplication.instance().focusWindowChanged.connect(self.focus_changed)

    @property
    def labels(self) -> dict:
        return UI[self.overlay.lang]

    # ------------------------------------------------------------------ opening and closing

    def open(self) -> None:
        if not self.is_open:
            self.is_open = True
            self.input.edit.setPlaceholderText(self.labels["placeholder"])
            self.input.edit.clear()
            self.input.edit.setEnabled(True)
            self.overlay.pet.start_chat(time.monotonic())
            self.place()
        force_foreground(self.input)
        self.input.edit.setFocus()

    def close(self) -> None:
        if not self.is_open:
            return
        if self.pending:
            self.cancel_pending(speak=False)
        self.is_open = False
        self.input.hide()
        self.confirm.hide()
        self.overlay.bubble_lift = 0
        self.overlay.pet.end_chat()

    def focus_changed(self, window) -> None:
        """Clicking into another app takes focus away: put the boxes away too."""
        mine = (self.input.windowHandle(), self.confirm.windowHandle())
        if self.is_open and window not in mine and self.input.edit.isEnabled():
            self.close()

    # ------------------------------------------------------------------ layout

    def place(self) -> None:
        """Keeps the boxes above Clawd's head."""
        if not self.is_open:
            return
        overlay, pet = self.overlay, self.overlay.pet
        origin = overlay.geometry().topLeft()
        screen = overlay.geometry()
        cx = origin.x() + pet.x + pet.w / 2
        top = origin.y() + pet.y + pet.hop_off + pet.unit

        def put(widget: QWidget, bottom: float) -> float:
            x = min(max(cx - widget.width() / 2, screen.left() + 4), screen.right() - widget.width() - 4)
            y = max(bottom - widget.height(), screen.top() + 4)
            widget.move(QPoint(int(x), int(y)))
            return y

        y = put(self.input, top - GAP)
        lift = top - y + GAP
        if self.pending:
            y = put(self.confirm, y - GAP)
            lift = top - y + GAP
        overlay.bubble_lift = lift

    # ------------------------------------------------------------------ asking

    def submit(self) -> None:
        message = self.input.edit.text().strip()
        if not message:
            self.close()
            return
        if not self.input.edit.isEnabled() or self.pending:
            return
        self.input.edit.clear()
        self.ask(message)

    def ask(self, message: str) -> None:
        """Sends a message to Haiku; the answer is spoken and may offer a command to confirm."""
        overlay = self.overlay
        self.input.edit.setEnabled(False)
        overlay.pet.awaiting_reply = True
        overlay.say_text("…", True)
        context = overlay.brain.describe(overlay.pet)
        history = [list(turn) for turn in self.history]

        def work() -> None:
            error = ""
            try:
                out = self.haiku.run_web(lambda lang: claude.chat_prompt(lang, context, history, message))
                result = claude.parse_chat(out, 220)
                if result is None:
                    error = "empty answer"
            except Exception as e:
                print(f"chat: {e}", file=sys.stderr, flush=True)
                result, error = None, str(e)
            self.post(lambda: self.answered(message, result, error))

        threading.Thread(target=work, name="clawd-chat", daemon=True).start()

    def answered(self, message: str, result, error: str = "") -> None:
        overlay = self.overlay
        overlay.pet.awaiting_reply = False
        self.input.edit.setEnabled(True)
        if result is None:
            overlay.say_error("chat_error", error)
        else:
            note = f"{result.text} [offered to run: {result.command}]" if result.command else result.text
            self.history = (self.history + [["user", message], ["clawd", note]])[-HISTORY_TURNS:]
            if result.text:
                overlay.say_text(result.text, True)
            overlay.pet.hop(260)
            if result.command:
                self.show_confirm(result.command)
        if self.is_open and not self.pending:
            force_foreground(self.input)
            self.input.edit.setFocus()

    # ------------------------------------------------------------------ the command

    def show_confirm(self, command: str) -> None:
        self.pending = command
        self.confirm.show_command(command, self.labels)
        self.place()
        force_foreground(self.confirm)
        self.confirm.cancel.setFocus()  # Cancel is the default

    def hide_confirm(self) -> None:
        self.pending = None
        self.confirm.hide()
        self.overlay.bubble_lift = 0
        self.place()

    def cancel_pending(self, speak: bool = True) -> None:
        if not self.pending:
            return
        self.history = (self.history + [["user", f"(declined to run: {self.pending})"]])[-HISTORY_TURNS:]
        self.hide_confirm()
        if speak:
            self.overlay.say_text(self.labels["declined"], True)
            if self.is_open:
                force_foreground(self.input)
                self.input.edit.setFocus()

    def run_pending(self) -> None:
        command = self.pending
        if not command:
            return
        self.hide_confirm()
        overlay = self.overlay
        self.input.edit.setEnabled(False)
        overlay.pet.awaiting_reply = True
        overlay.say_text(self.labels["running"], True)

        def work() -> None:
            try:
                result = shell.run(command)
            except Exception as e:
                self.post(lambda: self.run_failed(str(e)))
                return
            status = "it timed out after 30 s" if result.timed_out else f"exit code {result.code}"
            output = result.output or "(no output)"
            followup = (
                f"(system) The user confirmed and the command `{command}` ran: {status}. Output:\n{output}\n"
                "Tell the user briefly what happened. Offer another command only if it's the natural next step."
            )
            self.post(lambda: self.ask(followup))

        threading.Thread(target=work, name="clawd-shell", daemon=True).start()

    def run_failed(self, error: str) -> None:
        self.overlay.pet.awaiting_reply = False
        self.input.edit.setEnabled(True)
        self.overlay.say_text(error, True)
