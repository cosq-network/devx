from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from devx.config import Settings
from devx.harness.approval import ApprovalManager, ApprovalDecision
from devx.harness.changes import ChangeJournal
from devx.harness.permissions import PermissionPolicy
from devx.harness.runtime import CancellationToken
from devx.harness.session import SessionStore
from devx.harness.tools import WorkspaceTools
from devx.token_optim.embeddings import LocalEmbedder


@dataclass
class AgentDependencies:
    """Container for all supervisor runtime dependencies."""
    settings: Settings
    approvals: ApprovalManager
    run_id: str
    cancellation: CancellationToken
    event_sink: Callable[[dict[str, Any]], None] | None
    session_id: str | None
    plan_only: bool
    policy: PermissionPolicy
    journal: ChangeJournal
    sessions: SessionStore
    tools: WorkspaceTools
    embedder: LocalEmbedder | None


class ToolCallBudget(Protocol):
    """Protocol for tool call budget consumption."""
    def __call__(self) -> None: ...


class ApprovalHandler(Protocol):
    """Protocol for approval handler."""
    def __call__(self, action: str, target: str, reason: str) -> ApprovalDecision: ...


class EventSink(Protocol):
    """Protocol for event sink."""
    def __call__(self, event: dict[str, Any]) -> None: ...


class ModelLike(Protocol):
    """Protocol for LangChain-compatible chat models."""
    def invoke(self, prompt: Any) -> Any: ...
    async def ainvoke(self, prompt: Any) -> Any: ...
    def stream(self, *args, **kwargs): ...
    async def astream(self, *args, **kwargs): ...
    def bind_tools(self, tools: list[Any]) -> Any: ...


class AgentLike(Protocol):
    """Protocol for LangChain agents."""
    def invoke(self, input: dict[str, Any]) -> dict[str, Any]: ...
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]: ...
    def stream(self, *args, **kwargs): ...
    def astream_events(self, *args, **kwargs): ...