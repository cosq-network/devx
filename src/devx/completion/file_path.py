from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prompt_toolkit.completion import Completer, Completion
else:
    try:
        from prompt_toolkit.completion import Completer, Completion
    except ImportError:  # pragma: no cover
        class Completer:
            pass
        
        class Completion:
            def __init__(self, text: str, start_position: int = 0, display: str | None = None):
                self.text = text
                self.start_position = start_position
                self.display = display or text


class FilePathCompleter(Completer):
    def __init__(self, workspace: Path | None = None, limit: int = 30):
        self.workspace, self.limit = (workspace or Path.cwd()).resolve(), limit

    def get_completions(self, document, complete_event):
        token = document.text_before_cursor.split()[-1] if document.text_before_cursor.split() else ""
        if not token.startswith("@"):
            return
        query = token[1:].replace("\\", "/")
        base = self.workspace / query.rsplit("/", 1)[0] if "/" in query else self.workspace
        try:
            base = base.resolve(strict=False)
            base.relative_to(self.workspace)
        except ValueError:
            return
        partial = query.rsplit("/", 1)[-1]
        if not base.is_dir():
            return
        prefix = query.rsplit("/", 1)[0] + "/" if "/" in query else ""
        IGNORED_NAMES = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
        count = 0
        for item in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not partial.startswith("."):
                if item.name.startswith(".") or item.name in IGNORED_NAMES:
                    continue
            elif item.name in IGNORED_NAMES:
                continue
            if partial.lower() in item.name.lower():
                display = "@" + prefix + item.name + ("/" if item.is_dir() else "")
                yield Completion(display, start_position=-len(token))
                count += 1
                if count >= self.limit:
                    break
