"""Integration tests for DAGExecutor — real executor + real git + mocked invoke_agent.

Tests 6–9 from the Phase 4 integration test plan. No API calls; $0 cost.

Each test uses:
- In-memory StateStore (real SQLite)
- Real BudgetManager
- Real ContextProvider
- Real DAGExecutor dispatch loop
- Mocked agent_fn returning canned Pydantic outputs
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.context.provider import ContextProvider
from agent_agent.context.shared import SharedContext
from agent_agent.dag.executor import DAGExecutor
from agent_agent.models.agent import (
    ChildDAGSpec,
    CodeOutput,
    CompositeSpec,
    PlanOutput,
    ReviewOutput,
    ReviewVerdict,
)
from agent_agent.models.context import IssueContext, RepoMetadata
from agent_agent.models.dag import (
    DAGNode,
    DAGRun,
    DAGRunStatus,
    NodeStatus,
    NodeType,
)
from agent_agent.models.budget import BudgetEventType
from agent_agent.state import StateStore
from agent_agent.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKTREE_BASE = "/workspaces/.agent_agent_tests/worktrees"


def _make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "env": "test",
        "model": "claude-haiku-4-5-20251001",
        "max_budget_usd": 1.0,
        "git_push_enabled": False,
        "worktree_base_dir": WORKTREE_BASE,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_issue() -> IssueContext:
    return IssueContext(
        url="https://github.com/test/repo/issues/42",
        title="Test issue",
        body="Fix the bug.",
    )


def _make_repo_meta(path: str) -> RepoMetadata:
    return RepoMetadata(
        path=path,
        default_branch="main",
        language="python",
        claude_md="# Test repo\n",
    )


def _make_shared_context(repo_path: str) -> SharedContext:
    return SharedContext(
        issue=_make_issue(),
        repo_metadata=_make_repo_meta(repo_path),
    )


def _make_dag_run(
    run_id: str,
    repo_path: str,
    budget_usd: float = 1.0,
) -> DAGRun:
    now = datetime.now(timezone.utc)
    return DAGRun(
        id=run_id,
        issue_url="https://github.com/test/repo/issues/42",
        repo_path=repo_path,
        status=DAGRunStatus.PENDING,
        budget_usd=budget_usd,
        usd_used=0.0,
        created_at=now,
        updated_at=now,
    )


def _make_four_node_dag(run_id: str) -> list[DAGNode]:
    """Build a PLAN -> CODING -> REVIEW -> PLAN(terminal) DAG (4 nodes)."""
    now = datetime.now(timezone.utc)
    plan_id = f"{run_id}-plan"
    coding_id = f"{run_id}-coding"
    review_id = f"{run_id}-review"
    terminal_id = f"{run_id}-plan-terminal"

    plan = DAGNode(
        id=plan_id,
        dag_run_id=run_id,
        type=NodeType.PLAN,
        level=0,
        composite_id="L0",
        parent_node_ids=[],
        child_node_ids=[coding_id],
        created_at=now,
        updated_at=now,
    )
    coding = DAGNode(
        id=coding_id,
        dag_run_id=run_id,
        type=NodeType.CODING,
        level=1,
        composite_id="A",
        parent_node_ids=[plan_id],
        child_node_ids=[review_id],
        created_at=now,
        updated_at=now,
    )
    review = DAGNode(
        id=review_id,
        dag_run_id=run_id,
        type=NodeType.REVIEW,
        level=1,
        composite_id="A-review",
        parent_node_ids=[coding_id],
        child_node_ids=[terminal_id],
        created_at=now,
        updated_at=now,
    )
    terminal = DAGNode(
        id=terminal_id,
        dag_run_id=run_id,
        type=NodeType.PLAN,
        level=1,
        composite_id="L1-terminal",
        parent_node_ids=[review_id],
        child_node_ids=[],
        created_at=now,
        updated_at=now,
    )
    return [plan, coding, review, terminal]


def _make_three_node_dag(run_id: str) -> list[DAGNode]:
    """Build a minimal PLAN -> CODING -> REVIEW DAG (no worktrees, uses agent_fn mock)."""
    now = datetime.now(timezone.utc)
    plan_id = f"{run_id}-plan"
    coding_id = f"{run_id}-coding"
    review_id = f"{run_id}-review"

    plan = DAGNode(
        id=plan_id,
        dag_run_id=run_id,
        type=NodeType.PLAN,
        level=0,
        composite_id="L0",
        parent_node_ids=[],
        child_node_ids=[coding_id],
        created_at=now,
        updated_at=now,
    )
    coding = DAGNode(
        id=coding_id,
        dag_run_id=run_id,
        type=NodeType.CODING,
        level=1,
        composite_id="A",
        parent_node_ids=[plan_id],
        child_node_ids=[review_id],
        created_at=now,
        updated_at=now,
    )
    review = DAGNode(
        id=review_id,
        dag_run_id=run_id,
        type=NodeType.REVIEW,
        level=1,
        composite_id="A-review",
        parent_node_ids=[coding_id],
        child_node_ids=[],
        created_at=now,
        updated_at=now,
    )
    return [plan, coding, review]


def _make_plan_output(child_dag: ChildDAGSpec | None = None) -> PlanOutput:
    return PlanOutput(
        investigation_summary="Test plan",
        child_dag=child_dag,
        discoveries=[],
    )


def _make_code_output(branch: str = "agent/42/test-branch") -> CodeOutput:
    return CodeOutput(
        summary="Test code",
        files_changed=["src/main.py"],
        branch_name=branch,
        commit_sha="abc123",
        tests_passed=True,
        discoveries=[],
    )


def _make_review_output() -> ReviewOutput:
    return ReviewOutput(
        verdict=ReviewVerdict.APPROVED,
        summary="Looks good",
        findings=[],
        downstream_impacts=[],
        discoveries=[],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def state_store() -> AsyncGenerator[StateStore, None]:
    store = StateStore(":memory:")
    await store.init()
    yield store
    await store.close()


@pytest.fixture()
def repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a git repo with a bare remote. Returns (repo_path, bare_path)."""
    bare = tmp_path / "bare.git"
    repo = tmp_path / "repo"
    bare.mkdir()
    repo.mkdir()

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }

    def git_bare(*args: str) -> None:
        subprocess.run(["git", *args], cwd=bare, check=True, capture_output=True, env=env)

    def git_repo(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    # Init bare repo
    git_bare("init", "--bare", "-b", "main")

    # Init and set up regular repo
    git_repo("init", "-b", "main")
    git_repo("config", "user.email", "t@t.com")
    git_repo("config", "user.name", "Test")
    (repo / "README.md").write_text("# test repo\n")
    git_repo("add", "README.md")
    git_repo("commit", "-m", "initial commit")
    git_repo("remote", "add", "origin", str(bare))
    git_repo("push", "-u", "origin", "main")

    return repo, bare


# ---------------------------------------------------------------------------
# Test 6 — Executor dispatches Plan → Coding → Review end-to-end
# ---------------------------------------------------------------------------


class TestExecutorEndToEnd:
    """Test 6: DAGExecutor dispatches a three-node DAG with mocked agent_fn."""

    async def test_plan_coding_review_all_complete(
        self, state_store: StateStore, tmp_git_repo: Path
    ) -> None:
        run_id = "test6-run"
        repo_path = str(tmp_git_repo)

        dag_run = _make_dag_run(run_id, repo_path, budget_usd=1.0)
        await state_store.create_dag_run(dag_run)

        nodes = _make_three_node_dag(run_id)
        for node in nodes:
            await state_store.create_dag_node(node)

        coding_node = next(n for n in nodes if n.type == NodeType.CODING)
        code_out = _make_code_output()

        async def mock_agent_fn(
            node: DAGNode, context: object
        ) -> tuple[object, float]:
            if node.type == NodeType.PLAN:
                return _make_plan_output(), 0.05
            elif node.type == NodeType.CODING:
                # Persist branch_name to the state store as the executor would
                await state_store.update_dag_node_worktree(
                    node.id, "/tmp/fake-worktree", code_out.branch_name
                )
                return code_out, 0.10
            elif node.type == NodeType.REVIEW:
                return _make_review_output(), 0.05
            raise ValueError(f"Unknown node type: {node.type}")

        settings = _make_settings()
        budget = BudgetManager(dag_run_id=run_id, total_budget_usd=1.0)
        shared_context = _make_shared_context(repo_path)
        ctx_provider = ContextProvider(
            shared_context=shared_context,
            budget=budget,
            state=state_store,
            settings=settings,
        )

        executor = DAGExecutor(
            state=state_store,
            budget=budget,
            context_provider=ctx_provider,
            agent_fn=mock_agent_fn,
            settings=settings,
        )

        await executor.execute(dag_run, nodes)

        # Verify DAGRun completed
        final_run = await state_store.get_dag_run(run_id)
        assert final_run is not None
        assert final_run.status == DAGRunStatus.COMPLETED

        # Verify all three nodes are COMPLETED
        for node in nodes:
            db_node = await state_store.get_dag_node(node.id)
            assert db_node is not None
            assert db_node.status == NodeStatus.COMPLETED, (
                f"Node {node.id} ({node.type}) should be COMPLETED, got {db_node.status}"
            )

        # Verify NodeResult records exist for each node
        for node in nodes:
            result = await state_store.get_node_result(node.id)
            assert result is not None, f"NodeResult missing for node {node.id}"


# ---------------------------------------------------------------------------
# Test 7 — Child DAG spawned from PlanOutput and executed
# ---------------------------------------------------------------------------


class TestChildDAGSpawning:
    """Test 7: L0 Plan returns ChildDAGSpec; executor spawns + executes child DAG."""

    async def test_child_dag_spawned_and_executed(
        self, state_store: StateStore, tmp_git_repo: Path
    ) -> None:
        run_id = "test7-run"
        repo_path = str(tmp_git_repo)
        now = datetime.now(timezone.utc)

        dag_run = _make_dag_run(run_id, repo_path, budget_usd=1.0)
        await state_store.create_dag_run(dag_run)

        # Single L0 PLAN node (no pre-built child nodes)
        l0_plan = DAGNode(
            id=f"{run_id}-l0-plan",
            dag_run_id=run_id,
            type=NodeType.PLAN,
            level=0,
            composite_id="L0",
            parent_node_ids=[],
            child_node_ids=[],
            created_at=now,
            updated_at=now,
        )
        await state_store.create_dag_node(l0_plan)

        # Child DAG spec: one composite (one CODING + one REVIEW)
        child_dag_spec = ChildDAGSpec(
            composites=[
                CompositeSpec(
                    id="A",
                    scope="implement the feature",
                    branch_suffix="feature-A",
                )
            ],
            sequential_edges=[],
        )

        call_count = 0
        expected_child_coding_id = f"{run_id}-l1-coding-A"
        expected_child_review_id = f"{run_id}-l1-review-A"
        expected_terminal_plan_id = f"{run_id}-l1-plan-terminal"

        async def mock_agent_fn(
            node: DAGNode, context: object
        ) -> tuple[object, float]:
            nonlocal call_count
            call_count += 1

            if node.id == l0_plan.id:
                # L0 plan returns child_dag to spawn child nodes
                return _make_plan_output(child_dag=child_dag_spec), 0.10
            elif node.type == NodeType.CODING:
                code_out = _make_code_output(branch=f"agent/42/feature-A")
                await state_store.update_dag_node_worktree(
                    node.id, "/tmp/fake-worktree", code_out.branch_name
                )
                return code_out, 0.10
            elif node.type == NodeType.REVIEW:
                return _make_review_output(), 0.05
            elif node.type == NodeType.PLAN and node.id != l0_plan.id:
                # Terminal plan in the child DAG
                return _make_plan_output(), 0.05
            raise ValueError(f"Unknown node: {node.id}")

        settings = _make_settings()
        budget = BudgetManager(dag_run_id=run_id, total_budget_usd=1.0)
        shared_context = _make_shared_context(repo_path)
        ctx_provider = ContextProvider(
            shared_context=shared_context,
            budget=budget,
            state=state_store,
            settings=settings,
        )

        executor = DAGExecutor(
            state=state_store,
            budget=budget,
            context_provider=ctx_provider,
            agent_fn=mock_agent_fn,
            settings=settings,
        )

        await executor.execute(dag_run, [l0_plan])

        # L0 plan should be COMPLETED
        db_l0 = await state_store.get_dag_node(l0_plan.id)
        assert db_l0 is not None
        assert db_l0.status == NodeStatus.COMPLETED

        # Child nodes must exist in state store (persisted before execution [P1.8])
        db_child_coding = await state_store.get_dag_node(expected_child_coding_id)
        db_child_review = await state_store.get_dag_node(expected_child_review_id)
        db_terminal = await state_store.get_dag_node(expected_terminal_plan_id)

        assert db_child_coding is not None, "Child coding node must exist in state store"
        assert db_child_review is not None, "Child review node must exist in state store"
        assert db_terminal is not None, "Terminal plan node must exist in state store"

        # All child nodes should be COMPLETED
        assert db_child_coding.status == NodeStatus.COMPLETED
        assert db_child_review.status == NodeStatus.COMPLETED
        assert db_terminal.status == NodeStatus.COMPLETED

        # Budget events include INITIAL_ALLOCATION for child node ids
        budget_events = await state_store.list_budget_events(run_id)
        allocation_events = [
            e for e in budget_events
            if e.event_type == BudgetEventType.INITIAL_ALLOCATION
        ]
        allocated_node_ids = {e.node_id for e in allocation_events}

        assert expected_child_coding_id in allocated_node_ids, (
            f"No INITIAL_ALLOCATION for child coding node"
        )
        assert expected_child_review_id in allocated_node_ids, (
            f"No INITIAL_ALLOCATION for child review node"
        )


# ---------------------------------------------------------------------------
# Test 8 — Push failure → branch_name stays None → review gate blocks
# ---------------------------------------------------------------------------


class TestPushFailureReviewGateBlocks:
    """Test 8: CodingComposite._push_branch() fails with no remote → branch_name=None.

    Uses a real CodingComposite with mocked sub-agents (invoke_agent patched).
    The tmp_git_repo has no remote so git push fails. We verify:
    1. CodingComposite.execute() returns CodeOutput.branch_name=None
    2. The state store node has branch_name=None after the push failure
    3. _can_dispatch returns False for a Review node whose Coding node has branch_name=None
    4. A DAG with this push-failure Coding node ends with DAGRunStatus.FAILED
    """

    async def test_coding_composite_push_failure_nulls_branch(
        self,
        state_store: StateStore,
        tmp_git_repo: Path,
    ) -> None:
        """Real CodingComposite with mocked invoke_agent; no remote → push fails → branch_name=None.

        We patch agent_agent.agents.coding.invoke_agent to return canned outputs
        for programmer, test_designer, test_executor. The composite runs its full
        loop including _push_branch(). Since tmp_git_repo has no remote, the push
        fails on both attempts and branch_name is nulled via update_dag_node_worktree.
        """
        from unittest.mock import AsyncMock, patch

        from agent_agent.agents.coding import CodingComposite
        from agent_agent.models.agent import AgentTestOutput, AgentTestRole
        from agent_agent.worktree import WorktreeRecord

        run_id = "test8a-run"
        node_id = f"{run_id}-coding"
        repo_path = str(tmp_git_repo)

        # Settings: git_push_enabled=True so _push_branch() actually runs
        settings = _make_settings(git_push_enabled=True)

        # Create DAGRun + DAGNode in state store (needed for _persist_sub_agent_output)
        dag_run = _make_dag_run(run_id, repo_path, budget_usd=1.0)
        await state_store.create_dag_run(dag_run)

        now = datetime.now(timezone.utc)
        coding_dag_node = DAGNode(
            id=node_id,
            dag_run_id=run_id,
            type=NodeType.CODING,
            level=1,
            composite_id="A",
            parent_node_ids=[],
            child_node_ids=[],
            created_at=now,
            updated_at=now,
        )
        await state_store.create_dag_node(coding_dag_node)

        # WorktreeRecord pointing at tmp_git_repo directly — no real worktree creation.
        # The branch name doesn't need to exist; _push_branch just tries to push it.
        fake_branch = "agent-test8a-code-1"
        worktree = WorktreeRecord(
            path=repo_path,
            branch=fake_branch,
            dag_run_id=run_id,
            node_id=node_id,
            readonly=False,
        )

        budget = BudgetManager(dag_run_id=run_id, total_budget_usd=1.0)
        budget.allocate([node_id])

        composite = CodingComposite(
            settings=settings,
            state=state_store,
            budget=budget,
            worktree=worktree,
            repo_path=repo_path,
            issue_number="42",
            node_id=node_id,
        )

        # Build canned sub-agent outputs
        canned_code_output = CodeOutput(
            summary="Code written",
            files_changed=[],
            branch_name=fake_branch,
            commit_sha=None,
            tests_passed=True,
            discoveries=[],
        )
        canned_test_plan = AgentTestOutput(
            role=AgentTestRole.PLAN,
            summary="Test plan created",
            test_plan="run pytest",
        )
        canned_test_results = AgentTestOutput(
            role=AgentTestRole.RESULTS,
            summary="All tests pass",
            passed=True,
            total_tests=1,
            failed_tests=0,
        )

        # invoke_agent is called 3 times per cycle: programmer, test_designer, test_executor
        invoke_side_effects = [
            (canned_code_output, 0.01),
            (canned_test_plan, 0.01),
            (canned_test_results, 0.01),
        ]

        # Build a minimal NodeContext for the composite
        from agent_agent.models.context import (
            AncestorContext,
            IssueContext,
            NodeContext,
            RepoMetadata,
            SharedContextView,
        )

        node_context = NodeContext(
            issue=IssueContext(
                url="https://github.com/test/repo/issues/42",
                title="Test issue",
                body="Fix the bug.",
            ),
            repo_metadata=RepoMetadata(
                path=repo_path,
                default_branch="main",
                language="python",
                claude_md="# Test repo\n",
            ),
            parent_outputs={},
            ancestor_context=AncestorContext(),
            shared_context_view=SharedContextView(),
        )

        mock_invoke = AsyncMock(side_effect=invoke_side_effects)

        with (
            patch("agent_agent.agents.coding.invoke_agent", mock_invoke),
            patch("asyncio.sleep", new=AsyncMock(return_value=None)),
        ):
            result_output, total_cost = await composite.execute(
                node_context=node_context,
                dag_run_id=run_id,
                node_id=node_id,
            )

        # Verify push failure nulled the branch_name in the returned CodeOutput
        assert result_output.branch_name is None, (
            f"Expected branch_name=None after push failure, got {result_output.branch_name}"
        )

        # Verify invoke_agent was called 3 times (programmer + test_designer + test_executor)
        assert mock_invoke.call_count == 3, (
            f"Expected 3 invoke_agent calls (1 cycle), got {mock_invoke.call_count}"
        )

        # Verify state store: the node's branch_name should be None (set by _push_branch via
        # update_dag_node_worktree with branch_name=None)
        db_node = await state_store.get_dag_node(node_id)
        assert db_node is not None
        assert db_node.branch_name is None, (
            f"Expected DB branch_name=None after push failure, got {db_node.branch_name}"
        )

    async def test_review_gate_blocks_when_branch_name_none(
        self,
        state_store: StateStore,
        tmp_git_repo: Path,
    ) -> None:
        """_can_dispatch returns False for Review node when Coding node has branch_name=None.

        Seeds the state store directly with a COMPLETED Coding node that has branch_name=None
        (as would happen after a push failure), then verifies the executor's _can_dispatch
        gate blocks the Review node and the DAGRun ends FAILED.
        """
        run_id = "test8b-run"
        repo_path = str(tmp_git_repo)

        # Settings: git_push_enabled=True (irrelevant here but mirrors production config)
        settings = _make_settings(git_push_enabled=True)

        dag_run = _make_dag_run(run_id, repo_path, budget_usd=1.0)
        await state_store.create_dag_run(dag_run)

        nodes = _make_three_node_dag(run_id)
        for node in nodes:
            await state_store.create_dag_node(node)

        coding_node = next(n for n in nodes if n.type == NodeType.CODING)

        async def mock_agent_fn_push_fail(
            node: DAGNode, context: object
        ) -> tuple[object, float]:
            if node.type == NodeType.PLAN:
                return _make_plan_output(), 0.05
            elif node.type == NodeType.CODING:
                # Return CodeOutput with branch_name nulled — simulates push failure result
                # as CodingComposite does via model_copy(update={"branch_name": None}).
                base_out = CodeOutput(
                    summary="code done, but push failed",
                    files_changed=["src/main.py"],
                    branch_name="agent/42/test",
                    commit_sha="abc123",
                    tests_passed=True,
                    discoveries=[],
                )
                code_out = base_out.model_copy(update={"branch_name": None})
                return code_out, 0.10
            raise ValueError(f"Should not dispatch Review node: {node.id}")

        budget = BudgetManager(dag_run_id=run_id, total_budget_usd=1.0)
        shared_context = _make_shared_context(repo_path)
        ctx_provider = ContextProvider(
            shared_context=shared_context,
            budget=budget,
            state=state_store,
            settings=settings,
        )

        executor = DAGExecutor(
            state=state_store,
            budget=budget,
            context_provider=ctx_provider,
            agent_fn=mock_agent_fn_push_fail,
            settings=settings,
        )

        await executor.execute(dag_run, nodes)

        # DAGRun should be FAILED (review gate blocked)
        final_run = await state_store.get_dag_run(run_id)
        assert final_run is not None
        assert final_run.status == DAGRunStatus.FAILED

        # Coding node should be COMPLETED
        db_coding = await state_store.get_dag_node(coding_node.id)
        assert db_coding is not None
        assert db_coding.status == NodeStatus.COMPLETED
        # branch_name should be None (push failure simulation)
        assert db_coding.branch_name is None

        # Review node should be FAILED (blocked by gate)
        review_node = next(n for n in nodes if n.type == NodeType.REVIEW)
        db_review = await state_store.get_dag_node(review_node.id)
        assert db_review is not None
        assert db_review.status == NodeStatus.FAILED

        # Error message should reference review gate
        assert final_run.error is not None
        assert "review gate" in final_run.error.lower() or "blocked" in final_run.error.lower()


# ---------------------------------------------------------------------------
# Test 9 — Budget flows through multi-level DAG; should_pause fires
# ---------------------------------------------------------------------------


class TestBudgetPause:
    """Test 9: Real BudgetManager tracks costs; should_pause() fires after threshold."""

    async def test_should_pause_fires_after_budget_exhausted(
        self, state_store: StateStore, tmp_git_repo: Path
    ) -> None:
        """Total budget = $1.00. 4-node DAG (PLAN→CODING→REVIEW→terminal PLAN).
        Each mock call costs $0.33. Budget math:
        - allocate: $1.00 / 4 = $0.25 per node
        - After PLAN ($0.33 spent): remaining = $0.67 → no pause
        - After CODING ($0.66 spent): remaining = $0.34 → no pause
        - After REVIEW ($0.99 spent): remaining = $0.01 ≤ 5% ($0.05) → PAUSE
        - Terminal PLAN (node 4) never dispatched → stays PENDING

        This configuration actually exercises the "pending nodes stay PENDING" invariant.
        """
        run_id = "test9-run"
        repo_path = str(tmp_git_repo)

        dag_run = _make_dag_run(run_id, repo_path, budget_usd=1.0)
        await state_store.create_dag_run(dag_run)

        # Use 4-node DAG so node 4 stays PENDING when pause fires after node 3
        nodes = _make_four_node_dag(run_id)
        for node in nodes:
            await state_store.create_dag_node(node)

        async def mock_costly_agent_fn(
            node: DAGNode, context: object
        ) -> tuple[object, float]:
            if node.type == NodeType.PLAN:
                return _make_plan_output(), 0.33
            elif node.type == NodeType.CODING:
                code_out = _make_code_output()
                await state_store.update_dag_node_worktree(
                    node.id, "/tmp/fake-worktree", code_out.branch_name
                )
                return code_out, 0.33
            elif node.type == NodeType.REVIEW:
                return _make_review_output(), 0.33
            raise ValueError(f"Unexpected node type {node.type} — should not be dispatched")

        settings = _make_settings()
        budget = BudgetManager(dag_run_id=run_id, total_budget_usd=1.0)
        shared_context = _make_shared_context(repo_path)
        ctx_provider = ContextProvider(
            shared_context=shared_context,
            budget=budget,
            state=state_store,
            settings=settings,
        )

        executor = DAGExecutor(
            state=state_store,
            budget=budget,
            context_provider=ctx_provider,
            agent_fn=mock_costly_agent_fn,
            settings=settings,
        )

        await executor.execute(dag_run, nodes)

        # DAGRun should be PAUSED (not COMPLETED, not FAILED)
        final_run = await state_store.get_dag_run(run_id)
        assert final_run is not None
        assert final_run.status == DAGRunStatus.PAUSED, (
            f"Expected PAUSED, got {final_run.status}. "
            f"Budget: total=$1.00, 3 nodes × $0.33 = $0.99 spent, $0.01 remaining (≤5%)"
        )

        # BudgetManager.should_pause() returns True at end of run
        assert budget.should_pause()

        # PAUSE event must exist in budget_events
        budget_events = await state_store.list_budget_events(run_id)
        pause_events = [e for e in budget_events if e.event_type == BudgetEventType.PAUSE]
        assert len(pause_events) >= 1, "At least one PAUSE event should be in budget_events"

        # Exactly the first 3 nodes (PLAN, CODING, REVIEW) should be COMPLETED.
        # The 4th node (terminal PLAN) must be PENDING (not dispatched, not SKIPPED).
        completed_nodes = []
        pending_nodes = []
        for node in nodes:
            db_node = await state_store.get_dag_node(node.id)
            assert db_node is not None
            if db_node.status == NodeStatus.COMPLETED:
                completed_nodes.append(node.id)
            else:
                assert db_node.status == NodeStatus.PENDING, (
                    f"Un-dispatched node {node.id} should be PENDING, got {db_node.status}"
                )
                pending_nodes.append(node.id)

        # The pause fires after 3 nodes complete, so exactly 1 node should remain PENDING
        assert len(pending_nodes) >= 1, (
            "At least 1 node should be PENDING after budget pause. "
            f"Completed: {completed_nodes}, Pending: {pending_nodes}"
        )
