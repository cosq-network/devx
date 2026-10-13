from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shlex
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from devx.harness.backend import BackendToolResult, WorkspaceBackend
from devx.harness.runtime import CancellationToken, RunCancelled

# How long ``git ls-files``-based remote file discovery may run before the
# tool falls back to a (slower) SFTP walk. Prevents a hung remote git process
# from blocking a tool call indefinitely.
_FAST_GIT_TIMEOUT = 15.0

IGNORE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", "node_modules", "dist", "build", "target",
    "out", "bin", "obj", ".next", ".nuxt", ".svelte-kit", "coverage",
    ".coverage", "htmlcov", ".tox", ".nox", "site-packages", "vendor",
    "bower_components", "jspm_packages", "*.egg-info", ".eggs",
}


@dataclass(frozen=True)
class SshConfig:
    host: str
    username: str
    port: int = 22
    password: str | None = None
    key_filename: str | None = None
    remote_path: str = "."
    proxy_command: str | None = None
    sock: Any = None
    # Path to a known_hosts file consulted for host-key verification. When set,
    # unknown- or rekeyed-host connections are refused. When unset the in-memory
    # key store is empty (every host is unknown) unless allow_unknown_host_keys.
    known_hosts: str | None = None
    # Explicit opt-in for throwaway/demo targets (``devx lab`` containers, CI
    # ephemeral servers): accepts a new host key on first connect instead of
    # refusing it. See DEVX_SSH_ALLOW_UNKNOWN_KEYS / ``--accept-new-keys``.
    allow_unknown_host_keys: bool = False

    @classmethod
    def load_ssh_config(cls, host: str) -> dict[str, Any]:
        """Load host configuration from ~/.ssh/config if available."""
        home = Path(os.getenv("HOME") or os.getenv("USERPROFILE") or Path.home())
        config_path = home / ".ssh" / "config"
        if not config_path.is_file():
            # fallback to standard os.path.expanduser
            p = Path(os.path.expanduser("~/.ssh/config"))
            if not p.is_file():
                return {}
            config_path = p
        try:
            import paramiko
            ssh_config = paramiko.SSHConfig()
            with open(config_path, "r", encoding="utf-8") as f:
                ssh_config.parse(f)
            host_config = ssh_config.lookup(host)
            return dict(host_config)
        except Exception:
            return {}

    @classmethod
    def from_uri(cls, uri: str) -> SshConfig:
        """Parse an SSH URI like ssh://user:pass@host:port/path/to/project or user@host:/path, resolving ~/.ssh/config."""
        explicit_user = False
        uri_gave_port = False
        if uri.startswith("ssh://"):
            parsed = urlparse(uri)
            host = parsed.hostname or "localhost"
            port = parsed.port or 22
            if parsed.port is not None:
                uri_gave_port = True
            if parsed.username:
                username = parsed.username
                explicit_user = True
            else:
                username = os.getenv("USER") or os.getenv("USERNAME") or "root"
            password = parsed.password
            remote_path = parsed.path.lstrip("/") or "."
            if not remote_path.startswith("/") and parsed.path.startswith("/"):
                remote_path = "/" + remote_path
        else:
            match = re.match(r"^(?:([^@:]+)@)?([^:]+):(.*)$", uri)
            if match:
                if match.group(1):
                    username = match.group(1)
                    explicit_user = True
                else:
                    username = os.getenv("USER") or os.getenv("USERNAME") or "root"
                host = match.group(2)
                remote_path = match.group(3) or "."
                port = 22
                password = None
            else:
                raise ValueError(f"Invalid SSH workspace URI: {uri}. Format: ssh://user@host:port/path or user@host:/path")

        # Resolve with ~/.ssh/config
        ssh_cfg = cls.load_ssh_config(host)
        resolved_host = ssh_cfg.get("hostname", host)
        resolved_user = username if explicit_user else ssh_cfg.get("user", username)
        # An explicit URI port always wins; ~/.ssh/config only supplies a port
        # when the URI omits one (so ``ssh://host:49153`` is never silently
        # redirected to a wildcard ``Port`` stanza in the user's config).
        resolved_port = int(port)
        if not uri_gave_port:
            cfg_port = ssh_cfg.get("port")
            if cfg_port is not None:
                resolved_port = int(cfg_port)
        key_filename = None
        if "identityfile" in ssh_cfg:
            identities = ssh_cfg["identityfile"]
            key_filename = identities[0] if isinstance(identities, list) else identities
        proxy_command = ssh_cfg.get("proxycommand")

        return cls(
            host=resolved_host,
            username=resolved_user,
            port=resolved_port,
            password=password,
            key_filename=key_filename,
            remote_path=remote_path,
            proxy_command=proxy_command,
        )


class SshWorkspaceBackend(WorkspaceBackend):
    """Remote workspace backend executing over SSH and SFTP."""

    def __init__(self, config: SshConfig, client: Any = None, sftp: Any = None):
        self.config = config
        self._client = client
        self._sftp = sftp
        self._connected = False
        self._home: str | None = None
        if client is not None:
            self._connected = True

    @property
    def root(self) -> str:
        return self.config.remote_path

    @property
    def is_remote(self) -> bool:
        return True

    def _ensure_connected(self, *, force: bool = False) -> None:
        """Ensure a live connection, reconnecting automatically when dropped.

        A previously-established connection whose transport is no longer active
        is transparently replaced, so a dropped link surfaces as at most one
        failed operation instead of poisoning every later call. ``force`` skips
        the liveness probe and always reconnects.
        """
        if not force and self._connected and self._client is not None and self._transport_active():
            return
        if self._client is not None and not force:
            # Stale/dead connection detected; drop it before reconnecting.
            self._reset()

        try:
            import paramiko
        except ImportError as exc:
            raise ImportError(
                "Remote SSH support requires paramiko. Install with: pip install 'devx-coding-agent[ssh]' or pip install paramiko"
            ) from exc

        last_error: Exception | None = None
        for attempt in range(3):
            client = paramiko.SSHClient()
            # Default to RejectPolicy: an unknown or rekeyed host is refused
            # instead of silently auto-trusted (MITM surface). Auto-add remains
            # available only through an explicit opt-in (DEVX_SSH_ALLOW_UNKNOWN_KEYS
            # / ``--accept-new-keys``) for throwaway targets such as ``devx lab``.
            policy = (
                paramiko.AutoAddPolicy()
                if self.config.allow_unknown_host_keys
                else paramiko.RejectPolicy()
            )
            client.set_missing_host_key_policy(policy)
            if self.config.known_hosts:
                try:
                    # Load a user-provided known_hosts file so RejectPolicy can
                    # accept hosts it already trusts and refuse everything else.
                    client.load_host_keys(os.path.expanduser(self.config.known_hosts))
                except Exception:
                    pass

            connect_kwargs: dict[str, Any] = {
                "hostname": self.config.host,
                "port": self.config.port,
                "username": self.config.username,
                "timeout": 15,
            }
            if self.config.password:
                connect_kwargs["password"] = self.config.password
            if self.config.key_filename:
                connect_kwargs["key_filename"] = self.config.key_filename
            if self.config.proxy_command:
                connect_kwargs["sock"] = paramiko.ProxyCommand(self.config.proxy_command)

            try:
                client.connect(**connect_kwargs)
            except Exception as exc:  # noqa: BLE001 - surfaced as a clear connection error
                last_error = exc
                try:
                    client.close()
                except Exception:
                    pass
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                continue

            # Enable TCP keepalive on transport (sends packet every 15s to keep NAT/firewalls alive)
            transport = client.get_transport()
            if transport is not None:
                try:
                    transport.set_keepalive(15)
                except Exception:
                    pass

            self._client = client
            self._sftp = client.open_sftp()
            self._connected = True
            return

        raise ConnectionError(
            f"Could not connect to SSH host {self.config.host}:{self.config.port} "
            f"after 3 attempts: {last_error}"
        ) from last_error

    def _transport_active(self) -> bool:
        """Return whether the current transport is still usable."""
        try:
            transport = self._client.get_transport() if self._client is not None else None
        except Exception:
            return False
        if transport is None:
            return False
        probe = getattr(transport, "is_active", None)
        if not callable(probe):
            return True
        try:
            return bool(probe())
        except Exception:
            return False

    def _reset(self) -> None:
        """Drop the current connection without raising."""
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._connected = False

    def reconnect(self) -> None:
        """Force a fresh connection, used after a detected drop."""
        self._reset()
        self._ensure_connected(force=True)

    def ensure_connected(self) -> None:
        """Public hook that establishes the connection (used by preflight)."""
        self._ensure_connected()

    def health_check(self) -> bool:
        """Run a trivial remote command, reconnecting once if the link dropped.

        Returns ``True`` when the workspace is reachable and responsive.
        """
        for force in (False, True):
            try:
                self._ensure_connected(force=force)
                result = self.run_command(
                    ["true"], timeout=10, cancellation=CancellationToken(), max_bytes=1024
                )
                if result.ok:
                    return True
            except Exception:  # noqa: BLE001 - health checks must never raise
                self._reset()
        return False

    def _root(self) -> str:
        """Absolute workspace root, resolving an unset path to the remote home.

        A URI without a path (``user@host``) leaves ``remote_path`` as ``"."``;
        anchor it to the SFTP-reported working directory once so absolute paths
        cannot escape an indeterminate root.
        """
        raw = self.config.remote_path
        if raw not in (".", ""):
            return posixpath.normpath(raw)
        if self._home is None:
            canon = self._sftp_normalize(".")
            if canon is None:
                raise PermissionError(
                    "Remote workspace path is not set and the server did not report a working directory"
                )
            self._home = canon
        return self._home

    def _sftp_normalize(self, path: str) -> str | None:
        """Realpath via SFTP, following symlinks where the server supports it.

        Returns ``None`` when the connection, extension, or target is
        unavailable so callers fall back to the (still contained) lexical path.
        """
        if self._sftp is None:
            return None
        try:
            canon = self._sftp.normalize(path)
        except Exception:
            return None
        if isinstance(canon, str) and canon.startswith("/"):
            return posixpath.normpath(canon)
        return None

    @staticmethod
    def _inside(root: str, path: str) -> bool:
        if root == "/":
            return path.startswith("/")
        return path == root or path.startswith(root + "/")

    def _resolve(self, rel_path: str) -> str:
        clean = rel_path.strip().replace("\\", "/")
        root = self._root()
        if clean.startswith("/"):
            # Ensure it starts with remote_path (as a real path boundary, not a
            # string prefix: ``/srv/app2`` must not pass for root ``/srv/app``).
            norm = posixpath.normpath(clean)
        else:
            norm = posixpath.normpath(posixpath.join(root, clean))
        if not self._inside(root, norm):
            raise PermissionError(f"Path is outside remote workspace: {rel_path}")
        # Follow symlinks so a link inside the workspace cannot point outside it.
        canon = self._sftp_normalize(norm)
        if canon is not None and not self._inside(root, canon):
            raise PermissionError(f"Path resolves outside remote workspace: {rel_path}")
        return canon or norm

    def read_bytes(self, rel_path: str, max_bytes: int | None = None) -> bytes:
        self._ensure_connected()
        remote = self._resolve(rel_path)
        with self._sftp.open(remote, "rb") as f:
            return f.read() if max_bytes is None else f.read(max_bytes)

    def write_bytes(self, rel_path: str, data: bytes) -> None:
        self._ensure_connected()
        remote = self._resolve(rel_path)
        # Ensure remote parent directory exists
        parent = posixpath.dirname(remote)
        self._mkdir_p(parent)
        tmp = posixpath.join(parent, f".{posixpath.basename(remote)}.devx-tmp-{uuid.uuid4().hex}")
        with self._sftp.open(tmp, "wb") as f:
            f.write(data)
        # Atomic replace via SFTP rename / posix rename
        try:
            self._sftp.posix_rename(tmp, remote)
        except (AttributeError, OSError):
            try:
                self._sftp.remove(remote)
            except OSError:
                pass
            self._sftp.rename(tmp, remote)

    def _mkdir_p(self, remote_dir: str) -> None:
        if remote_dir in {"", ".", "/"}:
            return
        parts = remote_dir.strip("/").split("/")
        current = "/" if remote_dir.startswith("/") else ""
        for part in parts:
            current = posixpath.join(current, part)
            try:
                self._sftp.stat(current)
            except OSError:
                try:
                    self._sftp.mkdir(current)
                except OSError:
                    pass

    def exists(self, rel_path: str) -> bool:
        self._ensure_connected()
        try:
            self._sftp.stat(self._resolve(rel_path))
            return True
        except OSError:
            return False

    def is_file(self, rel_path: str) -> bool:
        self._ensure_connected()
        try:
            st = self._sftp.stat(self._resolve(rel_path))
            return stat.S_ISREG(st.st_mode)
        except OSError:
            return False

    def is_dir(self, rel_path: str) -> bool:
        self._ensure_connected()
        try:
            st = self._sftp.stat(self._resolve(rel_path))
            return stat.S_ISDIR(st.st_mode)
        except OSError:
            return False

    def stat_size(self, rel_path: str) -> int:
        self._ensure_connected()
        return self._sftp.stat(self._resolve(rel_path)).st_size

    def remove(self, rel_path: str) -> bool:
        self._ensure_connected()
        remote = self._resolve(rel_path)
        try:
            self._sftp.remove(remote)
            return True
        except OSError:
            return False

    def list_dir(self, rel_path: str = ".") -> list[str]:
        self._ensure_connected()
        remote = self._resolve(rel_path)
        try:
            return self._sftp.listdir(remote)
        except OSError:
            return []

    def walk_files(self, max_files: int = 200, code_exts: set[str] | None = None) -> list[str]:
        self._ensure_connected()
        root = self._root()

        # Optimization: Attempt single round-trip remote discovery using git or find command
        fast_list = self._fast_remote_file_discovery(root, max_files, code_exts)
        if fast_list is not None:
            return fast_list

        # Fallback to SFTP recursive walking
        results: list[str] = []

        def _walk(curr: str) -> None:
            nonlocal results
            if len(results) >= max_files:
                return
            try:
                entries = self._sftp.listdir_attr(curr)
            except OSError:
                return
            for entry in entries:
                name = entry.filename
                if name in IGNORE_DIRS or name.startswith("."):
                    continue
                path = posixpath.join(curr, name)
                if stat.S_ISDIR(entry.st_mode):
                    _walk(path)
                elif stat.S_ISREG(entry.st_mode):
                    _, ext = posixpath.splitext(name)
                    if code_exts is None or ext.lower() in code_exts:
                        if entry.st_size <= 1_000_000:
                            rel = posixpath.relpath(path, root)
                            results.append(rel)
                            if len(results) >= max_files:
                                return

        _walk(root)
        return sorted(results, key=lambda p: (p.count("/"), p.lower()))

    def _fast_remote_file_discovery(self, root: str, max_files: int, code_exts: set[str] | None) -> list[str] | None:
        try:
            # Check git repository first for clean tracked + untracked files
            cmd = f"cd {shlex.quote(root)} && git ls-files -co --exclude-standard"
            transport = self._client.get_transport()
            if transport is None:
                return None
            channel = transport.open_session()
            channel.exec_command(cmd)
            data = bytearray()
            # Bound the read loop: a hung remote ``git ls-files`` (huge repo,
            # wedged process) must not block the tool call indefinitely.
            deadline = time.monotonic() + _FAST_GIT_TIMEOUT
            exit_ready = False
            while time.monotonic() < deadline:
                if channel.exit_status_ready() and not channel.recv_ready():
                    exit_ready = True
                    break
                if channel.recv_ready():
                    chunk = channel.recv(8192)
                    if chunk:
                        data.extend(chunk)
                else:
                    time.sleep(0.01)
            if not exit_ready:
                # Timed out waiting for the remote process; fall back to the
                # slower SFTP walk instead of hanging the tool.
                channel.close()
                return None
            while channel.recv_ready():
                chunk = channel.recv(8192)
                if chunk:
                    data.extend(chunk)
            exit_code = channel.recv_exit_status()
            if exit_code == 0 and data:
                lines = [line.strip().replace("\\", "/") for line in data.decode("utf-8", errors="ignore").splitlines()]
                filtered = []
                for p in lines:
                    if not p:
                        continue
                    _, ext = posixpath.splitext(p)
                    if code_exts is None or ext.lower() in code_exts:
                        filtered.append(p)
                        if len(filtered) >= max_files:
                            break
                return sorted(filtered, key=lambda p: (p.count("/"), p.lower()))
        except Exception:
            pass
        return None

    def file_hash(self, rel_path: str) -> str | None:
        self._ensure_connected()
        if not self.is_file(rel_path):
            return None
        remote = self._resolve(rel_path)
        digest = hashlib.sha256()
        with self._sftp.open(remote, "rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def run_command(
        self,
        command: list[str] | str,
        timeout: float,
        cancellation: CancellationToken,
        max_bytes: int,
    ) -> BackendToolResult:
        self._ensure_connected()
        try:
            return self._run_command_once(command, timeout, cancellation, max_bytes)
        except RunCancelled:
            raise
        except (OSError, EOFError) as exc:
            # A dropped link surfaces as an OSError/EOFError. Invalidate the
            # connection so the next call reconnects, and report clearly.
            self._reset()
            raise ConnectionError(
                f"Remote connection lost while running command: {exc}"
            ) from exc

    def _run_command_once(
        self,
        command: list[str] | str,
        timeout: float,
        cancellation: CancellationToken,
        max_bytes: int,
    ) -> BackendToolResult:
        cmd_str = " ".join(shlex.quote(c) for c in command) if isinstance(command, list) else command

        # Record the remote process-group id so cancellation/timeout can kill
        # the whole process tree; closing an SSH channel is not a guaranteed
        # process kill on the remote host.
        pgid_path = f"/tmp/devx-pgid-{uuid.uuid4().hex}"
        inner = (
            f"echo $$ > {shlex.quote(pgid_path)}; "
            f"cd {shlex.quote(self._root())} && "
            f"if [ -f .venv/bin/activate ]; then . .venv/bin/activate; fi && "
            f"{cmd_str}"
        )
        quoted = shlex.quote(inner)
        # Prefer ``setsid`` so the command becomes its own session/process group
        # leader; fall back to a plain shell where setsid is unavailable.
        full_remote_cmd = (
            f"if command -v setsid >/dev/null 2>&1; then setsid sh -c {quoted}; "
            f"else sh -c {quoted}; fi"
        )

        transport = self._client.get_transport()
        channel = transport.open_session()
        channel.set_combine_stderr(True)
        channel.exec_command(full_remote_cmd)

        started = time.monotonic()
        output = bytearray()
        timed_out = False

        while not channel.exit_status_ready():
            if cancellation.cancelled:
                channel.close()
                self._kill_remote_group(pgid_path)
                raise RunCancelled("Remote command cancelled")
            if time.monotonic() - started > timeout:
                channel.close()
                self._kill_remote_group(pgid_path)
                timed_out = True
                break
            while channel.recv_ready():
                chunk = channel.recv(8192)
                if chunk:
                    remaining = max_bytes - len(output)
                    if remaining > 0:
                        output.extend(chunk[:remaining])
            time.sleep(0.05)

        # Drain any residual bytes
        while channel.recv_ready():
            chunk = channel.recv(8192)
            if chunk:
                remaining = max_bytes - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])

        exit_code = -1 if timed_out else channel.recv_exit_status()
        text = bytes(output).decode("utf-8", errors="replace")
        # Strip ANSI escape codes to keep clean test diagnostics
        ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        text = ansi_escape.sub("", text)
        self._cleanup_pgid_file(pgid_path)
        if timed_out:
            bounded = ("Remote command timed out\n" + text).encode("utf-8")[:max_bytes].decode("utf-8", errors="replace")
            return BackendToolResult(False, bounded, exit_code)
        return BackendToolResult(exit_code == 0, text, exit_code)

    def _cleanup_pgid_file(self, pgid_path: str) -> None:
        """Best-effort removal of the per-command process-group marker."""
        if self._sftp is None:
            return
        try:
            self._sftp.remove(pgid_path)
        except Exception:
            pass

    def _remote_pgid(self, pgid_path: str) -> int | None:
        """Read the remote process-group id written by ``_run_command_once``."""
        if self._sftp is None:
            return None
        try:
            with self._sftp.open(pgid_path, "rb") as stream:
                raw = stream.read(64)
        except Exception:
            return None
        if not raw:
            return None
        text = raw.decode("ascii", errors="ignore").strip().split()[0] if raw.strip() else ""
        return int(text) if text.isdigit() else None

    def _kill_remote_group(self, pgid_path: str) -> bool:
        """Best-effort kill of the remote process group. Never raises."""
        pgid = self._remote_pgid(pgid_path)
        if pgid is None:
            return False
        try:
            transport = self._client.get_transport() if self._client is not None else None
            if transport is None:
                return False
            channel = transport.open_session()
            channel.exec_command(
                f"kill -TERM -{pgid} 2>/dev/null; sleep 0.2; "
                f"kill -KILL -{pgid} 2>/dev/null; rm -f {shlex.quote(pgid_path)}"
            )
            channel.close()
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._connected = False


def preflight_remote(
    config: SshConfig,
    backend: Any = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Validate a remote workspace without running the agent.

    Returns a JSON-serialisable report. ``status`` is ``"error"`` only when a
    hard requirement fails (connection, workspace directory, or writability);
    an absent git work tree or virtualenv is reported as ``"missing"`` but is
    not fatal. Owns and closes the backend when it creates it.
    """
    checks: dict[str, Any] = {}
    owns_backend = backend is None
    if backend is None:
        backend = SshWorkspaceBackend(config)

    try:
        backend.ensure_connected()
    except Exception as exc:  # noqa: BLE001 - report, do not raise
        checks["connection"] = {"status": "error", "detail": str(exc)}
        return _preflight_report(config, checks, backend if owns_backend else None)

    checks["connection"] = {"status": "ok"}

    # ── workspace path ──
    is_dir = False
    try:
        is_dir = bool(backend.is_dir("."))
        checks["workspace_path"] = (
            {"status": "ok"} if is_dir else {"status": "error", "detail": "remote path is not a directory"}
        )
    except Exception as exc:  # noqa: BLE001
        checks["workspace_path"] = {"status": "error", "detail": str(exc)}

    # ── writability ──
    if is_dir:
        probe = f".devx-preflight-{uuid.uuid4().hex}"
        try:
            backend.write_bytes(probe, b"ok")
            backend.remove(probe)
            checks["writable"] = {"status": "ok"}
        except Exception as exc:  # noqa: BLE001
            checks["writable"] = {"status": "error", "detail": str(exc)}
    else:
        checks["writable"] = {"status": "skipped"}

    # ── git work tree ──
    try:
        result = backend.run_command(
            ["git", "rev-parse", "--is-inside-work-tree"],
            timeout=timeout,
            cancellation=CancellationToken(),
            max_bytes=4096,
        )
        if getattr(result, "ok", False) and "true" in (getattr(result, "output", "") or "").lower():
            checks["git"] = {"status": "ok"}
        else:
            checks["git"] = {"status": "missing", "detail": "not a git work tree"}
    except Exception as exc:  # noqa: BLE001
        checks["git"] = {"status": "missing", "detail": str(exc)}

    # ── virtualenv (optional) ──
    try:
        checks["venv"] = {"status": "ok"} if backend.exists(".venv/bin/activate") else {"status": "missing"}
    except Exception as exc:  # noqa: BLE001
        checks["venv"] = {"status": "missing", "detail": str(exc)}

    return _preflight_report(config, checks, backend if owns_backend else None)


def _preflight_report(config: SshConfig, checks: dict[str, Any], backend_to_close: Any) -> dict[str, Any]:
    if backend_to_close is not None:
        try:
            backend_to_close.close()
        except Exception:
            pass
    has_error = any(entry.get("status") == "error" for entry in checks.values())
    return {
        "remote": config.host,
        "port": config.port,
        "username": config.username,
        "path": config.remote_path,
        "status": "error" if has_error else "ok",
        "checks": checks,
    }
