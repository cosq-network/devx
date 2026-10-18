from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from devx.cli.app import app


def test_cli_review_empty_diff(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEVX_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("DEVX_API_KEY", "sk-test")

    runner = CliRunner()
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(stdout="", returncode=0)
        res = runner.invoke(app, ["review"])

    assert res.exit_code == 0
    assert "No git diff detected to review" in res.output


def test_cli_design_diagram(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEVX_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("DEVX_API_KEY", "sk-test")

    (tmp_path / "app.py").write_text("import helper\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("pass\n", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(app, ["design", "--diagram", "flowchart"])
    assert res.exit_code == 0
    assert "flowchart TD" in res.output


def test_cli_design_adr(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEVX_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("DEVX_API_KEY", "sk-test")

    runner = CliRunner()
    res = runner.invoke(app, ["design", "--adr", "Event Driven Bus"])
    assert res.exit_code == 0
    assert "Created ADR-0001" in res.output


def test_cli_test_impact(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEVX_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("DEVX_API_KEY", "sk-test")

    src = tmp_path / "src"
    src.mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (src / "core.py").write_text("def run(): pass\n", encoding="utf-8")
    (tests / "test_core.py").write_text("import core\n", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(app, ["test", "impact", "src/core.py"])
    assert res.exit_code == 0
    assert "Impacted test files" in res.output
    assert "test_core.py" in res.output
