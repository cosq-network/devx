from __future__ import annotations

from pathlib import Path

from textual.suggester import Suggester

COMMAND_SUGGESTIONS = (
    "/help",
    "/help setup",
    "/help provider",
    "/help model",
    "/help session",
    "/help shortcuts",
    "/setup",
    "/provider",
    "/provider list",
    "/provider use",
    "/model",
    "/model catalog",
    "/model catalog free",
    "/model live",
    "/model live free",
    "/model live tools",
    "/model list",
    "/model free",
    "/model tools",
    "/model use",
    "/api-key set",
    "/config show",
    "/config reload",
    "/config reset",
    "/session",
    "/session use",
    "/session rename",
    "/session export",
    "/session import",
    "/remote",
    "/remote connect",
    "/remote disconnect",
    "/agent",
    "/agent supervisor",
    "/agent planner",
    "/agent coder",
    "/agent tester",
    "/copy",
    "/clear",
    "/rollback",
    "/cancel",
    "/exit",
)


class CommandMentionSuggester(Suggester):
    """Suggest slash commands and workspace paths in a coding prompt."""

    def __init__(self, workspace: Path):
        super().__init__(case_sensitive=False)
        self.workspace = workspace.resolve()

    async def get_suggestion(self, value: str) -> str | None:
        if value.startswith("/"):
            return next((item for item in COMMAND_SUGGESTIONS if item.startswith(value)), None)

        token_start = max(value.rfind(" "), value.rfind("\t")) + 1
        token = value[token_start:]
        if not token.startswith("@"):
            return None
        return self._mention_suggestion(value, token_start, token[1:])

    def _mention_suggestion(self, value: str, token_start: int, raw_path: str) -> str | None:
        candidate = (self.workspace / raw_path).resolve(strict=False)
        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            return None
        if raw_path.endswith(("/", "\\")):
            parent, partial = candidate, ""
        else:
            parent, partial = candidate.parent, candidate.name
        if not parent.is_dir():
            return None
        for item in sorted(parent.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower())):
            if partial and not item.name.lower().startswith(partial.lower()):
                continue
            relative = item.relative_to(self.workspace).as_posix()
            suffix = "/" if item.is_dir() else ""
            return value[:token_start] + "@" + relative + suffix
        return None
