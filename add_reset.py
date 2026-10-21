#!/usr/bin/env python
"""Add devx reset command to CLI app"""

with open('src/devx/cli/app.py', 'r', errors='replace') as f:
    content = f.read()

# The reset command to add - I'll insert it after the test_cmd function
# Find the test_cmd function end
test_cmd_marker = 'subprocess.run(unit_cmd.split(), cwd=str(settings.workspace), check=False)'
test_cmd_pos = content.find(test_cmd_marker)

if test_cmd_pos >= 0:
    # Find the end of that line
    insert_pos = content.rfind('\\n', 0, test_cmd_pos) + 1
    
    # The reset command function
    reset_func = '''

@app.command()
def reset(
    _click: typer.Context,
    --all: bool = typer.Option(
        True,
        "--all",
        help="Reset all configurations and caches. This is the default behavior.",
    ),
    --global: bool = typer.Option(
        False,
        "--global",
        help="Reset only global configuration and sessions (~/.devx/config.json and ~/.devx/sessions.db).",
    ),
    --project: bool = typer.Option(
        False,
        "--project",
        help="Reset only project-level configuration (.devx/ and .env). Keeps global config intact.",
    ),
    --cache: bool = typer.Option(
        False,
        "--cache",
        help="Reset only cache and embeddings (~/.cache/devx/ and ~/.devx/cache.db).",
    ),
    --sessions: bool = typer.Option(
        False,
        "--sessions",
        help="Reset only session database and event logs (~/.devx/sessions.db).",
    ),
    --force: bool = typer.Option(
        False,
        "--force",
        help="Skip confirmation prompt.",
    ),
    --yes: bool = typer.Option(
        False,
        "--yes",
        help="Assume yes for all prompts, useful for CI/CD.",
    ),
) -> None:
    \"\"\"Reset devx configurations and caches.

    Options:
    --all: Reset everything. Removes global config, sessions, cache, and project config.
    --global: Reset only global config and sessions.
    --project: Reset only project-level config (.devx/ and .env). Keeps global config intact.
    --cache: Reset only cache and embeddings.
    --sessions: Reset only session database and event logs.
    --force: Skip confirmation prompt.
    --yes: Assume yes for all prompts.

    Examples:
    devx reset                   # Reset everything
    devx reset --global          # Reset global config and sessions only
    devx reset --project         # Reset project config only
    devx reset --cache           # Reset cache and embeddings only
    devx reset --sessions        # Reset session database only
    devx reset --force           # Skip confirmation prompt
    devx reset --yes           # Assume yes for all prompts
    \"\"\"
    import shutil
    from pathlib import Path
    
    def _reset_global_config() -> None:
        \"\"\"Reset global config and sessions.\"\"\"\n    devx_global = Path.home() / \".devx\"\n    config_path = devx_global / \"config.json\"\n    if config_path.exists():\n        config_path.unlink()\n    sessions_path = devx_global / \"sessions.db\"\n    if sessions_path.exists():\n        sessions_path.unlink()\n\n\ndef _reset_cache() -> None:\n    \"\"\"Reset cache and embeddings.\"\"\"\n    cache_dir = Path.home() / \".cache\" / \"devx\"\n    if cache_dir.exists():\n        shutil.rmtree(cache_dir)\n    cache_db = devx_global / \"cache.db\"\n    if cache_db.exists():\n        cache_db.unlink()\n\n\ndef _reset_project_config(workspace: Path) -> None:\n    \"\"\"Reset project-level configuration.\"\"\"\n    devx_dir = workspace / \".devx\"\n    if devx_dir.exists():\n        shutil.rmtree(devx_dir)\n    env_path = workspace / \".env\"\n    if env_path.exists():\n        env_path.unlink()\n\n\ndef _confirm_reset(reset_type: str, force: bool = False, yes: bool = False) -> bool:\n    \"\"\"Prompt user for confirmation.\"\"\"\n    if force or yes:\n        return True\n    prompts = {\n        \"all\": \"This will reset ALL devx configurations and caches.\\n  - ~/.devx/ (sessions, knowledge graph, config)\\n  - ~/.cache/devx/ (embedding models)\\n  - Project .devx/ and .env (if in workspace)\\n\\nAre you sure? [y/N]: \",\n        \"global\": \"This will reset global config and sessions.\\n  - ~/.devx/config.json and ~/.devx/sessions.db\\n\\nAre you sure? [y/N]: \",\n        \"project\": \"This will reset project config (.devx/ and .env).\\n  - Keeps global config intact.\\n\\nAre you sure? [y/N]: \",\n        \"cache\": \"This will reset cache and embedding models.\\n  - ~/.cache/devx/ (ONNX models)\\n  - ~/.devx/cache.db\\n\\nAre you sure? [y/N]: \",\n        \"sessions\": \"This will reset session database and event logs.\\n  - ~/.devx/sessions.db\\n  - DLQ entries removed\\n\\nAre you sure? [y/N]: \",\n    }\n    if yes:\n        return True\n    response = input(prompts[reset_type])\n    return response.lower().strip() in (\"y\", \"yes\")\n\n\ndef _reset_all(force: bool = False, yes: bool = False) -> None:\n    \"\"\"Reset all configurations and caches.\"\"\"\n    if not _confirm_reset(\"all\", force, yes):\n        typer.echo(\"Reset cancelled.\")\n        return\n    _reset_global_config()\n    _reset_cache()\n    workspace = Path.cwd()\n    if (workspace / \".devx\").exists():\n        _reset_project_config(workspace)\n    typer.echo(\"Reset complete! Your devx installation is now fresh.\")\n\n\ndef _reset_global(force: bool = False, yes: bool = False) -> None:\n    \"\"\"Reset only global configuration and sessions.\"\"\"\n    if not _confirm_reset(\"global\", force, yes):\n        typer.echo(\"Reset cancelled.\")\n        return\n    _reset_global_config()\n    typer.echo(\"Global config and sessions reset complete!\")\n\n\ndef _reset_project(workspace: Path, force: bool = False, yes: bool = False) -> None:\n    \"\"\"Reset only project-level configuration.\"\"\"\n    if not _confirm_reset(\"project\", force, yes):\n        typer.echo(\"Reset cancelled.\")\n    _reset_project_config(workspace)\n    typer.echo(\"Project config reset complete! Global config kept intact.\")\n\n\ndef _reset_cache_only(force: bool = False, yes: bool = False) -> None:\n    \"\"\"Reset only cache and embeddings.\"\"\"\n    if not _confirm_reset(\"cache\", force, yes):\n        typer.echo(\"Reset cancelled.\")\n    _reset_cache()\n    typer.echo(\"Cache reset complete!\")\n\n\ndef _reset_sessions_only(force: bool = False, yes: bool = False) -> None:\n    \"\"\"Reset only session database and event logs.\"\"\"\n    if not _confirm_reset(\"sessions\", force, yes):\n        typer.echo(\"Reset cancelled.\")\n    _reset_global_config()\n    typer.echo(\"Session database reset complete!\")\n\n\ndef _reset_global_config() -> None:\n    \"\"\"Reset global config and sessions.\"\"\"\n    devx_global = Path.home() / \".devx\"\n    config_path = devx_global / \"config.json\"\n    if config_path.exists():\n        config_path.unlink()\n    sessions_path = devx_global / \"sessions.db\"\n    if sessions_path.exists():\n        sessions_path.unlink()\n\n\ndef _reset_cache() -> None:\n    \"\"\"Reset cache and embeddings.\"\"\"\n    cache_dir = Path.home() / \".cache\" / \"devx\"\n    if cache_dir.exists():\n        shutil.rmtree(cache_dir)\n    cache_db = devx_global / \"cache.db\"\n    if cache_db.exists():\n        cache_db.unlink()\n\n\ndef _reset_project_config(workspace: Path) -> None:\n    \"\"\"Reset project-level configuration.\"\"\"\n    devx_dir = workspace / \".devx\"\n    if devx_dir.exists():\n        shutil.rmtree(devx_dir)\n    env_path = workspace / \".env\"\n    if env_path.exists():\n        env_path.unlink()\n\n\n@app.command()\ndef reset(\n    _click: typer.Context,\n    --all: bool = typer.Option(\n        True,\n        \"--all\",\n        help=\"Reset all configurations and caches. This is the default behavior.\",\n    ),\n    --global: bool = typer.Option(\n        False,\n        \"--global\",\n        help=\"Reset only global configuration and sessions (~/.devx/config.json and ~/.devx/sessions.db).\",\n    ),\n    --project: bool = typer.Option(\n        False,\n        \"--project\",\n        help=\"Reset only project-level configuration (.devx/ and .env). Keeps global config intact.\",\n    ),\n    --cache: bool = typer.Option(\n        False,\n        \"--cache\",\n        help=\"Reset only cache and embeddings (~/.cache/devx/ and ~/.devx/cache.db).\",\n    ),\n    --sessions: bool = typer.Option(\n        False,\n        \"--sessions\",\n        help=\"Reset only session database and event logs (~/.devx/sessions.db).\",\n    ),\n    --force: bool = typer.Option(\n        False,\n        \"--force\",\n        help=\"Skip confirmation prompt.\",\n    ),\n    --yes: bool = typer.Option(\n        False,\n        \"--yes\",\n        help=\"Assume yes for all prompts, useful for CI/CD.\",\n    ),
) -> None:\n    \"\"\"Reset devx configurations and caches.\n\n    Options:\n    --all: Reset everything. Removes global config, sessions, cache, and project config.\n    --global: Reset only global config and sessions.\n    --project: Reset only project-level config (.devx/ and .env). Keeps global config intact.\n    --cache: Reset only cache and embeddings.\n    --sessions: Reset only session database and event logs.\n    --force: Skip confirmation prompt.\n    --yes: Assume yes for all prompts.\n\n    Examples:\n    devx reset                   # Reset everything\n    devx reset --global          # Reset global config and sessions only\n    devx reset --project         # Reset project config only\n    devx reset --cache           # Reset cache and embeddings only\n    devx reset --sessions        # Reset session database only\n    devx reset --force           # Skip confirmation prompt\n    devx reset --yes           # Assume yes for all prompts\n    \"\"\"\n    reset_type = None\n    if --all:\n        reset_type = \"all\"\n    elif --global:\n        reset_type = \"global\"\n    elif --project:\n        reset_type = \"project\"\n    elif --cache:\n        reset_type = \"cache\"\n    elif --sessions:\n        reset_type = \"sessions\"\n    \n    if --force or --yes:\n        pass  # confirmation skipped\n    elif not _confirm_reset(reset_type, --force, --yes):\n        typer.echo(\"Reset cancelled.\")\n        return\n    \n    if reset_type == \"all\":\n        _reset_all(--force, --yes)\n    elif reset_type == \"global\":\n        _reset_global(--force, --yes)\n    elif reset_type == \"project\":\n        _reset_project(Path.cwd(), --force, --yes)\n    elif reset_type == \"cache\":\n        _reset_cache_only(--force, --yes)\n    elif reset_type == \"sessions\":\n        _reset_sessions_only(--force, --yes)\n'''

# Find the position - after the test_cmd function
test_cmd_marker = 'subprocess.run(unit_cmd.split(), cwd=str(settings.workspace), check=False)'
test_cmd_pos = content.find(test_cmd_marker)

if test_cmd_pos >= 0:
    # Insert after the test_cmd function
    insert_pos = test_cmd_pos + len(test_cmd_marker)
    new_content = content[:insert_pos] + reset_func + content[insert_pos:]
    with open('src/devx/cli/app.py', 'w', errors='replace') as f:
        f.write(new_content)
    print("Reset command added successfully")
else:
    print("Could not find test_cmd marker")
PYEOF