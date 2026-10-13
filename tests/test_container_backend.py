import hashlib
import shlex
import subprocess

import pytest

from devx.harness.container_backend import ContainerConfig, ContainerWorkspaceBackend
from devx.harness.runtime import CancellationToken, RunCancelled


class _Result:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeContainerRunner:
    """Emulates the exact ``sh -c`` scripts the container backend produces."""

    def __init__(self, root: str = "/app"):
        self.root = root
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/", "/app"}
        self.timeout_commands: set[str] = set()
        self.scripts: list[str] = []
        self.pgid: str = ""
        self.kill_scripts: list[str] = []
        self.realpaths: dict[str, str] = {}

    def __call__(self, argv, *, input=None, timeout=120):
        script = argv[-1]
        self.scripts.append(script)
        if script.startswith("cd "):
            return self._run_command(script, timeout)
        tokens = shlex.split(script)
        head = tokens[0]
        if head == "pwd":
            return _Result(0, (self.root + "\n").encode())
        if head == "realpath" or head == "readlink":
            path = tokens[2]
            resolved = self.realpaths.get(path)
            if resolved is None:
                return _Result(1, b"", b"not supported")
            return _Result(0, (resolved + "\n").encode())
        if head == "head":
            path = tokens[3]
            if path not in self.files:
                return _Result(1, b"", b"no such file")
            return _Result(0, self.files[path][: int(tokens[2])])
        if head == "cat":
            path = tokens[1]
            if path.startswith("/tmp/devx-cgid-"):
                return _Result(0, self.pgid.encode())
            return _Result(0, self.files.get(path, b""))
        if head == "kill":  # kill -TERM -<pid> ... issued by _kill_group
            self.kill_scripts.append(script)
            self.pgid = ""
            return _Result(0)
        if head == "mkdir":  # mkdir -p P && cat > T && mv -f T DEST
            parent, dest = tokens[2], tokens[11]
            self.dirs.add(parent)
            self.files[dest] = input or b""
            return _Result(0)
        if head == "test":
            path = tokens[2]
            flag = tokens[1]
            if flag == "-e":
                present = path in self.files or path in self.dirs
            elif flag == "-f":
                present = path in self.files
            else:
                present = path in self.dirs
            return _Result(0 if present else 1)
        if head == "wc":
            path = tokens[3]
            if path not in self.files:
                return _Result(1, b"", b"no such file")
            return _Result(0, str(len(self.files[path])).encode())
        if head == "rm":
            self.files.pop(tokens[2], None)
            return _Result(0)
        if head == "ls":
            path = tokens[2]
            prefix = path.rstrip("/") + "/"
            children = {f[len(prefix):].split("/")[0] for f in self.files if f.startswith(prefix)}
            return _Result(0, ("\n".join(sorted(children)) + "\n").encode())
        if head == "find":
            root = tokens[1].rstrip("/")
            prefix = root + "/"
            found = [f for f in self.files if f.startswith(prefix)]
            return _Result(0, ("\n".join(found) + "\n").encode())
        if head == "sha256sum":
            path = tokens[1]
            if path not in self.files:
                return _Result(1, b"", b"no such file")
            return _Result(0, hashlib.sha256(self.files[path]).hexdigest().encode() + b"  " + path.encode())
        return _Result(0)

    def _run_command(self, script, timeout):
        if "echo $$ > " in script:
            self.pgid = "4242"
        cmd = script.rsplit("&&", 1)[1].strip()
        if cmd in self.timeout_commands:
            raise subprocess.TimeoutExpired(cmd, timeout)
        if cmd.startswith("echo "):
            return _Result(0, (cmd[len("echo "):].strip("'\"") + "\n").encode())
        if cmd == "false":
            return _Result(1)
        return _Result(0)


@pytest.fixture()
def backend():
    runner = FakeContainerRunner()
    cfg = ContainerConfig(container="devx-test", remote_path="/app")
    return ContainerWorkspaceBackend(cfg, runner=runner), runner


# ── configuration ──────────────────────────────────────────────────────────


def test_container_config_from_uri():
    assert ContainerConfig.from_uri("container://box/srv/app") == ContainerConfig(
        container="box", remote_path="/srv/app"
    )
    assert ContainerConfig.from_uri("docker://box/app") == ContainerConfig(
        container="box", remote_path="/app"
    )
    assert ContainerConfig.from_uri("container://box").remote_path == "."


def test_container_config_from_uri_rejects_unknown_scheme():
    with pytest.raises(ValueError):
        ContainerConfig.from_uri("ssh://user@host/path")


# ── filesystem ─────────────────────────────────────────────────────────────


def test_container_read_write_roundtrip(backend):
    store, runner = backend
    store.write_bytes("src/main.py", b"print('hi')\n")
    assert "/app/src/main.py" in runner.files
    assert store.read_bytes("src/main.py", 1000) == b"print('hi')\n"


def test_container_read_bytes_unbounded_uses_cat(backend):
    """P0.4: ``read_bytes(rel)`` with no cap returns the full file (journal
    backups must never truncate a large pre-write copy), not ``head -c``."""
    store, runner = backend
    payload = b"x" * 5000
    runner.files["/app/big.bin"] = payload
    assert store.read_bytes("big.bin") == payload
    # The bounded path still reads a prefix via head -c.
    assert store.read_bytes("big.bin", 16) == b"x" * 16


def test_container_write_leaves_no_temp_file(backend):
    store, runner = backend
    store.write_bytes("a.txt", b"data")
    assert list(runner.files) == ["/app/a.txt"]


def test_container_read_missing_file_raises(backend):
    store, _ = backend
    with pytest.raises(OSError):
        store.read_bytes("missing.txt", 10)


def test_container_exists_file_and_dir(backend):
    store, _ = backend
    store.write_bytes("a.txt", b"x")
    assert store.exists("a.txt") is True
    assert store.exists("nope.txt") is False
    assert store.is_file("a.txt") is True
    assert store.is_dir("a.txt") is False
    assert store.is_dir(".") is True


def test_container_stat_size_and_remove(backend):
    store, runner = backend
    store.write_bytes("a.txt", b"12345")
    assert store.stat_size("a.txt") == 5
    assert store.remove("a.txt") is True
    assert "a.txt" not in runner.files


def test_container_list_dir_and_walk_files(backend):
    store, _ = backend
    store.write_bytes("src/main.py", b"a")
    store.write_bytes("src/util.py", b"b")
    store.write_bytes("README.md", b"c")
    store.write_bytes(".venv/lib.py", b"d")  # ignored dir

    assert set(store.list_dir("src")) == {"main.py", "util.py"}
    walked = store.walk_files(max_files=50, code_exts={".py"})
    assert walked == ["src/main.py", "src/util.py"]


def test_container_file_hash(backend):
    store, _ = backend
    store.write_bytes("a.txt", b"hello")
    assert store.file_hash("a.txt") == hashlib.sha256(b"hello").hexdigest()
    assert store.file_hash("missing.txt") is None


def test_container_path_sandbox_rejects_escape(backend):
    store, _ = backend
    with pytest.raises(PermissionError):
        store.read_bytes("../outside.txt", 10)
    with pytest.raises(PermissionError):
        store.write_bytes("/etc/passwd", b"x")


def test_container_rejects_symlink_escape(backend):
    """A link inside the workspace that resolves outside it must be refused."""
    store, runner = backend
    runner.files["/app/escape"] = b"payload"
    runner.realpaths["/app/escape"] = "/etc/passwd"
    with pytest.raises(PermissionError):
        store.read_bytes("escape", 10)


def test_container_relative_root_anchored_via_pwd():
    """A bare ``container://name`` root is anchored to the real workdir so
    absolute paths cannot slip past an indeterminate ``.`` root."""
    runner = FakeContainerRunner(root="/app")
    cfg = ContainerConfig(container="devx-test", remote_path=".")
    store = ContainerWorkspaceBackend(cfg, runner=runner)
    runner.files["/app/note.txt"] = b"hello"
    assert store.read_bytes("note.txt", 100) == b"hello"
    with pytest.raises(PermissionError):
        store.read_bytes("/etc/passwd", 10)


# ── command execution ──────────────────────────────────────────────────────


def test_container_run_command_success(backend):
    store, runner = backend
    store.write_bytes(".venv/bin/activate", b"# activate")
    result = store.run_command(["echo", "hello"], timeout=10, cancellation=CancellationToken(), max_bytes=1000)
    assert result.ok is True
    assert result.output.strip() == "hello"
    # The virtualenv is auto-activated when present.
    assert any(".venv/bin/activate" in script for script in runner.scripts)


def test_container_run_command_failure(backend):
    store, _ = backend
    result = store.run_command("false", timeout=10, cancellation=CancellationToken(), max_bytes=1000)
    assert result.ok is False
    assert result.exit_code == 1


def test_container_run_command_timeout(backend):
    store, runner = backend
    runner.timeout_commands.add("sleep 30")
    result = store.run_command("sleep 30", timeout=1, cancellation=CancellationToken(), max_bytes=1000)
    assert result.ok is False
    assert "timed out" in result.output
    # The recorded pgid must have been signalled inside the container and the
    # marker cleaned up, even though the docker CLI client itself timed out.
    assert runner.pgid == ""
    assert any("kill -TERM" in script for script in runner.kill_scripts)


def test_container_run_command_cancelled(backend):
    store, _ = backend
    token = CancellationToken()
    token.cancel()
    with pytest.raises(RunCancelled):
        store.run_command("echo hi", timeout=10, cancellation=token, max_bytes=1000)


def test_container_close_is_noop(backend):
    store, _ = backend
    assert store.close() is None
    assert store.is_remote is True
    assert store.root == "/app"
