from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _load_env_file(path: Path) -> dict[str, str]:
    """Load environment variables from a .env file.

    Values are stripped of surrounding quotes and an ``export `` prefix.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        # An unreadable or non-UTF-8 .env must not crash every Settings.load();
        # treat it as empty so the rest of the environment still resolves.
        return values
    for raw in lines:
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


# ── Default configuration values ──────────────────────────────────────
# Centralized magic numbers for better maintainability

MAX_TOOL_CALLS_DEFAULT = 40
COMMAND_TIMEOUT_DEFAULT = 120.0
MAX_CONTEXT_FILES_DEFAULT = 5
MAX_CONTEXT_MESSAGES_DEFAULT = 24
DIAGNOSTIC_DISTILLATION_MAX_CHARS_DEFAULT = 4000
DIAGNOSTIC_DISTILLATION_MIN_CHARS = 100
CONTEXT_MIN_MESSAGES = 2
CONTEXT_MIN_CHARS = 256
CONTEXT_SUMMARY_MIN_CHARS = 128

# Secret-scan behavior for agent reads
SECRET_SCAN_MODE_DEFAULT = "warn"  # off | warn | redact | block

# Context budget modes
CONTEXT_BUDGET_MODE_FIXED = "fixed"
CONTEXT_BUDGET_MODE_ADAPTIVE = "adaptive"

# Priority levels for message retention
CONTEXT_PRIORITY_CRITICAL = "critical"
CONTEXT_PRIORITY_HIGH = "high"
CONTEXT_PRIORITY_NORMAL = "normal"
CONTEXT_PRIORITY_LOW = "low"
CONTEXT_PRIORITY_MIN = 0
CONTEXT_PRIORITY_MAX = 100

class Settings(BaseModel):
    """Configuration settings for devx, loaded with environment variable precedence.

    Settings are resolved in order of precedence:
    1. Environment variables (``DEVX_*`` or legacy ``DEV_*``)
    2. Workspace ``.env`` file
    3. User global config (``~/.devx/config.env``)
    4. Non-secret project config (``.devx/config.json``)
    5. Built-in defaults
    """

    # LLM configuration
    base_url: str = Field(default="http://localhost:4000/v1", description="OpenAI-compatible API endpoint")
    api_key: str = Field(default="sk-placeholder", description="API key for the LLM provider")
    model: str = Field(default="gpt-4o", description="Model name to use")
    provider: str = Field(default="openai", description="Provider: openai, anthropic, google, azure")

    # Workspace configuration
    workspace: Path = Field(default_factory=Path.cwd, description="Workspace root directory")
    session_db: Path = Field(
        default_factory=lambda: Path.home() / ".devx" / "sessions.db",
        description="SQLite session database path",
    )
    cache_db: Path = Field(
        default_factory=lambda: Path.home() / ".devx" / "cache.db",
        description="Semantic cache database path",
    )
    approval_required: bool = Field(default=True, description="Require approval for file writes and commands")
    max_iterations: int = Field(default=8, description="Maximum agent loop iterations per run")
    max_tool_calls: int = Field(default=MAX_TOOL_CALLS_DEFAULT, description="Maximum tool calls per run")
    command_timeout: float = Field(default=COMMAND_TIMEOUT_DEFAULT, description="Shell command timeout in seconds")
    test_command: str = Field(default="", description="Custom test command for tester agent")
    max_file_bytes: int = Field(default=1_000_000, description="Maximum file size to read (bytes)")
    max_context_files: int = Field(default=MAX_CONTEXT_FILES_DEFAULT, description="Maximum files selected for each run (fixed mode)")
    context_budget_mode: str = Field(
        default=CONTEXT_BUDGET_MODE_FIXED, description="Context budgeting mode: fixed or adaptive"
    )
    lint_command: str = Field(default="", description="Optional lint command")
    unit_test_command: str = Field(default="", description="Optional command dedicated for fast unit tests")
    integration_test_command: str = Field(default="", description="Optional command dedicated for integration tests")
    test_impact_enabled: bool = Field(default=True, description="Enable Test-Impact Analysis (TIA) for selective testing")
    review_linter_enabled: bool = Field(default=True, description="Enable local linter diagnostics in code review")

    # Token optimization
    embeddings_enabled: bool = Field(default=False, description="Enable local ONNX embeddings")
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2", description="Local embedding model identifier"
    )
    embedding_cache_dir: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "devx" / "embeddings",
        description="Local embedding model cache directory",
    )
    embeddings_offline: bool = Field(default=True, description="Prevent embedding model downloads")

    # Observability
    tracing_enabled: bool = Field(default=False, description="Enable LangSmith tracing")
    auto_context: bool = Field(default=True, description="Auto-discover project files")

    # Project rules
    permission_rules: tuple[dict[str, str], ...] = Field(default=(), description="Project permission rules")
    policy_preset: str = Field(default="", description="Named permission preset (e.g. remote_only, deny_local_writes, strict)")
    plugin_paths: tuple[str, ...] = Field(default=(), description="Project plugin paths")
    mcp_servers: tuple[dict[str, Any], ...] = Field(default=(), description="MCP server configurations")

    # Context compaction
    context_compaction_enabled: bool = Field(default=True, description="Enable context compaction")
    max_context_messages: int = Field(default=MAX_CONTEXT_MESSAGES_DEFAULT, description="Maximum context messages (fixed mode)")
    context_priority_retention: tuple[str, int, int] = Field(
        default=(CONTEXT_PRIORITY_CRITICAL, CONTEXT_PRIORITY_HIGH, CONTEXT_PRIORITY_NORMAL),
        description="Priority levels and score thresholds for message retention"
    )
    max_context_chars: int = Field(default=24_000, description="Maximum context characters")
    compaction_summary_chars: int = Field(default=4_000, description="Compaction summary characters")
    diagnostic_distillation_enabled: bool = Field(
        default=True, description="Enable diagnostic distillation"
    )
    diagnostic_distillation_max_chars: int = Field(
        default=DIAGNOSTIC_DISTILLATION_MAX_CHARS_DEFAULT, description="Diagnostic distillation character limit"
    )
    decoupled_context_enabled: bool = Field(
        default=True, description="Enable decoupled context"
    )
    remote_ssh: str = Field(default="", description="Remote SSH workspace URI")
    ssh_key_path: str = Field(default="", description="Path to SSH private key")
    container: str = Field(default="", description="Container workspace URI (container://name/path)")
    # Path to an SSH known_hosts file used to verify remote host keys. When set
    # (and ssh_allow_unknown_keys is off), hosts not already listed are refused.
    ssh_known_hosts: str = Field(
        default="", description="Path to an SSH known_hosts file for host-key verification"
    )
    # Opt-in to auto-accept a new SSH host key on first connect. Only intended
    # for throwaway/demo targets (devx lab containers, CI ephemeral servers).
    ssh_allow_unknown_keys: bool = Field(
        default=False, description="Auto-add a new SSH host key on first connect (throwaway targets only)"
    )
    secret_scan_mode: str = Field(
        default=SECRET_SCAN_MODE_DEFAULT,
        description="Secret scanning on agent reads: off, warn, redact, or block",
    )
    mode: str = Field(
        default="code",
        description="Operational task mode: code, architect, review, ask",
    )
    fallback_provider: str = Field(
        default="",
        description="Fallback provider for automatic failover (e.g. openai, anthropic, google)",
    )
    fallback_model: str = Field(
        default="",
        description="Fallback model name for automatic failover",
    )
    sandbox: str = Field(
        default="none",
        description="Local execution sandboxing driver: none, bwrap",
    )

    # ── Pydantic V2 validators ──

    @field_validator("max_iterations")
    def positive_iterations(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_iterations must be positive")
        return v

    @field_validator("max_tool_calls")
    def positive_tool_calls(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_tool_calls must be positive")
        return v

    @field_validator("max_context_messages")
    def positive_context_messages(cls, v: int) -> int:
        if v < CONTEXT_MIN_MESSAGES:
            raise ValueError(f"max_context_messages must be at least {CONTEXT_MIN_MESSAGES}")
        return v

    @field_validator("max_context_chars")
    def positive_context_chars(cls, v: int) -> int:
        if v < CONTEXT_MIN_CHARS:
            raise ValueError(f"max_context_chars must be at least {CONTEXT_MIN_CHARS}")
        return v

    @field_validator("compaction_summary_chars")
    def positive_summary_chars(cls, v: int) -> int:
        if v < CONTEXT_SUMMARY_MIN_CHARS:
            raise ValueError(f"compaction_summary_chars must be at least {CONTEXT_SUMMARY_MIN_CHARS}")
        return v

    @field_validator("diagnostic_distillation_max_chars")
    def positive_distillation_chars(cls, v: int) -> int:
        if v < DIAGNOSTIC_DISTILLATION_MIN_CHARS:
            raise ValueError(f"diagnostic_distillation_max_chars must be at least {DIAGNOSTIC_DISTILLATION_MIN_CHARS}")
        return v

    @field_validator("mode")
    def valid_mode(cls, v: str) -> str:
        clean = v.strip().lower()
        if clean not in {"code", "architect", "review", "ask"}:
            raise ValueError(f"Invalid mode: {v}. Must be one of: code, architect, review, ask")
        return clean

    @field_validator("command_timeout")
    def positive_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("command_timeout must be positive")
        return v

    @field_validator("max_file_bytes")
    def positive_file_bytes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_file_bytes must be positive")
        return v

    model_config = {"validate_assignment": True, "extra": "ignore"}

    # ── Env var resolution ──

    @classmethod
    def load(cls, *, workspace: Path | None = None) -> "Settings":
        """Load settings with environment variable precedence.

        Precedence (highest to lowest):
        1. Environment variables ``DEVX_*`` / legacy ``DEV_*``
        2. Workspace ``.env`` file
        3. User global config ``~/.devx/config.env``
        4. Non-secret project config ``.devx/config.json``
        5. Built-in defaults
        """
        # ── 1. Load env files ────────────────────────────────────────────
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

        def get(
            name: str,
            default: str,
            legacy_name: str | None = None,
        ) -> str:
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

        # ── 2. Determine workspace root ──────────────────────────────────
        root = (
            workspace or Path(get("DEVX_WORKSPACE", str(cwd), "DEV_WORKSPACE"))
        ).expanduser().resolve()
        if root != workspace_hint.expanduser().resolve():
            workspace_values = _load_env_file(root / ".env")
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")

        # ── 3. Load project config ───────────────────────────────────────
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
            raw_mcp = [
                dict(value, name=name) if isinstance(value, dict) else value
                for name, value in raw_mcp.items()
            ]
        if not isinstance(raw_mcp, (list, tuple)):
            raise TypeError("Project mcp_servers must be a list or object")
        mcp_servers = tuple(item for item in raw_mcp if isinstance(item, dict))
        compaction = project.get("context_compaction", {})
        if not isinstance(compaction, dict):
            raise TypeError("context_compaction must be an object")

        # ── 4. Build the Settings instance ───────────────────────────────
        # Resolve all values with precedence, then construct the pydantic model.

        def get_field(name: str, default: str, legacy_name: str | None = None) -> Any:
            return get(name, default, legacy_name)

        # Resolve boolean fields
        approval_required = boolean("DEVX_APPROVAL_REQUIRED", True, "DEV_APPROVAL_REQUIRED")
        embeddings_enabled = boolean("DEVX_EMBEDDINGS_ENABLED", False, "DEV_EMBEDDINGS_ENABLED")
        embeddings_offline = boolean("DEVX_EMBEDDINGS_OFFLINE", True, "DEV_EMBEDDINGS_OFFLINE")
        tracing_enabled = boolean("LANGSMITH_TRACING", False)
        auto_context = boolean("DEVX_AUTO_CONTEXT", True, "DEV_AUTO_CONTEXT")
        context_compaction_enabled = boolean(
            "DEVX_CONTEXT_COMPACTION_ENABLED",
            bool(compaction.get("enabled", True)),
            "DEV_CONTEXT_COMPACTION_ENABLED",
        )
        diagnostic_distillation_enabled = boolean(
            "DEVX_DIAGNOSTIC_DISTILLATION_ENABLED", True, "DEV_DIAGNOSTIC_DISTILLATION_ENABLED"
        )
        decoupled_context_enabled = boolean(
            "DEVX_DECOUPLED_CONTEXT_ENABLED", True, "DEV_DECOUPLED_CONTEXT_ENABLED"
        )

        # Resolve integer fields using centralized constants
        max_iterations = int(get("DEVX_MAX_ITERATIONS", "8", "DEV_MAX_ITERATIONS"))
        max_tool_calls = int(get("DEVX_MAX_TOOL_CALLS", str(MAX_TOOL_CALLS_DEFAULT), "DEV_MAX_TOOL_CALLS"))
        max_file_bytes = int(get("DEVX_MAX_FILE_BYTES", "1000000", "DEV_MAX_FILE_BYTES"))
        max_context_files = int(get("DEVX_MAX_CONTEXT_FILES", str(MAX_CONTEXT_FILES_DEFAULT), "DEV_MAX_CONTEXT_FILES"))
        max_context_messages = int(
            get("DEVX_MAX_CONTEXT_MESSAGES", str(compaction.get("max_messages", MAX_CONTEXT_MESSAGES_DEFAULT)), "DEV_MAX_CONTEXT_MESSAGES")
        )
        max_context_chars = int(
            get("DEVX_MAX_CONTEXT_CHARS", str(compaction.get("max_chars", 24_000)), "DEV_MAX_CONTEXT_CHARS")
        )
        compaction_summary_chars = int(
            get(
                "DEVX_COMPACTION_SUMMARY_CHARS",
                str(compaction.get("summary_chars", DIAGNOSTIC_DISTILLATION_MAX_CHARS_DEFAULT)),
                "DEV_COMPACTION_SUMMARY_CHARS",
            )
        )
        diagnostic_distillation_max_chars = int(
            get(
                "DEVX_DIAGNOSTIC_DISTILLATION_MAX_CHARS",
                str(DIAGNOSTIC_DISTILLATION_MAX_CHARS_DEFAULT),
                "DEV_DIAGNOSTIC_DISTILLATION_MAX_CHARS",
            )
        )

        # Resolve string fields with fallback to defaults
        base_url = get("DEVX_BASE_URL", "http://localhost:4000/v1", "DEV_BASE_URL")
        api_key = get("DEVX_API_KEY", "sk-placeholder", "DEV_API_KEY")
        model = get("DEVX_MODEL", "gpt-4o", "DEV_MODEL")
        provider = get("DEVX_PROVIDER", "openai", "DEV_PROVIDER")
        test_command = get("DEVX_TEST_COMMAND", "", "DEV_TEST_COMMAND")
        unit_test_command = get("DEVX_UNIT_TEST_COMMAND", str(project.get("unit_test_command", "")), "DEV_UNIT_TEST_COMMAND")
        integration_test_command = get("DEVX_INTEGRATION_TEST_COMMAND", str(project.get("integration_test_command", "")), "DEV_INTEGRATION_TEST_COMMAND")
        test_impact_enabled = boolean("DEVX_TEST_IMPACT", bool(project.get("test_impact_enabled", True)), "DEV_TEST_IMPACT")
        review_linter_enabled = boolean("DEVX_REVIEW_LINTER", bool(project.get("review_linter_enabled", True)), "DEV_REVIEW_LINTER")
        lint_command = get("DEVX_LINT_COMMAND", "", "DEV_LINT_COMMAND")
        embedding_model = get(
            "DEVX_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2", "DEV_EMBEDDING_MODEL"
        )
        remote_ssh = get("DEVX_REMOTE_SSH", "", "DEV_REMOTE_SSH")
        ssh_key_path = get("DEVX_SSH_KEY", "", "DEV_SSH_KEY")
        ssh_known_hosts = get("DEVX_SSH_KNOWN_HOSTS", "", "DEV_SSH_KNOWN_HOSTS")
        ssh_allow_unknown_keys = boolean("DEVX_SSH_ALLOW_UNKNOWN_KEYS", False, "DEV_SSH_ALLOW_UNKNOWN_KEYS")
        container = get("DEVX_CONTAINER", "", "DEV_CONTAINER")
        policy_preset = str(
            get("DEVX_POLICY_PRESET", str(project.get("policy_preset", "")), "DEV_POLICY_PRESET")
        ).strip()
        secret_scan_mode = str(
            get(
                "DEVX_SECRET_SCAN",
                str(project.get("secret_scan_mode", SECRET_SCAN_MODE_DEFAULT)),
                "DEV_SECRET_SCAN",
            )
        ).strip().lower()
        mode = str(get("DEVX_MODE", str(project.get("mode", "code")), "DEV_MODE")).strip().lower()
        fallback_provider = str(get("DEVX_FALLBACK_PROVIDER", str(project.get("fallback_provider", "")), "DEV_FALLBACK_PROVIDER")).strip()
        fallback_model = str(get("DEVX_FALLBACK_MODEL", str(project.get("fallback_model", "")), "DEV_FALLBACK_MODEL")).strip()
        sandbox = str(get("DEVX_SANDBOX", str(project.get("sandbox", "none")), "DEV_SANDBOX")).strip().lower()

        # Resolve project config-derived fields
        permission_rules = tuple(item for item in raw_permissions if isinstance(item, dict))
        plugin_paths = tuple(str(item) for item in raw_plugins)
        mcp_servers = tuple(item for item in raw_mcp if isinstance(item, dict))

        # ── 5. Validate and return ───────────────────────────────────────
        # pydantic validators will raise on invalid values
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            provider=provider,
            workspace=root,
            session_db=Path(get("DEVX_SESSION_DB", str(Path.home() / ".devx" / "sessions.db"), "DEV_SESSION_DB")).expanduser(),
            cache_db=Path(get("DEVX_CACHE_DB", str(Path.home() / ".devx" / "cache.db"), "DEV_CACHE_DB")).expanduser(),
            approval_required=approval_required,
            max_iterations=max_iterations,
            max_tool_calls=max_tool_calls,
            command_timeout=float(get("DEVX_COMMAND_TIMEOUT", "120", "DEV_COMMAND_TIMEOUT")),
            test_command=test_command,
            unit_test_command=unit_test_command,
            integration_test_command=integration_test_command,
            test_impact_enabled=test_impact_enabled,
            review_linter_enabled=review_linter_enabled,
            max_file_bytes=max_file_bytes,
            max_context_files=max_context_files,
            lint_command=lint_command,
            embeddings_enabled=embeddings_enabled,
            embedding_model=embedding_model,
            embedding_cache_dir=Path(
                get(
                    "DEVX_EMBEDDING_CACHE_DIR",
                    str(Path.home() / ".cache" / "devx" / "embeddings"),
                    "DEV_EMBEDDING_CACHE_DIR",
                )
            ).expanduser(),
            embeddings_offline=embeddings_offline,
            tracing_enabled=tracing_enabled,
            auto_context=auto_context,
            permission_rules=permission_rules,
            policy_preset=policy_preset,
            plugin_paths=plugin_paths,
            mcp_servers=mcp_servers,
            context_compaction_enabled=context_compaction_enabled,
            max_context_messages=max_context_messages,
            max_context_chars=max_context_chars,
            compaction_summary_chars=compaction_summary_chars,
            diagnostic_distillation_enabled=diagnostic_distillation_enabled,
            diagnostic_distillation_max_chars=diagnostic_distillation_max_chars,
            decoupled_context_enabled=decoupled_context_enabled,
            remote_ssh=remote_ssh,
            ssh_key_path=ssh_key_path,
            ssh_known_hosts=ssh_known_hosts,
            ssh_allow_unknown_keys=ssh_allow_unknown_keys,
            container=container,
            secret_scan_mode=secret_scan_mode,
            mode=mode,
            fallback_provider=fallback_provider,
            fallback_model=fallback_model,
            sandbox=sandbox,
        )

    # ── Validation ────────────────────────────────────────────────────

    def validate(self) -> None:
        """Validate cross-field limits.

        Kept for backwards compatibility with the previous dataclass API.
        Pydantic already validates individual fields at construction time; this
        adds the cross-field checks callers relied on.
        """
        if self.max_iterations < 1 or self.max_tool_calls < 1 or self.max_context_files < 1:
            raise ValueError("Iteration, tool-call, and context-file limits must be positive")
        if (
            self.max_context_messages < CONTEXT_MIN_MESSAGES
            or self.max_context_chars < CONTEXT_MIN_CHARS
            or self.compaction_summary_chars < CONTEXT_SUMMARY_MIN_CHARS
        ):
            raise ValueError("Context compaction limits are too small")
        if self.diagnostic_distillation_max_chars < DIAGNOSTIC_DISTILLATION_MIN_CHARS:
            raise ValueError("Diagnostic distillation character limit is too small")
        if self.command_timeout <= 0 or self.max_file_bytes <= 0:
            raise ValueError("Timeout and file-size limits must be positive")
        if self.policy_preset:
            from devx.harness.permissions import POLICY_PRESETS

            if self.policy_preset not in POLICY_PRESETS:
                raise ValueError(
                    f"Unknown policy preset {self.policy_preset!r}; "
                    f"choose from {', '.join(sorted(POLICY_PRESETS))}"
                )
        if self.secret_scan_mode not in {"off", "warn", "redact", "block"}:
            raise ValueError(
                f"secret_scan_mode must be one of off, warn, redact, block; got {self.secret_scan_mode!r}"
            )

    # ── Convenience accessor ──────────────────────────────────────────

    def get(self, name: str, default: Any = None) -> Any:
        """Get a setting by name, for backwards compatibility."""
        return getattr(self, name, default)


# ── Backwards-compatibility alias ─────────────────────────────────────

DevState = Settings  # type: ignore[assignment]