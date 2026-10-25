"""Claude Code integration: hooks in ~/.claude/settings.json POST each event to
a tiny HTTP endpoint on localhost, and Clawd acts out what the session is doing."""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePath

from . import claude
from .claude import Haiku, HaikuError

PORT = 47823
MARKER = "127.0.0.1:47823/hook/"
EVENTS = ("UserPromptSubmit", "PreToolUse", "Stop", "Notification", "SessionEnd")
MAX_BODY = 8 << 20
TRANSCRIPT_TAIL = 256 << 10
THOUGHT_EVERY = 20.0


def str_field(value, key: str) -> str:
    field = value.get(key) if isinstance(value, dict) else None
    return field if isinstance(field, str) else ""


def file_name(path: str) -> str:
    return PurePath(path).name or path


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text[:limit] + "…" if len(text) > limit else text


def tool_detail(tool: str, tool_input) -> str:
    """Short human description of what a tool call is about."""
    if tool in ("Read", "Edit", "MultiEdit", "Write", "NotebookEdit"):
        path = str_field(tool_input, "file_path") or str_field(tool_input, "notebook_path")
        detail = file_name(path)
    elif tool == "Bash":
        detail = str_field(tool_input, "command")
    elif tool in ("Grep", "Glob"):
        detail = str_field(tool_input, "pattern")
    elif tool == "WebSearch":
        detail = str_field(tool_input, "query")
    elif tool == "WebFetch":
        url = str_field(tool_input, "url")
        parts = url.split("/")
        detail = parts[2] if len(parts) > 2 else url
    elif tool in ("Task", "Agent"):
        detail = str_field(tool_input, "description")
    else:
        detail = ""
    return clip(detail, 40)


def is_inside(path: str, folder: Path) -> bool:
    try:
        return Path(path).is_relative_to(folder)
    except (ValueError, OSError):
        return False


def parse(body, internal: Path) -> dict | None:
    """Turns a hook payload into a pet event: {"event": {...}, "transcript": Path|None, "last_message": str|None}."""
    if not isinstance(body, dict):
        return None
    cwd = str_field(body, "cwd")
    # Clawd's own Haiku calls run in `internal`; never react to those.
    if cwd and is_inside(cwd, internal):
        return None
    session = str_field(body, "session_id")
    project = file_name(cwd)
    name = str_field(body, "hook_event_name")
    if name == "UserPromptSubmit":
        event = {"kind": "start", "session": session, "project": project}
    elif name == "PreToolUse":
        tool = str_field(body, "tool_name")
        event = {
            "kind": "tool",
            "session": session,
            "project": project,
            "tool": tool,
            "detail": tool_detail(tool, body.get("tool_input")),
        }
    elif name == "Notification":
        event = {
            "kind": "attention",
            "session": session,
            "project": project,
            "message": clip(str_field(body, "message"), 80),
        }
    elif name == "Stop":
        event = {"kind": "stop", "session": session, "project": project}
    elif name == "SessionEnd":
        event = {"kind": "end", "session": session}
    else:
        return None
    transcript = str_field(body, "transcript_path")
    last_message = str_field(body, "last_assistant_message")
    return {
        "event": event,
        "transcript": Path(transcript) if transcript else None,
        "last_message": last_message if last_message.strip() else None,
    }


def last_assistant_text(path: Path) -> str | None:
    """The text of the most recent assistant message in a session transcript (JSONL)."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - TRANSCRIPT_TAIL))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if str_field(entry, "type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content") if isinstance(entry.get("message"), dict) else None
        if not isinstance(content, list):
            continue
        text = [
            str_field(block, "text")
            for block in content
            if str_field(block, "type") == "text" and str_field(block, "text").strip()
        ]
        if text:
            return "\n".join(text)
    return None


def first_sentence(text: str, limit: int) -> str:
    """First sentence of a message, cleaned of markdown, for when Haiku isn't available."""
    plain = "".join(c for c in text if c not in "#*`_>")
    plain = " ".join(plain.split())
    # A sentence ends at . ! ? followed by a space, so "main.rs" stays whole.
    end = len(plain)
    for i, c in enumerate(plain):
        if i >= 10 and c in ".!?" and plain[i + 1 : i + 2] == " ":
            end = i + 1
            break
    return clip(plain[:end], limit)


class Hub:
    def __init__(self, haiku: Haiku, emit) -> None:
        """`emit(event_dict)` may be called from any thread."""
        self.haiku = haiku
        self.emit = emit
        self.sessions: dict[str, dict] = {}
        self.lock = threading.Lock()

    def ask_haiku(self, prompt, limit: int) -> str | None:
        """Runs Haiku if it's available and enabled; None means "fall back to plain text"."""
        try:
            out = self.haiku.run(prompt)
        except HaikuError as e:
            print(f"haiku: {e}", file=sys.stderr)
            return None
        lines = claude.phrases(out, limit)
        return lines[0] if lines else None

    def handle(self, body) -> None:
        parsed = parse(body, self.haiku.workdir)
        if parsed is None:
            return
        event = parsed["event"]
        transcript = parsed["transcript"]
        kind = event["kind"]
        session = event.get("session", "")
        if kind == "start":
            # Remember the previous answer so it isn't mistaken for a new thought.
            text = (last_assistant_text(transcript) if transcript else None) or ""
            with self.lock:
                self.sessions.setdefault(session, {"last_text": "", "last_thought": None, "thinking": False})[
                    "last_text"
                ] = text
            self.emit(event)
        elif kind == "tool":
            self.emit(event)
            text = last_assistant_text(transcript) if transcript else None
            if not text:
                return
            with self.lock:
                state = self.sessions.setdefault(session, {"last_text": "", "last_thought": None, "thinking": False})
                last = state["last_thought"]
                due = last is None or time.monotonic() - last >= THOUGHT_EVERY
                if text == state["last_text"] or state["thinking"] or not due:
                    return
                state["last_text"] = text
                state["thinking"] = True
                state["last_thought"] = time.monotonic()
            threading.Thread(target=self._think, args=(session, text), daemon=True).start()
        elif kind == "stop":
            self.emit(event)
            text = parsed["last_message"] or (last_assistant_text(transcript) if transcript else None)
            if not text:
                return
            threading.Thread(target=self._report, args=(session, event["project"], text), daemon=True).start()
        elif kind == "end":
            with self.lock:
                self.sessions.pop(session, None)
            self.emit(event)
        else:
            self.emit(event)

    def _think(self, session: str, text: str) -> None:
        thought = self.ask_haiku(lambda lang: claude.thought_prompt(lang, clip(text, 1500)), 60) or first_sentence(
            text, 60
        )
        with self.lock:
            if session in self.sessions:
                self.sessions[session]["thinking"] = False
        self.emit({"kind": "thought", "session": session, "text": thought})

    def _report(self, session: str, project: str, text: str) -> None:
        report = self.ask_haiku(lambda lang: claude.done_prompt(lang, project, clip(text, 2000)), 80) or first_sentence(
            text, 70
        )
        self.emit({"kind": "report", "session": session, "project": project, "text": report})


def serve(token: str, hub: Hub) -> ThreadingHTTPServer:
    """Starts the hook endpoint on a background thread; raises OSError if the port is taken."""
    path = f"/hook/{token}"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 2

        def log_message(self, *args) -> None:  # silence the default access log
            pass

        def _reply(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if self.path != path or not 0 <= length <= MAX_BODY:
                self._reply(404)
                return
            try:
                data = self.rfile.read(length)
            except OSError:
                return
            if len(data) != length:
                return
            self._reply(204)
            try:
                value = json.loads(data)
            except ValueError:
                return
            try:
                hub.handle(value)
            except Exception as e:  # a bad payload must not kill the endpoint
                print(f"hook: {e}", file=sys.stderr)

        def do_GET(self) -> None:
            self._reply(404)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def load_token(directory: Path) -> str:
    """A per-install secret in the hook URL, so web pages can't make Clawd talk."""
    path = directory / "hook-token"
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 16 and token.isascii() and token.isalnum():
            return token
    except OSError:
        pass
    token = secrets.token_hex(16)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    except OSError:
        pass
    return token


def settings_path() -> Path | None:
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    if config:
        return Path(config) / "settings.json"
    home = claude.home_dir()
    return home / ".claude" / "settings.json" if home else None


def hook_command(token: str) -> str:
    return f"curl -s -m 2 -o /dev/null -H Content-Type:application/json --data-binary @- http://{MARKER}{token} || true"


def is_clawd_group(group) -> bool:
    hooks = group.get("hooks") if isinstance(group, dict) else None
    return isinstance(hooks, list) and any(MARKER in str_field(h, "command") for h in hooks)


def with_hooks(settings, command: str | None):
    """Removes Clawd's hooks from settings and, given a command, adds fresh ones."""
    if not isinstance(settings, dict):
        raise ValueError("settings.json is not a JSON object")
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError('"hooks" in settings.json is not an object')
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"hooks.{event} is not an array")
        groups[:] = [g for g in groups if not is_clawd_group(g)]
        if command is not None:
            hook = {"type": "command", "command": command, "timeout": 5}
            groups.append({"matcher": "*", "hooks": [hook]} if event == "PreToolUse" else {"hooks": [hook]})
        if not groups:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return settings


def is_installed(path: Path) -> bool:
    try:
        return MARKER in Path(path).read_text(encoding="utf-8")
    except OSError:
        return False


def set_installed(path: Path, token: str | None) -> None:
    """Adds (with a token) or removes Clawd's hooks, keeping everything else intact."""
    path = Path(path)
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        original = None
    if original is not None and original.strip():
        try:
            settings = json.loads(original)
        except ValueError as e:
            raise ValueError(f"settings.json is not valid JSON: {e}") from e
    else:
        settings = {}
    updated = with_hooks(settings, hook_command(token) if token else None)
    if original is not None:
        backup = path.with_name(path.name + ".clawd-backup")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".clawd-tmp")
    tmp.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
