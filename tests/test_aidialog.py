import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from clawd.aidialog import AiDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_dialog_remembers_each_providers_fields(app):
    dialog = AiDialog("en", "claude", {"claude": {"model": "sonnet"}}, lambda *a: "ok")
    assert dialog.model.currentText() == "sonnet" and not dialog.base.isEnabled() and dialog.test.isEnabled()
    dialog.provider.setCurrentIndex(dialog.provider.findData("openrouter"))
    assert dialog.base.text() == "https://openrouter.ai/api/v1" and dialog.base.isEnabled()
    dialog.key.setText("sk-or-1")
    dialog.model.setCurrentText("x/y")
    dialog.provider.setCurrentIndex(dialog.provider.findData("openai"))
    assert dialog.key.text() == "" and dialog.model.currentText() == "gpt-4o-mini"
    dialog.provider.setCurrentIndex(dialog.provider.findData("openrouter"))
    assert dialog.key.text() == "sk-or-1" and dialog.model.currentText() == "x/y"
    dialog.accept()
    provider, config = dialog.result_config()
    assert provider == "openrouter" and config["openrouter"]["key"] == "sk-or-1" and config["claude"]["model"] == "sonnet"
