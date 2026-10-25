"""What the user is up to: the focused window and how fast they type.
Only the number of key presses is counted; which keys were pressed is never kept."""
from __future__ import annotations

import sys
import threading
import time
from collections import deque

POLL = 0.03
WINDOW_EVERY = 33  # polls, ~1 s
TYPING_EVERY = 2.0
TYPING_WINDOW = 10.0
MAX_TITLE = 120


def count_new_presses(prev: int, now: int) -> int:
    """Keys are bit sets; a press is a bit that is on now and was off before."""
    return (now & ~prev).bit_count()


def shorten(title: str) -> str:
    title = title.strip()
    return title[:MAX_TITLE] + "…" if len(title) > MAX_TITLE else title


def watch(probe: Probe, enabled: threading.Event, emit) -> None:
    """Polls `probe` while `enabled` is set."""
    last_window = None
    prev_keys = 0
    presses: deque[tuple[float, int]] = deque()
    last_typing = time.monotonic()
    last_kpm = 0
    tick = 0
    while enabled.is_set():
        time.sleep(POLL)
        tick += 1
        now = time.monotonic()

        keys = probe.keys()
        n = count_new_presses(prev_keys, keys)
        prev_keys = keys
        if n:
            presses.append((now, n))
        while presses and now - presses[0][0] > TYPING_WINDOW:
            presses.popleft()
        if now - last_typing >= TYPING_EVERY:
            last_typing = now
            kpm = int(sum(c for _, c in presses) * 60 / TYPING_WINDOW)
            if kpm != last_kpm:
                last_kpm = kpm
                emit({"kind": "typing", "kpm": kpm})

        if tick % WINDOW_EVERY == 0:
            found = probe.active_window()
            if found:
                # Clicking Clawd focuses his own overlay; that's not interesting.
                current = (found[0], shorten(found[1]))
                if current[1] != "Clawd" and current != last_window:
                    last_window = current
                    emit({"kind": "window", "app": current[0], "title": current[1]})


def spawn(enabled: threading.Event, emit) -> threading.Thread:
    """Watches the desktop on a background thread for the lifetime of the app.
    `emit` gets {"kind": "window", "app", "title"} and {"kind": "typing", "kpm"}."""

    def run() -> None:
        while True:
            if not enabled.is_set():
                time.sleep(0.5)
                continue
            probe = Probe.create()
            if probe is None:
                print("context: no desktop session to watch", file=sys.stderr)
                time.sleep(30)
                continue
            try:
                watch(probe, enabled, emit)
            except Exception as e:  # e.g. the X server went away; start over with a fresh probe
                print(f"context: {e}", file=sys.stderr)
                time.sleep(3)

    thread = threading.Thread(target=run, name="clawd-context", daemon=True)
    thread.start()
    return thread


class Probe:
    @staticmethod
    def create() -> Probe | None:
        try:
            if sys.platform == "win32":
                return WindowsProbe()
            if sys.platform.startswith("linux"):
                return X11Probe()
        except Exception as e:
            print(f"context: {e}", file=sys.stderr)
        return None

    def active_window(self) -> tuple[str, str] | None:
        return None

    def keys(self) -> int:
        return 0


# Virtual-key codes that make up typing: no mouse buttons, modifiers or media keys.
TYPING_KEYS = (0x08, 0x09, 0x0D, *range(0x20, 0x5B), *range(0x60, 0x70), *range(0xBA, 0xE3))


class WindowsProbe(Probe):
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def active_window(self) -> tuple[str, str] | None:
        ctypes, wintypes = self.ctypes, self.wintypes
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        buf = ctypes.create_unicode_buffer(512)
        self.user32.GetWindowTextW(hwnd, buf, len(buf))
        title = buf.value

        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = ""
        process = self.kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if process:
            path = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(path))
            if self.kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                name = path.value.replace("\\", "/").rsplit("/", 1)[-1]
                app = name.rsplit(".", 1)[0] if "." in name else name
            self.kernel32.CloseHandle(process)
        return app, title

    def keys(self) -> int:
        bits = 0
        get = self.user32.GetAsyncKeyState
        for vk in TYPING_KEYS:
            if get(vk) & 0x8000:
                bits |= 1 << vk
        return bits


class X11Probe(Probe):
    def __init__(self) -> None:
        from Xlib import X, display  # python-xlib

        self.X = X
        self.display = display.Display()
        self.root = self.display.screen().root
        self.net_active_window = self.display.intern_atom("_NET_ACTIVE_WINDOW")
        self.net_wm_name = self.display.intern_atom("_NET_WM_NAME")

    def active_window(self) -> tuple[str, str] | None:
        prop = self.root.get_full_property(self.net_active_window, self.X.AnyPropertyType)
        if prop is None or not len(prop.value) or not prop.value[0]:
            return None
        window = self.display.create_resource_object("window", prop.value[0])
        name = window.get_full_property(self.net_wm_name, self.X.AnyPropertyType)
        title = bytes(name.value).decode("utf-8", "replace") if name and name.value else (window.get_wm_name() or "")
        # WM_CLASS is (instance, Class); the class reads like an app name.
        wm_class = window.get_wm_class()
        app = wm_class[-1] if wm_class else ""
        return app, title

    def keys(self) -> int:
        bits = 0
        for i, byte in enumerate(self.display.query_keymap()):
            bits |= int(byte) << (8 * i)
        return bits
