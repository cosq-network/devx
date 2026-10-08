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
                 rules: Iterable[PermissionRule | dict[str, Any]] | None = None):
        self.workspace = workspace.resolve()
        self.approval_required = approval_required
        self.rules = tuple(
            item if isinstance(item, PermissionRule) else PermissionRule.from_mapping(item)
            for item in (rules or ())
        )

    @staticmethod
    def _action(action: str) -> str:
        return {"write": "edit", "command": "shell"}.get(action.strip().lower(), action.strip().lower())

    def _targets(self, target: str | Path) -> tuple[str, ...]:
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
        """Return the last matching configured effect, if any."""
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

    def path(self, path: Path, *, write: bool = False, approved: bool = False) -> Path:
        candidate = path.expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError(f"Path is outside workspace: {candidate}") from exc
        self._enforce("edit" if write else "read", candidate, approved=approved)
        return candidate

    def command(self, command: list[str] | str, *, approved: bool = False) -> list[str]:
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
