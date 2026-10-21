from __future__ import annotations

import difflib
import os
import subprocess  # nosec B404 - commands are tokenized, shell=False, approval-gated, and policy-validated.
from dataclasses import dataclass
from pathlib import Path

from devx.harness.backend import WorkspaceBackend
from devx.harness.changes import ChangeJournal
from devx.harness.local_backend import LocalWorkspaceBackend
from devx.harness.permissions import PermissionError, PermissionPolicy
from devx.harness.runtime import CancellationToken
from devx.secrets import SECRET_SCAN_MODE_DEFAULT, redact_secrets, scan_secrets


@dataclass(frozen=True)
class ToolResult:
    """Result of a workspace tool operation.

    Attributes:
        ok: Whether the operation succeeded.
        output: The command/output text produced.
        exit_code: Process exit code, if applicable (``None`` for read operations).
        warning: Optional advisory attached to a successful result (e.g. a
            secret-scan flag). Warnings never change ``ok``.
    """
    ok: bool
    output: str
    exit_code: int | None = None
    warning: str | None = None


class WorkspaceTools:
    """Workspace tools for reading, writing, searching, and running commands.

    All operations respect the ``PermissionPolicy`` and require explicit
    approval for writes and shell commands by default. File writes are
    atomic with automatic backups and before/after hashes recorded in a
    ``ChangeJournal``.

    Attributes:
        policy: The permission policy governing approval rules.
        max_bytes: Maximum file size to read/edit (bytes).
        timeout: Timeout for shell commands (seconds).
        journal: Change journal for tracking mutations (optional).
        run_id: Identifier for the current run.
        cancellation: Token for requesting cancellation.
        backend: Filesystem backend (local or SSH).
    """
    _preview_bytes = 12_000

    def __init__(self, policy: PermissionPolicy, *, max_bytes: int = 1_000_000, timeout: float = 120,
                 journal: ChangeJournal | None = None, run_id: str = "adhoc", cancellation: CancellationToken | None = None,
                 backend: WorkspaceBackend | None = None, secret_scan_mode: str = SECRET_SCAN_MODE_DEFAULT,
                 sandbox: str = "none"):
        self.policy, self.max_bytes, self.timeout = policy, max_bytes, timeout
        self.journal, self.run_id = journal, run_id
        self.cancellation = cancellation or CancellationToken()
        self.backend = backend or LocalWorkspaceBackend(policy.workspace, sandbox=sandbox)
        self.secret_scan_mode = secret_scan_mode

    def _secret_summary(self, path: str, hits: list) -> str:
        kinds = sorted({hit.kind for hit in hits})
        detail = ", ".join(kinds)
        return (
            f"Potential secret(s) detected in {path}: {detail}. "
            f"Review before sharing this file with the model."
        )

    def _apply_secret_policy(self, rel: str, content: str) -> tuple[str, str | None]:
        """Apply the configured secret-scan mode to read content.

        Returns ``(content, warning)``. In ``block`` mode the caller receives a
        failure; here we only redact (``redact``) or annotate (``warn``).
        """
        if self.secret_scan_mode == "off":
            return content, None
        hits = scan_secrets(content)
        if not hits:
            return content, None
        warning = self._secret_summary(rel, hits)
        if self.secret_scan_mode == "redact":
            return redact_secrets(content), warning
        return content, warning

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
        target = self.policy.path(self._normalize_target_path(path), remote=self.backend.is_remote)
        try:
            rel = target.relative_to(self.policy.workspace).as_posix()
            data = self.backend.read_bytes(rel, self.max_bytes)
            content = data.decode("utf-8", errors="replace")
        except (OSError, UnicodeError) as exc:
            return ToolResult(False, str(exc))
        if self.secret_scan_mode == "block":
            hits = scan_secrets(content)
            if hits:
                return ToolResult(False, f"Blocked by secret scan: {self._secret_summary(rel, hits)}")
        content, warning = self._apply_secret_policy(rel, content)
        return ToolResult(True, content, warning=warning)

    def write(self, path: str | Path, content: str, *, approved: bool = False, expected_hash: str | None = None) -> ToolResult:
        """Write content to a file inside the workspace.

        If ``expected_hash`` is provided, the file's current SHA-256 hash is
        checked *before* writing; if the hash differs, the write is rejected
        so that a concurrent reader cannot silently clobber hand-edited content.
        """
        target = self._approved_path(self._normalize_target_path(path), approved)
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_bytes:
            return ToolResult(False, f"Content exceeds the {self.max_bytes}-byte file limit")
        rel = target.relative_to(self.policy.workspace).as_posix()
        # Writes are scanned too: in ``block`` mode a credential must not be
        # persisted to disk, not just kept away from the model on read.
        if self.secret_scan_mode == "block":
            hits = scan_secrets(content)
            if hits:
                return ToolResult(False, f"Blocked by secret scan: {self._secret_summary(rel, hits)}")
        warning = None
        if self.secret_scan_mode in ("warn", "redact"):
            hits = scan_secrets(content)
            if hits:
                warning = self._secret_summary(rel, hits)
        # --- TOCTOU guard: re-read and verify hash if expected_hash given ---
        if expected_hash is not None:
            try:
                current_hash = self.backend.file_hash(rel)
                if current_hash != expected_hash:
                    return ToolResult(False, "CONFLICT: file changed since inspection; write aborted")
            except (OSError, UnicodeError):
                pass  # if we can't hash, proceed (best-effort)
        before = self.backend.file_hash(rel)
        backup = None
        if self.journal and self.backend.exists(rel):
            backup_dir = self.journal.db_path.parent / "backups" / self.run_id
            relative = target.relative_to(self.policy.workspace)
            backup = backup_dir / relative.parent / f"{relative.name}.{before}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                try:
                    # Unbounded read: a pre-existing file larger than max_bytes
                    # must still be captured in full so rollback restores it
                    # byte-for-byte instead of truncating it to the read cap.
                    backup.write_bytes(self.backend.read_bytes(rel))
                except OSError:
                    pass
        try:
            self.backend.write_bytes(rel, encoded)
            after = self.backend.file_hash(rel)
            if self.journal:
                # ``rel`` is the backend-relative path — the only value that lets
                # drift checks and rollback locate the file on a remote/container
                # workspace (``target`` is a local-workspace absolute path).
                self.journal.record(self.run_id, target, before, after, approved, backup, rel_path=rel)
            return ToolResult(True, f"Wrote {target}", warning=warning)
        except (OSError, UnicodeError) as exc:
            return ToolResult(False, str(exc))

    def preview_write(self, path: str | Path, content: str) -> str:
        """Return a bounded unified diff for a proposed write without mutating files."""
        target = self.policy.path(self._normalize_target_path(path), remote=self.backend.is_remote)
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
        # Re-read the file right before writing to catch any concurrent modification
        try:
            current_before_write = self.backend.read_bytes(rel, self.max_bytes).decode("utf-8")
        except (OSError, UnicodeError) as exc:
            return ToolResult(False, str(exc))
        # Verify the hash is still the same after the re-read
        if expected_hash is not None:
            new_hash = self.backend.file_hash(rel)
            if new_hash != expected_hash:
                return ToolResult(False, "CONFLICT: file changed between read and write")
        # Perform the replacement on the re-read content (protects against TOCTOU)
        updated = current_before_write.replace(old, new, 1)
        result = self.write(target, updated, approved=approved, expected_hash=expected_hash)
        preview = "".join(difflib.unified_diff(current.splitlines(True), updated.splitlines(True), fromfile=str(target), tofile=str(target)))
        return ToolResult(result.ok, (preview + "\n" + result.output)[:self.max_bytes], result.exit_code)

    def preview_replace(self, path: str | Path, old: str, new: str) -> str:
        """Return a bounded diff for an exact replacement proposal."""
        target = self.policy.path(self._normalize_target_path(path), remote=self.backend.is_remote)
        current = self._read_preview(target)
        proposed = current.replace(old, new, 1)
        diff = difflib.unified_diff(
            current.splitlines(True),
            proposed.splitlines(True),
            fromfile=str(target),
            tofile=str(target),
        )
        return self._bound_text("".join(diff))

    def _line_for_output(self, rel: str, line: str) -> tuple[str | None, str | None]:
        """Apply the secret-scan mode to one search result line.

        Returns ``(line, warning)``. ``line`` is ``None`` in ``block`` mode so
        the caller skips the line entirely rather than sending it to the model.
        """
        if self.secret_scan_mode == "off":
            return line, None
        hits = scan_secrets(line)
        if not hits:
            return line, None
        warning = self._secret_summary(rel, hits)
        if self.secret_scan_mode == "block":
            return None, warning
        if self.secret_scan_mode == "redact":
            return redact_secrets(line), warning
        return line, warning

    def search(self, query: str, path: str | Path = ".") -> ToolResult:
        if getattr(self.backend, "is_remote", False):
            try:
                rel_root = str(path).strip().replace("\\", "/").lstrip("./")
                files = self.backend.walk_files(max_files=500)
                output = bytearray()
                warnings: list[str] = []
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
                        if query.lower() not in line.lower():
                            continue
                        line_out, warning = self._line_for_output(rel, line)
                        if warning and warning not in warnings:
                            warnings.append(warning)
                        if line_out is None:
                            continue
                        encoded = f"{rel}:{number}:{line_out}\n".encode()
                        remaining = self.max_bytes - len(output)
                        if remaining <= 0:
                            return ToolResult(True, bytes(output).decode("utf-8", errors="replace"), warning="; ".join(warnings) or None)
                        output.extend(encoded[:remaining])
                return ToolResult(True, bytes(output).decode("utf-8", errors="replace"), warning="; ".join(warnings) or None)
            except OSError as exc:
                return ToolResult(False, str(exc))

        target = self.policy.path(Path(path), remote=self.backend.is_remote)
        try:
            output = bytearray()
            warnings: list[str] = []
            for candidate in target.rglob("*") if target.is_dir() else [target]:
                try:
                    candidate = self.policy.path(candidate, remote=self.backend.is_remote)
                    if not candidate.is_file() or candidate.stat().st_size > self.max_bytes:
                        continue
                except (OSError, PermissionError):
                    continue
                try:
                    for number, line in enumerate(candidate.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if query.lower() not in line.lower():
                            continue
                        line_out, warning = self._line_for_output(str(candidate), line)
                        if warning and warning not in warnings:
                            warnings.append(warning)
                        if line_out is None:
                            continue
                        encoded = f"{candidate}:{number}:{line_out}\n".encode()
                        remaining = self.max_bytes - len(output)
                        if remaining <= 0:
                            return ToolResult(True, bytes(output).decode("utf-8", errors="replace"), warning="; ".join(warnings) or None)
                        output.extend(encoded[:remaining])
                except OSError:
                    continue
            return ToolResult(True, bytes(output).decode("utf-8", errors="replace"), warning="; ".join(warnings) or None)
        except OSError as exc:
            return ToolResult(False, str(exc))

    def git_diff(self) -> ToolResult:
        return self.run(["git", "diff", "--no-ext-diff", "--", "."], approved=True)

    def run(self, command: list[str] | str, *, approved: bool = False) -> ToolResult:
        try:
            # Validate the command first so dangerous commands still raise a
            # policy error, while preserving the historical ToolResult-based
            # response for an ordinary command that simply needs approval.
            args = self.policy.command(command, approved=True, remote=self.backend.is_remote)
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
        return self.policy.path(Path(path), write=True, approved=approved, remote=self.backend.is_remote)

    def _read_preview(self, target: Path) -> str:
        try:
            rel = target.relative_to(self.policy.workspace).as_posix()
            return self.backend.read_bytes(rel, self._preview_bytes).decode("utf-8", errors="replace")
        except (FileNotFoundError, OSError):
            return ""

    def check_approval(self, action: str, target: str, reason: str = "") -> tuple[bool, str | None]:
        """Check if an action requires approval and return whether it's allowed.
        
        Returns (requires_approval, denial_reason) tuple.
        If approval is required and not granted, returns (True, denial_message).
        If approval is not required or already granted, returns (False, None).
        """
        if self.requires_approval(action, target):
            # Approval is required - the caller should check the user's decision
            # via the allowed() callback or present an approval prompt
            return True, None
        return False, None

    def _bound_text(self, value: str) -> str:
        return value.encode("utf-8")[: self._preview_bytes].decode("utf-8", errors="replace")

    @staticmethod
    def _args(command: list[str] | str) -> list[str]:
        import shlex
        return shlex.split(command, posix=False) if isinstance(command, str) else list(command)
