"""Phase 4e unit tests — composite dispatch, child DAG recursion, budget allocation."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.context.provider import ContextProvider
from agent_agent.dag.engine import build_stub_dag
from agent_agent.dag.executor import (
    DAGExecutor,
    ResourceExhaustionError,
)
from agent_agent.models.agent import (
    ChildDAGSpec,
    CodeOutput,
    CompositeSpec,
    PlanOutput,
    ReviewOutput,
    ReviewVerdict,
    SequentialEdge,
)
from agent_agent.models.context import IssueContext, RepoMetadata, SharedContext
from agent_agent.models.dag import DAGNode, DAGRun, DAGRunStatus, NodeType
from agent_agent.state import StateStore
from agent_agent.worktree import WorktreeManager, WorktreeRecord


# ---------------------------------------------------------------------------
# Helpers (reuse patterns from test_executor.py)
# ---------------------------------------------------------------------------


def _make_settings() -> Settings:
    s = MagicMock(spec=Settings)
    s.usd_per_byte = 0.0
    s.max_budget_usd = 10.0
    s.model = "claude-haiku-4-5-20251001"
    s.git_push_enabled = False
    s.worktree_base_dir = "/tmp/test-worktrees"
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
        id=run_id,
        issue_url="https://github.com/o/r/issues/1",
        repo_path="/repo",
        budget_usd=budget,
        created_at=now,
        updated_at=now,
    )


async def _setup_run(state: StateStore, run: DAGRun, nodes: list[DAGNode]) -> None:
    await state.create_dag_run(run)
    for node in nodes:
        await state.create_dag_node(node)


def _make_stub_agent(output_map: dict[str, tuple] | None = None):
    """Return an async agent_fn."""

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
            return ReviewOutput(verdict=ReviewVerdict.APPROVED, summary="stub review"), 0.0
        raise ValueError(f"unknown node type {node.type}")

    return agent_fn


def _make_executor(
    state: StateStore,
    budget: BudgetManager,
    agent_fn=None,
    **kwargs,
) -> DAGExecutor:
    shared = _make_shared()
    settings = _make_settings()
    provider = ContextProvider(shared, budget, state, settings)
    return DAGExecutor(
        state=state,
        budget=budget,
        context_provider=provider,
        agent_fn=agent_fn,
        settings=settings,
        **kwargs,
    )


def _make_child_dag_spec(
    composite_ids: list[str] | None = None,
    sequential_edges: list[SequentialEdge] | None = None,
) -> ChildDAGSpec:
    """Create a simple ChildDAGSpec for testing."""
    ids = composite_ids or ["A"]
    composites = [
        CompositeSpec(id=cid, scope=f"scope-{cid}", branch_suffix=f"fix-{cid}") for cid in ids
    ]
    return ChildDAGSpec(
        composites=composites,
        sequential_edges=sequential_edges or [],
    )


def _make_node(
    node_id: str,
    run_id: str,
    node_type: NodeType,
    level: int = 1,
    composite_id: str = "A",
    parent_node_ids: list[str] | None = None,
) -> DAGNode:
    now = datetime.now(timezone.utc)
    return DAGNode(
        id=node_id,
        dag_run_id=run_id,
        type=node_type,
        level=level,
        composite_id=composite_id,
        parent_node_ids=parent_node_ids or [],
        child_node_ids=[],
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Test 1: _dispatch_composite routes PLAN nodes to PlanComposite
# ---------------------------------------------------------------------------


async def test_dispatch_composite_routes_plan_to_plan_composite() -> None:
    """_dispatch_composite routes PLAN nodes to PlanComposite."""
    state = await _make_state()
    run = _make_run()
    node = _make_node("n-plan", run.id, NodeType.PLAN, level=0)
    all_nodes = [node]

    settings = _make_settings()
    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    budget.allocate([node.id])
    shared = _make_shared()
    provider = ContextProvider(shared, budget, state, settings)

    executor = DAGExecutor(
        state=state,
        budget=budget,
        context_provider=provider,
        agent_fn=None,
        settings=settings,
        repo_path="/repo",
    )

    context = await provider.build_context(node, all_nodes)

    mock_plan_output = PlanOutput(investigation_summary="plan done", child_dag=None)
    with patch(
        "agent_agent.agents.plan.PlanComposite",
        autospec=True,
    ) as MockPlanCls:
        instance = MockPlanCls.return_value
        instance.execute = AsyncMock(return_value=(mock_plan_output, 0.01))

        output, cost = await executor._dispatch_composite(node, context, run, all_nodes)

    assert isinstance(output, PlanOutput)
    assert output.investigation_summary == "plan done"
    MockPlanCls.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: _dispatch_composite routes CODING nodes to CodingComposite
# ---------------------------------------------------------------------------


async def test_dispatch_composite_routes_coding_to_coding_composite() -> None:
    """_dispatch_composite routes CODING nodes to CodingComposite (with worktree creation)."""
    state = await _make_state()
    run = _make_run()
    await state.create_dag_run(run)
    node = _make_node("n-code", run.id, NodeType.CODING)
    await state.create_dag_node(node)
    all_nodes = [node]

    settings = _make_settings()
    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    budget.allocate([node.id])
    shared = _make_shared()
    provider = ContextProvider(shared, budget, state, settings)

    mock_worktree_mgr = AsyncMock(spec=WorktreeManager)
    mock_record = WorktreeRecord(
        path="/tmp/wt-code",
        branch="agent-run1-code-1",
        dag_run_id=run.id,
        node_id=node.id,
        readonly=False,
    )
    mock_worktree_mgr.create_coding_worktree = AsyncMock(return_value=mock_record)
    mock_worktree_mgr.remove_worktree = AsyncMock()

    executor = DAGExecutor(
        state=state,
        budget=budget,
        context_provider=provider,
        agent_fn=None,
        settings=settings,
        worktree_manager=mock_worktree_mgr,
        repo_path="/repo",
        issue_number="1",
    )

    context = await provider.build_context(node, all_nodes)

    mock_code_output = CodeOutput(
        summary="coded", branch_name="agent-run1-code-1", files_changed=[]
    )
    with patch(
        "agent_agent.agents.coding.CodingComposite",
        autospec=True,
    ) as MockCodingCls:
        instance = MockCodingCls.return_value
        instance.execute = AsyncMock(return_value=(mock_code_output, 0.05))

        output, cost = await executor._dispatch_composite(node, context, run, all_nodes)

    assert isinstance(output, CodeOutput)
    mock_worktree_mgr.create_coding_worktree.assert_called_once()
    mock_worktree_mgr.remove_worktree.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: _dispatch_composite routes REVIEW nodes to ReviewComposite
# ---------------------------------------------------------------------------


async def test_dispatch_composite_routes_review_to_review_composite() -> None:
    """_dispatch_composite routes REVIEW nodes to ReviewComposite (with review worktree)."""
    state = await _make_state()
    run = _make_run()
    await state.create_dag_run(run)

    # Create coding parent with branch_name set
    coding_node = _make_node("n-code", run.id, NodeType.CODING)
    await state.create_dag_node(coding_node)
    await state.update_dag_node_status(coding_node.id, "completed", branch_name="agent-branch")

    review_node = _make_node(
        "n-review",
        run.id,
        NodeType.REVIEW,
        parent_node_ids=["n-code"],
    )
    await state.create_dag_node(review_node)
    all_nodes = [coding_node, review_node]

    settings = _make_settings()
    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    budget.allocate([n.id for n in all_nodes])
    shared = _make_shared()
    provider = ContextProvider(shared, budget, state, settings)

    mock_worktree_mgr = AsyncMock(spec=WorktreeManager)
    mock_record = WorktreeRecord(
        path="/tmp/wt-review",
        branch="agent-branch",
        dag_run_id=run.id,
        node_id=review_node.id,
        readonly=True,
    )
    mock_worktree_mgr.create_review_worktree = AsyncMock(return_value=mock_record)
    mock_worktree_mgr.remove_worktree = AsyncMock()

    executor = DAGExecutor(
        state=state,
        budget=budget,
        context_provider=provider,
        agent_fn=None,
        settings=settings,
        worktree_manager=mock_worktree_mgr,
        repo_path="/repo",
        issue_number="1",
    )

    context = await provider.build_context(review_node, all_nodes)

    mock_review_output = ReviewOutput(verdict=ReviewVerdict.APPROVED, summary="approved")
    with patch(
        "agent_agent.agents.review.ReviewComposite",
        autospec=True,
    ) as MockReviewCls:
        instance = MockReviewCls.return_value
        instance.execute = AsyncMock(return_value=(mock_review_output, 0.03))

        output, cost = await executor._dispatch_composite(review_node, context, run, all_nodes)

    assert isinstance(output, ReviewOutput)
    mock_worktree_mgr.create_review_worktree.assert_called_once()
    mock_worktree_mgr.remove_worktree.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: Child DAG spawn triggered by PlanOutput with child_dag
# ---------------------------------------------------------------------------


async def test_child_dag_spawn_triggered_by_plan_output() -> None:
    """PlanOutput with child_dag triggers _spawn_child_dag (not NotImplementedError)."""
    state = await _make_state()
    run = _make_run()
    nodes = build_stub_dag(run)
    await _setup_run(state, run, nodes)

    l0_plan_id = next(n.id for n in nodes if n.level == 0)

    child_spec = _make_child_dag_spec(["A"])

    call_tracker = {"spawn_called": False}

    async def plan_with_child(node: DAGNode, ctx):  # type: ignore[no-untyped-def]
        if node.id == l0_plan_id:
            return PlanOutput(investigation_summary="needs child", child_dag=child_spec), 0.0
        if node.type == NodeType.CODING:
            return CodeOutput(summary="code", branch_name=f"agent/stub/{node.composite_id}"), 0.0
        if node.type == NodeType.REVIEW:
            return ReviewOutput(verdict=ReviewVerdict.APPROVED, summary="ok"), 0.0
        return PlanOutput(investigation_summary="done", child_dag=None), 0.0

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, plan_with_child)

    # Patch _spawn_child_dag to verify it's called
    async def tracking_spawn(*args, **kwargs):
        call_tracker["spawn_called"] = True
        # Don't actually spawn — just verify it was called
        return

    executor._spawn_child_dag = tracking_spawn  # type: ignore[method-assign]
    await executor.execute(run, nodes)

    assert call_tracker["spawn_called"], "_spawn_child_dag was not called"


# ---------------------------------------------------------------------------
# Test 5: Child DAG spawn increments nesting level correctly
# ---------------------------------------------------------------------------


async def test_child_dag_nesting_level_incremented() -> None:
    """Child DAG spawn: nesting level incremented from parent plan node level."""
    state = await _make_state()
    run = _make_run()
    await state.create_dag_run(run)

    plan_node = _make_node("plan-l1", run.id, NodeType.PLAN, level=1)
    await state.create_dag_node(plan_node)

    child_spec = _make_child_dag_spec(["A"])

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    budget.allocate([plan_node.id])
    executor = _make_executor(state, budget, _make_stub_agent())

    # Build child nodes and check their level
    child_nodes = executor._build_child_dag_nodes(
        dag_run=run,
        spec=child_spec,
        level=2,
        plan_node_id=plan_node.id,
    )

    for node in child_nodes:
        assert node.level == 2, f"Expected level 2, got {node.level} for {node.id}"


# ---------------------------------------------------------------------------
# Test 6: Child DAG spawn raises ResourceExhaustionError at level > 4
# ---------------------------------------------------------------------------


async def test_child_dag_level_exceeds_max_raises_resource_exhaustion() -> None:
    """level > 4 raises ResourceExhaustionError [P1.10]."""
    state = await _make_state()
    run = _make_run()
    await state.create_dag_run(run)

    plan_node = _make_node("plan-l4", run.id, NodeType.PLAN, level=4)
    await state.create_dag_node(plan_node)

    child_spec = _make_child_dag_spec(["A"])

    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    budget.allocate([plan_node.id])
    executor = _make_executor(state, budget, _make_stub_agent())

    with pytest.raises(ResourceExhaustionError, match="nesting depth limit"):
        await executor._spawn_child_dag(run, plan_node, child_spec, [plan_node])


# ---------------------------------------------------------------------------
# Test 7: _build_child_dag_nodes creates (Coding, Review) pairs + terminal Plan
# ---------------------------------------------------------------------------


async def test_build_child_dag_nodes_creates_pairs_and_terminal() -> None:
    """_build_child_dag_nodes creates (Coding, Review) pairs + terminal Plan."""
    state = await _make_state()
    run = _make_run()
    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, _make_stub_agent())

    spec = _make_child_dag_spec(["A", "B"])
    nodes = executor._build_child_dag_nodes(
        dag_run=run,
        spec=spec,
        level=2,
        plan_node_id="parent-plan",
    )

    # 2 composites -> 2 coding + 2 review + 1 terminal plan = 5 nodes
    assert len(nodes) == 5

    coding_nodes = [n for n in nodes if n.type == NodeType.CODING]
    review_nodes = [n for n in nodes if n.type == NodeType.REVIEW]
    plan_nodes = [n for n in nodes if n.type == NodeType.PLAN]

    assert len(coding_nodes) == 2
    assert len(review_nodes) == 2
    assert len(plan_nodes) == 1


# ---------------------------------------------------------------------------
# Test 8: _build_child_dag_nodes: sequential edges wire Review -> Coding
# ---------------------------------------------------------------------------


async def test_build_child_dag_nodes_sequential_edges() -> None:
    """Sequential edges wire Review -> Coding dependencies."""
    state = await _make_state()
    run = _make_run()
    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, _make_stub_agent())

    spec = _make_child_dag_spec(
        ["A", "B"],
        sequential_edges=[SequentialEdge(from_composite_id="A", to_composite_id="B")],
    )
    nodes = executor._build_child_dag_nodes(
        dag_run=run,
        spec=spec,
        level=2,
        plan_node_id="parent-plan",
    )

    # B's coding node should depend on A's review node
    coding_b = next(n for n in nodes if n.type == NodeType.CODING and n.composite_id == "B")
    review_a_id = f"{run.id}-l2-review-A"

    assert review_a_id in coding_b.parent_node_ids, (
        f"Expected {review_a_id} in {coding_b.parent_node_ids}"
    )


# ---------------------------------------------------------------------------
# Test 9: _build_child_dag_nodes: terminal Plan depends on all Review nodes
# ---------------------------------------------------------------------------


async def test_build_child_dag_nodes_terminal_depends_on_all_reviews() -> None:
    """Terminal Plan depends on all Review nodes."""
    state = await _make_state()
    run = _make_run()
    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    executor = _make_executor(state, budget, _make_stub_agent())

    spec = _make_child_dag_spec(["A", "B", "C"])
    nodes = executor._build_child_dag_nodes(
        dag_run=run,
        spec=spec,
        level=2,
        plan_node_id="parent-plan",
    )

    terminal_plan = next(n for n in nodes if n.type == NodeType.PLAN)
    review_ids = {n.id for n in nodes if n.type == NodeType.REVIEW}

    assert set(terminal_plan.parent_node_ids) == review_ids


# ---------------------------------------------------------------------------
# Test 10: allocate_child splits remaining budget across child nodes
# ---------------------------------------------------------------------------


def test_allocate_child_splits_remaining_budget() -> None:
    """allocate_child splits remaining budget (not total) across child nodes."""
    budget = BudgetManager(dag_run_id="run-1", total_budget_usd=10.0)
    budget.allocate(["root-1", "root-2"])

    # Simulate spending on root nodes
    budget.record_usage("root-1", 3.0)
    budget.record_usage("root-2", 2.0)

    remaining_before = budget.remaining_dag()
    assert remaining_before == 5.0  # 10.0 - 3.0 - 2.0

    budget.allocate_child(["child-1", "child-2"])

    # Each child should get remaining / 2 = 2.5
    assert budget.remaining_node("child-1") == pytest.approx(2.5)
    assert budget.remaining_node("child-2") == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# Test 11: Backward compat — executor with agent_fn still works
# ---------------------------------------------------------------------------


async def test_backward_compat_executor_with_agent_fn() -> None:
    """Executor with agent_fn still works (Phase 2/3 tests unbroken)."""
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
# Test 12: is_consolidation detection — Plan with Review parent -> True
# ---------------------------------------------------------------------------


async def test_is_consolidation_with_review_parent() -> None:
    """Plan node with Review parent -> is_consolidation=True."""
    state = await _make_state()
    run = _make_run()
    await state.create_dag_run(run)

    review_node = _make_node("n-review", run.id, NodeType.REVIEW)
    plan_node = _make_node(
        "n-plan",
        run.id,
        NodeType.PLAN,
        parent_node_ids=["n-review"],
    )
    all_nodes = [review_node, plan_node]

    settings = _make_settings()
    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    budget.allocate([n.id for n in all_nodes])
    shared = _make_shared()
    provider = ContextProvider(shared, budget, state, settings)

    executor = DAGExecutor(
        state=state,
        budget=budget,
        context_provider=provider,
        agent_fn=None,
        settings=settings,
        repo_path="/repo",
    )

    context = await provider.build_context(plan_node, all_nodes)

    mock_plan_output = PlanOutput(investigation_summary="consolidation", child_dag=None)
    with patch(
        "agent_agent.agents.plan.PlanComposite",
        autospec=True,
    ) as MockPlanCls:
        instance = MockPlanCls.return_value
        instance.execute = AsyncMock(return_value=(mock_plan_output, 0.01))

        await executor._dispatch_plan(plan_node, context, run, all_nodes)

        # Verify is_consolidation=True was passed
        instance.execute.assert_called_once()
        call_kwargs = instance.execute.call_args
        assert call_kwargs.kwargs.get("is_consolidation") is True or (
            len(call_kwargs.args) >= 4 and call_kwargs.args[3] is True
        ), "is_consolidation should be True when parent is Review"


# ---------------------------------------------------------------------------
# Test 13: is_consolidation detection — Plan with no Review parent -> False
# ---------------------------------------------------------------------------


async def test_is_consolidation_without_review_parent() -> None:
    """Plan node with no Review parent -> is_consolidation=False."""
    state = await _make_state()
    run = _make_run()
    await state.create_dag_run(run)

    plan_node = _make_node("n-plan", run.id, NodeType.PLAN, level=0)
    all_nodes = [plan_node]

    settings = _make_settings()
    budget = BudgetManager(dag_run_id=run.id, total_budget_usd=10.0)
    budget.allocate([plan_node.id])
    shared = _make_shared()
    provider = ContextProvider(shared, budget, state, settings)

    executor = DAGExecutor(
        state=state,
        budget=budget,
        context_provider=provider,
        agent_fn=None,
        settings=settings,
        repo_path="/repo",
    )

    context = await provider.build_context(plan_node, all_nodes)

    mock_plan_output = PlanOutput(investigation_summary="analysis", child_dag=None)
    with patch(
        "agent_agent.agents.plan.PlanComposite",
        autospec=True,
    ) as MockPlanCls:
        instance = MockPlanCls.return_value
        instance.execute = AsyncMock(return_value=(mock_plan_output, 0.01))

        await executor._dispatch_plan(plan_node, context, run, all_nodes)

        # Verify is_consolidation=False was passed
        instance.execute.assert_called_once()
        call_kwargs = instance.execute.call_args
        assert call_kwargs.kwargs.get("is_consolidation") is False or (
            len(call_kwargs.args) >= 4 and call_kwargs.args[3] is False
        ), "is_consolidation should be False when no Review parent"
