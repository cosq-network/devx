from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any


class PermissionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Approval:
    action: str
    target: str
    reason: str


@dataclass(frozen=True)
class PermissionRule:
    """One ordered project permission rule.

    Rules intentionally use a small, predictable model: the last matching
    rule wins and the effect is one of ``allow``, ``ask``, or ``deny``.
    ``resource`` is matched against both the workspace-relative and absolute
    representation of a path, and against the normalized command text for
    shell actions.
    """

    action: str
    resource: str
    effect: str

    @classmethod
    def from_mapping(cls, value: Any) -> PermissionRule:
        if not isinstance(value, dict):
            raise TypeError("Permission rules must be objects")
        action = str(value.get("action", "")).strip().lower()
        resource = str(value.get("resource", "")).strip()
        effect = str(value.get("effect", "")).strip().lower()
        if not action or not resource:
            raise ValueError("Permission rules require action and resource")
        if effect not in {"allow", "ask", "deny"}:
            raise ValueError("Permission rule effect must be allow, ask, or deny")
        return cls(action, resource, effect)


@dataclass(frozen=True)
class PolicyPreset:
    """A named bundle of governance constraints.

    Presets are deliberately small and composable: they only toggle the
    hard constraints that cannot be expressed as path globs — whether the
    local machine may run commands at all (``remote_only``), whether the
    local filesystem may be written (``deny_local_writes``), and which
    paths always require approval (``protected_paths``).
    """

    name: str
    description: str
    remote_only: bool = False
    deny_local_writes: bool = False
    protected_paths: tuple[str, ...] = ()


POLICY_PRESETS: dict[str, PolicyPreset] = {
    "default": PolicyPreset(
        "default", "Prompt for edits and shell commands on the selected workspace."
    ),
    "remote_only": PolicyPreset(
        "remote_only",
        "Only the remote/container workspace may execute commands; the local machine is read-only.",
        remote_only=True,
    ),
    "deny_local_writes": PolicyPreset(
        "deny_local_writes",
        "Never modify the local filesystem; writes must target a remote/container workspace.",
        deny_local_writes=True,
    ),
    "protected": PolicyPreset(
        "protected",
        "Always require approval for writes to sensitive files (keys, env, secrets).",
        protected_paths=("*/.env*", "*.pem", "*.key", "*.p12", "secrets/**", "**/secrets/**"),
    ),
    "strict": PolicyPreset(
        "strict",
        "Remote execution only, no local writes; recommended for governed remote runs.",
        remote_only=True,
        deny_local_writes=True,
    ),
}


def preset_names() -> list[str]:
    return sorted(POLICY_PRESETS)



SYSTEM_COMMANDS = {"format", "diskpart", "cipher", "takeown", "icacls"}
DESTRUCTIVE_COMMANDS = {"del", "erase", "rm", "rmdir", "remove-item", "shutdown", "reboot"}
CHAIN_OPERATORS = {"&&", "||", ";", "|", "&"}
SHELL_FLAGS = {"-c", "/c", "/k", "-command"}
COMMAND_WRAPPERS = {"env", "command", "exec", "nice", "nohup", "sudo", "busybox"}
INLINE_DESTRUCTIVE = re.compile(
    r"(?i)(?:os\.(?:remove|unlink|rmdir|system|popen)|shutil\.rmtree|"
    r"subprocess\.(?:run|popen|call|check_call|check_output)|"
    r"(?:pathlib\.)?(?:PurePath|Path)\s*\([^)]*\)\s*\.\s*(?:unlink|rmdir|write_text|write_bytes|rename|replace)\s*\(|"
    r"\bopen\s*\([^)]*,\s*[\"']([^\"']*[wax+][^\"']*)[\"']\s*\)|"
    r"(?:remove-item|del|erase|rmdir|shutdown|reboot)\b)"
)


def _get_command_verbs(args: list[str]) -> list[str]:
    verbs: list[str] = []
    expect_verb = True
    for i, token in enumerate(args):
        cleaned = token.strip("\"'")
        if not cleaned:
            continue
        if expect_verb:
            cmd = Path(cleaned).name.lower().removesuffix(".exe")
            verbs.append(cmd)
            expect_verb = False
        elif cleaned in CHAIN_OPERATORS or cleaned.lower() == "sudo":
            expect_verb = True
        elif cleaned.lower() in SHELL_FLAGS and i + 1 < len(args):
            sub_str = args[i + 1].strip("\"'")
            try:
                sub_args = shlex.split(sub_str, posix=os.name != "nt")
            except ValueError:
                sub_args = sub_str.split()
            verbs.extend(_get_command_verbs(sub_args))
        elif verbs and verbs[-1] in COMMAND_WRAPPERS and not cleaned.startswith("-") and "=" not in cleaned:
            verbs.append(Path(cleaned).name.lower().removesuffix(".exe"))
    return verbs


class PermissionPolicy:
    def __init__(self, workspace: Path, approval_required: bool = True,
                 rules: Iterable[PermissionRule | dict[str, Any]] | None = None, *,
                 preset: str | None = None, remote_only: bool = False,
                 deny_local_writes: bool = False,
                 protected_paths: Iterable[str] | None = None):
        self.workspace = workspace.resolve()
        self.approval_required = approval_required
        self.rules = tuple(
            item if isinstance(item, PermissionRule) else PermissionRule.from_mapping(item)
            for item in (rules or ())
        )
        protected = tuple(protected_paths or ())
        if preset:
            selected = POLICY_PRESETS.get(preset)
            if selected is None:
                raise ValueError(
                    f"Unknown policy preset {preset!r}; choose from {', '.join(preset_names())}"
                )
            remote_only = remote_only or selected.remote_only
            deny_local_writes = deny_local_writes or selected.deny_local_writes
            protected = tuple(dict.fromkeys(selected.protected_paths + protected))
        self.preset = preset
        self.remote_only = remote_only
        self.deny_local_writes = deny_local_writes
        self.protected_paths = protected

    @classmethod
    def from_settings(cls, settings: Any, workspace: Path | None = None) -> "PermissionPolicy":
        """Build a policy from a settings object, honoring ``policy_preset``."""
        return cls(
            workspace if workspace is not None else settings.workspace,
            getattr(settings, "approval_required", True),
            getattr(settings, "permission_rules", ()),
            preset=getattr(settings, "policy_preset", "") or None,
        )

    @staticmethod
    def _action(action: str) -> str:
        """Normalize action name: 'write' -> 'edit', 'command' -> 'shell', else unchanged."""
        return {"write": "edit", "command": "shell"}.get(action.strip().lower(), action.strip().lower())

    def _targets(self, target: str | Path) -> tuple[str, ...]:
        """Return candidate target strings for rule matching.
        
        Candidates include the raw text, normalized forward-slash path,
        and (for absolute paths) the workspace-relative path and absolute form.
        Uses dict.fromkeys to deduplicate while preserving order.
        """
        text = str(target)
        candidates = [text, text.replace("\\", "/")]
        try:
            path = Path(text)
            if path.is_absolute():
                relative = path.resolve(strict=False).relative_to(self.workspace)
                candidates.extend((relative.as_posix(), str(relative)))
        except (OSError, ValueError):
            pass
        return tuple(dict.fromkeys(candidates))

    def effect(self, action: str, target: str | Path) -> str | None:
        """Return the last matching configured effect, if any.
        
        Evaluates rules in order; the last matching rule wins.
        ``resource`` is matched against both workspace-relative and absolute
        path representations, and against the normalized command text for
        shell actions.
        """
        normalized_action = self._action(action)
        result: str | None = None
        for rule in self.rules:
            rule_action = self._action(rule.action)
            if rule_action not in {normalized_action, "*"}:
                continue
            if any(fnmatchcase(candidate, rule.resource) for candidate in self._targets(target)):
                result = rule.effect
        return result

    def requires_approval(self, action: str, target: str | Path) -> bool:
        """Whether an action requires user approval based on the permission policy.
        
        Returns ``False`` if the policy ``effect`` is ``allow`` or ``deny``.
        Returns ``True`` if the effect is ``ask`` (user prompt needed) or if
        ``approval_required`` is ``True`` and the action is not a ``read`` action.
        """
        effect = self.effect(action, target)
        if effect in {"allow", "deny"}:
            return False
        return effect == "ask" or (self.approval_required and self._action(action) != "read")

    def _enforce(self, action: str, target: str | Path, *, approved: bool = False) -> None:
        effect = self.effect(action, target)
        if effect == "deny":
            raise PermissionError(f"Permission denied for {self._action(action)}: {target}")
        if effect == "ask" and not approved:
            raise PermissionError(f"{self._action(action).capitalize()} requires explicit approval")
        if effect is None and self.approval_required and self._action(action) in {"edit", "shell"} and not approved:
            raise PermissionError(f"{self._action(action).capitalize()} requires explicit approval")

    def _matches_protected(self, target: str | Path) -> bool:
        """Whether a target matches a configured protected-path pattern."""
        if not self.protected_paths:
            return False
        return any(
            fnmatchcase(candidate, pattern)
            for candidate in self._targets(target)
            for pattern in self.protected_paths
        )

    def path(self, path: Path, *, write: bool = False, approved: bool = False, remote: bool = False) -> Path:
        if write and self.deny_local_writes and not remote:
            raise PermissionError(
                "deny_local_writes policy forbids writing to the local filesystem"
            )
        candidate = path.expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError(f"Path is outside workspace: {candidate}") from exc
        if write and not approved and self._matches_protected(candidate):
            raise PermissionError(
                f"Edit requires explicit approval (protected path): {candidate}"
            )
        self._enforce("edit" if write else "read", candidate, approved=approved)
        return candidate

    def command(self, command: list[str] | str, *, approved: bool = False, remote: bool = False) -> list[str]:
        if self.remote_only and not remote:
            raise PermissionError(
                "remote_only policy forbids executing commands on the local machine"
            )
        args = shlex.split(command, posix=os.name != "nt") if isinstance(command, str) else list(command)
        if not args:
            raise PermissionError("Empty command")
        command_text = " ".join(args)
        if INLINE_DESTRUCTIVE.search(command_text):
            raise PermissionError("Destructive command is not permitted")
        verbs = _get_command_verbs(args)
        # Wrapper options (for example ``sudo -u user rm``) can obscure the
        # executable from a positional parser. Inspect every token as a
        # conservative second pass so wrappers cannot bypass the deny-list.
        if any(wrapper in verbs for wrapper in COMMAND_WRAPPERS):
            token_names = {
                Path(token.strip("\"'")).name.lower().removesuffix(".exe")
                for token in args
                if token.strip("\"'") and "=" not in token
            }
            verbs.extend(token_names)
        if any(v in SYSTEM_COMMANDS for v in verbs):
            raise PermissionError("System-level command is not permitted")
        if any(v in DESTRUCTIVE_COMMANDS for v in verbs):
            raise PermissionError("Destructive command is not permitted")
        self._enforce("shell", " ".join(args), approved=approved)
        return args


def policy_for_preset(
    name: str,
    workspace: Path,
    *,
    approval_required: bool = True,
    rules: Iterable[PermissionRule | dict[str, Any]] | None = None,
) -> PermissionPolicy:
    """Build a :class:`PermissionPolicy` for a named preset."""
    if name not in POLICY_PRESETS:
        raise ValueError(f"Unknown policy preset {name!r}; choose from {', '.join(preset_names())}")
    return PermissionPolicy(workspace, approval_required, rules, preset=name)

