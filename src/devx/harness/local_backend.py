from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from devx.harness.backend import BackendToolResult, WorkspaceBackend
from devx.harness.runtime import CancellationToken, RunCancelled

IGNORE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", "node_modules", "dist", "build", "target",
    "out", "bin", "obj", ".next", ".nuxt", ".svelte-kit", "coverage",
    ".coverage", "htmlcov", ".tox", ".nox", "site-packages", "vendor",
    "bower_components", "jspm_packages", "*.egg-info", ".eggs",
}


class LocalWorkspaceBackend(WorkspaceBackend):
    """Local filesystem and process execution backend."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    @property
    def root(self) -> str:
        return str(self.workspace)

    @property
    def is_remote(self) -> bool:
        return False

    def _resolve(self, rel_path: str) -> Path:
        clean = Path(rel_path).expanduser()
        if not clean.is_absolute():
            clean = self.workspace / clean
        clean = clean.resolve(strict=False)
        try:
            clean.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError(f"Path is outside workspace: {clean}") from exc
        return clean

    def read_bytes(self, rel_path: str, max_bytes: int | None = None) -> bytes:
        target = self._resolve(rel_path)
        with target.open("rb") as f:
            return f.read() if max_bytes is None else f.read(max_bytes)

    def write_bytes(self, rel_path: str, data: bytes) -> None:
        target = self._resolve(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.devx-tmp-{uuid.uuid4().hex}")
        try:
            tmp.write_bytes(data)
            tmp.replace(target)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def exists(self, rel_path: str) -> bool:
        try:
            return self._resolve(rel_path).exists()
        except (OSError, PermissionError):
            return False

    def is_file(self, rel_path: str) -> bool:
        try:
            return self._resolve(rel_path).is_file()
        except (OSError, PermissionError):
            return False

    def is_dir(self, rel_path: str) -> bool:
        try:
            return self._resolve(rel_path).is_dir()
        except (OSError, PermissionError):
            return False

    def stat_size(self, rel_path: str) -> int:
        return self._resolve(rel_path).stat().st_size

    def remove(self, rel_path: str) -> bool:
        try:
            target = self._resolve(rel_path)
            if target.is_file():
                target.unlink()
                return True
        except (OSError, PermissionError):
            pass
        return False

    def list_dir(self, rel_path: str = ".") -> list[str]:
        target = self._resolve(rel_path)
        if not target.is_dir():
            return []
        return [p.name for p in target.iterdir()]

    def walk_files(self, max_files: int = 200, code_exts: set[str] | None = None) -> list[str]:
        files: list[str] = []
        for candidate in self.workspace.rglob("*"):
            try:
                rel = candidate.relative_to(self.workspace)
            except ValueError:
                continue
            if any(part in IGNORE_DIRS or part.startswith(".") for part in rel.parts):
                continue
            if candidate.is_file():
                if code_exts is None or candidate.suffix.lower() in code_exts:
                    try:
                        if candidate.stat().st_size > 1_000_000:
                            continue
                    except OSError:
                        continue
                    files.append(rel.as_posix())
                    if len(files) >= max_files:
                        break
        return sorted(files, key=lambda p: (p.count("/"), p.lower()))

    def file_hash(self, rel_path: str) -> str | None:
        target = self._resolve(rel_path)
        if not target.is_file():
            return None
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def run_command(
        self,
        command: list[str] | str,
        timeout: float,
        cancellation: CancellationToken,
        max_bytes: int,
    ) -> BackendToolResult:
        args = command if isinstance(command, list) else [command]
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                args,
                cwd=self.workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            return BackendToolResult(False, str(exc))

        started = time.monotonic()
        output = bytearray()

        def drain_output() -> None:
            if process.stdout is None:
                return
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    return
                remaining = max_bytes - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])

        reader = threading.Thread(target=drain_output, daemon=True)
        reader.start()
        timed_out = False

        while process.poll() is None:
            if cancellation.cancelled:
                self._terminate(process)
                raise RunCancelled("Command cancelled")
            if time.monotonic() - started > timeout:
                self._terminate(process)
                timed_out = True
                break
            time.sleep(0.02)

        process.wait()
        reader.join(timeout=1)
        if reader.is_alive() and process.stdout is not None:
            process.stdout.close()
            reader.join(timeout=1)

        text = bytes(output).decode("utf-8", errors="replace")
        if timed_out:
            bounded = ("Command timed out\n" + text).encode("utf-8")[:max_bytes].decode("utf-8", errors="replace")
            return BackendToolResult(False, bounded, process.returncode)
        return BackendToolResult(process.returncode == 0, text, process.returncode)

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
            else:
                import signal
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()

    def close(self) -> None:
        pass
