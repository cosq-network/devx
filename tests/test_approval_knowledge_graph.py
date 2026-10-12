"""Tests for approval TTL/risk rules, rule matching, and the knowledge graph."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from devx.harness.approval import (
    ApprovalDecision,
    ApprovalManager,
    ApprovalRequest,
    matches_allow_rule,
)
from devx.harness.knowledge_graph import KnowledgeGraph

# ---------------------------------------------------------------------------
# matches_allow_rule
# ---------------------------------------------------------------------------


def test_matches_allow_rule_honours_allow_rules():
    rules = [
        {"action": "shell", "resource": "git *", "effect": "allow"},
        {"action": "edit", "resource": "tests/**", "effect": "allow"},
    ]
    assert matches_allow_rule(rules, "shell", "git status")
    assert matches_allow_rule(rules, "edit", "tests/test_x.py")


def test_matches_allow_rule_ignores_deny_and_action_mismatch():
    rules = [
        {"action": "shell", "resource": "rm *", "effect": "deny"},
        {"action": "edit", "resource": "**", "effect": "ask"},
    ]
    assert not matches_allow_rule(rules, "shell", "rm -rf .")
    assert not matches_allow_rule(rules, "edit", "src/app.py")
    # Non-matching action for an allow rule.
    assert not matches_allow_rule(
        [{"action": "shell", "resource": "*", "effect": "allow"}], "edit", "src/app.py"
    )


def test_matches_allow_rule_wildcards_and_bad_input():
    rules = [{"action": "*", "resource": "*", "effect": "allow"}]
    assert matches_allow_rule(rules, "read", "anything.goes")
    # Malformed rules are ignored rather than raising.
    assert not matches_allow_rule(["not-a-dict", None], "read", "x")
    assert not matches_allow_rule(None, "read", "x")


# ---------------------------------------------------------------------------
# Approval TTL / risk / level rules
# ---------------------------------------------------------------------------


def test_ttl_expired_is_denied_even_with_approving_handler():
    manager = ApprovalManager(lambda _request: ApprovalDecision.APPROVE)
    expired = ApprovalRequest("run", "edit", "x.py", "r", approval_ttl=time.time() - 5)
    assert manager.ask(expired) == ApprovalDecision.DENY


def test_ttl_in_future_defers_to_handler():
    manager = ApprovalManager(lambda _request: ApprovalDecision.APPROVE)
    pending = ApprovalRequest("run", "edit", "x.py", "r", approval_ttl=time.time() + 60)
    assert manager.ask(pending) == ApprovalDecision.APPROVE


def test_high_risk_overrides_approving_handler():
    manager = ApprovalManager(lambda _request: ApprovalDecision.APPROVE)
    risky = ApprovalRequest("run", "shell", "rm -rf /", "r", risk_score=95)
    assert manager.ask(risky) == ApprovalDecision.DENY


def test_moderate_risk_uses_handler():
    manager = ApprovalManager(lambda _request: ApprovalDecision.APPROVE)
    normal = ApprovalRequest("run", "edit", "x.py", "r", risk_score=20)
    assert manager.ask(normal) == ApprovalDecision.APPROVE


def test_level_defaults_without_handler():
    info = ApprovalManager()
    assert info.ask(ApprovalRequest("run", "read", "x.py", "r", approval_level="info")) == (
        ApprovalDecision.APPROVE
    )
    moderate = ApprovalManager()
    assert moderate.ask(ApprovalRequest("run", "edit", "x.py", "r")) == ApprovalDecision.DENY
    high = ApprovalManager()
    assert high.ask(ApprovalRequest("run", "edit", "x.py", "r", approval_level="high")) == (
        ApprovalDecision.DENY
    )


def test_bool_handler_is_converted_to_decision():
    assert ApprovalManager(lambda _r: True).ask(
        ApprovalRequest("run", "edit", "x.py", "r")
    ) == ApprovalDecision.APPROVE
    assert ApprovalManager(lambda _r: False).ask(
        ApprovalRequest("run", "edit", "x.py", "r")
    ) == ApprovalDecision.DENY


def test_allows_returns_bool():
    assert ApprovalManager(lambda _r: ApprovalDecision.APPROVE).allows("run", "edit", "x.py")
    assert not ApprovalManager().allows("run", "edit", "x.py")


def test_ask_appends_request_history():
    manager = ApprovalManager(lambda _r: True)
    manager.ask(ApprovalRequest("run", "edit", "x.py", "r"))
    manager.ask(ApprovalRequest("run", "edit", "y.py", "r"))
    assert [request.target for request in manager.requests] == ["x.py", "y.py"]


# ---------------------------------------------------------------------------
# KnowledgeGraph
# ---------------------------------------------------------------------------


def test_knowledge_graph_node_round_trip(tmp_path: Path):
    kg = KnowledgeGraph(tmp_path / "kg.db")
    kg.set_node("file:app.py", title="app.py", description="entry point", metadata={"hits": 3})
    node = kg.get_node("file:app.py")
    assert node is not None
    assert node["title"] == "app.py"
    assert node["description"] == "entry point"
    assert node["metadata"] == {"hits": 3}
    assert node["created_at"] > 0


def test_knowledge_graph_missing_node_returns_none(tmp_path: Path):
    kg = KnowledgeGraph(tmp_path / "kg.db")
    assert kg.get_node("does-not-exist") is None


def test_knowledge_graph_edges(tmp_path: Path):
    kg = KnowledgeGraph(tmp_path / "kg.db")
    kg.set_node("a")
    kg.set_node("b")
    kg.set_edge("a", "b", relation="imports", strength=0.8)
    edges = kg.get_edges("a")
    assert len(edges) == 1
    assert edges[0]["target_id"] == "b"
    assert edges[0]["relation"] == "imports"
    assert edges[0]["strength"] == pytest.approx(0.8)
    assert kg.get_edges("b") == []


def test_knowledge_graph_persists_across_instances(tmp_path: Path):
    db = tmp_path / "kg.db"
    first = KnowledgeGraph(db)
    first.set_node("persisted", title="kept")
    second = KnowledgeGraph(db)
    node = second.get_node("persisted")
    assert node is not None and node["title"] == "kept"


def test_knowledge_graph_kg_property_returns_self(tmp_path: Path):
    kg = KnowledgeGraph(tmp_path / "kg.db")
    assert kg.kg is kg


# ---------------------------------------------------------------------------
# Approval -> KnowledgeGraph integration
# ---------------------------------------------------------------------------


def test_approval_records_decision_in_knowledge_graph(tmp_path: Path):
    kg = KnowledgeGraph(tmp_path / "kg.db")
    manager = ApprovalManager(lambda _r: ApprovalDecision.APPROVE, knowledge_graph=kg)
    manager.ask(ApprovalRequest("run-1", "edit", "src/app.py", "needs write"))
    node = kg.get_node("approval:run-1:edit:src/app.py")
    assert node is not None
    assert node["metadata"]["decision"] == "approve"
    assert node["metadata"]["run_id"] == "run-1"


def test_knowledge_graph_failure_does_not_change_decision(tmp_path: Path):
    class BrokenGraph:
        def set_node(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    manager = ApprovalManager(lambda _r: True, knowledge_graph=BrokenGraph())  # type: ignore[arg-type]
    assert manager.ask(ApprovalRequest("run", "edit", "x.py", "r")) == ApprovalDecision.APPROVE
