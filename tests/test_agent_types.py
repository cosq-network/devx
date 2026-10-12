"""Tests for the agent dependency container and protocols (``devx.agents.types``)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from devx.agents.types import (
    AgentDependencies,
    ApprovalHandler,
    EventSink,
    ModelLike,
    ToolCallBudget,
)
from devx.config import Settings
from devx.harness.approval import ApprovalManager
from devx.harness.changes import ChangeJournal
from devx.harness.permissions import PermissionPolicy
from devx.harness.runtime import CancellationToken
from devx.harness.session import SessionStore
from devx.harness.tools import WorkspaceTools


def build_dependencies(tmp_path: Path, **overrides) -> AgentDependencies:
    settings = Settings(workspace=tmp_path, session_db=tmp_path / "sessions.db")
    policy = PermissionPolicy.from_settings(settings)
    values = dict(
        settings=settings,
        approvals=ApprovalManager(),
        run_id="run-1",
        cancellation=CancellationToken(),
        event_sink=None,
        session_id=None,
        plan_only=False,
        policy=policy,
        journal=ChangeJournal(settings.session_db),
        sessions=SessionStore(settings.session_db),
        tools=WorkspaceTools(
            policy,
            max_bytes=settings.max_file_bytes,
            timeout=settings.command_timeout,
        ),
        embedder=None,
    )
    values.update(overrides)
    return AgentDependencies(**values)


def test_constructs_with_real_components(tmp_path):
    deps = build_dependencies(tmp_path)
    assert deps.run_id == "run-1"
    assert deps.plan_only is False
    assert deps.embedder is None
    assert deps.session_id is None
    assert deps.cancellation is not None
    assert deps.journal is not None
    assert deps.sessions is not None
    assert deps.tools is not None


def test_equality_and_replace(tmp_path):
    deps = build_dependencies(tmp_path)
    other = replace(deps, run_id="run-2")
    assert deps.run_id == "run-1"
    assert other.run_id == "run-2"
    assert deps.plan_only is other.plan_only is False
    # replace() shares mutable components; only the requested field changes.
    assert deps.settings is other.settings
    assert deps.approvals is other.approvals
    copy = replace(deps)
    assert copy.run_id == "run-1"


def test_missing_required_field_raises_type_error(tmp_path):
    with pytest.raises(TypeError):
        AgentDependencies(settings=Settings(workspace=tmp_path))


def test_protocol_shapes():
    """The protocol stubs should be callable with the documented signatures."""

    def approve(action: str, target: str, reason: str = "") -> bool:
        return True

    def sink(event: dict) -> None:
        del event

    def budget() -> None:
        pass

    class ModelStub:
        def invoke(self, prompt):
            return prompt

        async def ainvoke(self, prompt):
            return prompt

        def stream(self, *args, **kwargs):
            return iter(())

        async def astream(self, *args, **kwargs):
            return iter(())

        def bind_tools(self, tools):
            return self

    model = ModelStub()
    # ApprovalHandler/EventSink/ToolCallBudget are Protocol classes; their
    # __call__/__init__ entries simply verify the expected attribute surface.
    assert callable(ApprovalHandler.__call__)
    assert callable(EventSink.__call__)
    assert callable(ToolCallBudget.__call__)
    for method in ("invoke", "ainvoke", "stream", "astream", "bind_tools"):
        assert callable(getattr(ModelLike, method))
    assert callable(getattr(model, "invoke"))
    assert callable(getattr(model, "bind_tools"))