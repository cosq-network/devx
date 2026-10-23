from __future__ import annotations

import threading
import uuid
from dataclasses import replace
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
from devx.secrets import redact_secrets
from devx.token_optim.compaction import append_message as compact_append_message
from devx.token_optim.context import (
    decoupled_coder_context,
    generate_file_manifest,
    read_context,
    select_relevant_files,
)
from devx.token_optim.distillation import distill_test_output


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
    def __init__(self, graph: Any, thread_id: str, graph_builder: Any = None, session_db: Any = None, backend: Any = None):
        self.graph, self.thread_id = graph, thread_id
        self.graph_builder, self.session_db = graph_builder, session_db
        self.backend = backend

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
            if isinstance(state, dict):
                result: dict[str, Any] = dict(state)
            else:
                try:
                    result = dict(state)
                except (TypeError, ValueError):
                    result = {}
                    if hasattr(state, "__dict__"):
                        result.update(vars(state))
            result["status"] = "cancelled"
            result["result"] = "Run cancelled by user."
            events = result.setdefault("events", [])
            events.append({"type": "cancelled"})
            return result
        except Exception as exc:
            error_msg = str(exc)
            if _is_rate_limit_error(exc):
                error_msg = f"Rate limit exceeded: {error_msg}. Please wait and retry, or switch to a different model."
            if isinstance(state, dict):
                result: dict[str, Any] = dict(state)
            else:
                try:
                    result = dict(state)
                except (TypeError, ValueError):
                    result = {}
                    if hasattr(state, "__dict__"):
                        result.update(vars(state))
            result["status"] = "failed"
            result["result"] = f"Agent run failed: {error_msg}"
            events = result.setdefault("events", [])
            events.append({"type": "agent_error", "error": error_msg})
            return result
        finally:
            if self.backend and getattr(self.backend, "is_remote", False):
                try:
                    self.backend.close()
                except Exception:
                    pass

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
            finally:
                if self.backend and getattr(self.backend, "is_remote", False):
                    try:
                        self.backend.close()
                    except Exception:
                        pass
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
            finally:
                if self.backend and getattr(self.backend, "is_remote", False):
                    try:
                        self.backend.close()
                    except Exception:
                        pass


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
    
    backend = None
    remote_ssh = getattr(settings, "remote_ssh", "")
    if remote_ssh:
        from devx.harness.ssh_backend import SshConfig, SshWorkspaceBackend
        ssh_cfg = SshConfig.from_uri(remote_ssh)
        if getattr(settings, "ssh_key_path", ""):
            ssh_cfg = replace(
                ssh_cfg,
                key_filename=settings.ssh_key_path,
            )
        ssh_cfg = replace(
            ssh_cfg,
            known_hosts=getattr(settings, "ssh_known_hosts", "") or None,
            allow_unknown_host_keys=getattr(settings, "ssh_allow_unknown_keys", False),
        )
        backend = SshWorkspaceBackend(ssh_cfg)

    container = getattr(settings, "container", "")
    if backend is None and container:
        from devx.harness.container_backend import ContainerConfig, ContainerWorkspaceBackend
        backend = ContainerWorkspaceBackend(ContainerConfig.from_uri(container))

    tools = WorkspaceTools(PermissionPolicy.from_settings(settings),
                           max_bytes=settings.max_file_bytes, timeout=settings.command_timeout,
                           journal=journal, run_id=run_id, cancellation=cancellation,
                           backend=backend,
                           secret_scan_mode=getattr(settings, "secret_scan_mode", "warn"),
                           sandbox=getattr(settings, "sandbox", "none"))

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
    policy = PermissionPolicy.from_settings(settings)
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
        max_context_files = max(1, int(getattr(settings, "max_context_files", 15)))
        # Auto-discover project files when no explicit files provided
        if not selected and getattr(settings, "auto_context", True):
            if backend is not None and getattr(backend, "is_remote", False):
                from devx.token_optim.context import CODE_EXTS
                remote_files = backend.walk_files(max_files=200, code_exts=CODE_EXTS)
                discovered = [Path(p) for p in remote_files]
            else:
                from devx.token_optim.context import discover_project_files
                discovered = discover_project_files(settings.workspace, max_files=200)
            if discovered:
                selected = select_relevant_files(task, discovered, max_context_files, embedder)
                if event_sink:
                    event_sink({"type": "context_auto_discovered", "files": [str(p) for p in selected]})
        elif len(selected) > max_context_files:
            selected = select_relevant_files(task, selected, max_context_files, embedder)

        if backend is not None and getattr(backend, "is_remote", False):
            context_blocks = []
            for path_item in selected:
                try:
                    rel_p = str(path_item).strip().replace("\\", "/").lstrip("/")
                    data = backend.read_bytes(rel_p, settings.max_file_bytes)
                    text = redact_secrets(data.decode("utf-8", errors="replace"))
                    context_blocks.append(f"--- {rel_p} ---\n{text}")
                except Exception:
                    pass
            context = "\n\n".join(context_blocks)
        else:
            context = read_context(selected, settings.max_file_bytes)

        from devx.rules import discover_project_rules, format_rules_for_prompt
        project_rules = discover_project_rules(settings.workspace)
        rules_text = format_rules_for_prompt(project_rules)
        if rules_text:
            context = f"{rules_text}\n\n{context}"
            if event_sink:
                event_sink({"type": "rules_discovered", "rules": [r["name"] for r in project_rules]})

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
            planner_context = (
                generate_file_manifest(selected_paths)
                if getattr(settings, "decoupled_context_enabled", True) and selected_paths
                else context
            )
            try:
                from devx.llm import create_fallback_llm
                fb_llm = create_fallback_llm(settings)
                response = invoke_with_retry(
                    create_llm(settings),
                    "Create a concrete coding plan. Do not edit files. "
                    f"Give special attention to the requested specialist perspective: {specialist}.\n\nTask:\n"
                    + task + "\n\nContext:\n" + planner_context,
                    provider=settings.provider,
                    fallback_model=fb_llm,
                    fallback_provider=getattr(settings, "fallback_provider", ""),
                )
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
        active_mode = getattr(settings, "mode", "code").strip().lower()
        if plan_only or active_mode in {"architect", "ask"}:
            state["status"] = "plan_only" if plan_only else "completed"
            state["result"] = state.get("plan", "")
            return "finish"
        if state.get("iteration", 0) >= settings.max_iterations:
            state["status"] = "limit_exceeded"
            state["result"] = "Iteration limit reached before coding."
            return "finish"
        # Use parallel coder for complex tasks (multiple files mentioned or large context)
        task = state.get("task", "")
        files_in_context = state.get("files_in_context", [])
        if len(files_in_context) > 3 or ("multiple" in task.lower() or "several" in task.lower() or "batch" in task.lower()):
            return "parallel_coder"
        return "coder"

    def coder(state: GraphState) -> GraphState:
        cancellation.raise_if_cancelled()
        emit(state, {"type": "agent_started", "agent": "coder"})
        task = state.get("task")
        if not task:
            raise ValueError("A task is required to run the supervisor graph")
        agent = build_coding_agent(settings, tools, approve, consume_tool_call, registry)
        context_str, _, selected_paths = context_for(task)
        if getattr(settings, "decoupled_context_enabled", True) and selected_paths:
            coder_context = decoupled_coder_context(selected_paths)
        else:
            coder_context = context_str
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
                "\n\nPlan:\n" + state.get("plan", "") + "\n\nContext:\n" + coder_context
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

    def parallel_coder(state: GraphState) -> GraphState:
        """Parallel coder that batches file reads using read_files tool."""
        cancellation.raise_if_cancelled()
        emit(state, {"type": "agent_started", "agent": "parallel_coder"})
        task = state.get("task")
        if not task:
            raise ValueError("A task is required to run the supervisor graph")
        
        # Use the parallel coder agent with read_files tool
        agent = build_coding_agent(settings, tools, approve, consume_tool_call, registry)
        context_str, _, selected_paths = context_for(task)
        if getattr(settings, "decoupled_context_enabled", True) and selected_paths:
            coder_context = decoupled_coder_context(selected_paths)
        else:
            coder_context = context_str
        
        prompt = (
            "Execute this coding task efficiently. Use read_files to batch-read multiple files at once. "
            "Make minimal changes and report exactly what happened.\n\nTask:\n" + task +
            "\n\nPlan:\n" + state.get("plan", "") + "\n\nContext:\n" + coder_context
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
            emit(state, {"type": "agent_error", "agent": "parallel_coder", "error": error_msg})
            state["status"] = "failed"
            state["result"] = f"Parallel coder failed: {error_msg}"
            return state
        append_state_message(state, "parallel_coder", state["result"])
        state["active_agent"] = "tester"
        state["iteration"] = state.get("iteration", 0) + 1
        emit(state, {"type": "agent_finished", "agent": "parallel_coder"})
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
        unit_cmd = getattr(settings, "unit_test_command", "")
        if not unit_cmd and getattr(settings, "test_impact_enabled", True) and not state.get("test_command"):
            try:
                from devx.test_engine.smart_runner import SmartTestRunner
                runner = SmartTestRunner(settings)
                impacted = runner.get_impacted_tests()
                if impacted:
                    targeted = runner.build_targeted_test_command(impacted)
                    if targeted:
                        unit_cmd = targeted
                        emit(state, {"type": "test_impact_selected", "tests": impacted})
            except Exception:
                pass

        command = state.get("test_command") or unit_cmd or settings.test_command or detect_test_command(settings.workspace, backend=backend)
        state["test_command"] = command or ""
        state["test_attempts"] = state.get("test_attempts", 0) + 1
        state["iteration"] = state.get("iteration", 0) + 1
        state["status"] = "running"
        lint_command = state.get("lint_command") or getattr(settings, "lint_command", "")
        state["lint_command"] = lint_command or ""
        
        # Run lint and tests in parallel when both are configured
        
        if lint_command and command:
            # Both lint and test configured - run in parallel
            import threading
            lint_result_holder = {"result": None}
            test_result_holder = {"result": None}
            
            def run_lint():
                if not approve("command", lint_command, "Configured lint command."):
                    lint_result_holder["result"] = ("denied", "Lint execution denied by user.")
                else:
                    result = tools.run(lint_command, approved=True)
                    lint_result_holder["result"] = ("ok" if result.ok else "failed", result.output or f"exit code: {result.exit_code}")
            
            def run_test():
                if not approve("command", command, "Detected/configured project test command."):
                    test_result_holder["result"] = ("denied", "Test execution denied by user.")
                else:
                    result = tools.run(command, approved=True)
                    test_result_holder["result"] = ("ok" if result.ok else "failed", result.output or f"exit code: {result.exit_code}")
            
            def safe_run_lint():
                try:
                    run_lint()
                except Exception as e:
                    lint_result_holder["result"] = ("failed", f"{e}")

            def safe_run_test():
                try:
                    run_test()
                except Exception as e:
                    test_result_holder["result"] = ("failed", f"{e}")

            lint_thread = threading.Thread(target=safe_run_lint)
            test_thread = threading.Thread(target=safe_run_test)
            lint_thread.start()
            test_thread.start()
            lint_thread.join()
            test_thread.join()

            if lint_result_holder["result"] is None:
                lint_status, lint_output = "failed", "Lint execution failed"
            else:
                lint_status, lint_output = lint_result_holder["result"]
            if test_result_holder["result"] is None:
                test_status, test_output = "failed", "Test execution failed"
            else:
                test_status, test_output = test_result_holder["result"]
            
            state["lint_result"] = lint_output
            emit(state, {"type": "lint_finished", "ok": lint_status == "ok", "exit_code": 0 if lint_status == "ok" else 1})
            
            max_diag_chars = int(getattr(settings, "diagnostic_distillation_max_chars", 4000))
            distill_enabled = getattr(settings, "diagnostic_distillation_enabled", True)

            if lint_status == "denied" or lint_status == "failed":
                lint_failure = distill_test_output(lint_output, max_diag_chars) if distill_enabled else lint_output
                state["test_result"] = "Lint failed:\n" + lint_failure
                state["status"] = "tests_failed"
            elif test_status == "denied":
                state["test_result"] = test_output
                state["status"] = "completed"
            elif test_status == "failed":
                state["test_result"] = distill_test_output(test_output, max_diag_chars) if distill_enabled else test_output
                state["status"] = "tests_failed"
                state["repair_attempts"] = state.get("repair_attempts", 0)
            else:
                state["test_result"] = test_output
                state["status"] = "completed"
                emit(state, {"type": "test_finished", "ok": True, "exit_code": 0})
        elif lint_command:
            # Only lint
            if not approve("command", lint_command, "Configured lint command."):
                state["lint_result"] = "Lint execution denied by user."
            else:
                lint_result = tools.run(lint_command, approved=True)
                state["lint_result"] = lint_result.output or f"exit code: {lint_result.exit_code}"
                emit(state, {"type": "lint_finished", "ok": lint_result.ok, "exit_code": lint_result.exit_code})
                if not lint_result.ok:
                    max_diag_chars = int(getattr(settings, "diagnostic_distillation_max_chars", 4000))
                    lint_failure = (
                        distill_test_output(state["lint_result"], max_diag_chars)
                        if getattr(settings, "diagnostic_distillation_enabled", True)
                        else state["lint_result"]
                    )
                    state["test_result"] = "Lint failed:\n" + lint_failure
                    state["status"] = "tests_failed"
                else:
                    state["status"] = "completed"
        elif command:
            # Only test
            if not approve("command", command, "Detected/configured project test command."):
                state["test_result"] = "Test execution denied by user."
                state["status"] = "completed"
            else:
                result = tools.run(command, approved=True)
                raw_test_output = result.output or f"exit code: {result.exit_code}"
                if not result.ok and getattr(settings, "diagnostic_distillation_enabled", True):
                    max_diag_chars = int(getattr(settings, "diagnostic_distillation_max_chars", 4000))
                    state["test_result"] = distill_test_output(raw_test_output, max_diag_chars)
                else:
                    state["test_result"] = raw_test_output
                state["status"] = "completed" if result.ok else "tests_failed"
                emit(state, {"type": "test_finished", "ok": result.ok, "exit_code": result.exit_code})
                if not result.ok:
                    state["repair_attempts"] = state.get("repair_attempts", 0)
        else:
            state["test_result"] = "No test command detected."
            state["status"] = "completed"

        # Stage 2: Integration tests (if configured and unit tests passed)
        integ_cmd = getattr(settings, "integration_test_command", "")
        if state.get("status") == "completed" and integ_cmd:
            if not approve("command", integ_cmd, "Configured integration test command."):
                emit(state, {"type": "integration_test_denied"})
            else:
                emit(state, {"type": "integration_test_started"})
                integ_res = tools.run(integ_cmd, approved=True)
                raw_integ_output = integ_res.output or f"exit code: {integ_res.exit_code}"
                if not integ_res.ok:
                    max_diag_chars = int(getattr(settings, "diagnostic_distillation_max_chars", 4000))
                    distilled = (
                        distill_test_output(raw_integ_output, max_diag_chars)
                        if getattr(settings, "diagnostic_distillation_enabled", True)
                        else raw_integ_output
                    )
                    state["test_result"] = f"Integration tests failed:\n{distilled}"
                    state["status"] = "tests_failed"
                    state["repair_attempts"] = state.get("repair_attempts", 0)
                    emit(state, {"type": "integration_test_finished", "ok": False, "exit_code": integ_res.exit_code})
                else:
                    emit(state, {"type": "integration_test_finished", "ok": True, "exit_code": 0})
        
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
    graph.add_node("parallel_coder", parallel_coder)
    graph.add_node("tester", tester)
    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", route_after_planner, {"coder": "coder", "parallel_coder": "parallel_coder", "finish": END})
    graph.add_conditional_edges("coder", route_after_coder, {"tester": "tester", "finish": END})
    graph.add_conditional_edges("parallel_coder", route_after_coder, {"tester": "tester", "finish": END})
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
            backend=backend,
        )
    except (ImportError, TypeError):
        return ConfiguredGraph(graph.compile(), session_id or run_id, backend=backend)
