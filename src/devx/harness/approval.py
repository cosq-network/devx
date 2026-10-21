from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from devx.harness.knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)


class ApprovalDecision(str, Enum):
    """The possible decisions for an approval request."""
    APPROVE = "approve"
    DENY = "deny"
    CANCEL = "cancel"
    CONDITIONAL = "conditional"  # Approved with restrictions


@dataclass(frozen=True)
class ApprovalRequest:
    """A request for user approval of an action.

    Attributes:
        run_id: Identifier for the current run/context.
        action: The type of action (e.g., 'edit', 'shell', 'read').
        target: The path or command target.
        reason: Human-readable explanation of why approval is needed.
        approval_level: Risk level of the action (info/moderate/high).
        approval_ttl: Expiration timestamp for the approval decision.
        risk_score: Numeric risk score (0-100) for the action.
    """
    run_id: str
    action: str
    target: str
    reason: str
    approval_level: str = "moderate"
    approval_ttl: float = field(default_factory=lambda: float("inf"))
    risk_score: int = field(default=50)


ApprovalHandler = Callable[[ApprovalRequest], ApprovalDecision | bool]


def matches_allow_rule(rules: object, action: str, target: str) -> bool:
    """Return ``True`` when a project permission rule explicitly allows an action."""
    import fnmatch

    normalized = str(action).strip().lower()
    if normalized == "write":
        normalized = "edit"
    if normalized == "command":
        normalized = "shell"

    for rule in rules or ():  # type: ignore[union-attr]
        if not isinstance(rule, dict):
            continue
        if str(rule.get("effect", "")).strip().lower() != "allow":
            continue
        rule_action = str(rule.get("action", "*")).strip().lower()
        if rule_action == "write":
            rule_action = "edit"
        if rule_action == "command":
            rule_action = "shell"
        if rule_action not in ("*", normalized):
            continue
        if fnmatch.fnmatchcase(str(target), str(rule.get("resource", "*"))):
            return True
    return False


class ApprovalManager:
    """Single approval boundary used by every mutating tool.

    All file writes and shell commands pass through this manager,
    which coordinates with an approval handler callback to determine
    whether an operation should be allowed, denied, or cancelled.
    """

    def __init__(
        self,
        handler: ApprovalHandler | None = None,
        knowledge_graph: KnowledgeGraph | None = None,
    ):
        self.handler = handler
        self.requests: list[ApprovalRequest] = []
        self.knowledge_graph = knowledge_graph

    def allows(self, run_id: str, action: str, target: str, reason: str = "") -> bool:
        """Check whether an action is allowed for the given run.

        Creates an ``ApprovalRequest`` and delegates to ``ask()``,
        returning ``True`` only if the decision is ``APPROVE``.
        """
        try:
            from devx.harness.approval import ApprovalRequest  # noqa: F811

            req = ApprovalRequest(run_id, action, target, reason)
            result = self.ask(req)
            return result == ApprovalDecision.APPROVE
        except Exception as exc:  # noqa: BLE001
            logger.error("Error checking approval for %s %s: %s", action, target[:80], exc)
            return False

    def _record(self, request: ApprovalRequest, decision: ApprovalDecision) -> None:
        """Persist a decision to the knowledge graph when one is attached.

        Recording is best-effort: a knowledge-graph failure must never change an
        approval outcome or crash a run.
        """
        if self.knowledge_graph is None:
            return
        try:
            node_id = f"approval:{request.run_id}:{request.action}:{request.target}"
            self.knowledge_graph.set_node(
                node_id,
                title=f"{request.action} {request.target}",
                description=request.reason,
                metadata={
                    "decision": decision.value,
                    "risk_score": request.risk_score,
                    "approval_level": request.approval_level,
                    "run_id": request.run_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record approval decision in knowledge graph: %s", exc)

    def ask(self, request: ApprovalRequest) -> ApprovalDecision:
        """Record an approval request and seek a decision.

        The request is appended to the internal history. If no handler
        is configured, the default decision is ``DENY``. Otherwise the
        handler's decision is returned (or converted from ``bool`` to
        ``ApprovalDecision``).

        Additional checks:
        - ``approval_ttl``: If set and expired, the approval is automatically denied.
        - ``risk_score``: High-risk actions (>80) require explicit approval even with a handler.
        - ``approval_level``: Different levels have different default decisions.
        """
        import time as _time

        logger.debug(
            "Approval request: %s %s level=%s risk=%s TTL=%.1fs",
            request.action, request.target[:80],
            getattr(request, "approval_level", "moderate"),
            getattr(request, "risk_score", 50),
            max(0, getattr(request, "approval_ttl", float("inf")) - _time.time()),
        )
        self.requests.append(request)
        decision = self._evaluate(request)
        self._record(request, decision)
        return decision

    def _evaluate(self, request: ApprovalRequest) -> ApprovalDecision:
        """Apply TTL, risk, and level rules, then fall back to the handler."""
        import time as _time

        # ── 1. TTL check ────────────────────────────────────────────
        ttl = getattr(request, "approval_ttl", float("inf"))
        if ttl < _time.time():
            logger.warning("Approval expired for %s %s", request.action, request.target[:80])
            return ApprovalDecision.DENY

        # ── 2. Risk-based filtering ────────────────────────────────
        risk_score = getattr(request, "risk_score", 50)
        action = getattr(request, "action", "")
        if risk_score > 80 and self.handler is not None:
            # High-risk: override handler and require explicit user confirmation
            logger.warning(
                "High-risk action %s (score=%.1f) overriding handler", action, risk_score
            )
            return ApprovalDecision.DENY

        # ── 3. Level-based defaults ────────────────────────────────
        level = getattr(request, "approval_level", "moderate")
        if level == "info" and self.handler is None:
            logger.info("Info-level action auto-approved: %s", action)
            return ApprovalDecision.APPROVE
        if level == "moderate" and self.handler is None:
            logger.info("Moderate-level action default-denied: %s", action)
            return ApprovalDecision.DENY

        # ── 4. Handler or default ────────────────────────────────────
        if self.handler is None:
            logger.warning("No approval handler configured; denying request: %s", action)
            return ApprovalDecision.DENY
        decision = self.handler(request)
        if isinstance(decision, bool):
            result = ApprovalDecision.APPROVE if decision else ApprovalDecision.DENY
            logger.info("Approval %s for %s: %s", result, action, request.target[:80])
            return result
        logger.info("Handler returned decision %s for %s", decision, action)
        return decision
