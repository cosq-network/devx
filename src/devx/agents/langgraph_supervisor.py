from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, TypedDict

from devx.agents.runtime import (
    AgentToolLimitExceeded,
    build_coding_agent,
    build_tool_registry,
    response_text,
    stream_agent_text,
)
from devx.harness.approval import ApprovalManager
from devx.harness.changes import ChangeJournal
from devx.harness.permissions import PermissionPolicy
from devx.harness.runtime import CancellationToken, RunCancelled
from devx.harness.session import SessionStore
from devx.harness.testing import detect_test_command
from devx.harness.tools import WorkspaceTools
from devx.llm import create_llm, invoke_with_retry, _is_rate_limit_error
from devx.token_optim.compaction import append_message as compact_append_message
from devx.token_optim.context import read_context, select_relevant_files


class GraphState(TypedDict, total=False):
    task: str
    messages: list[dict[str, str]]
    files_in_context: list[str]
    plan: str
    result: str
    test_result: str
    events: list[dict[str, Any]]
    status: str
    active_agent: str
    iteration: int
    test_attempts: int
    repair_attempts: int
    test_command: str
    lint_command: str
    lint_result: str
    requested_agent: str


class ConfiguredGraph:
    """Small adapter that supplies a stable LangGraph checkpoint thread ID."""
    def __init__(self, graph: Any, thread_id: str, graph_builder: Any = None, session_db: Any = None):
        self.graph, self.thread_id = graph, thread_id
        self.graph_builder, self.session_db = graph_builder, session_db

    def _config(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(config or {})
        configurable = dict(merged.get("configurable", {}))
        configurable.setdefault("thread_id", self.thread_id)
        merged["configurable"] = configurable
        return merged

    def invoke(self, state: GraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self.graph.invoke(state, config=self._config(config))
        except RunCancelled:
            result: dict[str, Any] = dict(state)
            result["status"] = "cancelled"
            result["result"] = "Run cancelled by user."
            events = result.setdefault("events", [])
            events.append({"type": "cancelled"})
            return result
        except Exception as exc:
            error_msg = str(exc)
            if _is_rate_limit_error(exc):
                error_msg = f"Rate limit exceeded: {error_msg}. Please wait and retry, or switch to a different model."
            result: dict[str, Any] = dict(state)
            result["status"] = "failed"
            result["result"] = f"Agent run failed: {error_msg}"
            events = result.setdefault("events", [])
            events.append({"type": "agent_error", "error": error_msg})
            return result

    async def ainvoke(self, state: GraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.graph_builder is None or self.session_db is None:
            try:
                return await self.graph.ainvoke(state, config=self._config(config))
            except RunCancelled:
                result: dict[str, Any] = dict(state)
                result["status"] = "cancelled"
                result["result"] = "Run cancelled by user."
                events = result.setdefault("events", [])
                events.append({"type": "cancelled"})
                return result
            except Exception as exc:
                error_msg = str(exc)
                if _is_rate_limit_error(exc):
                    error_msg = f"Rate limit exceeded: {error_msg}. Please wait and retry, or switch to a different model."
                result: dict[str, Any] = dict(state)
                result["status"] = "failed"
                result["result"] = f"Agent run failed: {error_msg}"
                events = result.setdefault("events", [])
                events.append({"type": "agent_error", "error": error_msg})
                return result
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError as exc:
            raise ImportError(
                "Async LangGraph runs require aiosqlite. Install the agent extra "
                "or run: pip install aiosqlite"
            ) from exc
        async with aiosqlite.connect(str(self.session_db)) as connection:
            checkpointer = AsyncSqliteSaver(connection)
            async_graph = self.graph_builder.compile(checkpointer=checkpointer)
            try:
                return await async_graph.ainvoke(state, config=self._config(config))
            except RunCancelled:
                result: dict[str, Any] = dict(state)
                result["status"] = "cancelled"
                result["result"] = "Run cancelled by user."
                events = result.setdefault("events", [])
                events.append({"type": "cancelled"})
                return result
            except Exception as exc:
                error_msg = str(exc)
                if _is_rate_limit_error(exc):
                    error_msg = f"Rate limit exceeded: {error_msg}. Please wait and retry, or switch to a different model."
                result: dict[str, Any] = dict(state)
                result["status"] = "failed"
                result["result"] = f"Agent run failed: {error_msg}"
                events = result.setdefault("events", [])
                events.append({"type": "agent_error", "error": error_msg})
                return result


def build_langgraph_supervisor(settings: Any, approvals: ApprovalManager, files: list[str] | None = None,
                               run_id: str | None = None, session_id: str | None = None,
                               cancellation: CancellationToken | None = None, event_sink: Any = None,
                               plan_only: bool = False):
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise ImportError("Install the agent extra for LangGraph orchestration") from exc

    run_id = run_id or str(uuid.uuid4())
    cancellation = cancellation or CancellationToken()
    session_store = SessionStore(settings.session_db)
    journal = ChangeJournal(settings.session_db)
    tools = WorkspaceTools(PermissionPolicy(
                               settings.workspace,
                               settings.approval_required,
                               getattr(settings, "permission_rules", ()),
                           ),
                           max_bytes=settings.max_file_bytes, timeout=settings.command_timeout,
                           journal=journal, run_id=run_id, cancellation=cancellation)

    def approve(action: str, target: str, reason: str = "") -> bool:
        return approvals.allows(run_id, action, target, reason)

    registry = build_tool_registry(
        settings,
        approve,
        load_extensions=not plan_only,
    )
    tool_call_count = 0
    tool_call_lock = threading.Lock()

    def consume_tool_call() -> None:
        nonlocal tool_call_count
        with tool_call_lock:
            tool_call_count += 1
            if tool_call_count > max(1, int(settings.max_tool_calls)):
                raise AgentToolLimitExceeded(f"Tool-call limit exceeded ({settings.max_tool_calls})")
    paths = files or []
    policy = PermissionPolicy(
        settings.workspace,
        settings.approval_required,
        getattr(settings, "permission_rules", ()),
    )
    path_objects = [policy.path(Path(path)) for path in paths]
    embedder = None
    semantic_cache = None
    if settings.embeddings_enabled:
        try:
            from devx.token_optim.cache import SemanticCache
            from devx.token_optim.embeddings import LocalEmbedder
            embedder = LocalEmbedder(settings.embedding_model, settings.embedding_cache_dir, settings.embeddings_offline)
            namespace = f"{settings.provider}:{settings.model}:{settings.embedding_model}"
            semantic_cache = SemanticCache(settings.cache_db, embedder=embedder, namespace=namespace)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            if event_sink:
                event_sink({"type": "embedding_unavailable", "error": str(exc)})
    context_cache: dict[str, tuple[str, str, list[Path]]] = {}

    def context_for(task: str) -> tuple[str, str, list[Path]]:
        if task in context_cache:
            return context_cache[task]
        selected = path_objects
        max_context_files = max(1, int(getattr(settings, "max_context_files", 5)))
        if len(path_objects) > max_context_files:
            selected = select_relevant_files(task, path_objects, max_context_files, embedder)
        context = read_context(selected, settings.max_file_bytes)
        fingerprint = ""
        if semantic_cache:
            from devx.token_optim.cache import fingerprint_files
            fingerprint = fingerprint_files(selected)
        context_cache[task] = (context, fingerprint, selected)
        return context_cache[task]

    def emit(state: GraphState, event: dict[str, Any]) -> None:
        events = state.setdefault("events", [])
        events.append(event)
        if event_sink:
            event_sink(event)
        session_store.append_event(session_id or run_id, run_id, event)

    def append_state_message(state: GraphState, role: str, content: str) -> None:
        result = compact_append_message(
            state.setdefault("messages", []),
            role,
            content,
            enabled=getattr(settings, "context_compaction_enabled", True),
            max_messages=max(2, int(getattr(settings, "max_context_messages", 24))),
            max_chars=max(256, int(getattr(settings, "max_context_chars", 24_000))),
            summary_chars=max(128, int(getattr(settings, "compaction_summary_chars", 4_000))),
        )
        if result.compacted:
            emit(state, {"type": "context_compacted", "removed_messages": result.removed_messages})

    def planner(state: GraphState) -> GraphState:
        cancellation.raise_if_cancelled()
        emit(state, {"type": "agent_started", "agent": "planner"})
        task = state.get("task")
        if not task:
            raise ValueError("A task is required to run the supervisor graph")
        specialist = state.get("requested_agent", "supervisor")
        context, context_fingerprint, selected_paths = context_for(task)
        if selected_paths != path_objects:
            state["files_in_context"] = [str(path) for path in selected_paths]
            emit(state, {"type": "context_selected", "files": state["files_in_context"]})
        cached_plan = semantic_cache.get("plan:" + task, context_fingerprint) if semantic_cache else None
        if cached_plan:
            state["plan"] = cached_plan
            emit(state, {"type": "cache_hit", "kind": "plan"})
        else:
            try:
                response = invoke_with_retry(create_llm(settings), "Create a concrete coding plan. Do not edit files. "
                                          f"Give special attention to the requested specialist perspective: {specialist}.\n\nTask:\n"
                                          + task + "\n\nContext:\n" + context)
            except Exception as exc:
                error_msg = str(exc)
                if _is_rate_limit_error(exc):
                    error_msg = f"Rate limit exceeded: {error_msg}. Please wait and retry, or switch to a different model."
                emit(state, {"type": "agent_error", "agent": "planner", "error": error_msg})
                state["status"] = "failed"
                state["result"] = f"Planner failed: {error_msg}"
                return state
            state["plan"] = str(getattr(response, "content", response))
            if semantic_cache:
                semantic_cache.set("plan:" + task, state["plan"], context_fingerprint)
        append_state_message(state, "planner", state["plan"])
        state["active_agent"] = "coder"
        state["iteration"] = state.get("iteration", 0) + 1
        emit(state, {"type": "agent_finished", "agent": "planner"})
        return state

    def route_after_planner(state: GraphState) -> str:
        if plan_only:
            state["status"] = "plan_only"
            state["result"] = state.get("plan", "")
            return "finish"
        if state.get("iteration", 0) >= settings.max_iterations:
            state["status"] = "limit_exceeded"
            state["result"] = "Iteration limit reached before coding."
            return "finish"
        return "coder"

    def coder(state: GraphState) -> GraphState:
        cancellation.raise_if_cancelled()
        emit(state, {"type": "agent_started", "agent": "coder"})
        task = state.get("task")
        if not task:
            raise ValueError("A task is required to run the supervisor graph")
        agent = build_coding_agent(settings, tools, approve, consume_tool_call, registry)
        context = context_for(task)[0]
        if state.get("repair_attempts", 0):
            prompt = (
                "The latest lint/test run failed. Inspect the failure, make the smallest approved fix, "
                "and report exactly what changed. Do not weaken or remove tests.\n\nFailure:\n"
                + state.get("test_result", "")
            )
        else:
            prompt = (
                "Execute this approved coding task. Inspect before editing, make minimal changes, "
                "and report exactly what happened.\n\nTask:\n" + task +
                "\n\nPlan:\n" + state.get("plan", "") + "\n\nContext:\n" + context
            )
        requested_agent = state.get("requested_agent")
        if requested_agent:
            prompt += "\n\nRequested specialist perspective: " + requested_agent
        messages = [{"role": "user", "content": prompt}]
        try:
            if event_sink:
                state["result"] = stream_agent_text(agent, messages, lambda event: emit(state, event))
            else:
                state["result"] = response_text(agent.invoke({"messages": messages}))  # type: ignore[arg-type]
        except Exception as exc:
            error_msg = str(exc)
            if _is_rate_limit_error(exc):
                error_msg = f"Rate limit exceeded: {error_msg}. Please wait and retry, or switch to a different model."
            emit(state, {"type": "agent_error", "agent": "coder", "error": error_msg})
            state["status"] = "failed"
            state["result"] = f"Coder failed: {error_msg}"
            return state
        append_state_message(state, "coder", state["result"])
        state["active_agent"] = "tester"
        state["iteration"] = state.get("iteration", 0) + 1
        emit(state, {"type": "agent_finished", "agent": "coder"})
        return state

    def route_after_coder(state: GraphState) -> str:
        if state.get("iteration", 0) >= settings.max_iterations:
            state["status"] = "limit_exceeded"
            state["result"] = state.get("result", "") + "\n\nIteration limit reached before testing."
            return "finish"
        return "tester"

    def tester(state: GraphState) -> GraphState:
        cancellation.raise_if_cancelled()
        emit(state, {"type": "agent_started", "agent": "tester"})
        command = state.get("test_command") or settings.test_command or detect_test_command(settings.workspace)
        state["test_command"] = command or ""
        state["test_attempts"] = state.get("test_attempts", 0) + 1
        state["iteration"] = state.get("iteration", 0) + 1
        state["status"] = "running"
        lint_command = state.get("lint_command") or getattr(settings, "lint_command", "")
        state["lint_command"] = lint_command or ""
        if lint_command:
            if not approve("command", lint_command, "Configured lint command."):
                state["lint_result"] = "Lint execution denied by user."
            else:
                lint_result = tools.run(lint_command, approved=True)
                state["lint_result"] = lint_result.output or f"exit code: {lint_result.exit_code}"
                emit(state, {"type": "lint_finished", "ok": lint_result.ok, "exit_code": lint_result.exit_code})
                if not lint_result.ok:
                    state["test_result"] = "Lint failed:\n" + state["lint_result"]
                    state["status"] = "tests_failed"
        if state.get("status") == "tests_failed":
            pass
        elif not command:
            state["test_result"] = "No test command detected."
            state["status"] = "completed"
        elif not approve("command", command, "Detected/configured project test command."):
            state["test_result"] = "Test execution denied by user."
            state["status"] = "completed"
        else:
            result = tools.run(command, approved=True)
            state["test_result"] = result.output or f"exit code: {result.exit_code}"
            state["status"] = "completed" if result.ok else "tests_failed"
            emit(state, {"type": "test_finished", "ok": result.ok, "exit_code": result.exit_code})
            if not result.ok:
                state["repair_attempts"] = state.get("repair_attempts", 0)
        append_state_message(state, "tester", state.get("test_result", ""))
        state["active_agent"] = "supervisor"
        state["status"] = state.get("status", "completed")
        emit(state, {"type": "agent_finished", "agent": "tester"})
        return state

    def route_after_tester(state: GraphState) -> str:
        """Retry failed tests at most twice, subject to the run iteration cap."""
        if state.get("status") != "tests_failed":
            return "finish"
        max_repairs = min(2, max(0, (settings.max_iterations - state.get("iteration", 0)) // 2))
        if state.get("repair_attempts", 0) >= max_repairs:
            return "finish"
        state["repair_attempts"] = state.get("repair_attempts", 0) + 1
        state["active_agent"] = "coder"
        return "repair"

    graph = StateGraph(GraphState)
    graph.add_node("planner", planner)
    graph.add_node("coder", coder)
    graph.add_node("tester", tester)
    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", route_after_planner, {"coder": "coder", "finish": END})
    graph.add_conditional_edges("coder", route_after_coder, {"tester": "tester", "finish": END})
    graph.add_conditional_edges("tester", route_after_tester, {"repair": "coder", "finish": END})
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver
        connection = sqlite3.connect(settings.session_db, check_same_thread=False)
        return ConfiguredGraph(
            graph.compile(checkpointer=SqliteSaver(connection)),
            session_id or run_id,
            graph,
            settings.session_db,
        )
    except (ImportError, TypeError):
        return ConfiguredGraph(graph.compile(), session_id or run_id)
