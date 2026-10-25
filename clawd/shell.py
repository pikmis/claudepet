"""Runs a shell command the user confirmed in Clawd's chat, with a time limit."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 30
MAX_OUTPUT = 4000


@dataclass
class CommandResult:
    code: int | None  # None when the command was killed by the timeout
    output: str
    timed_out: bool


def shell_name() -> str:
    """The shell `run` uses, so Haiku writes commands for the right one."""
    return "Windows PowerShell" if sys.platform == "win32" else "sh on Linux"


def tail(text: str, limit: int) -> str:
    """Keeps the end of long output: that's where results and errors usually are."""
    return text if len(text) <= limit else "…" + text[-limit:]


def _decode(data: bytes) -> str:
    if sys.platform == "win32":
        import ctypes

        # PowerShell writes to a pipe in the console's OEM code page (cp866 on Russian systems).
        encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("utf-8", errors="replace")


def run(command: str) -> CommandResult:
    if sys.platform == "win32":
        args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        extra = {"creationflags": 0x0800_0000}  # CREATE_NO_WINDOW
    else:
        args = ["sh", "-c", command]
        extra = {}
    try:
        done = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=Path.home(),
            timeout=TIMEOUT,
            **extra,
        )
    except subprocess.TimeoutExpired as e:
        partial = _decode((e.stdout or b"") + (e.stderr or b""))
        return CommandResult(None, tail(partial.rstrip(), MAX_OUTPUT), True)
    except OSError as e:
        raise RuntimeError(str(e)) from e
    output = _decode(done.stdout)
    err = _decode(done.stderr)
    if err.strip():
        if output and not output.endswith("\n"):
            output += "\n"
        output += err
    return CommandResult(done.returncode, tail(output.rstrip(), MAX_OUTPUT), False)
