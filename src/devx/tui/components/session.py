from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

try:
    from textual.widgets import RichLog, Static
except ImportError:
    class RichLog:
        pass
    class Static:
        pass

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
        chat = self.app.query_one("#chat-view", RichLog)
        chat.clear()
        for line in self.app._transcript:
            chat.write(line)
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

    def fork_session(self, name: str = "") -> None:
        from devx.config import Settings
        from devx.harness.session import SessionStore
        name = name.strip()
        try:
            store = SessionStore(Settings.load().session_db)
            current_id = self.app.session_id
            forked_id = store.fork_session(current_id, new_name=name or None)
            self.switch_session(forked_id)
            self.app._write(f"Forked session '{current_id}' -> '{forked_id}'")
        except (KeyError, OSError, sqlite3.Error) as exc:
            self.app._write(f"Fork failed: {exc}")

    def show_tree(self) -> None:
        from typing import Any
        from devx.config import Settings
        from devx.harness.session import SessionStore
        try:
            store = SessionStore(Settings.load().session_db)
            roots = store.get_session_tree()
            if not roots:
                self.app._write("No saved sessions.")
                return
            lines = ["Session Tree:"]
            def _render_node(node: dict[str, Any], prefix: str = "", is_last: bool = True) -> None:
                current_marker = "● (active) " if node["id"] == self.app.session_id else "○ "
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{current_marker}{node['name']} [{node['id'][:8]}]")
                children = node.get("children", [])
                child_prefix = prefix + ("    " if is_last else "│   ")
                for i, child in enumerate(children):
                    _render_node(child, child_prefix, i == len(children) - 1)

            for i, root in enumerate(roots):
                _render_node(root, "", i == len(roots) - 1)
            self.app._write("\n".join(lines))
        except (OSError, sqlite3.Error) as exc:
            self.app._write(f"Unable to show session tree: {exc}")

    def compact_session(self) -> None:
        from devx.config import Settings
        from devx.harness.session import SessionStore
        try:
            store = SessionStore(Settings.load().session_db)
            result = store.compact_session(self.app.session_id)
            if result.get("compacted"):
                self.switch_session(self.app.session_id)
                self.app._write(f"Context compacted! Pruned {result.get('removed_messages')} older messages into milestone summary.")
            else:
                self.app._write(f"Context already compact ({result.get('message_count', 0)} messages). No compaction needed.")
        except (KeyError, OSError, sqlite3.Error) as exc:
            self.app._write(f"Compaction failed: {exc}")