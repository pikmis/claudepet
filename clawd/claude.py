"""Asks Claude Haiku for Clawd's lines through the Claude Code CLI in headless
mode, so it runs on the user's own Claude Code login and needs no API key."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import llm
from .settings import LANGUAGE_NAMES
from .shell import shell_name

TIMEOUT = 90
DEFAULT_MODEL = "claude-haiku-5-5"
# Names under which Haiku is known to Claude Code and the gateways in front of it.
HAIKU_NAMES = (
    "claude-haiku-5-5",
    "haiku",
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
)
VERSION_TIMEOUT = 20
WINDOWS = sys.platform == "win32"


class HaikuError(Exception):
    def __init__(self, message: str, fatal: bool = False) -> None:
        """`fatal`: nothing will work until the settings change, so stop asking for a while."""
        super().__init__(message)
        self.fatal = fatal


def is_model_error(message: str) -> bool:
    low = message.lower()
    return any(
        hint in low for hint in ("selected model", "may not exist", "model_not_found", "retired", "not available")
    )


class Haiku:
    """Haiku as the rest of the app sees it: where `claude` lives, which language
    to speak and whether it's switched on. All of these can change at runtime."""

    def __init__(self, workdir: Path, lang: str, enabled: bool) -> None:
        self.workdir = workdir
        self.cli: Path | None = None
        self.lang = lang
        self.enabled = enabled
        self.provider = "claude"  # or "openrouter", "openai", "custom": see llm.PRESETS
        self.api_base = ""
        self.api_key = ""
        self.isolated = False  # Claude Code: ignore the user's settings.json (gateways, env, hooks)
        self.api_model = DEFAULT_MODEL  # for Claude Code this is the --model alias; empty means its default

    def configure(self, provider: str, base: str = "", key: str = "", model: str = "", isolated: bool = False) -> None:
        self.isolated = isolated
        self.provider = provider if provider in llm.PROVIDERS else "claude"
        self.api_base, self.api_key, self.api_model = base, key, model

    def run(self, prompt, web: bool = False) -> str:
        """Runs a prompt written for the current language: `prompt(lang) -> str`.
        With `web`, Haiku may search and read the web while answering."""
        if not self.enabled:
            raise HaikuError("disabled", fatal=True)
        lang = self.lang
        if self.provider != "claude":
            try:
                return llm.complete(
                    self.api_base, self.api_key, self.api_model, persona(lang), prompt(lang), self.provider
                )
            except llm.ApiError as e:
                raise HaikuError(str(e), fatal=e.fatal) from None
        cli = self.cli
        if cli is None:
            raise HaikuError("claude CLI not found", fatal=True)
        text = prompt(lang)
        try:
            return ask(cli, self.workdir, lang, text, web, self.api_model, self.isolated)
        except HaikuError as e:
            if not (self.api_model and is_model_error(str(e))):
                raise
            # The account (or the gateway in front of it) may name the model differently. Try the
            # usual names of the same family, keeping to Haiku when that is what was asked for.
            family = "haiku" if "haiku" in self.api_model.lower() else ""
            first, tried = e, [self.api_model]
            for candidate in HAIKU_NAMES if family else ():
                if candidate in tried:
                    continue
                tried.append(candidate)
                try:
                    out = ask(cli, self.workdir, lang, text, web, candidate, self.isolated)
                except HaikuError as inner:
                    if is_model_error(str(inner)):
                        continue
                    raise
                print(f"claude: using model {candidate!r} instead of {self.api_model!r}", file=sys.stderr, flush=True)
                self.api_model = candidate
                return out
            if family:
                raise HaikuError(
                    f"No Haiku model worked (tried {', '.join(tried)}). Enter the exact name your provider uses "
                    f"in Model and API (see /model in Claude Code). First error: {first}",
                    fatal=True,
                ) from None
            raise HaikuError(
                    f"{e} Enter the exact Haiku name your provider uses in Model and API "
                    "(see /model inside Claude Code).",
                    fatal=True,
                ) from None
            raise

    def run_web(self, prompt) -> str:
        return self.run(prompt, web=True)


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


def ask(cli: Path, workdir: Path, lang: str, prompt: str, web: bool = False, model: str = DEFAULT_MODEL,
        isolated: bool = False) -> str:
    """Runs one headless Haiku turn with no MCP servers, skills or hooks. Its only possible
    tools are web search and fetch, when `web` is set; never the shell or files.
    `workdir` is a private folder, so no project CLAUDE.md gets loaded."""
    tools = "WebSearch,WebFetch" if web else ""
    args = [
        str(cli),
        *(["--allowedTools", tools] if web else []),
        "-p",
        *(["--model", model] if model else []),
        *(["--setting-sources", "project"] if isolated else []),
        "--output-format",
        "text",
        "--no-session-persistence",
        "--tools",
        tools,
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
        f"that Clawd could say right now, in {lang_name(lang)}. Most lines should react to the situation "
        "above (time of day, the open window, the music, whether the user is busy or idle). If the user seems "
        "away or idle, gently tease it (e.g. hint that you've been standing around doing nothing). Keep a "
        "couple of lines generic. Light, warm, geeky humour. One per line."
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


def chat_prompt(lang: str, context: str, history: list, message: str) -> str:
    """The prompt for a reply to something the user typed to Clawd in the chat box.
    `history` holds earlier turns as [speaker, text], speaker being "user" or "clawd"."""
    dialogue = ""
    for who, text in history[-8:]:
        dialogue += f"{'User' if who == 'user' else 'Clawd'}: {text}\n"
    return (
        f"What is going on right now:\n{context}\n\nConversation so far:\n{dialogue}User: {message}\n\n"
        f"Answer the user as Clawd, in {lang_name(lang)}, in one or two short sentences (under 200 characters), "
        "staying in character. Plain text only, no markdown or links. You may search the web if it helps.\n\n"
        f"You can also offer to run ONE shell command on the user's computer ({shell_name()}). The user sees it "
        "and must confirm before it runs. To offer one, put it alone on the last line as: COMMAND: <command>\n"
        "Rules for commands: only when the user asks for something on their computer; prefer read-only "
        "commands; before anything destructive (killing processes, deleting files) first offer a read-only "
        "command that shows exactly what would be affected, and only then the destructive one with concrete "
        "PIDs or paths; never touch the process named clawd or system-critical processes; no sudo, no "
        "interactive programs."
    )


@dataclass
class ChatReply:
    """A chat answer: what Clawd says, and a shell command he offers to run, if any."""

    text: str
    command: str | None


def strip_markdown(text: str) -> str:
    """Turns markdown into plain text for the bubble: drops emphasis marks and keeps link text."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("__", "").replace("`", "")


def reply(output: str, max_chars: int) -> str | None:
    """One clean reply for the speech bubble: no quotes, speaker prefix or markdown, whitespace collapsed."""
    text = " ".join(strip_markdown(output).split())
    if text.startswith("Clawd:"):
        text = text[len("Clawd:") :].strip()
    text = text.strip("\"«»“”").strip()
    if not text:
        return None
    return text[:max_chars].rstrip() + "…" if len(text) > max_chars else text


def parse_chat(output: str, max_chars: int) -> ChatReply | None:
    """Splits Haiku's chat output into the spoken reply and an offered `COMMAND:` line."""
    command = None
    spoken = []
    for line in output.splitlines():
        trimmed = line.strip().strip("`").strip()
        if trimmed.startswith("COMMAND:") and trimmed[len("COMMAND:") :].strip():
            command = trimmed[len("COMMAND:") :].strip()
        else:
            spoken.append(line)
    text = reply("\n".join(spoken), max_chars)
    if text is None and command is None:
        return None
    return ChatReply(text or "", command)
