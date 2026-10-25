import sys

import pytest

from clawd import claude, shell
from clawd.chat import is_dangerous
from tests.test_pet import make_pet, run


def test_chat_reply_is_one_clean_line():
    assert claude.reply("Clawd: «Привет!\n  Как дела?»", 200) == "Привет! Как дела?"
    assert claude.reply("   ", 200) is None
    assert claude.reply("a" * 10, 4) == "aaaa…"


def test_chat_output_splits_into_reply_and_command():
    out = "Сейчас посмотрю, кто ест память.\nCOMMAND: ps -eo pid,comm,%mem --sort=-%mem | head -5\n"
    assert claude.parse_chat(out, 200) == claude.ChatReply(
        "Сейчас посмотрю, кто ест память.", "ps -eo pid,comm,%mem --sort=-%mem | head -5"
    )
    plain = claude.parse_chat("**Rust 1.99** вышел, см. [блог](https://blog.rust-lang.org)", 200)
    assert plain.text == "Rust 1.99 вышел, см. блог" and plain.command is None
    assert claude.parse_chat("  \n", 200) is None
    assert claude.parse_chat("`COMMAND: dir`", 200).command == "dir"


def test_chat_prompt_keeps_recent_turns():
    history = [["user", f"m{i}"] for i in range(12)]
    prompt = claude.chat_prompt("en", "- ctx", history, "hi")
    assert "User: m3\n" not in prompt and "User: m4\n" in prompt and "User: hi" in prompt
    assert "COMMAND:" in prompt and shell.shell_name() in prompt


@pytest.mark.parametrize(
    "command,danger",
    [
        ("Get-Process | Sort-Object WS -Descending | Select -First 5", False),
        ("df -h", False),
        ("kill -9 1234", True),
        ("Stop-Process -Id 42", True),
        ("rm -rf ~/old", True),
        ("echo hi > /dev/sda", True),
        ("Remove-Item C:/tmp/x", True),
    ],
)
def test_dangerous_commands_are_flagged(command, danger):
    assert is_dangerous(command) is danger


def test_runs_commands_and_captures_output():
    ok = shell.run("echo hello")
    assert ok.code == 0 and ok.output.strip() == "hello" and not ok.timed_out
    failed = shell.run("exit 3")
    assert failed.code == 3 and not failed.timed_out
    assert shell.tail("x" * 10 + "END", 5) == "…xxEND" and shell.tail("short", 5) == "short"
    if sys.platform == "win32":
        assert "привет" in shell.run("Write-Output 'привет'").output


def test_chatting_pauses_wandering_and_shows_the_spinner_while_waiting():
    pet, fx = make_pet()
    import time

    now = run(pet, 1, time.monotonic())
    pet.start_chat(now)
    assert pet.chatting and "chat_hello" in fx.said
    x = pet.x
    now = run(pet, 20, now)
    assert pet.x == x and pet.state == "idle"
    pet.awaiting_reply = True
    frame = pet.frame(now)
    assert frame.eyes == "up" and frame.spinner >= 0
    pet.end_chat()
    assert not pet.chatting and not pet.awaiting_reply


def test_idle_chatter_comes_regularly():
    pet, fx = make_pet()
    import time

    now = run(pet, 1, time.monotonic())
    pet.last_interaction = now - 30
    fx.said.clear()
    run(pet, 90, now, dt=0.1)
    assert fx.said.count("idle") >= 2
