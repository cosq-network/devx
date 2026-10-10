from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devx.harness.runtime import CancellationToken


@dataclass(frozen=True)
class BackendToolResult:
    ok: bool
    output: str
    exit_code: int | None = None


class WorkspaceBackend(abc.ABC):
    """Abstract interface defining filesystem and command execution in a workspace."""

    @property
    @abc.abstractmethod
    def root(self) -> str:
        """The root workspace path as a string (local path or remote absolute path)."""
        ...

    @property
    @abc.abstractmethod
    def is_remote(self) -> bool:
        """Whether this backend operates over a remote connection."""
        ...

    @abc.abstractmethod
    def read_bytes(self, rel_path: str, max_bytes: int) -> bytes:
        """Read up to max_bytes from a workspace-relative file."""
        ...

    @abc.abstractmethod
    def write_bytes(self, rel_path: str, data: bytes) -> None:
        """Write bytes atomically to a workspace-relative file."""
        ...

    @abc.abstractmethod
    def exists(self, rel_path: str) -> bool:
        """Check whether a workspace-relative path exists."""
        ...

    @abc.abstractmethod
    def is_file(self, rel_path: str) -> bool:
        """Check whether a workspace-relative path is a regular file."""
        ...

    @abc.abstractmethod
    def is_dir(self, rel_path: str) -> bool:
        """Check whether a workspace-relative path is a directory."""
        ...

    @abc.abstractmethod
    def stat_size(self, rel_path: str) -> int:
        """Return the size in bytes of a workspace-relative file."""
        ...

    @abc.abstractmethod
    def remove(self, rel_path: str) -> bool:
        """Remove a workspace-relative file if it exists."""
        ...

    @abc.abstractmethod
    def list_dir(self, rel_path: str = ".") -> list[str]:
        """List filenames/subdirectories inside a workspace-relative directory."""
        ...

    @abc.abstractmethod
    def walk_files(self, max_files: int = 200, code_exts: set[str] | None = None) -> list[str]:
        """Walk workspace files respecting standard ignore directories."""
        ...

    @abc.abstractmethod
    def file_hash(self, rel_path: str) -> str | None:
        """Compute SHA-256 hash of a workspace file."""
        ...

    @abc.abstractmethod
    def run_command(
        self,
        command: list[str] | str,
        timeout: float,
        cancellation: CancellationToken,
        max_bytes: int,
    ) -> BackendToolResult:
        """Execute a command in the workspace directory."""
        ...

    @abc.abstractmethod
    def close(self) -> None:
        """Clean up connections or temporary resources."""
        ...
