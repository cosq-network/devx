from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from devx.llm import create_llm
from devx.token_optim.context import read_context


@dataclass
class DevxState:
    task: str
    messages: list[dict[str, str]] = field(default_factory=list)
    files_in_context: list[str] = field(default_factory=list)
    active_agent: str = "supervisor"
    requested_agent: str = "supervisor"
    events: list[dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    status: str = "pending"
    result: str = ""
    test_result: str = ""
    lint_command: str = ""
    lint_result: str = ""


DevState = DevxState


@dataclass
class AgentGraph:
    runner: Callable[[DevxState], DevxState]

    async def ainvoke(self, state: dict[str, Any] | DevxState) -> dict[str, Any]:
        current = state if isinstance(state, DevxState) else DevxState(**state)
        import asyncio
        output = await asyncio.to_thread(self.runner, current)
        return output.__dict__

    def invoke(self, state: dict[str, Any] | DevxState) -> dict[str, Any]:
        current = state if isinstance(state, DevxState) else DevxState(**state)
        return self.runner(current).__dict__


def build_devx_graph(*, runner: Callable[[DevxState], DevxState] | None = None, max_iterations: int = 8) -> AgentGraph:
    warnings.warn("build_devx_graph is a test compatibility helper; use build_supervisor_graph for production runs", DeprecationWarning, stacklevel=2)
    def default(state: DevxState) -> DevxState:
        state.iteration += 1
        state.status = "completed" if state.iteration <= max_iterations else "limit_exceeded"
        state.result = state.result or "Agent graph is ready; configure a model-backed runner to execute tasks."
        state.messages.append({"role": "assistant", "content": state.result})
        return state
    return AgentGraph(runner or default)


build_dev_graph = build_devx_graph


def build_model_graph(settings: Any, files: list[str] | None = None) -> AgentGraph:
    warnings.warn("build_model_graph is deprecated; use build_supervisor_graph", DeprecationWarning, stacklevel=2)
    from pathlib import Path

    from devx.harness.permissions import PermissionPolicy
    policy = PermissionPolicy.from_settings(settings)
    context = read_context([policy.path(Path(item)) for item in (files or [])], settings.max_file_bytes)

    def run(state: DevxState) -> DevxState:
        prompt = ("You are a careful coding assistant. Analyze the request and supplied files. "
                  "Do not claim to have edited or tested anything. Provide concrete next steps.\n\n"
                  f"Request:\n{state.task}\n\nContext:\n{context}")
        try:
            from devx.agents.runtime import build_coding_agent, response_text
            from devx.harness.permissions import PermissionPolicy
            from devx.harness.tools import WorkspaceTools
            agent = build_coding_agent(
                settings,
                WorkspaceTools(PermissionPolicy.from_settings(settings)),
            )
            content = response_text(agent.invoke({"messages": [{"role": "user", "content": prompt}]}))
        except (ImportError, RuntimeError):
            model = create_llm(settings)
            response = model.invoke(prompt)
            content = getattr(response, "content", str(response))
        state.iteration += 1
        state.status = "completed"
        state.result = content if isinstance(content, str) else str(content)
        state.messages.append({"role": "assistant", "content": state.result})
        return state

    return AgentGraph(run)


def build_supervisor_graph(
    settings: Any,
    approvals: Any,
    files: list[str] | None = None,
    run_id: str | None = None,
    cancellation: Any = None,
    event_sink: Any = None,
    session_id: str | None = None,
    plan_only: bool = False,
) -> Any:
    import uuid

    try:
        from devx.agents.langgraph_supervisor import build_langgraph_supervisor
        return build_langgraph_supervisor(
            settings, approvals, files, run_id or str(uuid.uuid4()), session_id, cancellation, event_sink,
            plan_only=plan_only,
        )
    except ImportError:
        pass
    from devx.agents.supervisor import SupervisorRunner
    runner = SupervisorRunner(
        settings, approvals, run_id or str(uuid.uuid4()), cancellation, event_sink, session_id, plan_only,
    )
    return AgentGraph(runner)