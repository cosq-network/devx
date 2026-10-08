from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

try:
    from textual.widgets import Static
except ImportError:
    Static = object

if TYPE_CHECKING:
    from devx.tui.app import DevTUI


class SessionManager:
    def __init__(self, app: DevTUI):
        self.app = app

    def list_sessions(self) -> None:
        from devx.config import Settings
        from devx.harness.session import SessionStore
        try:
            sessions = SessionStore(Settings.load().session_db).list_sessions()
            self.app._write("\n".join(f"{item['id']}  {item['name']}" for item in sessions) or "No saved sessions.")
        except (OSError, sqlite3.Error) as exc:
            self.app._write(f"Unable to list sessions: {exc}")

    def switch_session(self, requested: str) -> None:
        from devx.config import Settings
        from devx.harness.session import SessionStore

        requested = requested.strip()
        if not requested:
            self.app._write("Usage: /session use <id-or-name>")
            return
        try:
            session = SessionStore(Settings.load().session_db).load(requested)
        except (OSError, sqlite3.Error) as exc:
            self.app._write(f"Unable to load session: {exc}")
            return
        if not session:
            self.app._write(f"Session not found: {requested}")
            return
        self.app.session_id = session["id"]
        messages = session.get("state", {}).get("messages", [])
        self.app._transcript = [
            f"{message.get('role', 'agent')}: {message['content']}"
            for message in messages
            if isinstance(message, dict) and isinstance(message.get("content"), str)
        ]
        panel = self.app.query_one("#chat-view", Static)
        panel.text = "\n".join(self.app._transcript)
        self.app.query_one("#session-text", Static).update(f"session: {self.app.session_id}")
        self.app._write(f"Switched to session: {session['name']}")

    def rename_session(self, name: str) -> None:
        from devx.config import Settings
        from devx.harness.session import SessionStore
        name = name.strip()
        try:
            renamed = SessionStore(Settings.load().session_db).rename(self.app.session_id, name)
        except (OSError, sqlite3.Error) as exc:
            self.app._write(f"Unable to rename session: {exc}")
            return
        if renamed:
            self.app.query_one("#session-text", Static).update(f"session: {self.app.session_id}")
            self.app._write(f"Renamed session to: {name}")
        else:
            self.app._write("Session not found.")

    def export_session(self, destination: str) -> None:
        from devx.config import Settings
        from devx.harness.session import SessionStore
        from pathlib import Path
        destination = destination.strip()
        try:
            exported = SessionStore(Settings.load().session_db).export_session(self.app.session_id, Path(destination))
            self.app._write(f"Exported session: {exported}")
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            self.app._write(f"Export failed: {exc}")

    def import_session(self, source: str) -> None:
        from devx.config import Settings
        from devx.harness.session import SessionStore
        from pathlib import Path
        source = source.strip()
        try:
            self.app.session_id = SessionStore(Settings.load().session_db).import_session(Path(source))
            self.app._write(f"Imported session: {self.app.session_id}")
        except (OSError, TypeError, ValueError, KeyError, sqlite3.Error) as exc:
            self.app._write(f"Import failed: {exc}")