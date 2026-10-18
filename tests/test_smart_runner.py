from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from devx.config import Settings
from devx.test_engine.smart_runner import SmartTestRunner


def test_smart_runner_tia_impact(tmp_path: Path):
    settings = Settings(workspace=tmp_path, api_key="sk-test")
    runner = SmartTestRunner(settings)

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)

    (src_dir / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (tests_dir / "test_calc.py").write_text("from calc import add\ndef test_add(): assert add(1, 2) == 3\n", encoding="utf-8")

    impacted = runner.get_impacted_tests(["src/calc.py"])
    assert len(impacted) >= 1
    assert any("test_calc.py" in t for t in impacted)

    cmd = runner.build_targeted_test_command(impacted)
    assert cmd is not None
    assert "pytest" in cmd
    assert "test_calc.py" in cmd


def test_smart_runner_flakiness_check(tmp_path: Path):
    settings = Settings(workspace=tmp_path, api_key="sk-test")
    runner = SmartTestRunner(settings)

    # Mock subprocess.run to simulate consistent pass
    mock_res_ok = MagicMock()
    mock_res_ok.returncode = 0
    mock_res_ok.stdout = "1 passed"
    mock_res_ok.stderr = ""

    with patch("subprocess.run", return_value=mock_res_ok):
        rep = runner.check_flakiness("tests/test_calc.py", runs=3)

    assert rep.total_runs == 3
    assert rep.passes == 3
    assert rep.failures == 0
    assert rep.is_flaky is False
    assert rep.pass_rate == 100.0


def test_smart_runner_preflight(tmp_path: Path, monkeypatch):
    settings = Settings(workspace=tmp_path, api_key="sk-test")
    runner = SmartTestRunner(settings)

    monkeypatch.setenv("DATABASE_URL", "postgres://localhost/test")

    # Check env var success and missing env var
    rep = runner.run_preflight(required_envs=["DATABASE_URL", "MISSING_API_KEY"])
    assert rep.ok is False
    assert len(rep.checks) == 2
    assert rep.checks[0]["name"] == "env:DATABASE_URL"
    assert rep.checks[0]["ok"] is True
    assert rep.checks[1]["name"] == "env:MISSING_API_KEY"
    assert rep.checks[1]["ok"] is False
