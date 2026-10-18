from __future__ import annotations

from pathlib import Path
import pytest
from devx.config import Settings
from devx.harness.permissions import PermissionPolicy
from devx.harness.tool_registry import ToolRegistry
from devx.harness.tools import WorkspaceTools
from devx.agents.runtime import build_coding_agent


def test_settings_mode_validation(tmp_path: Path):
    s = Settings(workspace=tmp_path, mode="code")
    assert s.mode == "code"

    s_arch = Settings(workspace=tmp_path, mode="architect")
    assert s_arch.mode == "architect"

    with pytest.raises(ValueError, match="Invalid mode"):
        Settings(workspace=tmp_path, mode="hacker")


def test_architect_mode_restricts_tools(tmp_path: Path):
    settings = Settings(workspace=tmp_path, mode="architect", api_key="sk-test-key")
    tools = WorkspaceTools(PermissionPolicy.from_settings(settings))
    registry = ToolRegistry()
    build_coding_agent(settings, tools, registry=registry)

    tool_names = set(registry.names())
    # Read tools are present
    assert "read_file" in tool_names
    assert "read_files" in tool_names
    assert "search_files" in tool_names

    # Mutation and shell tools are excluded in architect mode
    assert "write_file" not in tool_names
    assert "replace_in_file" not in tool_names
    assert "write_files" not in tool_names
    assert "run_command" not in tool_names
    assert "git_branch" not in tool_names
    assert "git_commit" not in tool_names
    assert "git_push" not in tool_names


def test_review_mode_restricts_write_tools_but_allows_run_command(tmp_path: Path):
    settings = Settings(workspace=tmp_path, mode="review", api_key="sk-test-key")
    tools = WorkspaceTools(PermissionPolicy.from_settings(settings))
    registry = ToolRegistry()
    build_coding_agent(settings, tools, registry=registry)

    tool_names = set(registry.names())
    assert "read_file" in tool_names
    assert "search_files" in tool_names
    assert "run_command" in tool_names  # allowed to run tests / lint

    # Mutation tools are excluded
    assert "write_file" not in tool_names
    assert "replace_in_file" not in tool_names
    assert "write_files" not in tool_names


def test_code_mode_includes_all_tools(tmp_path: Path):
    settings = Settings(workspace=tmp_path, mode="code", api_key="sk-test-key")
    tools = WorkspaceTools(PermissionPolicy.from_settings(settings))
    registry = ToolRegistry()
    build_coding_agent(settings, tools, registry=registry)

    tool_names = set(registry.names())
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    assert "replace_in_file" in tool_names
    assert "run_command" in tool_names
    assert "git_commit" in tool_names
