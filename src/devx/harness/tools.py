from __future__ import annotations

import difflib
import os
import shutil
import subprocess  # nosec B404 - commands are tokenized, shell=False, approval-gated, and policy-validated.
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from devx.harness.backend import WorkspaceBackend
from devx.harness.changes import ChangeJournal, file_hash
from devx.harness.local_backend import LocalWorkspaceBackend
from devx.harness.permissions import PermissionError, PermissionPolicy
from devx.harness.runtime import CancellationToken, RunCancelled


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str
    exit_code: int | None = None


class WorkspaceTools:
    _preview_bytes = 12_000

    def __init__(self, policy: PermissionPolicy, *, max_bytes: int = 1_000_000, timeout: float = 120,
                 journal: ChangeJournal | None = None, run_id: str = "adhoc", cancellation: CancellationToken | None = None,
                 backend: WorkspaceBackend | None = None):
        self.policy, self.max_bytes, self.timeout = policy, max_bytes, timeout
        self.journal, self.run_id = journal, run_id
        self.cancellation = cancellation or CancellationToken()
        self.backend = backend or LocalWorkspaceBackend(policy.workspace)

    def requires_approval(self, action: str, target: str | Path) -> bool:
        """Return whether a configured policy still needs an approval callback."""
        return self.policy.requires_approval(action, target)

    def _normalize_target_path(self, path: str | Path) -> Path:
        clean = str(path).strip().replace("\\", "/")
        if getattr(self.backend, "is_remote", False) and clean.startswith("/"):
            import posixpath
            root_norm = posixpath.normpath(self.backend.root)
            clean_norm = posixpath.normpath(clean)
            if clean_norm.startswith(root_norm):
                return Path(posixpath.relpath(clean_norm, root_norm))
            return Path(clean_norm.lstrip("/"))
        return Path(path)

    def read(self, path: str | Path) -> ToolResult:
        target = self.policy.path(self._normalize_target_path(path))
        try:
            rel = target.relative_to(self.policy.workspace).as_posix()
            data = self.backend.read_bytes(rel, self.max_bytes)
            return ToolResult(True, data.decode("utf-8", errors="replace"))
        except (OSError, UnicodeError) as exc:
            return ToolResult(False, str(exc))

    def write(self, path: str | Path, content: str, *, approved: bool = False) -> ToolResult:
        target = self._approved_path(self._normalize_target_path(path), approved)
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_bytes:
            return ToolResult(False, f"Content exceeds the {self.max_bytes}-byte file limit")
        rel = target.relative_to(self.policy.workspace).as_posix()
        before = self.backend.file_hash(rel)
        backup = None
        if self.journal and self.backend.exists(rel):
            backup_dir = self.journal.db_path.parent / "backups" / self.run_id
            relative = target.relative_to(self.policy.workspace)
            backup = backup_dir / relative.parent / f"{relative.name}.{before}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                try:
                    backup.write_bytes(self.backend.read_bytes(rel, self.max_bytes))
                except OSError:
                    pass
        try:
            self.backend.write_bytes(rel, encoded)
            after = self.backend.file_hash(rel)
            if self.journal:
                self.journal.record(self.run_id, target, before, after, approved, backup)
            return ToolResult(True, f"Wrote {target}")
        except (OSError, UnicodeError) as exc:
            return ToolResult(False, str(exc))

    def preview_write(self, path: str | Path, content: str) -> str:
        """Return a bounded unified diff for a proposed write without mutating files."""
        target = self.policy.path(self._normalize_target_path(path))
        current = self._read_preview(target)
        proposed = content.encode("utf-8")[: self._preview_bytes].decode("utf-8", errors="replace")
        diff = difflib.unified_diff(
            current.splitlines(True),
            proposed.splitlines(True),
            fromfile=str(target),
            tofile=str(target),
        )
        return self._bound_text("".join(diff))

    def replace(self, path: str | Path, old: str, new: str, *, expected_hash: str | None = None,
                approved: bool = False) -> ToolResult:
        """Apply an exact, optimistic-concurrency-checked text replacement."""
        target = self._approved_path(self._normalize_target_path(path), approved)
        rel = target.relative_to(self.policy.workspace).as_posix()
        try:
            if self.backend.stat_size(rel) > self.max_bytes:
                return ToolResult(False, f"File exceeds the {self.max_bytes}-byte file limit")
            current = self.backend.read_bytes(rel, self.max_bytes).decode("utf-8")
        except (OSError, UnicodeError) as exc:
            return ToolResult(False, str(exc))
        current_hash = self.backend.file_hash(rel)
        if expected_hash is not None and current_hash != expected_hash:
            return ToolResult(False, "CONFLICT: file changed since it was inspected")
        if old not in current:
            return ToolResult(False, "PATCH FAILED: expected text was not found")
        if current.count(old) != 1:
            return ToolResult(False, "PATCH FAILED: expected text is not unique")
        preview = "".join(difflib.unified_diff(current.splitlines(True), current.replace(old, new).splitlines(True), fromfile=str(target), tofile=str(target)))
        result = self.write(target, current.replace(old, new), approved=approved)
        return ToolResult(result.ok, (preview + "\n" + result.output)[:self.max_bytes], result.exit_code)

    def preview_replace(self, path: str | Path, old: str, new: str) -> str:
        """Return a bounded diff for an exact replacement proposal."""
        target = self.policy.path(self._normalize_target_path(path))
        current = self._read_preview(target)
        proposed = current.replace(old, new, 1)
        diff = difflib.unified_diff(
            current.splitlines(True),
            proposed.splitlines(True),
            fromfile=str(target),
            tofile=str(target),
        )
        return self._bound_text("".join(diff))

    def search(self, query: str, path: str | Path = ".") -> ToolResult:
        if getattr(self.backend, "is_remote", False):
            try:
                rel_root = str(path).strip().replace("\\", "/").lstrip("./")
                files = self.backend.walk_files(max_files=500)
                output = bytearray()
                for rel in files:
                    if rel_root and not rel.startswith(rel_root):
                        continue
                    try:
                        if self.backend.stat_size(rel) > self.max_bytes:
                            continue
                        content = self.backend.read_bytes(rel, self.max_bytes).decode("utf-8", errors="replace")
                    except (OSError, UnicodeError):
                        continue
                    for number, line in enumerate(content.splitlines(), 1):
                        if query.lower() in line.lower():
                            encoded = f"{rel}:{number}:{line}\n".encode()
                            remaining = self.max_bytes - len(output)
                            if remaining <= 0:
                                return ToolResult(True, bytes(output).decode("utf-8", errors="replace"))
                            output.extend(encoded[:remaining])
                return ToolResult(True, bytes(output).decode("utf-8", errors="replace"))
            except OSError as exc:
                return ToolResult(False, str(exc))

        target = self.policy.path(Path(path))
        try:
            output = bytearray()
            for candidate in target.rglob("*") if target.is_dir() else [target]:
                try:
                    candidate = self.policy.path(candidate)
                    if not candidate.is_file() or candidate.stat().st_size > self.max_bytes:
                        continue
                except (OSError, PermissionError):
                    continue
                try:
                    for number, line in enumerate(candidate.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if query.lower() in line.lower():
                            encoded = f"{candidate}:{number}:{line}\n".encode()
                            remaining = self.max_bytes - len(output)
                            if remaining <= 0:
                                return ToolResult(True, bytes(output).decode("utf-8", errors="replace"))
                            output.extend(encoded[:remaining])
                except OSError:
                    continue
            return ToolResult(True, bytes(output).decode("utf-8", errors="replace"))
        except OSError as exc:
            return ToolResult(False, str(exc))

    def git_diff(self) -> ToolResult:
        return self.run(["git", "diff", "--no-ext-diff", "--", "."], approved=True)

    def run(self, command: list[str] | str, *, approved: bool = False) -> ToolResult:
        try:
            # Validate the command first so dangerous commands still raise a
            # policy error, while preserving the historical ToolResult-based
            # response for an ordinary command that simply needs approval.
            args = self.policy.command(command, approved=True)
            if self.policy.requires_approval("shell", " ".join(args)) and not approved:
                return ToolResult(False, "Command requires explicit approval")
            backend_res = self.backend.run_command(
                args,
                timeout=self.timeout,
                cancellation=self.cancellation,
                max_bytes=self.max_bytes,
            )
            return ToolResult(backend_res.ok, backend_res.output, backend_res.exit_code)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, str(exc))

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)  # nosec B603 B607
            else:
                import signal

                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()

    def _approved_path(self, path: str | Path, approved: bool) -> Path:
        return self.policy.path(Path(path), write=True, approved=approved)

    def _read_preview(self, target: Path) -> str:
        try:
            rel = target.relative_to(self.policy.workspace).as_posix()
            return self.backend.read_bytes(rel, self._preview_bytes).decode("utf-8", errors="replace")
        except (FileNotFoundError, OSError):
            return ""

    def _bound_text(self, value: str) -> str:
        return value.encode("utf-8")[: self._preview_bytes].decode("utf-8", errors="replace")

    @staticmethod
    def _args(command: list[str] | str) -> list[str]:
        import shlex
        return shlex.split(command, posix=False) if isinstance(command, str) else list(command)
