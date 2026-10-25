"""The "Model and API" window: pick who writes Clawd's lines — Claude Code, OpenRouter,
OpenAI or any OpenAI-compatible server — and test the connection."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from . import llm

TEXT = {
    "ru": {
        "title": "Модель и API",
        "provider": "Кто отвечает",
        "claude": "Claude Code (Haiku, без ключа)",
        "base": "Адрес API",
        "key": "API-ключ",
        "show": "показать",
        "model": "Модель",
        "test": "Проверить",
        "testing": "Проверяю…",
        "ok": "Работает. Ответ: {reply}",
        "fail": "Не вышло: {error}",
        "hint_claude": "Нужен установленный и залогиненный Claude Code. Модель: haiku, sonnet, opus или полное имя "
        "(пусто — модель Claude Code по умолчанию). Только в этом режиме Clawd умеет искать в интернете.",
        "hint_api": "Подойдёт любой сервис с OpenAI-совместимым API (/chat/completions). Ключ хранится в settings.json "
        "на этом компьютере, у локальных серверов (Ollama, LM Studio) ключ не нужен.",
        "save": "Сохранить",
        "cancel": "Отмена",
    },
    "en": {
        "title": "Model and API",
        "provider": "Who answers",
        "claude": "Claude Code (Haiku, no key needed)",
        "base": "API address",
        "key": "API key",
        "show": "show",
        "model": "Model",
        "test": "Test",
        "testing": "Testing…",
        "ok": "Works. Reply: {reply}",
        "fail": "Failed: {error}",
        "hint_claude": "Needs Claude Code installed and logged in. Model: haiku, sonnet, opus or a full name "
        "(empty: Claude Code's default). Web search in the chat works only in this mode.",
        "hint_api": "Any service with an OpenAI-compatible API (/chat/completions) works. The key is stored in "
        "settings.json on this computer; local servers (Ollama, LM Studio) need no key.",
        "save": "Save",
        "cancel": "Cancel",
    },
}


class AiDialog(QDialog):
    tested = Signal(bool, str)

    def __init__(self, lang: str, provider: str, saved: dict, test_fn) -> None:
        """`saved` maps provider -> {"base", "key", "model"} as in Settings.api; `test_fn(provider,
        base, key, model)` returns the model's reply or raises."""
        super().__init__()
        self.test_fn = test_fn
        self.t = TEXT[lang]
        self.setWindowTitle(self.t["title"])
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(460)
        self.config = {name: dict(conf) for name, conf in saved.items()}
        self.current = provider

        self.provider = QComboBox()
        self.provider.addItem(self.t["claude"], "claude")
        for name, (label, _, _) in llm.PRESETS.items():
            self.provider.addItem(label, name)
        self.base = QLineEdit()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("sk-…")
        self.show_key = QCheckBox(self.t["show"])
        self.show_key.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #666;")
        self.test = QPushButton(self.t["test"])
        self.status = QLabel()
        self.status.setWordWrap(True)

        key_row = QHBoxLayout()
        key_row.addWidget(self.key)
        key_row.addWidget(self.show_key)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test)
        test_row.addWidget(self.status, 1)
        form = QFormLayout()
        form.addRow(self.t["provider"], self.provider)
        form.addRow(self.t["base"], self.base)
        form.addRow(self.t["key"], key_row)
        form.addRow(self.t["model"], self.model)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(self.t["save"])
        buttons.button(QDialogButtonBox.Cancel).setText(self.t["cancel"])
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.hint)
        layout.addLayout(test_row)
        layout.addWidget(buttons)

        self.provider.setCurrentIndex(max(0, self.provider.findData(provider)))
        self.load_fields()
        self.provider.currentIndexChanged.connect(self.switched)
        self.test.clicked.connect(self.run_test)
        self.tested.connect(self.show_test)

    # ------------------------------------------------------------------ fields

    def defaults(self, name: str) -> dict:
        preset = llm.PRESETS.get(name)
        return {
            "base": preset[1] if preset else "",
            "key": "",
            "model": preset[2][0] if preset else "haiku",
        }

    def load_fields(self) -> None:
        name = self.provider.currentData()
        self.current = name
        api = name != "claude"
        saved = self.config.get(name, {})
        conf = {**self.defaults(name), **{k: v for k, v in saved.items() if v}}
        if name == "claude" and "model" in saved:
            conf["model"] = saved["model"]  # an empty model is a choice here: Claude Code's default
        self.base.setText(conf["base"])
        self.key.setText(conf["key"])
        self.model.clear()
        self.model.addItems(llm.PRESETS[name][2] if api else ["haiku", "sonnet", "opus"])
        self.model.setCurrentText(conf["model"])
        for widget in (self.base, self.key, self.show_key):
            widget.setEnabled(api)
        self.hint.setText(self.t["hint_api" if api else "hint_claude"])
        self.status.clear()

    def store_fields(self) -> None:
        if self.current == "claude":
            self.config["claude"] = {"model": self.model.currentText().strip()}
        else:
            self.config[self.current] = {
                "base": self.base.text().strip(),
                "key": self.key.text().strip(),
                "model": self.model.currentText().strip(),
            }

    def switched(self) -> None:
        self.store_fields()  # remember what was typed for the provider we are leaving
        self.load_fields()

    def accept(self) -> None:
        self.store_fields()
        super().accept()

    def result_config(self) -> tuple[str, dict]:
        return self.current, self.config

    # ------------------------------------------------------------------ testing

    def run_test(self) -> None:
        name = self.provider.currentData()
        base, key, model = self.base.text(), self.key.text(), self.model.currentText()
        self.status.setStyleSheet("")
        self.status.setText(self.t["testing"])
        self.test.setEnabled(False)

        def work() -> None:
            try:
                reply = self.test_fn(name, base, key, model)
                self.tested.emit(True, " ".join(reply.split())[:80])
            except Exception as e:  # never leave the button disabled
                self.tested.emit(False, str(e) or repr(e))

        threading.Thread(target=work, name="clawd-api-test", daemon=True).start()

    def show_test(self, ok: bool, text: str) -> None:
        self.test.setEnabled(True)
        self.status.setStyleSheet("color: #2e9e5b;" if ok else "color: #c0392b;")
        self.status.setText(self.t["ok" if ok else "fail"].format(reply=text, error=text))
