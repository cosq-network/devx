"""Container workspace backend.

Implements the same :class:`~devx.harness.backend.WorkspaceBackend` contract as
the local and SSH backends, so containers inherit identical governance:
approval gating, path sandboxing, atomic writes and journaled rollback. Commands
are executed with ``docker exec`` and file transfers use the container's own
shell, avoiding a bind mount requirement.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import shlex
import subprocess  # nosec B404 - fixed argv, no shell=True, values are quoted.
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from devx.harness.backend import BackendToolResult, WorkspaceBackend
from devx.harness.runtime import CancellationToken, RunCancelled
from devx.token_optim.context import IGNORE_DIRS

Runner = Callable[..., Any]


@dataclass(frozen=True)
class ContainerConfig:
    """How to reach a container workspace."""

    container: str
    remote_path: str = "."
    docker: str = "docker"

    @classmethod
    def from_uri(cls, uri: str, *, docker: str = "docker") -> "ContainerConfig":
        """Parse ``container://<name>/<path>`` or ``docker://<name>/<path>``."""
        for scheme in ("container://", "docker://"):
            if uri.startswith(scheme):
                remainder = uri[len(scheme):]
                name, _, path = remainder.partition("/")
                if not name:
                    raise ValueError(f"Container URI is missing a container name: {uri}")
                return cls(container=name, remote_path="/" + path if path else ".", docker=docker)
        raise ValueError(f"Unsupported container URI: {uri}")


class ContainerWorkspaceBackend(WorkspaceBackend):
    """Workspace backend that executes inside a running container."""

    def __init__(self, config: ContainerConfig, runner: Runner | None = None):
        self.config = config
        self._runner = runner or self._subprocess_run
        self._cwd: str | None = None
        self._real_cache: dict[str, str] = {}

    @property
    def root(self) -> str:
        return self.config.remote_path

    @property
    def is_remote(self) -> bool:
        return True

    # ── process plumbing ────────────────────────────────────────────────

    # Bytes retained from a single captured stream. The reader keeps draining
    # the pipe (so the child never blocks) but only the first
    # ``_MAX_CAPTURE_BYTES`` are kept, bounding devx's memory when a container
    # command emits unbounded output.
    _MAX_CAPTURE_BYTES = 64 * 1024 * 1024

    @classmethod
    def _drain_bounded(cls, stream: Any, limit: int) -> bytes:
        buf = bytearray()
        while True:
            try:
                chunk = stream.read(65536)
            except Exception:
                break
            if not chunk:
                break
            if len(buf) < limit:
                buf.extend(chunk[: limit - len(buf)])
        return bytes(buf)

    @classmethod
    def _subprocess_run(cls, args: list[str], *, input: bytes | None = None, timeout: float = 120) -> Any:
        # nosec B603 B607 - args are constructed here, never from raw user shell text.
        proc = subprocess.Popen(  # nosec B603 B607
            args,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        captured: dict[str, bytes] = {"stdout": b"", "stderr": b""}

        def _capture(stream: Any, key: str) -> None:
            captured[key] = cls._drain_bounded(stream, cls._MAX_CAPTURE_BYTES)

        threads = [
            threading.Thread(target=_capture, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=_capture, args=(proc.stderr, "stderr"), daemon=True),
        ]
        for thread in threads:
            thread.start()
        if input is not None and proc.stdin is not None:
            try:
                proc.stdin.write(input)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
        for thread in threads:
            thread.join(timeout=5)
        if timed_out:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        return subprocess.CompletedProcess(
            proc.args, proc.returncode, captured["stdout"], captured["stderr"]
        )

    def _exec(self, script: str, *, input: bytes | None = None, timeout: float = 120) -> Any:
        argv = [self.config.docker, "exec", "-i", self.config.container, "sh", "-c", script]
        return self._runner(argv, input=input, timeout=timeout)

    @staticmethod
    def _as_bytes(value: Any) -> bytes:
        if value is None:
            return b""
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8", errors="replace")
        return bytes(value)

    @classmethod
    def _decode(cls, result: Any) -> bytes:
        return cls._as_bytes(getattr(result, "stdout", b""))

    @classmethod
    def _stderr(cls, result: Any) -> str:
        return cls._as_bytes(getattr(result, "stderr", b"")).decode("utf-8", errors="replace").strip()

    @staticmethod
    def _inside(root: str, path: str) -> bool:
        if root == "/":
            return path.startswith("/")
        return path == root or path.startswith(root + "/")

    def _anchor(self) -> str:
        """Absolute workspace root inside the container.

        A bare ``container://name`` URI has no path; resolve its working
        directory once (via ``pwd``) so an indeterminate root cannot be used as
        a launchpad for absolute reads/writes.
        """
        raw = self.config.remote_path
        if raw not in (".", ""):
            return posixpath.normpath(raw)
        if self._cwd is None:
            result = self._exec("pwd")
            cwd = self._decode(result).decode("utf-8", errors="replace").strip()
            if getattr(result, "returncode", 1) != 0 or not cwd.startswith("/"):
                raise PermissionError(
                    "Container workspace path is not set and the container working directory could not be determined"
                )
            self._cwd = posixpath.normpath(cwd)
        return self._cwd

    def _realpath(self, norm: str) -> str | None:
        """Canonicalize a container path, following symlinks where supported.

        Uses ``realpath -m`` (GNU coreutils) and falls back to ``readlink -f``
        (BusyBox); returns ``None`` when neither is available so callers keep the
        lexical path (still anchored by the containment check).
        """
        for tool in ("realpath -m", "readlink -f"):
            try:
                result = self._exec(f"{tool} {shlex.quote(norm)}")
            except Exception:
                continue
            if getattr(result, "returncode", 1) != 0:
                continue
            text = self._decode(result).decode("utf-8", errors="replace").strip()
            if text.startswith("/"):
                return posixpath.normpath(text)
        return None

    def _canonical(self, norm: str) -> str:
        cached = self._real_cache.get(norm)
        if cached is not None:
            return cached
        canon = self._realpath(norm) or norm
        if len(self._real_cache) >= 4096:
            self._real_cache.clear()
        self._real_cache[norm] = canon
        return canon

    def _resolve(self, rel_path: str) -> str:
        clean = rel_path.strip().replace("\\", "/")
        root = self._anchor()
        if clean.startswith("/"):
            norm = posixpath.normpath(clean)
        else:
            norm = posixpath.normpath(posixpath.join(root, clean))
        if not self._inside(root, norm):
            raise PermissionError(f"Path is outside container workspace: {rel_path}")
        # Follow symlinks so a link inside the workspace cannot point outside it.
        canon = self._canonical(norm)
        if not self._inside(root, canon):
            raise PermissionError(f"Path resolves outside container workspace: {rel_path}")
        return canon

    # ── filesystem ──────────────────────────────────────────────────────

    def read_bytes(self, rel_path: str, max_bytes: int | None = None) -> bytes:
        remote = self._resolve(rel_path)
        # An unbounded read uses ``cat``; a bounded one uses ``head -c`` so the
        # remote side stops early on huge files instead of streaming them all.
        if max_bytes is None:
            result = self._exec(f"cat {shlex.quote(remote)}")
        else:
            result = self._exec(f"head -c {int(max_bytes)} {shlex.quote(remote)}")
        if getattr(result, "returncode", 1) != 0:
            raise OSError(self._stderr(result) or f"Could not read {rel_path}")
        return self._decode(result)

    def write_bytes(self, rel_path: str, data: bytes) -> None:
        remote = self._resolve(rel_path)
        parent = posixpath.dirname(remote)
        tmp = posixpath.join(parent, f".{posixpath.basename(remote)}.devx-tmp-{uuid.uuid4().hex}")
        script = (
            f"mkdir -p {shlex.quote(parent)} && "
            f"cat > {shlex.quote(tmp)} && "
            f"mv -f {shlex.quote(tmp)} {shlex.quote(remote)}"
        )
        result = self._exec(script, input=data)
        if getattr(result, "returncode", 1) != 0:
            raise OSError(self._stderr(result) or f"Could not write {rel_path}")

    def exists(self, rel_path: str) -> bool:
        remote = self._resolve(rel_path)
        return getattr(self._exec(f"test -e {shlex.quote(remote)}"), "returncode", 1) == 0

    def is_file(self, rel_path: str) -> bool:
        remote = self._resolve(rel_path)
        return getattr(self._exec(f"test -f {shlex.quote(remote)}"), "returncode", 1) == 0

    def is_dir(self, rel_path: str) -> bool:
        remote = self._resolve(rel_path)
        return getattr(self._exec(f"test -d {shlex.quote(remote)}"), "returncode", 1) == 0

    def stat_size(self, rel_path: str) -> int:
        remote = self._resolve(rel_path)
        result = self._exec(f"wc -c < {shlex.quote(remote)}")
        if getattr(result, "returncode", 1) != 0:
            raise OSError(self._stderr(result) or f"Could not stat {rel_path}")
        text = self._decode(result).decode("ascii", errors="ignore").strip()
        return int(text) if text.isdigit() else 0

    def remove(self, rel_path: str) -> bool:
        remote = self._resolve(rel_path)
        return getattr(self._exec(f"rm -f {shlex.quote(remote)}"), "returncode", 1) == 0

    def list_dir(self, rel_path: str = ".") -> list[str]:
        remote = self._resolve(rel_path)
        result = self._exec(f"ls -A1 {shlex.quote(remote)}")
        if getattr(result, "returncode", 1) != 0:
            return []
        return [line for line in self._decode(result).decode("utf-8", errors="replace").splitlines() if line]

    def walk_files(self, max_files: int = 200, code_exts: set[str] | None = None) -> list[str]:
        root = self._anchor()
        result = self._exec(f"find {shlex.quote(root)} -type f")
        if getattr(result, "returncode", 1) != 0:
            return []
        lines = self._decode(result).decode("utf-8", errors="replace").splitlines()
        found: list[str] = []
        for absolute in lines:
            absolute = absolute.strip()
            if not absolute:
                continue
            rel = posixpath.relpath(absolute, root)
            if rel.startswith(".."):
                continue
            if any(part in IGNORE_DIRS or part.startswith(".") for part in rel.split("/")):
                continue
            _, ext = posixpath.splitext(rel)
            if code_exts is not None and ext.lower() not in code_exts:
                continue
            found.append(rel)
            if len(found) >= max_files:
                break
        return sorted(found, key=lambda p: (p.count("/"), p.lower()))

    def file_hash(self, rel_path: str) -> str | None:
        remote = self._resolve(rel_path)
        if not self.is_file(rel_path):
            return None
        result = self._exec(f"sha256sum {shlex.quote(remote)}")
        if getattr(result, "returncode", 1) != 0:
            return None
        text = self._decode(result).decode("ascii", errors="ignore").strip()
        token = text.split()[0] if text else ""
        return token or None

    # ── command execution ───────────────────────────────────────────────

    def run_command(
        self,
        command: list[str] | str,
        timeout: float,
        cancellation: CancellationToken,
        max_bytes: int,
    ) -> BackendToolResult:
        if cancellation.cancelled:
            raise RunCancelled("Container command cancelled")
        cmd_str = " ".join(shlex.quote(c) for c in command) if isinstance(command, list) else command
        # Record the shell's pid so timeout/cancellation can kill the process
        # inside the container. Killing the docker CLI client alone does not
        # stop the exec'd process, so unlike the SSH backend we issue a second
        # ``docker exec`` to signal the recorded pid.
        marker = f"/tmp/devx-cgid-{uuid.uuid4().hex}"
        script = (
            f"cd {shlex.quote(self._anchor())} && "
            f"if [ -f .venv/bin/activate ]; then . .venv/bin/activate; fi && "
            f"echo $$ > {shlex.quote(marker)} && "
            f"{cmd_str}"
        )
        done = threading.Event()

        def _watcher() -> None:
            while not done.wait(0.1):
                if cancellation.cancelled:
                    self._kill_group(marker)
                    return

        watcher = threading.Thread(target=_watcher, daemon=True)
        watcher.start()
        try:
            try:
                result = self._exec(script, timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill_group(marker)
                return BackendToolResult(False, "Remote command timed out", -1)
        finally:
            done.set()
        if cancellation.cancelled:
            self._kill_group(marker)
            raise RunCancelled("Container command cancelled")
        output = self._decode(result) + self._as_bytes(getattr(result, "stderr", b""))
        text = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", output.decode("utf-8", errors="replace"))[:max_bytes]
        return BackendToolResult(getattr(result, "returncode", 1) == 0, text, getattr(result, "returncode", 1))

    def _kill_group(self, marker: str) -> None:
        """Best-effort kill of the command process identified by ``marker``."""
        try:
            cat = self._exec(f"cat {shlex.quote(marker)}")
            token = self._decode(cat).decode("ascii", errors="ignore").strip().split()
            pid = token[0] if token else ""
        except Exception:
            pid = ""
        try:
            if pid.isdigit():
                self._exec(
                    f"kill -TERM -{pid} 2>/dev/null; kill -TERM {pid} 2>/dev/null; "
                    f"sleep 0.2; kill -KILL -{pid} 2>/dev/null; "
                    f"rm -f {shlex.quote(marker)}"
                )
            else:
                self._exec(f"rm -f {shlex.quote(marker)}")
        except Exception:
            pass

    def close(self) -> None:
        """No persistent connection to release; containers are managed externally."""
        return None

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()
