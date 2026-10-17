"""Security-focused tests for token-optimizer context building.

Covers secret redaction before content reaches the model and symlink-safe
project discovery. All offline.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from devx.token_optim.context import discover_project_files, read_context

_FAKE_OPENAI_KEY = "sk-" + "A" * 32


def test_read_context_redacts_secrets_by_default(tmp_path: Path):
    secret_file = tmp_path / "config.py"
    secret_file.write_text(f'openai_key = "{_FAKE_OPENAI_KEY}"\n', encoding="utf-8")

    context = read_context([secret_file])

    assert _FAKE_OPENAI_KEY not in context
    assert "[REDACTED]" in context


def test_read_context_redact_false_keeps_raw_content(tmp_path: Path):
    secret_file = tmp_path / "config.py"
    secret_file.write_text(f'openai_key = "{_FAKE_OPENAI_KEY}"\n', encoding="utf-8")

    context = read_context([secret_file], redact=False)

    assert _FAKE_OPENAI_KEY in context


def test_discover_project_files_skips_symlinked_files(tmp_path: Path):
    outside = tmp_path / "outside_secret.py"
    outside.write_text("TOKEN = 'leak'\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "real.py").write_text("x = 1\n", encoding="utf-8")
    link = workspace / "linked.py"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")

    discovered = discover_project_files(workspace)
    names = {path.name for path in discovered}
    assert "real.py" in names
    assert "linked.py" not in names


def test_read_context_bounded(tmp_path: Path):
    path = tmp_path / "big.py"
    path.write_text("x" * 1000, encoding="utf-8")
    context = read_context([path], max_total_bytes=64)
    assert len(context.encode("utf-8")) <= 64


def test_discover_project_files_includes_allowed_dotfiles_and_skips_env(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (tmp_path / ".dockerignore").write_text(".git\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=12345\n", encoding="utf-8")
    (tmp_path / ".hidden_file.py").write_text("secret = 1\n", encoding="utf-8")

    discovered = discover_project_files(tmp_path)
    names = {p.name for p in discovered}

    assert "main.py" in names
    assert ".gitignore" in names
    assert ".dockerignore" in names
    assert ".env" not in names
    assert ".hidden_file.py" not in names

