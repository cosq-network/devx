import hashlib
from pathlib import Path

import pytest

from devx.config import Settings
from devx.harness.permissions import (
    POLICY_PRESETS,
    PermissionPolicy,
    policy_for_preset,
    preset_names,
)
from devx.harness.permissions import PermissionError as PolicyPermissionError
from devx.harness.tools import WorkspaceTools


class RemoteStub:
    """Minimal remote-like backend for exercising policy decisions."""

    is_remote = True

    def __init__(self, root: str = "/remote"):
        self.root = root
        self.files: dict[str, bytes] = {}

    def file_hash(self, rel):
        return hashlib.sha256(self.files[rel]).hexdigest() if rel in self.files else None

    def write_bytes(self, rel, data):
        self.files[rel] = data

    def read_bytes(self, rel, max_bytes=None):
        data = self.files[rel]
        return data if max_bytes is None else data[:max_bytes]

    def exists(self, rel):
        return rel in self.files

    def stat_size(self, rel):
        return len(self.files[rel])

    def remove(self, rel):
        self.files.pop(rel, None)
        return True

    def walk_files(self, max_files=200, code_exts=None):
        return []

    def run_command(self, *args, **kwargs):  # pragma: no cover - not exercised here
        raise AssertionError("not expected")

    def close(self):
        return None


# ── presets ────────────────────────────────────────────────────────────────


def test_known_presets_are_exposed():
    assert set(preset_names()) == {"default", "remote_only", "deny_local_writes", "protected", "strict"}
    assert POLICY_PRESETS["strict"].remote_only is True
    assert POLICY_PRESETS["strict"].deny_local_writes is True
    assert POLICY_PRESETS["protected"].protected_paths


def test_unknown_preset_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="Unknown policy preset"):
        PermissionPolicy(tmp_path, preset="bogus")
    with pytest.raises(ValueError):
        policy_for_preset("bogus", tmp_path)


def test_remote_only_preset_blocks_local_commands(tmp_path: Path):
    policy = policy_for_preset("remote_only", tmp_path)
    with pytest.raises(PolicyPermissionError, match="remote_only"):
        policy.command("echo hi", approved=True)
    # The same command is fine when it runs on a remote workspace.
    assert policy.command("echo hi", approved=True, remote=True) == ["echo", "hi"]


def test_deny_local_writes_preset_blocks_local_writes(tmp_path: Path):
    policy = policy_for_preset("deny_local_writes", tmp_path)
    with pytest.raises(PolicyPermissionError, match="deny_local_writes"):
        policy.path(tmp_path / "a.py", write=True, approved=True)
    # Remote writes are allowed.
    assert policy.path(tmp_path / "a.py", write=True, approved=True, remote=True)


def test_strict_preset_combines_constraints(tmp_path: Path):
    policy = policy_for_preset("strict", tmp_path)
    assert policy.remote_only is True
    assert policy.deny_local_writes is True
    with pytest.raises(PolicyPermissionError):
        policy.command("echo hi", approved=True)


def test_default_preset_is_permissive(tmp_path: Path):
    policy = policy_for_preset("default", tmp_path)
    assert policy.command("echo hi", approved=True) == ["echo", "hi"]


def test_protected_paths_force_approval_even_when_approval_disabled(tmp_path: Path):
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=secret")
    policy = PermissionPolicy(tmp_path, approval_required=False, preset="protected")

    with pytest.raises(PolicyPermissionError, match="protected"):
        policy.path(secret, write=True, approved=False)
    # Approval lets it through ...
    policy.path(secret, write=True, approved=True)
    # ... while ordinary files need no approval at all.
    assert policy.path(tmp_path / "main.py", write=True, approved=False)


def test_protected_paths_do_not_block_reads(tmp_path: Path):
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=secret")
    policy = PermissionPolicy(tmp_path, preset="protected")
    assert policy.path(secret, write=False)


def test_from_settings_applies_preset(tmp_path: Path):
    settings = Settings(workspace=tmp_path, policy_preset="strict")
    policy = PermissionPolicy.from_settings(settings)
    assert policy.remote_only is True
    assert policy.deny_local_writes is True


# ── WorkspaceTools integration ─────────────────────────────────────────────


def test_deny_local_writes_blocks_local_write_through_tools(tmp_path: Path):
    settings = Settings(workspace=tmp_path, policy_preset="deny_local_writes")
    tools = WorkspaceTools(PermissionPolicy.from_settings(settings))
    with pytest.raises(PolicyPermissionError):
        tools.write("a.py", "hello", approved=True)


def test_deny_local_writes_allows_remote_write_through_tools(tmp_path: Path):
    settings = Settings(workspace=tmp_path, policy_preset="deny_local_writes")
    tools = WorkspaceTools(PermissionPolicy.from_settings(settings), backend=RemoteStub())
    result = tools.write("a.py", "hello", approved=True)
    assert result.ok is True


def test_remote_only_blocks_local_command_through_tools(tmp_path: Path):
    settings = Settings(workspace=tmp_path, policy_preset="remote_only")
    tools = WorkspaceTools(PermissionPolicy.from_settings(settings))
    result = tools.run("echo hi", approved=True)
    assert result.ok is False
    assert "remote_only" in result.output.lower()


# ── settings plumbing ──────────────────────────────────────────────────────


def test_settings_load_reads_preset_and_container(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DEVX_POLICY_PRESET", "strict")
    monkeypatch.setenv("DEVX_CONTAINER", "container://box/app")
    settings = Settings.load(workspace=tmp_path)
    assert settings.policy_preset == "strict"
    assert settings.container == "container://box/app"


def test_settings_validate_rejects_unknown_preset(tmp_path: Path):
    settings = Settings(workspace=tmp_path, policy_preset="bogus")
    with pytest.raises(ValueError, match="Unknown policy preset"):
        settings.validate()


# ── command tokenisation (Windows quote handling) ──────────────────────────


def test_split_command_args_posix_uses_shlex():
    from devx.harness.permissions import split_command_args

    assert split_command_args('echo "a b" "c d"', posix=True) == ["echo", "a b", "c d"]
    assert split_command_args("python -m pytest tests/test_x.py", posix=True) == [
        "python", "-m", "pytest", "tests/test_x.py",
    ]


def test_split_command_args_windows_strips_quotes_keeps_backslashes():
    """P0.4: on Windows, quoted args must become clean argv tokens and
    backslashes in paths must survive (shlex posix=True would eat them)."""
    from devx.harness.permissions import split_command_args

    args = split_command_args(
        'python -m pytest "tests/test x y.py" --opt "C:\\Users\\dev\\file.txt"',
        posix=False,
    )
    assert args == ["python", "-m", "pytest", "tests/test x y.py", "--opt", "C:\\Users\\dev\\file.txt"]

    # Single quotes are handled the same way.
    assert split_command_args("git commit -m 'hello world'", posix=False) == [
        "git", "commit", "-m", "hello world",
    ]


def test_deny_local_writes_policy_checks_command_with_quoted_args(tmp_path: Path):
    """Quoted arguments still flow through deny-list checks and parse cleanly."""
    policy = policy_for_preset("deny_local_writes", tmp_path)
    args = policy.command('echo "safe value"', approved=True)
    assert args == ["echo", "safe value"]
