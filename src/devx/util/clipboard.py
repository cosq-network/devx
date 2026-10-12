"""OS-default clipboard copying with terminal fallbacks.

Priority order:

1. The platform's native clipboard utility (``clip`` / ``pbcopy`` /
   ``wl-copy`` / ``xclip`` / ``xsel``) — the operating system's default path.
2. Textual's ``App.copy_to_clipboard``, which emits the OSC 52 terminal
   escape sequence (supported by most modern terminals, including over SSH)
   when copying from inside a running TUI app.
3. The stdlib ``tkinter`` clipboard as a final optional tier — no extra
   dependency, and it writes the real OS clipboard where a display/Tk exists.

``copy_to_clipboard`` returns a short label describing how the copy happened
so the TUI's status bar can mirror it. Every tier is failure-tolerant: if
nothing is available a :class:`RuntimeError` with an install hint is raised
instead of the copy silently failing.
"""

from __future__ import annotations

import base64
import platform
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App


def _native_commands() -> list[list[str]]:
    """Platform-native clipboard write commands, best utility first."""
    return {
        "Windows": [["clip.exe"], ["clip"]],
        "Darwin": [["pbcopy"]],
        "Linux": [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ],
    }.get(platform.system(), [])


def os_native_copy(text: str, timeout: float = 5.0) -> bool:
    """Copy via the OS-native clipboard utility, if one is installed."""
    for command in _native_commands():
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(command, input=text, text=True, check=True, timeout=timeout)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def osc52_encode(text: str) -> str:
    """Encode *text* as an OSC 52 terminal clipboard write sequence."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"\x1b]52;c;{payload}\a"


def tk_clipboard_copy(text: str) -> bool:
    """Copy via the stdlib ``tkinter`` clipboard (optional final tier)."""
    try:
        import tkinter
    except Exception:
        return False
    try:
        root = tkinter.Tk()
        root.withdraw()
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
        finally:
            root.destroy()
        return True
    except Exception:
        return False


def copy_to_clipboard(text: str, *, app: "App | None" = None) -> str:
    """Copy *text* using the operating system's clipboard and return a label.

    Raises :class:`RuntimeError` for empty *text*, or when no clipboard
    mechanism is reachable (with an install hint). Fallback order: the OS-native
    utility, then the Textual OSC 52 terminal clipboard (only meaningful when
    *app* is a live Textual app), then stdlib tkinter.
    """
    if not text:
        raise RuntimeError("The transcript is empty.")
    if os_native_copy(text):
        return "OS clipboard"
    if app is not None:
        try:
            app.copy_to_clipboard(text)
            return "terminal clipboard (OSC 52)"
        except Exception:
            pass
    if tk_clipboard_copy(text):
        return "OS clipboard (Tk)"
    hint = " Install wl-copy, xclip, or xsel." if platform.system() == "Linux" else ""
    raise RuntimeError(f"No clipboard utility found.{hint}")
