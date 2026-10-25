"""Asks Claude Haiku for Clawd's lines through the Claude Code CLI in headless
mode, so it runs on the user's own Claude Code login and needs no API key."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .settings import LANGUAGE_NAMES

TIMEOUT = 90
VERSION_TIMEOUT = 20
WINDOWS = sys.platform == "win32"


class HaikuError(Exception):
    pass


class Haiku:
    """Haiku as the rest of the app sees it: where `claude` lives, which language
    to speak and whether it's switched on. All of these can change at runtime."""

    def __init__(self, workdir: Path, lang: str, enabled: bool) -> None:
        self.workdir = workdir
        self.cli: Path | None = None
        self.lang = lang
        self.enabled = enabled

    def run(self, prompt) -> str:
        """Runs a prompt written for the current language: `prompt(lang) -> str`."""
        if not self.enabled:
            raise HaikuError("disabled")
        cli = self.cli
        if cli is None:
            raise HaikuError("claude CLI not found")
        lang = self.lang
        return ask(cli, self.workdir, lang, prompt(lang))


def lang_name(lang: str) -> str:
    return LANGUAGE_NAMES.get(lang, "English")


def persona(lang: str) -> str:
    return (
        "You are Clawd, a tiny orange pixel-art pet and the mascot of Claude Code. You live on the user's "
        "desktop: you stroll along the taskbar, read, code, dance to music and cheer the user on. You speak "
        f"{lang_name(lang)}, in the first person, very briefly, warmly and with light geeky humour. Reply with "
        "the lines only: no quotes, emoji, numbering, markdown or explanations."
    )


def exe_names() -> list[str]:
    return ["claude.exe", "claude.cmd"] if WINDOWS else ["claude"]


def home_dir() -> Path | None:
    value = os.environ.get("USERPROFILE" if WINDOWS else "HOME")
    return Path(value) if value else None


def subdirs(directory: Path, *suffix: str) -> list[Path]:
    """Subfolders of `directory` (e.g. one per installed Node version), newest name first."""
    try:
        found = [directory.joinpath(e.name, *suffix) for e in os.scandir(directory)]
    except OSError:
        return []
    return sorted(found, reverse=True)


def install_dirs() -> list[Path]:
    """Where installers put `claude` besides PATH. A desktop launcher often starts
    apps without the PATH set up in ~/.bashrc or ~/.zshrc (nvm, volta, ...)."""
    home = home_dir()
    if home is None:
        return []
    dirs = [home / ".local" / "bin", home / ".claude" / "local"]
    if WINDOWS:

        def var(name: str) -> Path | None:
            value = os.environ.get(name)
            return Path(value) if value else None

        if appdata := var("APPDATA"):
            dirs.append(appdata / "npm")
            dirs.extend(subdirs(appdata / "nvm"))
            # Claude Desktop keeps its own copy of Claude Code.
            dirs.extend(subdirs(appdata / "Claude" / "claude-code"))
        if local := var("LOCALAPPDATA"):
            dirs.append(local / "pnpm")
            dirs.append(local / "Volta" / "bin")
        dirs.append(home / "scoop" / "shims")
        if program_files := var("ProgramFiles"):
            dirs.append(program_files / "nodejs")
    else:
        for rel in (
            ".npm-global/bin",
            ".volta/bin",
            ".bun/bin",
            ".local/share/pnpm",
            ".yarn/bin",
            ".asdf/shims",
            ".local/share/mise/shims",
        ):
            dirs.append(home / rel)
        dirs.extend(subdirs(home / ".nvm" / "versions" / "node", "bin"))
        dirs.extend(subdirs(home / ".local" / "share" / "fnm" / "node-versions", "installation", "bin"))
        dirs.extend(Path(d) for d in ("/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/snap/bin"))
    return dirs


def hide_console() -> dict:
    """Keyword arguments for subprocess that keep a console window from flashing up."""
    return {"creationflags": 0x0800_0000} if WINDOWS else {}


def run_with_timeout(args: list, timeout: float) -> str:
    """Runs a short command and returns its stdout, giving up after `timeout`."""
    try:
        done = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            **hide_console(),
        )
    except subprocess.TimeoutExpired:
        raise HaikuError("timed out") from None
    except OSError as e:
        raise HaikuError(str(e)) from e
    if done.returncode != 0:
        raise HaikuError(f"exited with {done.returncode}")
    return done.stdout.decode("utf-8", errors="replace")


def find_in(dirs: list[Path]) -> Path | None:
    for directory in dirs:
        for name in exe_names():
            path = directory / name
            if path.is_file():
                return path
    return None


def shell_lookup() -> Path | None:
    """PATH as the user's shell sees it, which may differ from the one Clawd was started with."""
    if WINDOWS:
        # The registry has the PATH from user and system settings, even if it changed after login.
        import winreg

        dirs: list[Path] = []
        for root, key in (
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        ):
            try:
                with winreg.OpenKey(root, key) as handle:
                    value, _ = winreg.QueryValueEx(handle, "Path")
            except OSError:
                continue
            dirs.extend(Path(os.path.expandvars(d)) for d in str(value).split(";") if d.strip())
        return find_in(dirs)
    shell = os.environ.get("SHELL", "/bin/sh")
    try:
        out = run_with_timeout([shell, "-ilc", "command -v claude"], VERSION_TIMEOUT)
    except HaikuError:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("/") and Path(line).is_file():
            return Path(line)
    return None


def find_cli(saved: str | Path | None = None) -> Path | None:
    """Locates the `claude` executable: a path the user picked, $CLAWD_CLAUDE_PATH,
    PATH, the usual install folders, and finally the user's login shell."""
    if saved and Path(saved).is_file():
        return Path(saved)
    env = os.environ.get("CLAWD_CLAUDE_PATH")
    if env and Path(env).is_file():
        return Path(env)
    dirs = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    dirs.extend(install_dirs())
    return find_in(dirs) or shell_lookup()


def verify(path: Path) -> str:
    """Checks that `path` really is Claude Code; returns its version line."""
    out = run_with_timeout([str(path), "--version"], VERSION_TIMEOUT)
    line = (out.splitlines() or [""])[0].strip()
    if "Claude Code" in line or line[:1].isdigit():
        return line
    raise HaikuError(f"unexpected --version output: {line}")


def ask(cli: Path, workdir: Path, lang: str, prompt: str) -> str:
    """Runs one headless Haiku turn with no tools, MCP servers, skills or hooks.
    `workdir` is a private folder, so no project CLAUDE.md gets loaded."""
    args = [
        str(cli),
        "-p",
        "--model",
        "haiku",
        "--output-format",
        "text",
        "--no-session-persistence",
        "--tools",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--settings",
        '{"disableAllHooks":true}',
        "--system-prompt",
        persona(lang),
    ]
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            args,
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workdir,
            env={**os.environ, "CLAWD_INTERNAL": "1"},
            timeout=TIMEOUT,
            **hide_console(),
        )
    except subprocess.TimeoutExpired:
        raise HaikuError("claude timed out") from None
    except OSError as e:
        raise HaikuError(f"cannot start claude: {e}") from e
    out = done.stdout.decode("utf-8", errors="replace")
    if done.returncode != 0:
        err = done.stderr.decode("utf-8", errors="replace").strip()
        raise HaikuError(f"claude failed: {err or out.strip()}")
    return out


def strip_list_marker(line: str) -> str:
    """Drops a leading "- ", "• " or "1. " / "2) " list marker."""
    bullet = line.lstrip("-*•–—")
    if len(bullet) != len(line):
        return bullet.lstrip()
    digits = len(line) - len(line.lstrip("0123456789"))
    if digits and line[digits : digits + 1] in (".", ")"):
        return line[digits + 1 :].lstrip()
    return line


def phrases(output: str, max_chars: int) -> list[str]:
    """Turns model output into clean one-line phrases."""
    seen: list[str] = []
    for raw in output.splitlines():
        line = strip_list_marker(raw.strip()).strip("\"«»“”`'").strip()
        if 2 <= len(line) <= max_chars and line not in seen:
            seen.append(line)
    return seen


def idle_prompt(lang: str, context: str, count: int) -> str:
    return (
        f"What is going on right now:\n{context}\n\nWrite {count} different short lines (up to 40 characters) "
        f"that Clawd could say right now, in {lang_name(lang)}. Teasing the situation a little is welcome. "
        "One per line."
    )


def thought_prompt(lang: str, task_text: str) -> str:
    return (
        f"Claude is working on the user's task and writes:\n\n{task_text}\n\nRetell it as one short line "
        f"(up to 50 characters) in {lang_name(lang)}: Clawd thinking out loud, as if he were doing the work."
    )


def done_prompt(lang: str, project: str, last_message: str) -> str:
    return (
        f'Claude finished a task in the project "{project}". Its final message:\n\n{last_message}\n\n'
        f"In one short line (up to 60 characters) in {lang_name(lang)}, tell the user what was done "
        "and what to check."
    )
