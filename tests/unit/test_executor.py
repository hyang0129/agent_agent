"""Unit tests for dag/executor.py — dispatch, Review gate, failure classification, pause."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.context.provider import ContextProvider
from agent_agent.dag.engine import build_stub_dag
from agent_agent.dag.executor import (
    AgentError,
    DAGExecutor,
    DeterministicError,
    FailureCategory,
    ResourceExhaustionError,
    SafetyViolationError,
    TransientError,
    classify_failure,
)
from agent_agent.models.agent import CodeOutput, PlanOutput, ReviewOutput, ReviewVerdict
from agent_agent.models.context import IssueContext, RepoMetadata, SharedContext
from agent_agent.models.dag import DAGNode, DAGRun, DAGRunStatus, NodeStatus, NodeType
from agent_agent.state import StateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings() -> Settings:
    s = MagicMock(spec=Settings)
    s.usd_per_byte = 0.0
    return s  # type: ignore[return-value]


def _make_shared() -> SharedContext:
    return SharedContext(
        issue=IssueContext(url="https://github.com/o/r/issues/1", title="T", body="B"),
        repo_metadata=RepoMetadata(path="/repo", default_branch="main", claude_md=""),
    )


async def _make_state() -> StateStore:
    store = StateStore(":memory:")
    await store.init()
    return store


def _make_run(run_id: str = "run-1", budget: float = 10.0) -> DAGRun:
    now = datetime.now(timezone.utc)
    return DAGRun(
        id=run_id, issue_url="https://github.com/o/r/issues/1",
        repo_path="/repo", budget_usd=budget,
        created_at=now, updated_at=now,
    )


async def _setup_run(state: StateStore, run: DAGRun, nodes: list[DAGNode]) -> None:
    await state.create_dag_run(run)
    for node in nodes:
        await state.create_dag_node(node)


def _make_stub_agent(output_map: dict[str, tuple] | None = None):
    """Return an async agent_fn.

    output_map: {node_id: (AgentOutput, cost_usd)}  — defaults per node type.
    """
    async def agent_fn(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        if output_map and node.id in output_map:
            return output_map[node.id]
        if node.type == NodeType.PLAN:
            return PlanOutput(investigation_summary="stub", child_dag=None), 0.0
        elif node.type == NodeType.CODING:
            return CodeOutput(
                summary="stub code",
                branch_name=f"agent/stub/{node.composite_id}",
            ), 0.0
        elif node.type == NodeType.REVIEW:
            return ReviewOutput(
                verdict=ReviewVerdict.APPROVED, summary="stub review"
            ), 0.0
        raise ValueError(f"unknown node type {node.type}")
    return agent_fn


def _make_executor(state: StateStore, budget: BudgetManager, agent_fn) -> DAGExecutor:
    shared = _make_shared()
    provider = ContextProvider(shared, budget, state, _make_settings())
    return DAGExecutor(
        state=state,
        budget=budget,
        context_provider=provider,
        agent_fn=agent_fn,
        settings=_make_settings(),
    )


# ---------------------------------------------------------------------------
# classify_failure
# ---------------------------------------------------------------------------

def test_classify_transient() -> None:
    assert classify_failure(TransientError("net")) == FailureCategory.TRANSIENT

def test_classify_agent_error() -> None:
    assert classify_failure(AgentError("bad")) == FailureCategory.AGENT_ERROR

def test_classify_resource_exhaustion() -> None:
    assert classify_failure(ResourceExhaustionError()) == FailureCategory.RESOURCE_EXHAUSTION

def test_classify_deterministic() -> None:
    assert classify_failure(DeterministicError()) == FailureCategory.DETERMINISTIC

def test_classify_safety_violation() -> None:
    assert classify_failure(SafetyViolationError()) == FailureCategory.SAFETY_VIOLATION

def test_classify_unknown() -> None:
    assert classify_failure(RuntimeError("weird")) == FailureCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Dispatch order
# ---------------------------------------------------------------------------

async def test_dispatch_order() -> None:
    """Nodes must complete in topological order: L0-plan → L1-coding → L1-review → L1-plan."""
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    completed: list[str] = []

    async def recording_agent(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        completed.append(node.id)
        if node.type == NodeType.PLAN:
            return PlanOutput(investigation_summary="done", child_dag=None), 0.0
        elif node.type == NodeType.CODING:
            return CodeOutput(summary="code", branch_name="agent/stub/A"), 0.0
        return ReviewOutput(verdict=ReviewVerdict.APPROVED, summary="ok"), 0.0

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, recording_agent)
    await executor.execute(run, nodes)

    node_ids = [n.id for n in nodes]
    l0_plan = next(n.id for n in nodes if n.level == 0)
    l1_coding = next(n.id for n in nodes if n.level == 1 and n.type == NodeType.CODING)
    l1_review = next(n.id for n in nodes if n.level == 1 and n.type == NodeType.REVIEW)
    l1_plan = next(n.id for n in nodes if n.level == 1 and n.type == NodeType.PLAN)

    assert completed.index(l0_plan) < completed.index(l1_coding)
    assert completed.index(l1_coding) < completed.index(l1_review)
    assert completed.index(l1_review) < completed.index(l1_plan)


async def test_dag_completed_after_successful_run() -> None:
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, _make_stub_agent())
    await executor.execute(run, nodes)

    final = await state.get_dag_run(run.id)
    assert final is not None
    assert final.status == DAGRunStatus.COMPLETED


# ---------------------------------------------------------------------------
# Review gate
# ---------------------------------------------------------------------------

async def test_review_gate_branch_name_none_blocks_dispatch() -> None:
    """If coding node has no branch_name in the state store, Review is not dispatched."""
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    # Manually set coding node's branch_name to None (default — never set)
    # The coding stub intentionally does NOT set branch_name in this variant
    async def no_branch_agent(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        if node.type == NodeType.PLAN:
            return PlanOutput(investigation_summary="done", child_dag=None), 0.0
        if node.type == NodeType.CODING:
            # Return CodeOutput but the executor won't find branch_name in db
            # because we override the state store to keep branch_name=None
            return CodeOutput(summary="code", branch_name=""), 0.0
        raise AssertionError("Review should never be reached")

    # Monkey-patch update_dag_node_status to skip setting branch_name
    original_update = state.update_dag_node_status

    async def no_branch_update(node_id, status, worktree_path=None, branch_name=None):
        # Never persist branch_name
        return await original_update(node_id, status)

    state.update_dag_node_status = no_branch_update  # type: ignore[method-assign]

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, no_branch_agent)
    await executor.execute(run, nodes)

    # Review node must NOT be COMPLETED
    review_node = next(n for n in nodes if n.type == NodeType.REVIEW)
    db_review = await state.get_dag_node(review_node.id)
    assert db_review is not None
    assert db_review.status != NodeStatus.COMPLETED

    # DAG should have failed or been blocked
    final_run = await state.get_dag_run(run.id)
    assert final_run is not None
    assert final_run.status in (DAGRunStatus.FAILED, DAGRunStatus.ESCALATED)


async def test_review_gate_branch_name_set_dispatches() -> None:
    """Review is dispatched when coding node has branch_name set."""
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, _make_stub_agent())
    await executor.execute(run, nodes)

    review_node = next(n for n in nodes if n.type == NodeType.REVIEW)
    db_review = await state.get_dag_node(review_node.id)
    assert db_review is not None
    assert db_review.status == NodeStatus.COMPLETED


# ---------------------------------------------------------------------------
# Pause after budget overrun
# ---------------------------------------------------------------------------

async def test_pause_after_overrun() -> None:
    """Stub agent over-spends → DAG status = PAUSED; pending nodes remain PENDING."""
    state = await _make_state()
    run = _make_run(budget=1.0)
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    l0_plan_id = next(n.id for n in nodes if n.level == 0)

    async def overspend_agent(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        if node.id == l0_plan_id:
            # Spend 97% of the total budget on the first node
            return PlanOutput(investigation_summary="done", child_dag=None), 0.97
        raise AssertionError(f"Should not be dispatched after pause: {node.id}")

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=1.0)
    executor = _make_executor(state, budget, overspend_agent)
    await executor.execute(run, nodes)

    # DAG must be PAUSED
    final_run = await state.get_dag_run(run.id)
    assert final_run is not None
    assert final_run.status == DAGRunStatus.PAUSED

    # All non-L0 nodes must remain PENDING (not SKIPPED, not COMPLETED)
    for node in nodes:
        if node.id == l0_plan_id:
            continue  # L0 plan completed
        db_node = await state.get_dag_node(node.id)
        assert db_node is not None, f"Node {node.id} missing from state store"
        assert db_node.status == NodeStatus.PENDING, (
            f"Node {node.id} should be PENDING after pause, got {db_node.status}"
        )


# ---------------------------------------------------------------------------
# Rerun cap [P10.9]
# ---------------------------------------------------------------------------

async def test_rerun_cap_node_fails_twice_escalated() -> None:
    """Node fails twice with AgentError → third invocation not attempted → escalation."""
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    l0_plan_id = next(n.id for n in nodes if n.level == 0)
    invocation_count = {"n": 0}

    async def failing_agent(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        if node.id == l0_plan_id:
            invocation_count["n"] += 1
            raise AgentError("stubbed failure")
        raise AssertionError("should not reach other nodes")

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, failing_agent)
    await executor.execute(run, nodes)

    # Exactly 2 invocations (initial + 1 rerun); no third attempt
    assert invocation_count["n"] == 2

    # DAG must be escalated
    final_run = await state.get_dag_run(run.id)
    assert final_run is not None
    assert final_run.status == DAGRunStatus.ESCALATED

    # An escalation record must exist
    escalations = await state.list_escalations(run.id)
    assert len(escalations) == 1


# ---------------------------------------------------------------------------
# No-retry failures [P10.9]
# ---------------------------------------------------------------------------

async def test_safety_violation_escalated_on_first_occurrence() -> None:
    """SafetyViolationError → CRITICAL escalation on first invocation; no second attempt."""
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    l0_plan_id = next(n.id for n in nodes if n.level == 0)
    invocations = {"n": 0}

    async def safety_agent(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        invocations["n"] += 1
        raise SafetyViolationError("unsafe content detected")

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, safety_agent)
    await executor.execute(run, nodes)

    assert invocations["n"] == 1  # no second invocation

    from agent_agent.models.escalation import EscalationSeverity
    escalations = await state.list_escalations(run.id)
    assert len(escalations) == 1
    assert escalations[0].severity == EscalationSeverity.CRITICAL


async def test_resource_exhaustion_escalated_immediately() -> None:
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    invocations = {"n": 0}

    async def resource_agent(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        invocations["n"] += 1
        raise ResourceExhaustionError("out of tokens")

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, resource_agent)
    await executor.execute(run, nodes)

    assert invocations["n"] == 1
    final_run = await state.get_dag_run(run.id)
    assert final_run is not None
    assert final_run.status == DAGRunStatus.ESCALATED


async def test_deterministic_error_escalated_immediately() -> None:
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    invocations = {"n": 0}

    async def det_agent(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        invocations["n"] += 1
        raise DeterministicError("always fails")

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, det_agent)
    await executor.execute(run, nodes)

    assert invocations["n"] == 1
    final_run = await state.get_dag_run(run.id)
    assert final_run is not None
    assert final_run.status == DAGRunStatus.ESCALATED


# ---------------------------------------------------------------------------
# Transient retry [P10.7]
# ---------------------------------------------------------------------------

async def test_transient_retry_succeeds_on_third_attempt() -> None:
    """TransientError twice → success on third; retry_count=2; no rerun; COMPLETED."""
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    l0_plan_id = next(n.id for n in nodes if n.level == 0)
    attempts = {"n": 0}

    async def flaky_agent(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        if node.id == l0_plan_id:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise TransientError(f"transient failure #{attempts['n']}")
            return PlanOutput(investigation_summary="done", child_dag=None), 0.0
        # Other nodes succeed normally
        if node.type == NodeType.CODING:
            return CodeOutput(summary="code", branch_name="agent/stub/A"), 0.0
        return ReviewOutput(verdict=ReviewVerdict.APPROVED, summary="ok"), 0.0

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, flaky_agent)
    await executor.execute(run, nodes)

    # 3 total attempts (2 retries)
    assert attempts["n"] == 3

    # No escalation triggered
    escalations = await state.list_escalations(run.id)
    assert escalations == []

    # DAG completed successfully
    final_run = await state.get_dag_run(run.id)
    assert final_run is not None
    assert final_run.status == DAGRunStatus.COMPLETED

    # L0-plan node is COMPLETED
    db_node = await state.get_dag_node(l0_plan_id)
    assert db_node is not None
    assert db_node.status == NodeStatus.COMPLETED
