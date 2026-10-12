"""Tests for the conservative test-command detector
(``devx.harness.testing.detect_test_command``), both local and remote."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devx.harness.testing import detect_test_command


class FakeBackend:
    """In-memory WorkspaceBackend over a path -> contents mapping."""

    def __init__(self, files=None, dirs=None, *, is_remote=True):
        self.files = dict(files or {})
        self.dirs = set(dirs or [])
        self.is_remote = is_remote

    def exists(self, rel_path):
        return rel_path in self.files or rel_path in self.dirs

    def is_file(self, rel_path):
        return rel_path in self.files

    def is_dir(self, rel_path):
        return rel_path in self.dirs

    def read_bytes(self, rel_path, max_bytes=None):
        data = self.files[rel_path]
        return data if max_bytes is None else data[:max_bytes]


def remote_backend(**kwargs):
    return FakeBackend(**{**{"is_remote": True}, **kwargs})


# ── Local detection (no backend) ─────────────────────────────────────────

@pytest.mark.parametrize(
    "marker",
    ["pyproject.toml", "pytest.ini", "tox.ini", "noxfile.py"],
)
def test_local_python_marker_files(tmp_path, marker):
    (tmp_path / marker).write_text("[project]", encoding="utf-8")
    assert detect_test_command(tmp_path) == "python -m pytest -q"


def test_local_tests_directory(tmp_path):
    (tmp_path / "tests").mkdir()
    assert detect_test_command(tmp_path) == "python -m pytest -q"


def test_local_npm_without_lockfile(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
    )
    assert detect_test_command(tmp_path) == "npm test"


def test_local_pnpm_preferred(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8"
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9", encoding="utf-8")
    assert detect_test_command(tmp_path) == "pnpm test"


def test_local_yarn_preferred(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
    )
    (tmp_path / "yarn.lock").write_text("lock", encoding="utf-8")
    assert detect_test_command(tmp_path) == "yarn test"


def test_local_bun_preferred(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "test"}}), encoding="utf-8"
    )
    (tmp_path / "bun.lockb").write_text("lock", encoding="utf-8")
    assert detect_test_command(tmp_path) == "bun test"


def test_local_package_json_without_test_script_falls_through(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"build": "tsc"}}), encoding="utf-8"
    )
    assert detect_test_command(tmp_path) is None


def test_local_bun_lock_new_format(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "test"}}), encoding="utf-8"
    )
    (tmp_path / "bun.lock").write_text("lock", encoding="utf-8")
    assert detect_test_command(tmp_path) == "bun test"


def test_local_invalid_package_json_ignored(tmp_path):
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    assert detect_test_command(tmp_path) is None


def test_local_makefile_with_test_target(tmp_path):
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    assert detect_test_command(tmp_path) == "make test"


def test_local_makefile_without_test_target(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\tgo build\n", encoding="utf-8")
    assert detect_test_command(tmp_path) is None


def test_local_makefile_read_error_ignored(tmp_path, monkeypatch):
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    original = Path.read_text

    def boom(self, encoding=None):
        raise OSError("read denied")

    monkeypatch.setattr(Path, "read_text", boom)
    assert detect_test_command(tmp_path) is None
    monkeypatch.setattr(Path, "read_text", original)


def test_local_cargo_project(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert detect_test_command(tmp_path) == "cargo test"


def test_local_go_project(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    assert detect_test_command(tmp_path) == "go test ./..."


def test_no_markers_returns_none(tmp_path):
    assert detect_test_command(tmp_path) is None


# ── Remote detection (backend provided) ──────────────────────────────────

def test_remote_python_markers():
    for marker in ("pyproject.toml", "pytest.ini", "tox.ini", "noxfile.py"):
        backend = remote_backend(files={marker: b"[project]"})
        assert detect_test_command(Path("."), backend) == "python -m pytest -q"


def test_remote_tests_directory():
    backend = remote_backend(dirs={"tests"})
    assert detect_test_command(Path("."), backend) == "python -m pytest -q"


def test_remote_npm_without_lockfile():
    backend = remote_backend(files={"package.json": b'{"scripts": {"test": "jest"}}'})
    assert detect_test_command(Path("."), backend) == "npm test"


def test_remote_pnpm_preferred():
    backend = remote_backend(
        files={
            "package.json": b'{"scripts": {"test": "vitest"}}',
            "pnpm-lock.yaml": b"lock",
        }
    )
    assert detect_test_command(Path("."), backend) == "pnpm test"


def test_remote_yarn_preferred():
    backend = remote_backend(
        files={"package.json": b'{"scripts": {"test": "jest"}}', "yarn.lock": b"lock"}
    )
    assert detect_test_command(Path("."), backend) == "yarn test"


def test_remote_bun_preferred():
    backend = remote_backend(
        files={"package.json": b'{"scripts": {"test": "test"}}', "bun.lock": b"lock"}
    )
    assert detect_test_command(Path("."), backend) == "bun test"


def test_remote_invalid_package_json_falls_through():
    backend = remote_backend(files={"package.json": b"{bad json"})
    assert detect_test_command(Path("."), backend) is None


def test_remote_makefile_with_test_target():
    backend = remote_backend(files={"Makefile": b"test:\n\tpytest\n"})
    assert detect_test_command(Path("."), backend) == "make test"


def test_remote_makefile_without_test_target():
    backend = remote_backend(files={"Makefile": b"build:\n\tgo build\n"})
    assert detect_test_command(Path("."), backend) is None


def test_remote_cargo_project():
    backend = remote_backend(files={"Cargo.toml": b"[package]\n"})
    assert detect_test_command(Path("."), backend) == "cargo test"


def test_remote_go_project():
    backend = remote_backend(files={"go.mod": b"module x\n"})
    assert detect_test_command(Path("."), backend) == "go test ./..."


def test_remote_no_markers_returns_none():
    backend = remote_backend(files={}, dirs={})
    assert detect_test_command(Path("."), backend) is None


def test_non_remote_backend_uses_local_logic(tmp_path):
    class NotRemoteBackend:
        is_remote = False

        def exists(self, rel):
            return False

        def is_file(self, rel):
            return False

        def is_dir(self, rel):
            return False

        def read_bytes(self, rel, max_bytes=None):
            return b""

    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    assert detect_test_command(tmp_path, NotRemoteBackend()) == "python -m pytest -q"