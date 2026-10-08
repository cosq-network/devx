from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip('"').strip("'")
    return values


def _load_project_config(root: Path) -> dict[str, Any]:
    """Load optional, non-secret project configuration from ``.devx/config.json``."""
    path = root / ".devx" / "config.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid project configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"Project configuration must be an object: {path}")
    return value


@dataclass(frozen=True)
class Settings:
    base_url: str = "http://localhost:4000/v1"
    api_key: str = "sk-placeholder"
    model: str = "gpt-4o"
    provider: str = "openai"
    workspace: Path = field(default_factory=Path.cwd)
    session_db: Path = field(default_factory=lambda: Path.home() / ".devx" / "sessions.db")
    cache_db: Path = field(default_factory=lambda: Path.home() / ".devx" / "cache.db")
    approval_required: bool = True
    max_iterations: int = 8
    max_tool_calls: int = 40
    command_timeout: float = 120.0
    test_command: str = ""
    max_file_bytes: int = 1_000_000
    max_context_files: int = 15
    lint_command: str = ""
    embeddings_enabled: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "devx" / "embeddings")
    embeddings_offline: bool = True
    tracing_enabled: bool = False
    auto_context: bool = True
    permission_rules: tuple[dict[str, str], ...] = ()
    plugin_paths: tuple[str, ...] = ()
    mcp_servers: tuple[dict[str, Any], ...] = ()
    context_compaction_enabled: bool = True
    max_context_messages: int = 24
    max_context_chars: int = 24_000
    compaction_summary_chars: int = 4_000

    @classmethod
    def load(cls, *, workspace: Path | None = None) -> Settings:
        legacy_path = Path.home() / ".dev" / "config.env"
        config_path = Path.home() / ".devx" / "config.env"
        user_values = _load_env_file(legacy_path)
        user_values.update(_load_env_file(config_path))

        cwd = Path.cwd().resolve()
        workspace_hint = workspace or Path(
            os.getenv("DEVX_WORKSPACE")
            or os.getenv("DEV_WORKSPACE")
            or user_values.get("DEVX_WORKSPACE")
            or user_values.get("DEV_WORKSPACE")
            or cwd
        )
        workspace_values = _load_env_file(workspace_hint.expanduser().resolve() / ".env")

        def get(name: str, default: str, legacy_name: str | None = None) -> str:
            val = os.getenv(name)
            if val is not None:
                return val
            if legacy_name:
                legacy_val = os.getenv(legacy_name)
                if legacy_val is not None:
                    return legacy_val
            if name in workspace_values:
                return workspace_values[name]
            if legacy_name and legacy_name in workspace_values:
                return workspace_values[legacy_name]
            if name in user_values:
                return user_values[name]
            if legacy_name and legacy_name in user_values:
                return user_values[legacy_name]
            return default

        def boolean(name: str, default: bool, legacy_name: str | None = None) -> bool:
            value = get(name, str(default), legacy_name).strip().lower()
            if value in {"1", "true", "yes", "on"}:
                return True
            if value in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"{name} must be a boolean value")

        root = (workspace or Path(get("DEVX_WORKSPACE", str(cwd), "DEV_WORKSPACE"))).expanduser().resolve()
        if root != workspace_hint.expanduser().resolve():
            workspace_values = _load_env_file(root / ".env")
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")
        project = _load_project_config(root)
        raw_permissions = project.get("permissions", ())
        if not isinstance(raw_permissions, (list, tuple)):
            raise TypeError("Project permissions must be a list")
        permission_rules = tuple(item for item in raw_permissions if isinstance(item, dict))
        raw_plugins = project.get("plugins", project.get("plugin_paths", ()))
        if isinstance(raw_plugins, str):
            raw_plugins = [raw_plugins]
        if not isinstance(raw_plugins, (list, tuple)):
            raise TypeError("Project plugins must be a list of paths")
        raw_mcp = project.get("mcp_servers", ())
        if isinstance(raw_mcp, dict):
            raw_mcp = [dict(value, name=name) if isinstance(value, dict) else value for name, value in raw_mcp.items()]
        if not isinstance(raw_mcp, (list, tuple)):
            raise TypeError("Project mcp_servers must be a list or object")
        mcp_servers = tuple(item for item in raw_mcp if isinstance(item, dict))
        compaction = project.get("context_compaction", {})
        if not isinstance(compaction, dict):
            raise TypeError("context_compaction must be an object")
        return cls(
            base_url=get("DEVX_BASE_URL", cls.base_url, "DEV_BASE_URL"),
            api_key=get("DEVX_API_KEY", cls.api_key, "DEV_API_KEY"),
            model=get("DEVX_MODEL", cls.model, "DEV_MODEL"),
            provider=get("DEVX_PROVIDER", cls.provider, "DEV_PROVIDER"),
            workspace=root,
            session_db=Path(get("DEVX_SESSION_DB", str(Path.home() / ".devx" / "sessions.db"), "DEV_SESSION_DB")).expanduser(),
            cache_db=Path(get("DEVX_CACHE_DB", str(Path.home() / ".devx" / "cache.db"), "DEV_CACHE_DB")).expanduser(),
            approval_required=boolean("DEVX_APPROVAL_REQUIRED", True, "DEV_APPROVAL_REQUIRED"),
            max_iterations=int(get("DEVX_MAX_ITERATIONS", "8", "DEV_MAX_ITERATIONS")),
            max_tool_calls=int(get("DEVX_MAX_TOOL_CALLS", "40", "DEV_MAX_TOOL_CALLS")),
            command_timeout=float(get("DEVX_COMMAND_TIMEOUT", "120", "DEV_COMMAND_TIMEOUT")),
            test_command=get("DEVX_TEST_COMMAND", "", "DEV_TEST_COMMAND"),
            max_file_bytes=int(get("DEVX_MAX_FILE_BYTES", "1000000", "DEV_MAX_FILE_BYTES")),
            max_context_files=int(get("DEVX_MAX_CONTEXT_FILES", "5", "DEV_MAX_CONTEXT_FILES")),
            lint_command=get("DEVX_LINT_COMMAND", "", "DEV_LINT_COMMAND"),
            embeddings_enabled=boolean("DEVX_EMBEDDINGS_ENABLED", False, "DEV_EMBEDDINGS_ENABLED"),
            embedding_model=get("DEVX_EMBEDDING_MODEL", cls.embedding_model, "DEV_EMBEDDING_MODEL"),
            embedding_cache_dir=Path(get(
                "DEVX_EMBEDDING_CACHE_DIR",
                str(Path.home() / ".cache" / "devx" / "embeddings"),
                "DEV_EMBEDDING_CACHE_DIR",
            )).expanduser(),
embeddings_offline=boolean("DEVX_EMBEDDINGS_OFFLINE", True, "DEV_EMBEDDINGS_OFFLINE"),
            auto_context=boolean("DEVX_AUTO_CONTEXT", True, "DEV_AUTO_CONTEXT"),
            tracing_enabled=boolean("LANGSMITH_TRACING", False),
            permission_rules=permission_rules,
            plugin_paths=tuple(str(item) for item in raw_plugins),
            mcp_servers=mcp_servers,
            context_compaction_enabled=boolean(
                "DEVX_CONTEXT_COMPACTION_ENABLED",
                bool(compaction.get("enabled", True)),
                "DEV_CONTEXT_COMPACTION_ENABLED",
            ),
            max_context_messages=int(get(
                "DEVX_MAX_CONTEXT_MESSAGES", str(compaction.get("max_messages", 24)), "DEV_MAX_CONTEXT_MESSAGES"
            )),
            max_context_chars=int(get(
                "DEVX_MAX_CONTEXT_CHARS", str(compaction.get("max_chars", 24_000)), "DEV_MAX_CONTEXT_CHARS"
            )),
            compaction_summary_chars=int(get(
                "DEVX_COMPACTION_SUMMARY_CHARS", str(compaction.get("summary_chars", 4_000)),
                "DEV_COMPACTION_SUMMARY_CHARS"
            )),
        )

    def validate(self) -> None:
        if self.max_iterations < 1 or self.max_tool_calls < 1 or self.max_context_files < 1:
            raise ValueError("Iteration, tool-call, and context-file limits must be positive")
        if self.max_context_messages < 2 or self.max_context_chars < 256 or self.compaction_summary_chars < 128:
            raise ValueError("Context compaction limits are too small")
        if self.command_timeout <= 0 or self.max_file_bytes <= 0:
            raise ValueError("Timeout and file-size limits must be positive")
