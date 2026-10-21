#!/usr/bin/env python
"""Simple devx reset script - no Unicode checkmarks"""

import os
import shutil
from pathlib import Path


def reset_all():
    """Reset all devx configurations and caches."""
    devx_global = Path.home() / ".devx"

    # Remove config
    config_path = devx_global / "config.json"
    if config_path.exists():
        config_path.unlink()

    # Remove sessions db
    sessions_path = devx_global / "sessions.db"
    if sessions_path.exists():
        sessions_path.unlink()

    # Remove cache db
    cache_db = devx_global / "cache.db"
    if cache_db.exists():
        cache_db.unlink()

    # Remove ONNX cache
    cache_dir = Path.home() / ".cache" / "devx"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)

    # Reset project config if in a devx workspace
    workspace = Path.cwd()
    devx_dir = workspace / ".devx"
    if devx_dir.exists():
        shutil.rmtree(devx_dir)
    env_path = workspace / ".env"
    if env_path.exists():
        env_path.unlink()

    print("Reset complete! Your devx installation is now fresh.")


def reset_global():
    """Reset only global configuration and sessions."""
    devx_global = Path.home() / ".devx"

    config_path = devx_global / "config.json"
    if config_path.exists():
        config_path.unlink()

    sessions_path = devx_global / "sessions.db"
    if sessions_path.exists():
        sessions_path.unlink()

    print("Global config and sessions reset complete!")


def reset_cache():
    """Reset only cache and embeddings."""
    cache_dir = Path.home() / ".cache" / "devx"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)

    cache_db = Path.home() / ".devx" / "cache.db"
    if cache_db.exists():
        cache_db.unlink()

    print("Cache reset complete!")


def reset_project():
    """Reset only project-level configuration."""
    workspace = Path.cwd()
    devx_dir = workspace / ".devx"

    if devx_dir.exists():
        shutil.rmtree(devx_dir)
    env_path = workspace / ".env"
    if env_path.exists():
        env_path.unlink()

    print("Project config reset complete! Global config kept intact.")


def reset_sessions():
    """Reset only session database and event logs."""
    devx_global = Path.home() / ".devx"

    sessions_path = devx_global / "sessions.db"
    if sessions_path.exists():
        sessions_path.unlink()

    print("Session database reset complete!")


def main():
    import sys
    args = sys.argv[1:]

    if not args:
        reset_all()
    elif "--global" in args:
        reset_global()
    elif "--cache" in args:
        reset_cache()
    elif "--project" in args:
        reset_project()
    elif "--sessions" in args:
        reset_sessions()


if __name__ == "__main__":
    main()