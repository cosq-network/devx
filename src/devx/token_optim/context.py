from __future__ import annotations

import os
from pathlib import Path

from devx.secrets import redact_secrets


CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".swift", ".cpp", ".cc", ".c", ".h", ".hpp", ".cs", ".php", ".rb", ".scala", ".clj", ".hs", ".ml", ".fs", ".vue", ".svelte", ".astro", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".md", ".txt", ".sql", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".dockerfile"}

ALLOWED_DOTFILES = {".gitignore", ".dockerignore"}

IGNORE_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv", "env", "node_modules", "dist", "build", "target", "out", "bin", "obj", ".next", ".nuxt", ".svelte-kit", "coverage", ".coverage", "htmlcov", ".tox", ".nox", "site-packages", "vendor", "bower_components", "jspm_packages", "*.egg-info", ".eggs"}


def discover_project_files(workspace: Path, max_files: int = 200) -> list[Path]:
    """Discover code files in the workspace, respecting common ignore patterns.

    The walk never follows symlinks and skips symlinked files, so a link inside
    the workspace cannot pull files from outside it into the agent's context.
    Safe configuration dotfiles (.gitignore, .dockerignore) are included, while
    credential files (.env) are strictly excluded.
    """
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(workspace, followlinks=False):
        dirnames[:] = [
            name for name in dirnames if name not in IGNORE_DIRS and not name.startswith(".")
        ]
        for name in filenames:
            lower = name.lower()
            if name.startswith("."):
                if lower not in ALLOWED_DOTFILES:
                    continue
            candidate = Path(dirpath) / name
            if lower not in ALLOWED_DOTFILES and candidate.suffix.lower() not in CODE_EXTS:
                continue
            if candidate.is_symlink():
                continue
            try:
                if candidate.stat().st_size > 1_000_000:
                    continue
            except OSError:
                continue
            files.append(candidate)
            if len(files) >= max_files:
                break
        if len(files) >= max_files:
            break
    return sorted(files, key=lambda p: (len(p.parts), p.name.lower()))


def select_relevant_files(query: str, candidate_files: list[Path], max_files: int = 5, embedder=None) -> list[Path]:
    """Select files deterministically; use optional embeddings when supplied."""
    if len(candidate_files) <= max_files:
        return candidate_files
    if embedder is not None:
        try:
            q = embedder.embed([query])[0]
            contents = []
            for path in candidate_files:
                with path.open("rb") as stream:
                    contents.append(stream.read(2000).decode("utf-8", errors="ignore"))
            vectors = embedder.embed(contents)
            try:
                import numpy as np

                scores = list(np.asarray(vectors) @ np.asarray(q))
            except ImportError:
                scores = [sum(a * b for a, b in zip(v, q)) for v in vectors]
            return [candidate_files[i] for i in sorted(range(len(candidate_files)), key=lambda i: scores[i], reverse=True)[:max_files]]
        except (OSError, TypeError, ValueError):
            pass
    terms = {word.lower() for word in query.split() if len(word) > 2}
    scored = []
    for index, path in enumerate(candidate_files):
        text = path.name.lower()
        scored.append((sum(term in text for term in terms), -index, path))
    return [item[2] for item in sorted(scored, reverse=True)[:max_files]]


def generate_file_manifest(files: list[Path], max_chars: int = 8000) -> str:
    """Generate a lightweight structural outline of target files for the planner.

    Extracts file paths, sizes, and top-level definitions or preview lines
    without embedding multi-megabyte source bodies into the planner context.
    """
    manifest_lines: list[str] = ["Target Workspace Files:"]
    total_chars = len(manifest_lines[0])

    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        file_header = f"\n- {path} ({size} bytes)"
        if total_chars + len(file_header) > max_chars:
            manifest_lines.append("\n... [remaining files truncated]")
            break
        manifest_lines.append(file_header)
        total_chars += len(file_header)

        # Attempt to extract top-level structure (function/class headers or first comments)
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                previews = []
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if (
                        stripped.startswith("def ")
                        or stripped.startswith("class ")
                        or stripped.startswith("export ")
                        or stripped.startswith("function ")
                        or stripped.startswith("async def ")
                    ):
                        previews.append(f"    {redact_secrets(stripped)}")
                        if len(previews) >= 8:
                            previews.append("    ...")
                            break
                if previews:
                    preview_text = "\n" + "\n".join(previews)
                    if total_chars + len(preview_text) <= max_chars:
                        manifest_lines.append(preview_text)
                        total_chars += len(preview_text)
        except OSError:
            pass

    return "".join(manifest_lines)


def decoupled_coder_context(files: list[Path]) -> str:
    """Generate a decoupled context reference for the coder agent.

    Provides the target file paths and explicitly instructs the coder
    to inspect exact ranges on-demand using read_file or read_files.
    """
    if not files:
        return "No specific files pre-selected. Use search_files or read_file to inspect the workspace."
    paths_str = "\n".join(f"- {p}" for p in files)
    return (
        "Target Files in Scope:\n"
        f"{paths_str}\n\n"
        "Note: Read specific lines or files on-demand using `read_file` or `read_files` before editing."
    )


def read_context(
    files: list[Path],
    max_file_bytes: int = 1_000_000,
    max_total_bytes: int = 5_000_000,
    *,
    redact: bool = True,
) -> str:
    chunks: list[str] = []
    total = 0
    for path in files:
        if total >= max_total_bytes:
            break
        try:
            with path.open("rb") as stream:
                content = stream.read(max_file_bytes).decode("utf-8", errors="replace")
        except OSError:
            continue
        if redact:
            # Don't ship anything that looks like a live credential to the model.
            content = redact_secrets(content)
        remaining = max_total_bytes - total
        header = f"\n--- {path} ---\n"
        header_bytes = header.encode("utf-8")
        if len(header_bytes) >= remaining:
            chunks.append(header_bytes[:remaining].decode("utf-8", errors="ignore"))
            total = max_total_bytes
            break
        remaining_content = max(0, remaining - len(header.encode("utf-8")))
        content = content.encode("utf-8")[:remaining_content].decode("utf-8", errors="ignore")
        chunks.append(header + content)
        total += len(header.encode("utf-8")) + len(content.encode("utf-8"))
    return "".join(chunks)
