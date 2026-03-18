"""Unit tests for dag/engine.py — construction, topological sort, dependency resolution."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_agent.dag.engine import build_stub_dag, topological_sort
from agent_agent.models.dag import DAGRun, DAGRunStatus, NodeType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_run() -> DAGRun:
    now = datetime.now(timezone.utc)
    return DAGRun(
        id="test-run-abc123",
        issue_url="https://github.com/org/repo/issues/1",
        repo_path="/tmp/repo",
        status=DAGRunStatus.PENDING,
        budget_usd=5.0,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# L0 DAG structure
# ---------------------------------------------------------------------------


def test_l0_has_one_plan_node() -> None:
    nodes = build_stub_dag(_make_run())
    l0_nodes = [n for n in nodes if n.level == 0]
    assert len(l0_nodes) == 1
    assert l0_nodes[0].type == NodeType.PLAN


def test_l0_plan_has_no_parents() -> None:
    nodes = build_stub_dag(_make_run())
    l0 = next(n for n in nodes if n.level == 0)
    assert l0.parent_node_ids == []


def test_l0_plan_points_to_l1_coding() -> None:
    nodes = build_stub_dag(_make_run())
    node_map = {n.id: n for n in nodes}
    l0 = next(n for n in nodes if n.level == 0)
    assert len(l0.child_node_ids) == 1
    child = node_map[l0.child_node_ids[0]]
    assert child.type == NodeType.CODING
    assert child.level == 1


# ---------------------------------------------------------------------------
# L1 child DAG structure
# ---------------------------------------------------------------------------


def test_l1_has_coding_review_plan() -> None:
    nodes = build_stub_dag(_make_run())
    l1_nodes = [n for n in nodes if n.level == 1]
    types = [n.type for n in l1_nodes]
    assert NodeType.CODING in types
    assert NodeType.REVIEW in types
    assert NodeType.PLAN in types


def test_l1_coding_depends_on_l0_plan() -> None:
    nodes = build_stub_dag(_make_run())
    l0_plan = next(n for n in nodes if n.level == 0)
    l1_coding = next(n for n in nodes if n.level == 1 and n.type == NodeType.CODING)
    assert l0_plan.id in l1_coding.parent_node_ids


def test_l1_review_depends_on_l1_coding() -> None:
    nodes = build_stub_dag(_make_run())
    l1_coding = next(n for n in nodes if n.level == 1 and n.type == NodeType.CODING)
    l1_review = next(n for n in nodes if n.level == 1 and n.type == NodeType.REVIEW)
    assert l1_coding.id in l1_review.parent_node_ids


def test_l1_terminal_plan_depends_on_review() -> None:
    nodes = build_stub_dag(_make_run())
    l1_review = next(n for n in nodes if n.level == 1 and n.type == NodeType.REVIEW)
    l1_plan = next(n for n in nodes if n.level == 1 and n.type == NodeType.PLAN)
    assert l1_review.id in l1_plan.parent_node_ids


def test_terminal_plan_has_no_successors() -> None:
    """Terminal plan node must have no child_node_ids (it is the last node)."""
    nodes = build_stub_dag(_make_run())
    l1_plan = next(n for n in nodes if n.level == 1 and n.type == NodeType.PLAN)
    assert l1_plan.child_node_ids == []


def test_all_nodes_share_dag_run_id() -> None:
    run = _make_run()
    nodes = build_stub_dag(run)
    assert all(n.dag_run_id == run.id for n in nodes)


def test_total_node_count() -> None:
    nodes = build_stub_dag(_make_run())
    assert len(nodes) == 4  # L0-plan, L1-coding, L1-review, L1-plan


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------


def test_topological_sort_order() -> None:
    """L0-PLAN must come before all L1 nodes; L1-CODING before REVIEW; REVIEW before PLAN."""
    nodes = build_stub_dag(_make_run())
    ordered = topological_sort(nodes)
    ids = [n.id for n in ordered]

    l0_plan = next(n for n in nodes if n.level == 0)
    l1_coding = next(n for n in nodes if n.level == 1 and n.type == NodeType.CODING)
    l1_review = next(n for n in nodes if n.level == 1 and n.type == NodeType.REVIEW)
    l1_plan = next(n for n in nodes if n.level == 1 and n.type == NodeType.PLAN)

    assert ids.index(l0_plan.id) < ids.index(l1_coding.id)
    assert ids.index(l1_coding.id) < ids.index(l1_review.id)
    assert ids.index(l1_review.id) < ids.index(l1_plan.id)


def test_topological_sort_all_nodes_present() -> None:
    nodes = build_stub_dag(_make_run())
    ordered = topological_sort(nodes)
    assert len(ordered) == len(nodes)


def test_topological_sort_cycle_raises() -> None:
    """A cyclic graph should raise ValueError."""
    from datetime import datetime, timezone
    from agent_agent.models.dag import DAGNode, NodeStatus

    now = datetime.now(timezone.utc)
    a = DAGNode(
        id="a",
        dag_run_id="r",
        type=NodeType.PLAN,
        status=NodeStatus.PENDING,
        level=0,
        composite_id="A",
        parent_node_ids=["b"],
        created_at=now,
        updated_at=now,
    )
    b = DAGNode(
        id="b",
        dag_run_id="r",
        type=NodeType.PLAN,
        status=NodeStatus.PENDING,
        level=0,
        composite_id="B",
        parent_node_ids=["a"],
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ValueError, match="cycle"):
        topological_sort([a, b])


def test_node_dependency_resolution_via_parent_ids() -> None:
    """Every node's parent_node_ids must resolve to existing node IDs."""
    nodes = build_stub_dag(_make_run())
    node_ids = {n.id for n in nodes}
    for node in nodes:
        for parent_id in node.parent_node_ids:
            assert parent_id in node_ids, f"{node.id} references unknown parent {parent_id}"
