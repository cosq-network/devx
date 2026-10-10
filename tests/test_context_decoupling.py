from __future__ import annotations

from pathlib import Path

from devx.token_optim.context import decoupled_coder_context, generate_file_manifest


def test_generate_file_manifest_extracts_structure(tmp_path: Path):
    file1 = tmp_path / "service.py"
    file1.write_text(
        '"""Service module docstring."""\n\n'
        "class OrderService:\n"
        "    def __init__(self):\n"
        "        pass\n\n"
        "    def create_order(self, item_id: str):\n"
        "        return item_id\n",
        encoding="utf-8",
    )
    file2 = tmp_path / "helpers.py"
    file2.write_text(
        "def format_currency(amount: float) -> str:\n"
        "    return f'${amount:.2f}'\n",
        encoding="utf-8",
    )

    manifest = generate_file_manifest([file1, file2])
    assert "Target Workspace Files:" in manifest
    assert str(file1) in manifest
    assert str(file2) in manifest
    assert "class OrderService:" in manifest
    assert "def create_order(self, item_id: str):" in manifest
    assert "def format_currency(amount: float) -> str:" in manifest
    # Whole bodies like 'pass' or return statements don't need to be fully repeated
    assert manifest.startswith("Target Workspace Files:")


def test_decoupled_coder_context_includes_paths_and_instructions(tmp_path: Path):
    f1 = tmp_path / "app.py"
    f2 = tmp_path / "config.py"

    context = decoupled_coder_context([f1, f2])
    assert "Target Files in Scope:" in context
    assert str(f1) in context
    assert str(f2) in context
    assert "read_file" in context


def test_decoupled_coder_context_handles_empty_list():
    context = decoupled_coder_context([])
    assert "No specific files pre-selected" in context
