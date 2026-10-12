"""``devx lab`` tests: start/stop against a mocked Docker CLI.

Real containers are exercised by the opt-in SSH e2e suite; here we verify the
orchestration logic and CLI plumbing with a fake ``subprocess.run``.
"""

import subprocess

from typer.testing import CliRunner

import devx.harness.lab as lab
from devx.cli.app import app

runner = CliRunner()


def _fake_run():
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "port"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"127.0.0.1:49153\n", stderr=b"")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    return fake_run


def test_start_lab_returns_connection_details(monkeypatch):
    monkeypatch.setattr(lab, "_wait_for_port", lambda *a, **k: None)
    monkeypatch.setattr(lab.subprocess, "run", _fake_run())
    info = lab.start_lab()
    assert info["name"].startswith("devx-lab-")
    assert info["port"] == "49153"
    assert info["ssh_uri"].startswith("ssh://devx@127.0.0.1:49153/config/repo")
    assert info["container_uri"].startswith("container://devx-lab-")
    assert info["remote_path"] == "/config/repo"


def test_start_lab_without_docker_raises(monkeypatch):
    monkeypatch.setattr(lab, "docker_available", lambda: False)
    try:
        lab.start_lab()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "Docker is required" in str(exc)


def test_stop_lab_removes_container(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(lab.subprocess, "run", fake_run)
    lab.stop_lab("devx-lab-abc")
    assert ["docker", "rm", "-f", "devx-lab-abc"] in calls


def test_cli_lab_up_prints_uris(monkeypatch):
    monkeypatch.setattr(lab, "_wait_for_port", lambda *a, **k: None)
    monkeypatch.setattr(lab.subprocess, "run", _fake_run())
    result = runner.invoke(app, ["lab", "up"])
    assert result.exit_code == 0
    assert "Started disposable remote workspace" in result.output
    assert "devx ask --remote" in result.output
    assert "devx doctor --remote" in result.output

    name = next(
        line.split()[-1]
        for line in result.output.splitlines()
        if line.startswith("Started disposable")
    )
    result_down = runner.invoke(app, ["lab", "down", name])
    assert result_down.exit_code == 0
    assert f"Stopped lab container {name}" in result_down.output


def test_cli_lab_up_json(monkeypatch):
    monkeypatch.setattr(lab, "_wait_for_port", lambda *a, **k: None)
    monkeypatch.setattr(lab.subprocess, "run", _fake_run())
    result = runner.invoke(app, ["lab", "up", "--json"])
    assert result.exit_code == 0
    import json

    payload = json.loads(result.output)
    assert payload["ssh_uri"].startswith("ssh://")


def test_cli_lab_up_without_docker(monkeypatch):
    monkeypatch.setattr(lab, "docker_available", lambda: False)
    result = runner.invoke(app, ["lab", "up"])
    assert result.exit_code == 2
    assert "Docker is required" in result.output


def test_cli_lab_down_requires_name():
    result = runner.invoke(app, ["lab", "down"])
    assert result.exit_code == 2
    assert "Provide the container name" in result.output


def test_cli_lab_unknown_action():
    result = runner.invoke(app, ["lab", "frobnicate"])
    assert result.exit_code == 2
    assert "devx lab up | devx lab down" in result.output