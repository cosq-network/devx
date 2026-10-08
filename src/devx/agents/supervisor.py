from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from devx.agents.graph import DevxState
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
from devx.llm import invoke_with_retry, _is_rate_limit_error
from devx.token_optim.compaction import append_message
from devx.token_optim.context import read_context, select_relevant_files

if TYPE_CHECKING:
    from devx.config import Settings
    from devx.token_optim.embeddings import LocalEmbedder


@dataclass
class SupervisorRunner:
    settings: Settings
    approvals: ApprovalManager
    run_id: str = "run"
    cancellation: CancellationToken | None = None
    event_sink: Any = None
    session_id: str | None = None
    plan_only: bool = False

    def __post_init__(self) -> None:
        policy = PermissionPolicy(
            self.settings.workspace,
            self.settings.approval_required,
            getattr(self.settings, "permission_rules", ()),
        )
        self.policy = policy
        journal = ChangeJournal(self.settings.session_db)
        self.sessions = SessionStore(self.settings.session_db)
        self.cancellation = self.cancellation or CancellationToken()
        assert self.cancellation is not None
        self.embedder: LocalEmbedder | None = None
        if getattr(self.settings, "embeddings_enabled", False):
            try:
                from devx.token_optim.embeddings import LocalEmbedder
                self.embedder = LocalEmbedder(
                    self.settings.embedding_model,
                    self.settings.embedding_cache_dir,
                    self.settings.embeddings_offline,
                )
            except (ImportError, OSError, RuntimeError, ValueError):
                self.embedder = None
        self.tools = WorkspaceTools(
            policy,
            max_bytes=self.settings.max_file_bytes,
            timeout=self.settings.command_timeout,
            run_id=self.run_id,
            journal=journal,
            cancellation=self.cancellation,
        )
        self.registry = build_tool_registry(
            self.settings,
            self._approve,
            load_extensions=not self.plan_only,
        )
        self._tool_calls = 0

    def _event(self, state: DevxState, event: dict[str, Any]) -> None:
        state.events.append(event)
        self.sessions.append_event(self.session_id or self.run_id, self.run_id, event)
        if self.event_sink:
            self.event_sink(event)

    def _approve(self, action: str, target: str, reason: str = "") -> bool:
        return self.approvals.allows(self.run_id, action, target, reason)

    def _consume_tool_call(self) -> None:
        assert self.cancellation is not None
        self.cancellation.raise_if_cancelled()
        self._tool_calls += 1
        limit = max(1, int(getattr(self.settings, "max_tool_calls", 40)))
        if self._tool_calls > limit:
            raise AgentToolLimitExceeded(f"Tool-call limit exceeded ({limit})")

    def _append_message(self, state: DevxState, role: str, content: str) -> None:
        result = append_message(
            state.messages,
            role,
            content,
            enabled=getattr(self.settings, "context_compaction_enabled", True),
            max_messages=max(2, int(getattr(self.settings, "max_context_messages", 24))),
            max_chars=max(256, int(getattr(self.settings, "max_context_chars", 24_000))),
            summary_chars=max(128, int(getattr(self.settings, "compaction_summary_chars", 4_000))),
        )
        if result.compacted:
            self._event(state, {"type": "context_compacted", "removed_messages": result.removed_messages})

    def __call__(self, state: DevxState) -> DevxState:
        state.status = "running"
        cancellation = self.cancellation
        assert cancellation is not None
        try:
            if state.iteration >= self.settings.max_iterations:
                state.status = "limit_exceeded"
                state.result = "Iteration limit reached before the run started."
                return state
            cancellation.raise_if_cancelled()
            self._event(state, {"type": "agent_started", "agent": "planner"})
            from devx.llm import create_llm
            model = create_llm(self.settings)
            context_paths = [self.policy.path(Path(path)) for path in state.files_in_context]
            max_context_files = max(1, int(getattr(self.settings, "max_context_files", 5)))
            selected_paths = context_paths
            if len(context_paths) > max_context_files:
                selected_paths = select_relevant_files(state.task, context_paths, max_context_files, self.embedder)
                state.files_in_context = [str(path) for path in selected_paths]
                self._event(state, {"type": "context_selected", "files": [str(path) for path in selected_paths]})
            context = read_context(selected_paths, self.settings.max_file_bytes)
            try:
                plan_response = invoke_with_retry(model, 
                    "Create a concise, concrete coding plan. Identify files, intended changes, and tests. "
                    "Do not edit files.\n\nTask:\n" + state.task + "\n\nContext:\n" + context
                )
            except Exception as exc:
                error_msg = str(exc)
                if _is_rate_limit_error(exc):
                    error_msg = f"Rate limit exceeded: {error_msg}. Please wait and retry, or switch to a different model."
                self._event(state, {"type": "agent_error", "agent": "planner", "error": error_msg})
                state.status = "failed"
                state.result = f"Planner failed: {error_msg}"
                return state
            plan = getattr(plan_response, "content", str(plan_response))
            self._event(state, {"type": "agent_finished", "agent": "planner"})
            self._append_message(state, "planner", str(plan))
            state.active_agent = "coder"
            state.iteration += 1
            if self.plan_only:
                state.status = "plan_only"
                state.result = str(plan)
                self._event(state, {"type": "plan_ready"})
                return state
            if state.iteration >= self.settings.max_iterations:
                state.status = "limit_exceeded"
                state.result = "Plan created, but the iteration limit was reached before coding."
                return state

            cancellation.raise_if_cancelled()
            self._event(state, {"type": "agent_started", "agent": "coder"})
            coder = build_coding_agent(
                self.settings, self.tools, self._approve, self._consume_tool_call, self.registry
            )
            coding_messages = [{"role": "user", "content": (
                "Execute this coding task using the available tools. Inspect first, make minimal approved edits, "
                "and report exactly what happened.\n\nTask:\n" + state.task + "\n\nPlan:\n" + str(plan) + "\n\nContext:\n" + context
                + ("\n\nRequested specialist perspective: " + state.requested_agent
                   if state.requested_agent != "supervisor" else "")
            )}]
            try:
                if self.event_sink:
                    coding_text = stream_agent_text(coder, coding_messages, lambda event: self._event(state, event))
                    coding = {"messages": [{"content": coding_text}]}
                else:
                    coding = coder.invoke({"messages": coding_messages})  # type: ignore[arg-type]
            except Exception as exc:
                error_msg = str(exc)
                if _is_rate_limit_error(exc):
                    error_msg = f"Rate limit exceeded: {error_msg}. Please wait and retry, or switch to a different model."
                self._event(state, {"type": "agent_error", "agent": "coder", "error": error_msg})
                state.status = "failed"
                state.result = f"Coder failed: {error_msg}"
                return state
            self._append_message(state, "coder", response_text(coding))
            self._event(state, {"type": "agent_finished", "agent": "coder"})
            state.active_agent = "tester"
            state.iteration += 1
            if state.iteration >= self.settings.max_iterations:
                state.status = "limit_exceeded"
                state.result = response_text(coding) + "\n\nIteration limit reached before testing."
                return state

            cancellation.raise_if_cancelled()
            self._event(state, {"type": "agent_started", "agent": "tester"})
            test_result = "No test command configured."
            test_command = self.settings.test_command or detect_test_command(self.settings.workspace)
            lint_command = getattr(self.settings, "lint_command", "")
            state.lint_command = lint_command or ""

            def run_lint() -> bool | None:
                if not lint_command:
                    return None
                if not self._approve("command", lint_command, "Configured lint command."):
                    state.lint_result = "Lint execution denied by user."
                    return None
                lint_result = self.tools.run(lint_command, approved=True)
                state.lint_result = lint_result.output or f"exit code: {lint_result.exit_code}"
                self._event(state, {"type": "lint_finished", "ok": lint_result.ok,
                                     "exit_code": lint_result.exit_code})
                return lint_result.ok

            def run_tests() -> tuple[bool, str] | None:
                if not test_command:
                    return None
                if not self._approve("command", test_command, "Detected/configured project test command."):
                    return None
                result = self.tools.run(test_command, approved=True)
                self._event(state, {"type": "test_finished", "ok": result.ok,
                                     "exit_code": result.exit_code})
                return result.ok, result.output or f"exit code: {result.exit_code}"

            lint_ok = run_lint()
            if lint_ok is False:
                test_result = "Lint failed:\n" + state.lint_result
                checks_ok = False
            elif test_command:
                test_run = run_tests()
                if test_run is None:
                    test_result = "Test execution denied by user."
                    checks_ok = True
                else:
                    checks_ok, test_result = test_run
            else:
                checks_ok = True

            # Give the coder a bounded opportunity to repair either lint or tests.
            for _ in range(max(0, min(2, self.settings.max_iterations - state.iteration))):
                cancellation.raise_if_cancelled()
                if checks_ok:
                    break
                state.active_agent = "coder"
                state.iteration += 1
                repair = coder.invoke({"messages": [{"role": "user", "content": (
                    "The latest lint/test run failed. Inspect the failure, make a minimal approved fix, "
                    "and report the change.\n\nFailure:\n" + test_result
                )}]})  # type: ignore[arg-type]
                self._append_message(state, "coder", response_text(repair))
                lint_ok = run_lint()
                if lint_ok is False:
                    test_result = "Lint failed:\n" + state.lint_result
                    checks_ok = False
                    continue
                if test_command:
                    test_run = run_tests()
                    if test_run is None:
                        test_result = "Test execution denied by user."
                        checks_ok = True
                    else:
                        checks_ok, test_result = test_run
                else:
                    checks_ok = True
            if not checks_ok:
                state.status = "tests_failed"
            state.test_result = test_result
            self._append_message(state, "tester", test_result)
            self._event(state, {"type": "agent_finished", "agent": "tester"})
            state.active_agent = "supervisor"
            if state.status != "tests_failed":
                state.status = "completed"
            state.result = response_text(coding) + "\n\nTests:\n" + test_result
        except RunCancelled as exc:
            state.status = "cancelled"
            self._event(state, {"type": "cancelled"})
            state.result = str(exc)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            state.status = "failed"
            self._event(state, {"type": "error", "error": str(exc)})
            state.result = f"Agent run failed: {exc}"
        finally:
            self.registry.close()
        return state


DevState = DevxState