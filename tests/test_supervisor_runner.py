"""Edge-path tests for ``SupervisorRunner`` (``devx.agents.supervisor``).

All tests are offline: the plan-only happy path mocks the LLM layer, and the
limit/cancellation paths return before any model is invoked.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

from devx.agents.graph import DevxState
from devx.agents.supervisor import SupervisorRunner
from devx.config import Settings
from devx.harness.approval import ApprovalManager
from devx.harness.runtime import CancellationToken


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
        cache_db=tmp_path / "cache.db",
        auto_context=False,
        max_iterations=4,
    )
    values.update(overrides)
    return Settings(**values)


def make_runner(tmp_path: Path, **overrides) -> SupervisorRunner:
    values = dict(
        settings=make_settings(tmp_path),
        approvals=ApprovalManager(),
        run_id="run-1",
        cancellation=CancellationToken(),
        event_sink=None,
        session_id=None,
        plan_only=True,
    )
    values.update(overrides)
    return SupervisorRunner(**values)


def fake_llm(monkeypatch, content: str = "THE PLAN"):
    """Fake the LLM layer exactly where ``SupervisorRunner`` binds it.

    ``create_llm`` is imported inside ``__call__`` (``from devx.llm import
    create_llm``), so patching ``devx.llm.create_llm`` works. ``invoke_with_retry``
    is imported at module top of ``devx.agents.supervisor``, so the module
    attribute itself is patched.
    """
    monkeypatch.setattr("devx.llm.create_llm", lambda settings: object())
    monkeypatch.setattr(
        "devx.agents.supervisor.invoke_with_retry",
        lambda model, prompt, *, provider: SimpleNamespace(content=content),
    )


class TestConstruction:
    def test_plan_only_builds_no_registry(self, tmp_path):
        runner = make_runner(tmp_path)
        assert runner.plan_only is True
        assert runner.registry.names() == ()
        assert runner.embedder is None

    def test_embeddings_enabled_falls_back_when_dependency_missing(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setitem(
            sys.modules, "sentence_transformers", types.ModuleType("sentence_transformers")
        )
        runner = make_runner(
            tmp_path, settings=make_settings(tmp_path, embeddings_enabled=True)
        )
        assert runner.embedder is None

    def test_provides_own_cancellation_when_none(self, tmp_path):
        runner = make_runner(tmp_path, cancellation=None)
        assert runner.cancellation is not None


class TestCall:
    def test_iteration_limit_at_entry(self, tmp_path):
        runner = make_runner(tmp_path, settings=make_settings(tmp_path, max_iterations=2))
        state = DevxState(task="t", iteration=2)
        result = runner(state)
        assert result.status == "limit_exceeded"
        assert "Iteration limit reached" in result.result
        assert result.events == []
        assert result.messages == []

    def test_cancelled_before_start(self, tmp_path):
        cancellation = CancellationToken()
        cancellation.cancel()
        runner = make_runner(tmp_path, cancellation=cancellation)
        state = DevxState(task="t")
        result = runner(state)
        assert result.status == "cancelled"
        assert state.events[-1]["type"] == "cancelled"

    def test_plan_only_happy_path(self, tmp_path, monkeypatch):
        fake_llm(monkeypatch)
        runner = make_runner(tmp_path)
        state = DevxState(task="do the thing")
        result = runner(state)
        assert result.status == "plan_only"
        assert result.result == "THE PLAN"
        types_seen = [event["type"] for event in result.events]
        assert "agent_started" in types_seen
        assert "agent_finished" in types_seen
        assert "plan_ready" in types_seen
        assert result.messages[-1]["role"] == "planner"
        assert result.messages[-1]["content"] == "THE PLAN"

    def test_plan_only_reads_explicit_files(self, tmp_path, monkeypatch):
        (tmp_path / "a.py").write_text("print('hi')\n", encoding="utf-8")
        fake_llm(monkeypatch)
        prompts: list[str] = []

        def recording_invoke(model, prompt, *, provider):
            prompts.append(prompt)
            return SimpleNamespace(content="plan")

        monkeypatch.setattr("devx.llm.create_llm", lambda settings: object())
        monkeypatch.setattr("devx.agents.supervisor.invoke_with_retry", recording_invoke)
        runner = make_runner(tmp_path)
        state = DevxState(task="refactor", files_in_context=[tmp_path / "a.py"])
        result = runner(state)
        assert result.status == "plan_only"
        assert "print('hi')" in prompts[-1]

    def test_plan_only_forwards_events_to_sink(self, tmp_path, monkeypatch):
        fake_llm(monkeypatch)
        received: list[dict] = []
        runner = make_runner(tmp_path, event_sink=received.append)
        runner(DevxState(task="t"))
        assert any(event.get("type") == "plan_ready" for event in received)

    def test_plan_only_persists_events_to_session(self, tmp_path, monkeypatch):
        fake_llm(monkeypatch)
        runner = make_runner(tmp_path, session_id="sess-1")
        runner(DevxState(task="t"))
        events = runner.sessions.events("sess-1")
        assert events
        assert any(event["event"].get("type") == "plan_ready" for event in events)