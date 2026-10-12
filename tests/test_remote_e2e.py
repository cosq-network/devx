"""Real SSH end-to-end tests (P0.4).

These exercise :class:`SshWorkspaceBackend` against an actual sshd — including
process-group cancellation and hash-guarded rollback — rather than mocking
paramiko. They are opt-in:

* Set ``DEVX_E2E_REMOTE=1`` to spin up a throwaway ``linuxserver/openssh-server``
  container via Docker (used by the dedicated CI job).
* Or set ``DEVX_E2E_SSH=user@host:/path`` (plus optional ``DEVX_E2E_SSH_PORT``,
  ``DEVX_E2E_SSH_KEY``, ``DEVX_E2E_SSH_PASSWORD``) to target a server you provide.

Otherwise the module is skipped, so the unit suite stays hermetic.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

from devx.harness.changes import ChangeJournal
from devx.harness.container_backend import ContainerConfig, ContainerWorkspaceBackend
from devx.harness.runtime import CancellationToken, RunCancelled
from devx.harness.ssh_backend import SshConfig, SshWorkspaceBackend, preflight_remote

E2E_IMAGE = os.environ.get("DEVX_E2E_IMAGE", "lscr.io/linuxserver/openssh-server:latest")
E2E_ENABLED = os.environ.get("DEVX_E2E_REMOTE") == "1"
E2E_EXTERNAL = os.environ.get("DEVX_E2E_SSH")

pytestmark = pytest.mark.skipif(
    not (E2E_ENABLED or E2E_EXTERNAL),
    reason="set DEVX_E2E_REMOTE=1 or DEVX_E2E_SSH=user@host:/path to run real SSH e2e tests",
)


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=True)
        return True
    except Exception:
        return False


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1.0)
            if probe.connect_ex((host, port)) == 0:
                return
        time.sleep(0.5)
    raise TimeoutError(f"sshd did not become ready on {host}:{port} within {timeout}s")


def _start_container() -> tuple[str, int]:
    name = f"devx-e2e-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::2222",
            "-e", "PUID=1000", "-e", "PGID=1000", "-e", "TZ=UTC",
            "-e", "PASSWORD_ACCESS=true", "-e", "USER_PASSWORD=devxpass", "-e", "USER_NAME=devx",
            E2E_IMAGE,
        ],
        check=True, capture_output=True, timeout=300,
    )
    mapping = subprocess.run(
        ["docker", "port", name, "2222"], check=True, capture_output=True, timeout=60
    ).stdout.decode().strip()
    port = int(mapping.rsplit(":", 1)[-1])
    _wait_for_port("127.0.0.1", port, 120)
    subprocess.run(
        ["docker", "exec", "-u", "root", name, "sh", "-c",
         "mkdir -p /config/repo && chown -R 1000:1000 /config/repo"],
        check=True, capture_output=True, timeout=60,
    )
    return name, port


@pytest.fixture(scope="module")
def _docker_container() -> tuple[str, int] | None:
    """Start one throwaway container shared by the SSH and container e2e tests."""
    if E2E_EXTERNAL:
        yield None
        return

    if not _docker_available():
        pytest.fail("DEVX_E2E_REMOTE=1 requires a working Docker daemon")
    name, port = _start_container()
    try:
        yield (name, port)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)


@pytest.fixture(scope="module")
def remote_config(_docker_container) -> SshConfig:
    if _docker_container is None:
        base = SshConfig.from_uri(E2E_EXTERNAL)
        port = os.environ.get("DEVX_E2E_SSH_PORT")
        return SshConfig(
            host=base.host,
            username=base.username,
            port=int(port) if port else base.port,
            password=os.environ.get("DEVX_E2E_SSH_PASSWORD") or base.password,
            key_filename=os.environ.get("DEVX_E2E_SSH_KEY") or base.key_filename,
            remote_path=base.remote_path,
            # CI/managed test targets are throwaway: their host keys are not
            # pre-registered, so opt into auto-add for the suite only.
            allow_unknown_host_keys=True,
        )

    _, port = _docker_container
    return SshConfig(
        host="127.0.0.1", username="devx", port=port,
        password="devxpass", remote_path="/config/repo",
        allow_unknown_host_keys=True,
    )


@pytest.fixture(scope="module")
def container_config(_docker_container) -> ContainerConfig:
    if _docker_container is None:
        pytest.skip("container e2e tests require the Docker-managed container")
    name, _ = _docker_container
    return ContainerConfig(container=name, remote_path="/config/repo")


def test_e2e_preflight_reports_ok(remote_config: SshConfig):
    report = preflight_remote(remote_config)
    assert report["status"] == "ok", report
    assert report["checks"]["connection"]["status"] == "ok"
    assert report["checks"]["workspace_path"]["status"] == "ok"
    assert report["checks"]["writable"]["status"] == "ok"


def test_e2e_file_roundtrip_and_hash(remote_config: SshConfig):
    backend = SshWorkspaceBackend(remote_config)
    try:
        backend.write_bytes("notes/hello.txt", b"hello over ssh\n")
        assert backend.read_bytes("notes/hello.txt", 1000) == b"hello over ssh\n"
        assert backend.exists("notes/hello.txt")
        assert backend.is_file("notes/hello.txt")
        assert backend.stat_size("notes/hello.txt") == len(b"hello over ssh\n")
        import hashlib

        assert backend.file_hash("notes/hello.txt") == hashlib.sha256(b"hello over ssh\n").hexdigest()
    finally:
        backend.close()


def test_e2e_sandbox_blocks_traversal(remote_config: SshConfig):
    backend = SshWorkspaceBackend(remote_config)
    try:
        with pytest.raises(PermissionError):
            backend.read_bytes("../../etc/passwd", 100)
    finally:
        backend.close()


def test_e2e_run_command(remote_config: SshConfig):
    backend = SshWorkspaceBackend(remote_config)
    try:
        result = backend.run_command(["echo", "remote-e2e-ok"], timeout=30, cancellation=CancellationToken(), max_bytes=4096)
        assert result.ok is True
        assert "remote-e2e-ok" in result.output
    finally:
        backend.close()


def test_e2e_cancellation_kills_remote_process_tree(remote_config: SshConfig):
    """Cancelling must stop the remote tree, not just close the channel."""
    backend = SshWorkspaceBackend(remote_config)
    marker = f"marker-{uuid.uuid4().hex}.txt"
    token = CancellationToken()
    outcome: dict[str, bool] = {}

    def _run() -> None:
        try:
            backend.run_command(
                ["sh", "-c", f"sleep 25; touch {marker}"],
                timeout=60, cancellation=token, max_bytes=4096,
            )
        except RunCancelled:
            outcome["cancelled"] = True

    try:
        worker = threading.Thread(target=_run)
        worker.start()
        time.sleep(1.5)
        token.cancel()
        worker.join(timeout=20)
        assert outcome.get("cancelled") is True
        # Give a (killed) leftover process no chance to create the marker.
        time.sleep(1.5)
        assert backend.exists(marker) is False
    finally:
        backend.close()


def test_e2e_hash_guarded_rollback_removes_created_file(remote_config: SshConfig, tmp_path: Path):
    backend = SshWorkspaceBackend(remote_config)
    journal = ChangeJournal(tmp_path / "changes.db")
    try:
        backend.write_bytes("created.txt", b"brand new\n")
        after = backend.file_hash("created.txt")
        journal.record(
            "run-e2e", Path(f"{remote_config.remote_path}/created.txt"),
            before_hash=None, after_hash=after, approved=True,
        )
        restored = journal.rollback("run-e2e", backend=backend)
        assert backend.exists("created.txt") is False
        assert restored
    finally:
        backend.close()


def test_e2e_hash_guarded_rollback_skips_modified_file(remote_config: SshConfig, tmp_path: Path):
    """If a file changed after the recorded write, rollback must not clobber it."""
    backend = SshWorkspaceBackend(remote_config)
    journal = ChangeJournal(tmp_path / "changes.db")
    try:
        backend.write_bytes("edited.txt", b"v1\n")
        after = backend.file_hash("edited.txt")
        journal.record(
            "run-edit", Path(f"{remote_config.remote_path}/edited.txt"),
            before_hash="deadbeef", after_hash=after, approved=True,
        )
        # Someone else edits the file afterwards.
        backend.write_bytes("edited.txt", b"v2-manual\n")
        restored = journal.rollback("run-edit", backend=backend)
        assert restored == []
        assert backend.read_bytes("edited.txt", 1000) == b"v2-manual\n"
    finally:
        backend.close()


# ── container backend (P1.1) ───────────────────────────────────────────────


def test_e2e_container_roundtrip_and_hash(container_config: ContainerConfig):
    backend = ContainerWorkspaceBackend(container_config)
    backend.write_bytes("notes/hello.txt", b"hello in container\n")
    assert backend.read_bytes("notes/hello.txt", 1000) == b"hello in container\n"
    assert backend.exists("notes/hello.txt")
    assert backend.is_file("notes/hello.txt")
    assert backend.is_dir("notes")
    assert backend.stat_size("notes/hello.txt") == len(b"hello in container\n")

    import hashlib

    assert backend.file_hash("notes/hello.txt") == hashlib.sha256(b"hello in container\n").hexdigest()
    assert backend.remove("notes/hello.txt") is True
    assert backend.exists("notes/hello.txt") is False


def test_e2e_container_sandbox_blocks_traversal(container_config: ContainerConfig):
    backend = ContainerWorkspaceBackend(container_config)
    with pytest.raises(PermissionError):
        backend.read_bytes("../../etc/passwd", 100)
    with pytest.raises(PermissionError):
        backend.write_bytes("/etc/passwd", b"x")


def test_e2e_container_run_command(container_config: ContainerConfig):
    backend = ContainerWorkspaceBackend(container_config)
    result = backend.run_command(
        ["echo", "container-e2e-ok"], timeout=30, cancellation=CancellationToken(), max_bytes=4096
    )
    assert result.ok is True
    assert "container-e2e-ok" in result.output


def test_e2e_container_run_command_timeout(container_config: ContainerConfig):
    backend = ContainerWorkspaceBackend(container_config)
    result = backend.run_command(
        "sleep 10", timeout=1, cancellation=CancellationToken(), max_bytes=4096
    )
    assert result.ok is False
    assert "timed out" in result.output


def test_e2e_container_hash_guarded_rollback(container_config: ContainerConfig, tmp_path: Path):
    backend = ContainerWorkspaceBackend(container_config)
    journal = ChangeJournal(tmp_path / "changes.db")
    backend.write_bytes("made.txt", b"created in container\n")
    after = backend.file_hash("made.txt")
    journal.record(
        "run-container", Path(f"{container_config.remote_path}/made.txt"),
        before_hash=None, after_hash=after, approved=True,
    )
    assert journal.rollback_one("run-container", "made.txt", backend=backend) is True
    assert backend.exists("made.txt") is False
