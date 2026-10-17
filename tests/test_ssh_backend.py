from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devx.harness.backend import BackendToolResult
from devx.harness.permissions import PermissionPolicy
from devx.harness.runtime import CancellationToken, RunCancelled
from devx.harness.ssh_backend import SshConfig, SshWorkspaceBackend, preflight_remote
from devx.harness.tools import WorkspaceTools


def test_ssh_config_from_uri():
    # 1. Full URI with port and credentials
    cfg1 = SshConfig.from_uri("ssh://alice:secret@example.com:2222/var/www/project")
    assert cfg1.host == "example.com"
    assert cfg1.username == "alice"
    assert cfg1.port == 2222
    assert cfg1.password == "secret"
    assert cfg1.remote_path == "/var/www/project"

    # 2. Standard user@host:/path syntax
    cfg2 = SshConfig.from_uri("deploy@prod-host:/srv/app")
    assert cfg2.host == "prod-host"
    assert cfg2.username == "deploy"
    assert cfg2.port == 22
    assert cfg2.password is None
    assert cfg2.remote_path == "/srv/app"


def test_ssh_config_invalid_uri_raises():
    with pytest.raises(ValueError):
        SshConfig.from_uri("not-a-valid-ssh-uri")


def test_ssh_workspace_backend_mock_file_operations():
    # Mock SFTP and SSHClient
    mock_client = MagicMock()
    mock_sftp = MagicMock()
    mock_file = MagicMock()
    mock_file.read.return_value = b"remote content line 1\nremote line 2"
    mock_sftp.open.return_value.__enter__.return_value = mock_file
    
    # Stat mocking
    mock_stat = MagicMock()
    mock_stat.st_size = 35
    mock_stat.st_mode = 0o100644  # regular file
    mock_sftp.stat.return_value = mock_stat

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)

    assert backend.is_remote is True
    assert backend.root == "/app"

    # Read bytes
    content = backend.read_bytes("src/main.py", 1000)
    assert content == b"remote content line 1\nremote line 2"
    mock_sftp.open.assert_called()

    # Write bytes
    backend.write_bytes("src/new.py", b"new content")
    mock_sftp.open.assert_called()

    # Stat size and exists
    assert backend.stat_size("src/main.py") == 35
    assert backend.exists("src/main.py") is True
    assert backend.is_file("src/main.py") is True


def test_ssh_workspace_backend_prevents_directory_traversal():
    mock_client = MagicMock()
    mock_sftp = MagicMock()
    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)

    with pytest.raises(PermissionError):
        backend.read_bytes("../outside.py", 100)

    with pytest.raises(PermissionError):
        backend.read_bytes("/etc/passwd", 100)

    # Sibling paths sharing a string prefix with the root must not punch
    # through: ``/app2`` starts with ``/app`` but is a different workspace.
    with pytest.raises(PermissionError):
        backend.read_bytes("../app2/x", 100)

    with pytest.raises(PermissionError):
        backend.read_bytes("/app2/x", 100)


def test_ssh_workspace_root_slash_allows_absolute_paths():
    mock_client = MagicMock()
    mock_sftp = MagicMock()
    mock_file = MagicMock()
    mock_file.read.return_value = b"remote bytes"
    mock_sftp.open.return_value.__enter__.return_value = mock_file
    mock_stat = MagicMock()
    mock_stat.st_size = 12
    mock_stat.st_mode = 0o100644
    mock_sftp.stat.return_value = mock_stat

    cfg = SshConfig(host="mock-host", username="user", remote_path="/")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)

    # A workspace rooted at the filesystem root legitimately allows absolutes.
    assert backend.read_bytes("/etc/hosts", 100) == b"remote bytes"


def _sftp_returning(payload: bytes, canonical: str | None):
    mock_client = MagicMock()
    mock_sftp = MagicMock()
    mock_file = MagicMock()
    mock_file.read.return_value = payload
    mock_sftp.open.return_value.__enter__.return_value = mock_file
    if canonical is not None:
        mock_sftp.normalize.return_value = canonical
    return mock_client, mock_sftp


def test_ssh_backend_rejects_symlink_escape():
    """A link inside the workspace resolving outside it must be refused."""
    mock_client, mock_sftp = _sftp_returning(b"secret", "/etc/passwd")
    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)
    with pytest.raises(PermissionError):
        backend.read_bytes("link.py", 100)


def test_ssh_backend_allows_symlink_within_workspace():
    mock_client, mock_sftp = _sftp_returning(b"ok", "/app/real.py")
    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)
    assert backend.read_bytes("link.py", 100) == b"ok"


def test_ssh_backend_rejects_nonexistent_target_in_escaping_symlink_dir():
    """A write to a new file inside a symlinked directory resolving outside the workspace must be refused."""
    mock_client = MagicMock()
    mock_sftp = MagicMock()

    def fake_normalize(path):
        if path == "/app/escape_dir/new_file.py":
            raise OSError("No such file")
        if path == "/app/escape_dir":
            return "/etc"
        return path

    mock_sftp.normalize.side_effect = fake_normalize
    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)
    with pytest.raises(PermissionError):
        backend.write_bytes("escape_dir/new_file.py", b"payload")


def test_ssh_backend_relative_root_anchored_to_home():
    """A URI without a path is anchored to the server-reported workdir so
    absolute paths cannot slip past an indeterminate ``.`` root."""
    mock_client, mock_sftp = _sftp_returning(b"ok", "/home/user")
    cfg = SshConfig(host="mock-host", username="user", remote_path=".")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)
    assert backend.read_bytes("note.txt", 100) == b"ok"
    with pytest.raises(PermissionError):
        backend.read_bytes("/etc/passwd", 100)


def test_ssh_config_uri_port_wins_over_ssh_config(monkeypatch):
    monkeypatch.setattr(
        SshConfig,
        "load_ssh_config",
        staticmethod(lambda host: {"hostname": "real-host", "port": "22", "user": "cfguser"}),
    )

    # An explicit URI port always wins over ~/.ssh/config; config hostname
    # aliasing and the explicit URI user still apply.
    cfg = SshConfig.from_uri("ssh://alice@example.com:49153/var/app")
    assert cfg.port == 49153
    assert cfg.username == "alice"
    assert cfg.host == "real-host"

    # Without a URI port, ~/.ssh/config supplies the port.
    cfg2 = SshConfig.from_uri("ssh://alice@example.com/var/app")
    assert cfg2.port == 22


def test_ssh_workspace_backend_mock_run_command():
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_channel = MagicMock()

    mock_client.get_transport.return_value = mock_transport
    mock_transport.open_session.return_value = mock_channel

    mock_channel.exit_status_ready.side_effect = [False, True]
    mock_channel.recv_ready.side_effect = [True, False, False]
    mock_channel.recv.return_value = b"test passed\n"
    mock_channel.recv_exit_status.return_value = 0

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())

    result = backend.run_command(["pytest"], timeout=10, cancellation=CancellationToken(), max_bytes=1000)
    assert result.ok is True
    assert "test passed" in result.output
    assert result.exit_code == 0
    assert "if [ -f .venv/bin/activate ]; then . .venv/bin/activate; fi && pytest" in mock_channel.exec_command.call_args[0][0]


def test_workspace_tools_with_ssh_backend(tmp_path: Path):
    # Test that WorkspaceTools functions correctly when plugged with SshWorkspaceBackend
    mock_client = MagicMock()
    mock_sftp = MagicMock()
    mock_file = MagicMock()
    mock_file.read.return_value = b"def calculate():\n    return 42\n"
    mock_sftp.open.return_value.__enter__.return_value = mock_file
    
    mock_stat = MagicMock()
    mock_stat.st_size = 32
    mock_stat.st_mode = 0o100644
    mock_sftp.stat.return_value = mock_stat

    cfg = SshConfig(host="mock", username="user", remote_path=tmp_path.as_posix())
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)
    
    policy = PermissionPolicy(tmp_path)
    tools = WorkspaceTools(policy, backend=backend)

    res = tools.read(tmp_path / "calc.py")
    assert res.ok is True
    assert "def calculate" in res.output


def test_ssh_config_reads_ssh_config_file(monkeypatch):
    mock_lookup = {
        "hostname": "10.0.0.42",
        "user": "ubuntu",
        "port": "2222",
        "identityfile": ["~/.ssh/id_rsa_custom"],
        "proxycommand": "ssh -W %h:%p bastion",
    }
    monkeypatch.setattr(SshConfig, "load_ssh_config", lambda host: mock_lookup if host == "dev-box" else {})

    cfg = SshConfig.from_uri("dev-box:/srv/app")
    assert cfg.host == "10.0.0.42"
    assert cfg.username == "ubuntu"
    assert cfg.port == 2222
    assert cfg.key_filename == "~/.ssh/id_rsa_custom"
    assert "bastion" in (cfg.proxy_command or "")
    assert cfg.remote_path == "/srv/app"


def test_ssh_workspace_backend_fast_file_discovery():
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_channel = MagicMock()

    mock_client.get_transport.return_value = mock_transport
    mock_transport.open_session.return_value = mock_channel

    mock_channel.exit_status_ready.side_effect = [False, True]
    mock_channel.recv_ready.side_effect = [True, False, False]
    mock_channel.recv.return_value = b"src/app.py\nsrc/utils.py\nREADME.md\n"
    mock_channel.recv_exit_status.return_value = 0

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())

    files = backend.walk_files(max_files=10, code_exts={".py", ".md"})
    assert "src/app.py" in files
    assert "src/utils.py" in files
    assert "README.md" in files
    cmd = mock_channel.exec_command.call_args[0][0]
    assert cmd.startswith("cd /app && git ls-files") or cmd.startswith("cd '/app' && git ls-files")


def test_ssh_workspace_backend_strips_ansi_escapes():
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_channel = MagicMock()

    mock_client.get_transport.return_value = mock_transport
    mock_transport.open_session.return_value = mock_channel

    mock_channel.exit_status_ready.side_effect = [False, True]
    mock_channel.recv_ready.side_effect = [True, False, False]
    # Output containing ANSI escape color codes
    mock_channel.recv.return_value = b"\x1b[32mPASSED\x1b[0m tests/test_one.py\n"
    mock_channel.recv_exit_status.return_value = 0

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())

    res = backend.run_command("pytest", timeout=10, cancellation=CancellationToken(), max_bytes=1000)
    assert res.ok is True
    assert "\x1b[" not in res.output
    assert "PASSED tests/test_one.py" in res.output


def test_ssh_workspace_backend_ensure_connected(monkeypatch):
    import sys
    mock_paramiko = MagicMock()
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_sftp = MagicMock()
    mock_paramiko.SSHClient.return_value = mock_client
    mock_client.get_transport.return_value = mock_transport
    mock_client.open_sftp.return_value = mock_sftp
    mock_proxy_inst = MagicMock()
    mock_paramiko.ProxyCommand.return_value = mock_proxy_inst

    monkeypatch.setitem(sys.modules, "paramiko", mock_paramiko)

    cfg = SshConfig(
        host="prod-bastion",
        username="admin",
        port=2222,
        password="secret-password",
        key_filename="/custom/key.pem",
        remote_path="/srv/deploy",
        proxy_command="ssh -W %h:%p gateway",
    )
    backend = SshWorkspaceBackend(cfg)
    assert backend._connected is False

    backend._ensure_connected()

    assert backend._connected is True
    assert backend._client is mock_client
    assert backend._sftp is mock_sftp
    mock_client.connect.assert_called_once_with(
        hostname="prod-bastion",
        port=2222,
        username="admin",
        timeout=15,
        password="secret-password",
        key_filename="/custom/key.pem",
        sock=mock_proxy_inst,
    )
    mock_transport.set_keepalive.assert_called_once_with(15)


def test_ssh_workspace_backend_missing_paramiko_raises_import_error(monkeypatch):
    import builtins
    orig_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "paramiko":
            raise ImportError("No module named 'paramiko'")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    cfg = SshConfig(host="mock", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg)

    with pytest.raises(ImportError, match="Remote SSH support requires paramiko"):
        backend._ensure_connected()


def test_ssh_workspace_backend_sftp_walk_fallback():
    import stat

    mock_client = MagicMock()
    mock_sftp = MagicMock()

    # Mock fast discovery returning None (forcing SFTP recursive fallback)
    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)
    backend._fast_remote_file_discovery = MagicMock(return_value=None)

    # Prepare mocked SFTP entries
    def make_entry(name: str, is_dir: bool, size: int = 100):
        entry = MagicMock()
        entry.filename = name
        entry.st_mode = stat.S_IFDIR if is_dir else stat.S_IFREG
        entry.st_size = size
        return entry

    def mock_listdir_attr(path: str):
        if path == "/app":
            return [
                make_entry("src", is_dir=True),
                make_entry(".git", is_dir=True),       # should be ignored
                make_entry("README.md", is_dir=False),  # should be included
                make_entry("photo.jpg", is_dir=False),  # should be excluded by ext filter
            ]
        elif path == "/app/src":
            return [
                make_entry("main.py", is_dir=False, size=500),
            ]
        return []

    mock_sftp.listdir_attr.side_effect = mock_listdir_attr

    found = backend.walk_files(max_files=50, code_exts={".py", ".md"})
    assert found == ["README.md", "src/main.py"]


def test_ssh_workspace_backend_write_bytes_creates_directories():
    mock_client = MagicMock()
    mock_sftp = MagicMock()
    mock_file = MagicMock()
    mock_sftp.open.return_value.__enter__.return_value = mock_file

    # stat on non-existing directory raises OSError, triggering mkdir
    def mock_stat(path: str):
        if path in {"/app/sub", "/app/sub/nested"}:
            raise OSError("No such file or directory")
        return MagicMock()

    mock_sftp.stat.side_effect = mock_stat

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)

    backend.write_bytes("sub/nested/file.py", b"print('hello')")

    mock_sftp.mkdir.assert_any_call("/app/sub")
    mock_sftp.mkdir.assert_any_call("/app/sub/nested")
    
    # Check atomic temporary file creation and posix rename
    open_call_path = mock_sftp.open.call_args[0][0]
    assert open_call_path.startswith("/app/sub/nested/.file.py.devx-tmp-")
    mock_file.write.assert_called_once_with(b"print('hello')")
    mock_sftp.posix_rename.assert_called_once_with(open_call_path, "/app/sub/nested/file.py")


def test_ssh_workspace_backend_file_hash():
    import hashlib

    mock_client = MagicMock()
    mock_sftp = MagicMock()
    mock_file = MagicMock()
    mock_file.read.side_effect = [b"chunk 1 ", b"chunk 2", b""]
    mock_sftp.open.return_value.__enter__.return_value = mock_file

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)
    backend.is_file = MagicMock(return_value=True)

    expected_hash = hashlib.sha256(b"chunk 1 chunk 2").hexdigest()
    actual_hash = backend.file_hash("data.txt")

    assert actual_hash == expected_hash


def test_ssh_workspace_backend_close():
    mock_client = MagicMock()
    mock_sftp = MagicMock()

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=mock_sftp)
    assert backend._connected is True

    backend.close()

    mock_sftp.close.assert_called_once()
    mock_client.close.assert_called_once()
    assert backend._connected is False
    assert backend._sftp is None
    assert backend._client is None


def _mock_paramiko(monkeypatch, connect_side_effect=None):
    import sys

    mock_paramiko = MagicMock()
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_sftp = MagicMock()
    mock_paramiko.SSHClient.return_value = mock_client
    mock_client.get_transport.return_value = mock_transport
    mock_client.open_sftp.return_value = mock_sftp
    if connect_side_effect is not None:
        mock_client.connect.side_effect = connect_side_effect
    monkeypatch.setitem(sys.modules, "paramiko", mock_paramiko)
    return mock_client, mock_transport, mock_sftp


def test_ssh_workspace_backend_reconnects_when_transport_inactive(monkeypatch):
    """P0.1: a dropped transport is transparently replaced on the next call."""
    mock_client, mock_transport, _ = _mock_paramiko(monkeypatch)

    cfg = SshConfig(host="prod", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg)

    mock_transport.is_active.return_value = True
    backend._ensure_connected()
    assert backend._connected is True
    assert mock_client.connect.call_count == 1

    # The link drops: the next ensure must reconnect rather than reuse it.
    mock_transport.is_active.return_value = False
    backend._ensure_connected()
    assert backend._connected is True
    assert mock_client.connect.call_count == 2


def test_ssh_workspace_backend_connect_failure_raises_clear_error(monkeypatch):
    """P0.1: a failed connect surfaces a clear, typed error."""
    _mock_paramiko(monkeypatch, connect_side_effect=OSError("connection refused"))
    monkeypatch.setattr("devx.harness.ssh_backend.time.sleep", lambda *_: None)

    cfg = SshConfig(host="unreachable", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg)

    with pytest.raises(ConnectionError, match="Could not connect to SSH host unreachable:22"):
        backend._ensure_connected()


def test_ssh_workspace_backend_run_command_reports_connection_loss():
    """P0.1: a mid-command drop raises ConnectionError and invalidates state."""
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_client.get_transport.return_value = mock_transport
    mock_transport.open_session.side_effect = OSError("socket closed")

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())

    with pytest.raises(ConnectionError, match="Remote connection lost"):
        backend.run_command(["pytest"], timeout=10, cancellation=CancellationToken(), max_bytes=1000)
    assert backend._connected is False


def test_ssh_workspace_backend_command_runs_under_setsid():
    """P0.3: commands run in their own session so the process group is killable."""
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_channel = MagicMock()
    mock_client.get_transport.return_value = mock_transport
    mock_transport.open_session.return_value = mock_channel
    mock_channel.exit_status_ready.side_effect = [False, True]
    mock_channel.recv_ready.side_effect = [True, False, False]
    mock_channel.recv.return_value = b"ok\n"
    mock_channel.recv_exit_status.return_value = 0

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())

    result = backend.run_command(["pytest"], timeout=10, cancellation=CancellationToken(), max_bytes=1000)
    assert result.ok is True
    command = mock_channel.exec_command.call_args[0][0]
    assert "setsid sh -c" in command
    assert "echo $$ >" in command


def test_ssh_workspace_backend_cancel_kills_remote_process_group():
    """P0.3: cancellation kills the remote process group, not just the channel."""
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_channel = MagicMock()
    mock_client.get_transport.return_value = mock_transport
    mock_transport.open_session.return_value = mock_channel
    mock_channel.exit_status_ready.return_value = False

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())
    backend._remote_pgid = MagicMock(return_value=4242)

    token = CancellationToken()
    token.cancel()
    with pytest.raises(RunCancelled):
        backend.run_command(["pytest"], timeout=10, cancellation=token, max_bytes=1000)

    commands = [call.args[0] for call in mock_channel.exec_command.call_args_list]
    assert any("kill -TERM -4242" in command for command in commands)


def test_ssh_workspace_backend_timeout_kills_remote_process_group(monkeypatch):
    """P0.3: a timed-out command also kills the remote process group."""
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_channel = MagicMock()
    mock_client.get_transport.return_value = mock_transport
    mock_transport.open_session.return_value = mock_channel
    mock_channel.exit_status_ready.return_value = False
    mock_channel.recv_ready.return_value = False

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())
    backend._remote_pgid = MagicMock(return_value=777)

    # Advance the clock so the first timeout check trips immediately.
    clock = iter([0.0, 100.0, 100.0])
    monkeypatch.setattr("devx.harness.ssh_backend.time.monotonic", lambda: next(clock))

    result = backend.run_command(["pytest"], timeout=5, cancellation=CancellationToken(), max_bytes=1000)
    assert result.ok is False
    assert "timed out" in result.output
    commands = [call.args[0] for call in mock_channel.exec_command.call_args_list]
    assert any("kill -TERM -777" in command for command in commands)


def test_ssh_workspace_backend_kill_group_without_pgid_is_noop():
    """P0.3: killing without a readable pgid is a safe no-op."""
    mock_client = MagicMock()
    backend = SshWorkspaceBackend(
        SshConfig(host="h", username="u", remote_path="/app"),
        client=mock_client,
        sftp=MagicMock(),
    )
    backend._remote_pgid = MagicMock(return_value=None)
    assert backend._kill_remote_group("/tmp/devx-pgid-x") is False


def test_ssh_workspace_backend_health_check_ok(monkeypatch):
    """P1.4: a responsive workspace reports healthy."""
    mock_client, mock_transport, _ = _mock_paramiko(monkeypatch)
    mock_channel = MagicMock()
    mock_transport.open_session.return_value = mock_channel
    mock_channel.exit_status_ready.side_effect = [False, True]
    mock_channel.recv_ready.side_effect = [True, False, False]
    mock_channel.recv.return_value = b""
    mock_channel.recv_exit_status.return_value = 0

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())

    assert backend.health_check() is True


def test_ssh_workspace_backend_health_check_never_raises(monkeypatch):
    """P1.4: an unreachable workspace reports unhealthy instead of raising."""
    mock_client, mock_transport, _ = _mock_paramiko(monkeypatch)
    mock_transport.open_session.side_effect = OSError("socket closed")

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())

    assert backend.health_check() is False


def test_preflight_remote_all_checks_ok():
    backend = MagicMock()
    backend.is_dir.return_value = True
    backend.exists.return_value = True
    backend.run_command.return_value = BackendToolResult(True, "true\n", 0)

    report = preflight_remote(SshConfig(host="h", username="u", remote_path="/app"), backend=backend)

    assert report["status"] == "ok"
    assert report["checks"]["connection"]["status"] == "ok"
    assert report["checks"]["workspace_path"]["status"] == "ok"
    assert report["checks"]["writable"]["status"] == "ok"
    assert report["checks"]["git"]["status"] == "ok"
    assert report["checks"]["venv"]["status"] == "ok"
    # An externally supplied backend is not closed by preflight.
    backend.close.assert_not_called()


def test_preflight_remote_missing_git_and_venv_is_non_fatal():
    backend = MagicMock()
    backend.is_dir.return_value = True
    backend.exists.return_value = False
    backend.run_command.return_value = BackendToolResult(False, "fatal: not a git repository\n", 128)

    report = preflight_remote(SshConfig(host="h", username="u", remote_path="/app"), backend=backend)

    assert report["status"] == "ok"
    assert report["checks"]["git"]["status"] == "missing"
    assert report["checks"]["venv"]["status"] == "missing"


def test_preflight_remote_connection_failure_is_fatal():
    backend = MagicMock()
    backend.ensure_connected.side_effect = OSError("authentication failed")

    report = preflight_remote(SshConfig(host="h", username="u", remote_path="/app"), backend=backend)

    assert report["status"] == "error"
    assert report["checks"]["connection"]["status"] == "error"
    assert "authentication failed" in report["checks"]["connection"]["detail"]


def test_preflight_remote_non_writable_path_is_fatal():
    backend = MagicMock()
    backend.is_dir.return_value = True
    backend.exists.return_value = False
    backend.run_command.return_value = BackendToolResult(False, "not a repo", 128)
    backend.write_bytes.side_effect = OSError("permission denied")

    report = preflight_remote(SshConfig(host="h", username="u", remote_path="/readonly"), backend=backend)

    assert report["status"] == "error"
    assert report["checks"]["writable"]["status"] == "error"


def test_preflight_remote_closes_owned_backend(monkeypatch):
    owned = MagicMock()
    owned.is_dir.return_value = True
    owned.exists.return_value = True
    owned.run_command.return_value = BackendToolResult(True, "true", 0)
    monkeypatch.setattr("devx.harness.ssh_backend.SshWorkspaceBackend", lambda cfg: owned)

    report = preflight_remote(SshConfig(host="h", username="u", remote_path="/app"))

    assert report["status"] == "ok"
    owned.close.assert_called_once()


def test_ssh_default_host_key_policy_is_reject(monkeypatch):
    """P0.3: unknown host keys are refused by default, never auto-trusted."""
    import sys

    mock_paramiko = MagicMock()
    mock_client = MagicMock()
    mock_paramiko.SSHClient.return_value = mock_client
    mock_client.get_transport.return_value = MagicMock()
    mock_client.open_sftp.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "paramiko", mock_paramiko)

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg)
    backend._ensure_connected()

    mock_client.set_missing_host_key_policy.assert_called_once()
    policy = mock_client.set_missing_host_key_policy.call_args[0][0]
    assert policy is mock_paramiko.RejectPolicy.return_value
    assert mock_paramiko.RejectPolicy.called
    assert not mock_paramiko.AutoAddPolicy.called


def test_ssh_allow_unknown_host_keys_opts_into_autoadd(monkeypatch):
    """P0.3: auto-add stays available, but only through an explicit opt-in."""
    import sys

    mock_paramiko = MagicMock()
    mock_client = MagicMock()
    mock_paramiko.SSHClient.return_value = mock_client
    mock_client.get_transport.return_value = MagicMock()
    mock_client.open_sftp.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "paramiko", mock_paramiko)

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app", allow_unknown_host_keys=True)
    backend = SshWorkspaceBackend(cfg)
    backend._ensure_connected()

    policy = mock_client.set_missing_host_key_policy.call_args[0][0]
    assert policy is mock_paramiko.AutoAddPolicy.return_value
    assert mock_paramiko.AutoAddPolicy.called
    assert not mock_paramiko.RejectPolicy.called


def test_ssh_known_hosts_file_is_loaded(monkeypatch, tmp_path):
    """P0.3: a configured known_hosts file is consulted under RejectPolicy."""
    import sys

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("mock-host ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI\n", encoding="utf-8")

    mock_paramiko = MagicMock()
    mock_client = MagicMock()
    mock_paramiko.SSHClient.return_value = mock_client
    mock_client.get_transport.return_value = MagicMock()
    mock_client.open_sftp.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "paramiko", mock_paramiko)

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app", known_hosts=str(known_hosts))
    backend = SshWorkspaceBackend(cfg)
    backend._ensure_connected()

    policy = mock_client.set_missing_host_key_policy.call_args[0][0]
    assert policy is mock_paramiko.RejectPolicy.return_value
    assert mock_paramiko.RejectPolicy.called
    mock_client.load_host_keys.assert_called_once_with(str(known_hosts))


def test_fast_remote_file_discovery_times_out_and_closes_channel(monkeypatch):
    """P1.4: a hung remote git process falls back instead of blocking forever."""
    monkeypatch.setattr("devx.harness.ssh_backend._FAST_GIT_TIMEOUT", 0.05)

    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_channel = MagicMock()
    mock_client.get_transport.return_value = mock_transport
    mock_transport.open_session.return_value = mock_channel
    # Neither exit-ready nor any data ever arrives (wedged remote process).
    mock_channel.exit_status_ready.return_value = False
    mock_channel.recv_ready.return_value = False

    cfg = SshConfig(host="mock-host", username="user", remote_path="/app")
    backend = SshWorkspaceBackend(cfg, client=mock_client, sftp=MagicMock())

    result = backend._fast_remote_file_discovery("/app", 100, None)

    assert result is None
    mock_channel.close.assert_called_once()
    mock_channel.recv_exit_status.assert_not_called()

