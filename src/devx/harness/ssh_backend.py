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
        if uri.startswith("ssh://"):
            parsed = urlparse(uri)
            host = parsed.hostname or "localhost"
            port = parsed.port or 22
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
        resolved_port = int(ssh_cfg.get("port", port))
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
        if client is not None:
            self._connected = True

    @property
    def root(self) -> str:
        return self.config.remote_path

    @property
    def is_remote(self) -> bool:
        return True

    def _ensure_connected(self) -> None:
        if self._connected and self._client is not None:
            return
        try:
            import paramiko
        except ImportError as exc:
            raise ImportError(
                "Remote SSH support requires paramiko. Install with: pip install 'devx-coding-agent[ssh]' or pip install paramiko"
            ) from exc

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
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
            import paramiko
            connect_kwargs["sock"] = paramiko.ProxyCommand(self.config.proxy_command)

        client.connect(**connect_kwargs)
        # Enable TCP keepalive on transport (sends packet every 15s to keep NAT/firewalls alive)
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(15)

        self._client = client
        self._sftp = client.open_sftp()
        self._connected = True

    def _resolve(self, rel_path: str) -> str:
        clean = rel_path.strip().replace("\\", "/")
        if clean.startswith("/"):
            # Ensure it starts with remote_path
            root_norm = posixpath.normpath(self.config.remote_path)
            clean_norm = posixpath.normpath(clean)
            if not clean_norm.startswith(root_norm):
                raise PermissionError(f"Path is outside remote workspace: {clean}")
            return clean_norm
        norm = posixpath.normpath(posixpath.join(self.config.remote_path, clean))
        root_norm = posixpath.normpath(self.config.remote_path)
        if not norm.startswith(root_norm):
            raise PermissionError(f"Path is outside remote workspace: {rel_path}")
        return norm

    def read_bytes(self, rel_path: str, max_bytes: int) -> bytes:
        self._ensure_connected()
        remote = self._resolve(rel_path)
        with self._sftp.open(remote, "rb") as f:
            return f.read(max_bytes)

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
        root = posixpath.normpath(self.config.remote_path)

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
            while not channel.exit_status_ready() or channel.recv_ready():
                if channel.recv_ready():
                    chunk = channel.recv(8192)
                    if chunk:
                        data.extend(chunk)
                else:
                    time.sleep(0.01)
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
        cmd_str = " ".join(shlex.quote(c) for c in command) if isinstance(command, list) else command
        
        # Priority 2: Remote virtualenv auto-activation
        # If .venv/bin/activate exists in remote_path, source it before executing command
        full_remote_cmd = (
            f"cd {shlex.quote(self.config.remote_path)} && "
            f"if [ -f .venv/bin/activate ]; then . .venv/bin/activate; fi && "
            f"{cmd_str}"
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
                raise RunCancelled("Remote command cancelled")
            if time.monotonic() - started > timeout:
                channel.close()
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
        if timed_out:
            bounded = ("Remote command timed out\n" + text).encode("utf-8")[:max_bytes].decode("utf-8", errors="replace")
            return BackendToolResult(False, bounded, exit_code)
        return BackendToolResult(exit_code == 0, text, exit_code)

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
