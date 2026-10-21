from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devx.harness.backend import WorkspaceBackend

MENTION_PATTERN = re.compile(r"(?<!\w)@([A-Za-z0-9._~\\/ -]+?)(?=\s|$|[,;:!?])")


def parse_file_mentions(
    text: str,
    workspace: Path | None = None,
    backend: WorkspaceBackend | None = None,
) -> tuple[str, list[Path], list[str]]:
    root = (workspace or Path.cwd()).resolve()
    files: list[Path] = []
    missing: list[str] = []
    for match in MENTION_PATTERN.finditer(text):
        raw = match.group(1).rstrip()
        if backend is not None and getattr(backend, "is_remote", False):
            clean = raw.strip().replace("\\", "/")
            # Block obvious traversal
            if ".." in clean.split("/"):
                missing.append(raw)
                continue
            if clean.startswith("/"):
                remote_root = posixpath.normpath(backend.root) or "/"
                clean_norm = posixpath.normpath(clean)
                # Ensure boundary
                if not (clean_norm == remote_root or clean_norm.startswith(remote_root.rstrip("/") + "/")):
                    missing.append(raw)
                    continue
                try:
                    rel = posixpath.relpath(clean_norm, remote_root)
                    if rel.startswith(".."):
                        missing.append(raw)
                        continue
                except Exception:
                    missing.append(raw)
                    continue
            else:
                rel = posixpath.normpath(clean)
                if rel.startswith("..") or rel.startswith("/"):
                    missing.append(raw)
                    continue

            if backend.exists(rel):
                p = Path(rel)
                if p not in files:
                    files.append(p)
            else:
                missing.append(raw)
            continue

        path = Path(raw).expanduser()
        resolved = (path if path.is_absolute() else root / path).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            missing.append(raw)
            continue
        if resolved.exists():
            if resolved not in files:
                files.append(resolved)
        else:
            missing.append(raw)
    cleaned = MENTION_PATTERN.sub(lambda m: f"[file:{m.group(1).rstrip()}]", text)
    return cleaned, files, missing

