import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from clawd import hooks
from clawd.hooks import (
    first_sentence,
    is_installed,
    last_assistant_text,
    parse,
    set_installed,
    with_hooks,
)


def test_install_keeps_user_settings_and_uninstall_restores_them():
    user = {
        "model": "opus",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}]},
    }
    installed = with_hooks(json.loads(json.dumps(user)), "abc")
    assert installed["model"] == "opus"
    assert len(installed["hooks"]["Stop"]) == 2
    assert installed["hooks"]["PreToolUse"][0]["matcher"] == "*"
    # Reinstalling replaces rather than duplicates.
    again = with_hooks(installed, "def")
    assert len(again["hooks"]["Stop"]) == 2
    text = json.dumps(again)
    assert "/hook/def" in text and "/hook/abc" not in text
    assert with_hooks(again, None) == user
    assert with_hooks({}, None) == {}


def test_refuses_to_touch_broken_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        set_installed(path, "abc")
    assert path.read_text(encoding="utf-8") == "{ not json"
    assert not (tmp_path / "settings.json.clawd-backup").exists()


def test_install_writes_backup_and_detects_itself(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"theme":"dark"}', encoding="utf-8")
    set_installed(path, "tok123")
    assert is_installed(path)
    assert (tmp_path / "settings.json.clawd-backup").read_text(encoding="utf-8") == '{"theme":"dark"}'
    set_installed(path, None)
    assert not is_installed(path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"theme": "dark"}


def test_install_creates_missing_settings(tmp_path):
    path = tmp_path / "new" / "settings.json"
    set_installed(path, "tok")
    assert is_installed(path)


def test_parses_hook_events():
    internal = Path("/home/u/.local/share/clawd/haiku")
    tool = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": "/home/u/proj",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/home/u/proj/src/main.rs"},
    }
    assert parse(tool, internal)["event"] == {
        "kind": "tool",
        "session": "s1",
        "project": "proj",
        "tool": "Edit",
        "detail": "main.rs",
    }
    own = {"hook_event_name": "Stop", "cwd": "/home/u/.local/share/clawd/haiku"}
    assert parse(own, internal) is None
    bash = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cargo   test\n --all"}}
    assert parse(bash, internal)["event"]["detail"] == "cargo test --all"
    assert parse({"hook_event_name": "Whatever"}, internal) is None
    assert parse([], internal) is None


def test_reads_last_assistant_text_from_transcript(tmp_path):
    path = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "fix it"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Сначала посмотрю тесты."}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
    ]
    path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8")
    assert last_assistant_text(path) == "Сначала посмотрю тесты."
    assert last_assistant_text(tmp_path / "missing.jsonl") is None


def test_first_sentence_is_short_and_plain():
    assert first_sentence("**Готово!** Я починил тесты. Ещё...", 60) == "Готово! Я починил тесты."
    assert first_sentence("## Итог: всё работает", 60) == "Итог: всё работает"
    assert first_sentence("Исправил баг в `main.rs`. Проверь.", 60) == "Исправил баг в main.rs."


def test_endpoint_accepts_only_the_right_token(monkeypatch):
    received = []
    done = threading.Event()

    class FakeHub:
        def handle(self, body):
            received.append(body)
            done.set()

    monkeypatch.setattr(hooks, "PORT", 0)
    server = hooks.serve("secret", FakeHub())
    port = server.server_address[1]
    try:
        def post(path):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b'{"a":1}', method="POST")
            return urllib.request.urlopen(req, timeout=5).status

        with pytest.raises(urllib.error.HTTPError) as err:
            post("/hook/wrong")
        assert err.value.code == 404
        assert post("/hook/secret") == 204
        assert done.wait(5)
        assert received == [{"a": 1}]
    finally:
        server.shutdown()


def _hub(tmp_path, enabled=True):
    from clawd.claude import Haiku

    events = []
    hub = hooks.Hub(Haiku(tmp_path / "haiku", "en", False), events.append, permissions_enabled=lambda: enabled)
    return hub, events


def _ask(hub, **fields):
    body = {"hook_event_name": "PermissionRequest", "session_id": "s", "cwd": "/home/u/proj", "tool_name": "Bash",
            "tool_input": {"command": "npm test"}, **fields}
    result = {}
    thread = threading.Thread(target=lambda: result.update(out=hub.permission(body)))
    thread.start()
    return thread, result


def test_permission_waits_for_the_answer_and_returns_a_decision(tmp_path):
    hub, events = _hub(tmp_path)
    thread, result = _ask(hub)
    while not events:
        thread.join(0.01)
    asked = events[0]
    assert asked["kind"] == "permission" and asked["tool"] == "Bash" and asked["detail"] == "npm test"
    hub.resolve(asked["id"], "allow")
    thread.join(5)
    decision = json.loads(result["out"])["hookSpecificOutput"]
    assert decision["hookEventName"] == "PermissionRequest" and decision["decision"] == {"behavior": "allow"}


def test_permission_deny_and_terminal(tmp_path):
    hub, events = _hub(tmp_path)
    for answer, expected in (("deny", "deny"), (None, "")):
        events.clear()
        thread, result = _ask(hub)
        while not events:
            thread.join(0.01)
        hub.resolve(events[0]["id"], answer)
        thread.join(5)
        assert (json.loads(result["out"])["hookSpecificOutput"]["decision"]["behavior"] if expected else result["out"]) == expected


def test_permission_times_out_to_the_normal_dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, "PERMISSION_WAIT", 0.05)
    hub, events = _hub(tmp_path)
    thread, result = _ask(hub)
    thread.join(5)
    assert result["out"] == ""
    assert [e["kind"] for e in events] == ["permission", "permission_done"]


def test_permission_ignores_questions_plans_and_disabled(tmp_path):
    hub, events = _hub(tmp_path)
    assert hub.permission({"tool_name": "AskUserQuestion", "cwd": "/x"}) == ""
    assert hub.permission({"tool_name": "ExitPlanMode", "cwd": "/x"}) == ""
    assert events == []
    off, off_events = _hub(tmp_path, enabled=False)
    assert off.permission({"tool_name": "Bash", "cwd": "/x"}) == "" and off_events == []


def test_old_links_without_permissions_count_as_not_installed(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"x": "curl http://127.0.0.1:47823/hook/abc"}', encoding="utf-8")
    assert not is_installed(path)
