"""Filesystem primitives backing the ``devx reset`` command.

The CLI command in :mod:`devx.cli.app` is responsible for parsing flags and
printing output; this module owns *what* gets deleted so the behaviour can be
unit tested without a Typer runner.

Layout of the locations this module manages:

``~/.devx/``
    User-global devx state: ``config.json`` (non-secret settings),
    ``config.env`` (credentials) and ``sessions.db`` (session history).
``~/.cache/devx/``
    Downloaded embedding models and other regenerable caches.
``<workspace>/.devx/`` and ``<workspace>/.env``
    Project-scoped configuration and project credentials.
"""

from __future__ import annotations

import shutil
from pathlib import Path

__all__ = [
    "RESET_SCOPES",
    "cache_db_path",
    "global_config_paths",
    "global_dir",
    "project_config_paths",
    "reset_cache",
    "reset_global_config",
    "reset_project_config",
    "reset_sessions",
]


#: The mutually exclusive scopes understood by ``devx reset``.
RESET_SCOPES: tuple[str, ...] = ("all", "global", "project", "cache", "sessions")


def global_dir() -> Path:
    """Return the user-global devx directory (``~/.devx``)."""
    return Path.home() / ".devx"


def global_config_paths() -> tuple[Path, ...]:
    """Return the user-global files removed by a global/session reset."""
    root = global_dir()
    return (root / "config.json", root / "config.env", root / "sessions.db")


def cache_db_path() -> Path:
    """Return the path of the global cache database (``~/.devx/cache.db``)."""
    return global_dir() / "cache.db"


def project_config_paths(workspace: Path) -> tuple[Path, ...]:
    """Return the project-scoped paths removed by a project reset."""
    return (workspace / ".devx", workspace / ".env")


def _unlink(path: Path) -> bool:
    """Delete *path* if it exists. Returns True when something was removed."""
    try:
        if path.is_file() or path.is_symlink():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def _rmtree(path: Path) -> bool:
    """Delete the directory *path* if it exists. Returns True when removed."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
            return True
    except OSError:
        return False
    return False


def reset_global_config() -> list[Path]:
    """Remove user-global config, credentials and session database.

    Returns the list of paths that were actually deleted.
    """
    removed: list[Path] = []
    for path in global_config_paths():
        if _unlink(path):
            removed.append(path)
    return removed


def reset_sessions() -> list[Path]:
    """Remove only the session database, keeping config and credentials."""
    removed: list[Path] = []
    sessions_db = global_dir() / "sessions.db"
    if _unlink(sessions_db):
        removed.append(sessions_db)
    return removed


def reset_cache() -> list[Path]:
    """Remove the embedding model cache and the global cache database.

    Returns the list of paths that were actually deleted.
    """
    removed: list[Path] = []
    cache_dir = Path.home() / ".cache" / "devx"
    if _rmtree(cache_dir):
        removed.append(cache_dir)
    db = cache_db_path()
    if _unlink(db):
        removed.append(db)
    return removed


def reset_project_config(workspace: Path) -> list[Path]:
    """Remove project-scoped configuration for *workspace*.

    Returns the list of paths that were actually deleted.
    """
    removed: list[Path] = []
    devx_dir, env_path = project_config_paths(workspace)
    if _rmtree(devx_dir):
        removed.append(devx_dir)
    if _unlink(env_path):
        removed.append(env_path)
    return removed
