"""Tests for the local workspace backend (``devx.harness.local_backend``)."""

from __future__ import annotations

import hashlib

import pytest

from devx.harness.local_backend import LocalWorkspaceBackend
from devx.harness.runtime import CancellationToken, RunCancelled


@pytest.fixture
def backend(tmp_path):
    return LocalWorkspaceBackend(tmp_path)


class TestRoot:
    def test_root_is_resolved_workspace_path(self, tmp_path):
        backend = LocalWorkspaceBackend(tmp_path)
        assert backend.root == str(tmp_path.resolve())

    def test_is_remote_false(self, backend):
        assert backend.is_remote is False


class TestResolve:
    def test_resolves_relative_paths(self, backend, tmp_path):
        assert backend._resolve("a/b.txt") == (tmp_path / "a" / "b.txt").resolve()

    def test_escaped_relative_path_rejected(self, backend, tmp_path):
        with pytest.raises(PermissionError, match="outside workspace"):
            backend._resolve("../escape.txt")

    def test_absolute_outside_path_rejected(self, backend, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        with pytest.raises(PermissionError, match="outside workspace"):
            backend._resolve(str(outside))


class TestFileOps:
    def test_write_then_read_round_trip(self, backend, tmp_path):
        backend.write_bytes("nested/f.txt", b"hello")
        assert backend.read_bytes("nested/f.txt") == b"hello"
        assert (tmp_path / "nested" / "f.txt").is_file()
        assert not list((tmp_path / "nested").glob(".f.txt.devx-tmp-*"))

    def test_read_bytes_max_bytes(self, backend):
        backend.write_bytes("f.txt", b"0123456789")
        assert backend.read_bytes("f.txt", max_bytes=4) == b"0123"

    def test_read_missing_file_raises(self, backend):
        with pytest.raises(FileNotFoundError):
            backend.read_bytes("missing.txt")

    def test_overwrite_existing_file(self, backend):
        backend.write_bytes("f.txt", b"one")
        backend.write_bytes("f.txt", b"two")
        assert backend.read_bytes("f.txt") == b"two"

    def test_exists_is_file_is_dir(self, backend, tmp_path):
        backend.write_bytes("a.txt", b"x")
        (tmp_path / "sub").mkdir()
        assert backend.exists("a.txt")
        assert backend.is_file("a.txt")
        assert not backend.is_dir("a.txt")
        assert backend.is_dir("sub")
        assert not backend.exists("nope")

    def test_exists_ignores_escape_errors(self, backend):
        assert backend.exists("../escape.txt") is False

    def test_stat_size(self, backend):
        backend.write_bytes("f.txt", b"12345")
        assert backend.stat_size("f.txt") == 5

    def test_remove(self, backend, tmp_path):
        backend.write_bytes("f.txt", b"x")
        (tmp_path / "sub").mkdir()
        assert backend.remove("f.txt") is True
        assert backend.remove("f.txt") is False
        assert backend.remove("sub") is False  # directories are not removed

    def test_list_dir(self, backend, tmp_path):
        (tmp_path / "sub").mkdir()
        backend.write_bytes("a.txt", b"x")
        assert sorted(backend.list_dir(".")) == ["a.txt", "sub"]

    def test_list_dir_non_directory_returns_empty(self, backend):
        backend.write_bytes("a.txt", b"x")
        assert backend.list_dir("a.txt") == []


class TestWalkFiles:
    def test_walk_files_filters_ignored_and_hidden_dirs(self, backend, tmp_path):
        for name in (".git", "node_modules", "__pycache__", ".hidden", "sub"):
            (tmp_path / name).mkdir()
        backend.write_bytes("a.py", b"")
        backend.write_bytes("b.txt", b"")
        backend.write_bytes("c.py", b"")
        backend.write_bytes("sub/d.py", b"")
        backend.write_bytes(".git/ignored.py", b"")
        backend.write_bytes("node_modules/x.js", b"")
        backend.write_bytes("__pycache__/y.py", b"")
        backend.write_bytes(".hidden/z.py", b"")
        files = backend.walk_files(code_exts={".py"})
        assert files == ["a.py", "c.py", "sub/d.py"]

    def test_walk_files_default_includes_all_extensions(self, backend, tmp_path):
        backend.write_bytes("a.py", b"x")
        backend.write_bytes("notes.md", b"x")
        assert backend.walk_files() == ["a.py", "notes.md"]

    def test_walk_files_max_files(self, backend, tmp_path):
        for i in range(5):
            backend.write_bytes(f"f{i}.py", b"x")
        assert len(backend.walk_files(max_files=2, code_exts={".py"})) == 2

    def test_walk_files_skips_large_files(self, backend):
        backend.write_bytes("big.py", b"x" * (1_000_001))
        backend.write_bytes("small.py", b"x")
        assert backend.walk_files(code_exts={".py"}) == ["small.py"]

    def test_walk_files_sorts_by_depth_then_name(self, backend, tmp_path):
        (tmp_path / "z").mkdir()
        backend.write_bytes("z/a.py", b"x")
        backend.write_bytes("b.py", b"x")
        assert backend.walk_files(code_exts={".py"}) == ["b.py", "z/a.py"]


class TestHashing:
    def test_file_hash_matches_sha256(self, backend):
        backend.write_bytes("f.py", b"hash me")
        assert backend.file_hash("f.py") == hashlib.sha256(b"hash me").hexdigest()

    def test_file_hash_missing_returns_none(self, backend):
        assert backend.file_hash("missing") is None


class TestRunCommand:
    def test_ok_command(self, backend):
        result = backend.run_command(
            ["python", "-c", "print('ran-ok')"], timeout=10,
            cancellation=CancellationToken(), max_bytes=50_000,
        )
        assert result.ok is True
        assert result.exit_code == 0
        assert "ran-ok" in result.output

    def test_failing_command(self, backend):
        result = backend.run_command(
            ["python", "-c", "import sys; sys.exit(7)"], timeout=10,
            cancellation=CancellationToken(), max_bytes=50_000,
        )
        assert result.ok is False
        assert result.exit_code == 7

    def test_missing_binary_returns_error_result(self, backend):
        result = backend.run_command(
            ["definitely-not-a-real-binary-xyz"], timeout=10,
            cancellation=CancellationToken(), max_bytes=50_000,
        )
        assert result.ok is False
        assert result.output

    def test_output_is_bounded_by_max_bytes(self, backend):
        script = "print('x' * 200_000)"
        result = backend.run_command(
            ["python", "-c", script], timeout=10,
            cancellation=CancellationToken(), max_bytes=1_000,
        )
        assert len(result.output) <= 1_000

    def test_timeout_marks_command_as_failed(self, backend):
        result = backend.run_command(
            ["python", "-c", "import time; time.sleep(10)"], timeout=0.3,
            cancellation=CancellationToken(), max_bytes=50_000,
        )
        assert result.ok is False
        assert "Command timed out" in result.output
        assert result.exit_code != 0

    def test_cancelled_command_raises(self, backend):
        cancellation = CancellationToken()
        cancellation.cancel()
        with pytest.raises(RunCancelled):
            backend.run_command(
                ["python", "-c", "import time; time.sleep(10)"], timeout=10,
                cancellation=cancellation, max_bytes=50_000,
            )

    def test_close_is_noop(self, backend):
        backend.close()