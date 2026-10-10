from __future__ import annotations

import json
from pathlib import Path


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devx.harness.backend import WorkspaceBackend


def detect_test_command(workspace: Path, backend: WorkspaceBackend | None = None) -> str | None:
    """Return a conservative test command based on project metadata.

    Commands are selected from repository markers only; user configuration
    remains authoritative through ``DEVX_TEST_COMMAND`` (with legacy
    ``DEV_TEST_COMMAND`` compatibility). The returned command
    is later passed through the normal approval and command-policy gates.
    """
    if backend is not None and getattr(backend, "is_remote", False):
        if (
            backend.exists("pyproject.toml")
            or backend.exists("pytest.ini")
            or backend.exists("tox.ini")
            or backend.exists("noxfile.py")
            or backend.is_dir("tests")
        ):
            return "python -m pytest -q"
        if backend.is_file("package.json"):
            try:
                package_data = backend.read_bytes("package.json", 100_000).decode("utf-8", errors="ignore")
                package = json.loads(package_data)
                if "test" in package.get("scripts", {}):
                    if backend.is_file("pnpm-lock.yaml"):
                        return "pnpm test"
                    if backend.is_file("yarn.lock"):
                        return "yarn test"
                    if backend.is_file("bun.lockb") or backend.is_file("bun.lock"):
                        return "bun test"
                    return "npm test"
            except (OSError, ValueError):
                pass
        if backend.is_file("Makefile"):
            try:
                makefile = backend.read_bytes("Makefile", 100_000).decode("utf-8", errors="ignore")
                if any(line.startswith("test:") for line in makefile.splitlines()):
                    return "make test"
            except OSError:
                pass
        if backend.exists("Cargo.toml"):
            return "cargo test"
        if backend.exists("go.mod"):
            return "go test ./..."
        return None

    if ((workspace / "pyproject.toml").exists() or (workspace / "pytest.ini").exists()
            or (workspace / "tox.ini").exists() or (workspace / "noxfile.py").exists()
            or (workspace / "tests").is_dir()):
        return "python -m pytest -q"
    if (workspace / "package.json").is_file():
        try:
            package = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
            if "test" in package.get("scripts", {}):
                if (workspace / "pnpm-lock.yaml").is_file():
                    return "pnpm test"
                if (workspace / "yarn.lock").is_file():
                    return "yarn test"
                if (workspace / "bun.lockb").is_file() or (workspace / "bun.lock").is_file():
                    return "bun test"
                return "npm test"
        except (OSError, ValueError):
            pass
    if (workspace / "Makefile").is_file():
        try:
            makefile = (workspace / "Makefile").read_text(encoding="utf-8")
            if any(line.startswith("test:") for line in makefile.splitlines()):
                return "make test"
        except OSError:
            pass
    if (workspace / "Cargo.toml").exists():
        return "cargo test"
    if (workspace / "go.mod").exists():
        return "go test ./..."
    return None
