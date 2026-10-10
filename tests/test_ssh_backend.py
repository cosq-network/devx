from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devx.harness.backend import WorkspaceBackend
from devx.harness.local_backend import LocalWorkspaceBackend
from devx.harness.permissions import PermissionPolicy
from devx.harness.runtime import CancellationToken
from devx.harness.ssh_backend import SshConfig, SshWorkspaceBackend
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

