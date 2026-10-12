import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

from devx.cli.app import app
from devx.config import Settings
from devx.harness.approval import ApprovalManager
from devx.harness.ssh_backend import SshWorkspaceBackend
from devx.tui.app import DevTUI
from textual.widgets import Static


def test_settings_loads_remote_ssh_env_vars(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DEVX_REMOTE_SSH", "deploy@remote-server.net:/opt/app")
    monkeypatch.setenv("DEVX_SSH_KEY", "/home/user/.ssh/id_ed25519")

    settings = Settings.load(workspace=tmp_path)
    assert settings.remote_ssh == "deploy@remote-server.net:/opt/app"
    assert settings.ssh_key_path == "/home/user/.ssh/id_ed25519"

    # Legacy DEV_ fallback
    monkeypatch.delenv("DEVX_REMOTE_SSH")
    monkeypatch.delenv("DEVX_SSH_KEY")
    monkeypatch.setenv("DEV_REMOTE_SSH", "dev@legacy-box:/srv")
    monkeypatch.setenv("DEV_SSH_KEY", "/legacy/key")

    legacy_settings = Settings.load(workspace=tmp_path)
    assert legacy_settings.remote_ssh == "dev@legacy-box:/srv"
    assert legacy_settings.ssh_key_path == "/legacy/key"


def test_settings_loads_ssh_host_key_env_vars(monkeypatch, tmp_path: Path):
    """P0.3: known_hosts / allow-unknown-key settings load from env (with DEV_ fallback)."""
    monkeypatch.setenv("DEVX_SSH_KNOWN_HOSTS", "C:\\Users\\dev\\.ssh\\known_hosts")
    monkeypatch.setenv("DEVX_SSH_ALLOW_UNKNOWN_KEYS", "1")

    settings = Settings.load(workspace=tmp_path)
    assert settings.ssh_known_hosts == "C:\\Users\\dev\\.ssh\\known_hosts"
    assert settings.ssh_allow_unknown_keys is True

    monkeypatch.delenv("DEVX_SSH_KNOWN_HOSTS")
    monkeypatch.delenv("DEVX_SSH_ALLOW_UNKNOWN_KEYS")
    monkeypatch.setenv("DEV_SSH_KNOWN_HOSTS", "/legacy/known_hosts")
    monkeypatch.setenv("DEV_SSH_ALLOW_UNKNOWN_KEYS", "false")

    legacy_settings = Settings.load(workspace=tmp_path)
    assert legacy_settings.ssh_known_hosts == "/legacy/known_hosts"
    assert legacy_settings.ssh_allow_unknown_keys is False


def test_cli_ask_passes_remote_and_ssh_key_options(monkeypatch, tmp_path: Path):
    runner = CliRunner()
    captured_settings = []

    def fake_build_supervisor_graph(settings, approvals, files, run_id, session_id, plan_only):
        captured_settings.append(settings)
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "status": "completed",
            "result": "task completed",
            "plan": "sample plan",
            "messages": [],
            "iteration": 1,
        }
        return mock_graph

    monkeypatch.setattr("devx.agents.graph.build_supervisor_graph", fake_build_supervisor_graph)

    # Invoke devx ask with --remote and --ssh-key
    result = runner.invoke(
        app,
        [
            "ask",
            "check codebase",
            "--remote", "ssh://user:pass@remote.io:2222/var/app",
            "--ssh-key", "/keys/id_rsa",
            "--plan-only",
        ],
    )

    assert result.exit_code == 0
    assert len(captured_settings) == 1
    assert captured_settings[0].remote_ssh == "ssh://user:pass@remote.io:2222/var/app"
    assert captured_settings[0].ssh_key_path == "/keys/id_rsa"


def test_cli_ask_short_remote_flag(monkeypatch, tmp_path: Path):
    runner = CliRunner()
    captured_settings = []

    def fake_build_supervisor_graph(settings, approvals, files, run_id, session_id, plan_only):
        captured_settings.append(settings)
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "status": "completed",
            "result": "task completed",
            "plan": "sample plan",
            "messages": [],
            "iteration": 1,
        }
        return mock_graph

    monkeypatch.setattr("devx.agents.graph.build_supervisor_graph", fake_build_supervisor_graph)

    result = runner.invoke(
        app,
        [
            "ask",
            "check codebase",
            "-r", "alice@bastion:/home/alice/repo",
            "--plan-only",
        ],
    )

    assert result.exit_code == 0
    assert len(captured_settings) == 1
    assert captured_settings[0].remote_ssh == "alice@bastion:/home/alice/repo"


def test_supervisor_graph_initializes_ssh_backend(tmp_path: Path, monkeypatch):
    from devx.agents.langgraph_supervisor import build_langgraph_supervisor

    settings = Settings(
        remote_ssh="deploy@remote-host:/srv/app",
        ssh_key_path="/keys/prod.pem",
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
    )

    # Mock SshWorkspaceBackend so it doesn't attempt real network connection
    mock_backend = MagicMock(spec=SshWorkspaceBackend)
    mock_backend.is_remote = True
    mock_backend.root = "/srv/app"

    instantiated_cfgs = []

    def fake_ssh_backend_init(cfg):
        instantiated_cfgs.append(cfg)
        return mock_backend

    monkeypatch.setattr("devx.harness.ssh_backend.SshWorkspaceBackend", fake_ssh_backend_init)

    approvals = ApprovalManager(lambda req: True)
    # Build supervisor graph
    compiled = build_langgraph_supervisor(
        settings=settings,
        approvals=approvals,
        files=[],
        run_id="run-test-ssh",
        session_id="session-test-ssh",
        plan_only=True,
    )

    assert compiled is not None
    assert len(instantiated_cfgs) == 1
    cfg = instantiated_cfgs[0]
    assert cfg.host == "remote-host"
    assert cfg.username == "deploy"
    assert cfg.key_filename == "/keys/prod.pem"
    assert cfg.remote_path == "/srv/app"


def test_supervisor_graph_initializes_container_backend(tmp_path: Path, monkeypatch):
    from devx.agents.langgraph_supervisor import build_langgraph_supervisor
    from devx.harness.container_backend import ContainerWorkspaceBackend

    settings = Settings(
        container="container://api-box/app",
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
    )

    mock_backend = MagicMock(spec=ContainerWorkspaceBackend)
    mock_backend.is_remote = True
    mock_backend.root = "/app"
    instantiated_cfgs = []

    def fake_container_backend_init(cfg):
        instantiated_cfgs.append(cfg)
        return mock_backend

    monkeypatch.setattr(
        "devx.harness.container_backend.ContainerWorkspaceBackend", fake_container_backend_init
    )

    approvals = ApprovalManager(lambda req: True)
    compiled = build_langgraph_supervisor(
        settings=settings,
        approvals=approvals,
        files=[],
        run_id="run-test-container",
        session_id="session-test-container",
        plan_only=True,
    )

    assert compiled is not None
    assert len(instantiated_cfgs) == 1
    assert instantiated_cfgs[0].container == "api-box"
    assert instantiated_cfgs[0].remote_path == "/app"


def test_cli_ask_passes_container_option(monkeypatch, tmp_path: Path):
    runner = CliRunner()
    captured_settings = []

    def fake_build_supervisor_graph(settings, approvals, files, run_id, session_id, plan_only):
        captured_settings.append(settings)
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "status": "completed",
            "result": "task completed",
            "plan": "sample plan",
            "messages": [],
            "iteration": 1,
        }
        return mock_graph

    monkeypatch.setattr("devx.agents.graph.build_supervisor_graph", fake_build_supervisor_graph)

    result = runner.invoke(
        app,
        ["ask", "check codebase", "--container", "container://api-box/app", "--plan-only"],
    )

    assert result.exit_code == 0
    assert len(captured_settings) == 1
    assert captured_settings[0].container == "container://api-box/app"


def test_tui_slash_remote_connect_and_disconnect(tmp_path: Path, monkeypatch):
    settings = Settings(
        api_key="test-key",
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
    )
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))

    async def submit_command(pilot, command: str) -> None:
        await pilot.click("#input-bar")
        await pilot.press(*list(command), "enter")
        await pilot.pause()

    async def run():
        async with DevTUI(session_id="remote-test").run_test() as pilot:
            # 1. Test /remote shows usage
            await submit_command(pilot, "/remote")
            assert "Usage: /remote connect <ssh-uri> or /remote disconnect" in pilot.app._transcript[-1]

            # 2. Test /remote connect with valid URI
            await submit_command(pilot, "/remote connect deploy@remote-server:/var/www/app")
            assert settings.remote_ssh == "deploy@remote-server:/var/www/app"
            assert "Connected remote SSH workspace: deploy@remote-server:/var/www/app" in pilot.app._transcript[-1]
            status_text = str(pilot.app.query_one("#status-text", Static).render())
            assert "remote-server:/var/www/app" in status_text

            # 3. Test /remote disconnect reverts
            await submit_command(pilot, "/remote disconnect")
            assert settings.remote_ssh == ""
            assert "Disconnected remote workspace. Reverted to local workspace." in pilot.app._transcript[-1]
            status_text = str(pilot.app.query_one("#status-text", Static).render())
            assert "ready" in status_text

            # 4. Test /remote connect with invalid URI
            await submit_command(pilot, "/remote connect not-a-valid-uri")
            assert "Remote connection error:" in pilot.app._transcript[-1]

    asyncio.run(run())


def test_parse_file_mentions_with_remote_backend():
    from devx.completion.parser import parse_file_mentions

    mock_backend = MagicMock()
    mock_backend.is_remote = True
    mock_backend.root = "/srv/app"
    mock_backend.exists.side_effect = lambda p: p in {"src/main.py", "config.json"}

    cleaned, files, missing = parse_file_mentions(
        "inspect @src/main.py and @missing.py also @/srv/app/config.json",
        backend=mock_backend,
    )

    assert "missing.py" in missing
    assert Path("src/main.py") in files
    assert Path("config.json") in files
    assert len(files) == 2


def test_workspace_tools_remote_search(tmp_path: Path):
    from devx.harness.permissions import PermissionPolicy
    from devx.harness.tools import WorkspaceTools

    mock_backend = MagicMock()
    mock_backend.is_remote = True
    mock_backend.root = "/srv/app"
    mock_backend.walk_files.return_value = ["src/app.py", "src/helper.py", "README.md"]
    mock_backend.stat_size.return_value = 50
    mock_backend.read_bytes.side_effect = lambda p, max_b: (
        b"def authenticate_user():\n    return True\n"
        if p == "src/app.py"
        else b"nothing here\n"
    )

    tools = WorkspaceTools(PermissionPolicy(tmp_path), backend=mock_backend)
    result = tools.search("authenticate")

    assert result.ok is True
    assert "src/app.py:1:def authenticate_user():" in result.output
    assert "src/helper.py" not in result.output


def test_detect_test_command_remote(tmp_path: Path):
    from devx.harness.testing import detect_test_command

    # 1. Pytest detection
    backend_py = MagicMock()
    backend_py.is_remote = True
    backend_py.is_dir.return_value = False
    backend_py.exists.side_effect = lambda p: p == "pyproject.toml"
    assert detect_test_command(tmp_path, backend=backend_py) == "python -m pytest -q"

    # 2. Package.json npm test detection
    backend_node = MagicMock()
    backend_node.is_remote = True
    backend_node.is_dir.return_value = False
    backend_node.exists.return_value = False
    backend_node.is_file.side_effect = lambda p: p == "package.json"
    backend_node.read_bytes.return_value = b'{"scripts": {"test": "jest"}}'
    assert detect_test_command(tmp_path, backend=backend_node) == "npm test"

    # 3. Cargo.toml detection
    backend_rust = MagicMock()
    backend_rust.is_remote = True
    backend_rust.is_dir.return_value = False
    backend_rust.exists.side_effect = lambda p: p == "Cargo.toml"
    backend_rust.is_file.return_value = False
    assert detect_test_command(tmp_path, backend=backend_rust) == "cargo test"


def test_change_journal_rollback_remote(tmp_path: Path):
    from devx.harness.changes import ChangeJournal

    db_path = tmp_path / "sessions.db"
    journal = ChangeJournal(db_path)

    backup_file = tmp_path / "main.py.bak"
    backup_file.write_bytes(b"original remote content")

    journal.record(
        run_id="run-remote-rollback",
        path=Path("/srv/app/src/main.py"),
        before_hash="hash_before",
        after_hash="hash_after",
        approved=True,
        backup_path=backup_file,
    )

    mock_backend = MagicMock()
    mock_backend.is_remote = True
    mock_backend.root = "/srv/app"
    mock_backend.file_hash.return_value = "hash_after"

    restored = journal.rollback("run-remote-rollback", backend=mock_backend)

    assert len(restored) == 1
    mock_backend.write_bytes.assert_called_once_with("src/main.py", b"original remote content")


def test_workspace_tools_remote_write_verify_rollback(tmp_path: Path):
    """Remote writes record backend-relative paths so drift checks and rollback
    locate the file without guessing (regression for the rejected/verify path
    mismatch: recorded local-absolute paths resolved to garbage on remote)."""
    import hashlib

    from devx.harness.changes import ChangeJournal
    from devx.harness.permissions import PermissionPolicy
    from devx.harness.tools import WorkspaceTools

    class FakeRemoteBackend:
        """Dict-backed stand-in for a remote/container workspace backend."""

        is_remote = True
        root = "/srv/app"

        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}

        def exists(self, rel: str) -> bool:
            return rel in self.files

        def file_hash(self, rel: str) -> str | None:
            data = self.files.get(rel)
            return hashlib.sha256(data).hexdigest() if data is not None else None

        def read_bytes(self, rel: str, max_bytes: int | None = None) -> bytes:
            try:
                data = self.files[rel]
                return data if max_bytes is None else data[:max_bytes]
            except KeyError as exc:
                raise FileNotFoundError(rel) from exc

        def write_bytes(self, rel: str, data: bytes) -> None:
            self.files[rel] = data

        def remove(self, rel: str) -> None:
            self.files.pop(rel, None)

    journal = ChangeJournal(tmp_path / "sessions.db")
    backend = FakeRemoteBackend()
    backend.files["src/main.py"] = b"original v1\n"
    tools = WorkspaceTools(
        PermissionPolicy(tmp_path),
        backend=backend,
        journal=journal,
        run_id="run-remote-write",
    )

    result = tools.write("src/main.py", "v2 content\n", approved=True)
    assert result.ok is True

    # The journal records the backend-relative path, not the local-absolute
    # display path that previously broke remote drift checks/rollback.
    records = journal.for_run("run-remote-write")
    assert len(records) == 1
    change = records[0]
    assert change.rel_path == "src/main.py"
    assert change.path != "src/main.py"

    # Drift check sees the freshly written file as intact.
    assert [e["status"] for e in journal.verify("run-remote-write", backend=backend)] == ["intact"]

    # Reject restores the original bytes through the backend.
    assert journal.rollback_one("run-remote-write", "src/main.py", backend=backend) is True
    assert backend.files["src/main.py"] == b"original v1\n"
    assert [e["status"] for e in journal.verify("run-remote-write", backend=backend)] == ["reverted"]

    # A hand-edited file is a conflict and rollback leaves it untouched.
    backend.files["src/main.py"] = b"manual edit\n"
    assert [e["status"] for e in journal.verify("run-remote-write", backend=backend)] == ["conflict"]
    assert journal.rollback_one("run-remote-write", "src/main.py", backend=backend) is False
    assert backend.files["src/main.py"] == b"manual edit\n"

    # New files are removed on rollback (create case uses backend.remove).
    tools.write("src/new.txt", "brand new\n", approved=True)
    assert journal.rollback_one("run-remote-write", "src/new.txt", backend=backend) is True
    assert "src/new.txt" not in backend.files


def test_write_backup_captures_full_large_preexisting_file(tmp_path: Path):
    """P0.4: a pre-existing file larger than max_bytes is backed up in full, so
    rollback restores it byte-for-byte instead of truncating it at the read cap."""
    from devx.harness.changes import ChangeJournal
    from devx.harness.local_backend import LocalWorkspaceBackend
    from devx.harness.permissions import PermissionPolicy
    from devx.harness.tools import WorkspaceTools

    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "big.txt"
    original = b"A" * 65_536  # far above the tiny read cap the tools use
    target.write_bytes(original)

    journal = ChangeJournal(tmp_path / "sessions.db")
    tools = WorkspaceTools(
        PermissionPolicy(workspace),
        journal=journal,
        run_id="run-big-backup",
        max_bytes=1024,
        backend=LocalWorkspaceBackend(workspace),
    )

    result = tools.write("big.txt", "small\n", approved=True)
    assert result.ok is True

    records = journal.for_run("run-big-backup")
    assert len(records) == 1
    backup_path = records[0].backup_path
    assert backup_path is not None
    # The backup holds the *entire* original, not just the first max_bytes.
    assert Path(backup_path).read_bytes() == original

    # And rollback restores the full bytes through the local backend.
    assert journal.rollback_one("run-big-backup", "big.txt") is True
    assert target.read_bytes() == original


def test_suggester_includes_remote_commands():
    from devx.completion.suggester import COMMAND_SUGGESTIONS

    assert "/remote" in COMMAND_SUGGESTIONS
    assert "/remote connect" in COMMAND_SUGGESTIONS
    assert "/remote disconnect" in COMMAND_SUGGESTIONS

