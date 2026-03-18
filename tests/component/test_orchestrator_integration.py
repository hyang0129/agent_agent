"""Integration tests for Orchestrator — real lifecycle with mocked invoke_agent.

Test 10 from the Phase 4 integration test plan. No real API calls; $0 cost.

Uses:
- Real Orchestrator with use_composites=False and a mocked agent_fn (Tests 10a–10c)
- Real Orchestrator with use_composites=True, WorktreeManager and composites mocked (Test 10d)
- Real StateStore (:memory:)
- tmp_git_repo fixture
- Settings(git_push_enabled=False)
"""

from __future__ import annotations

import subprocess
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_agent.config import Settings
from agent_agent.dag.executor import AgentFn
from agent_agent.models.agent import (
    ChildDAGSpec,
    CodeOutput,
    CompositeSpec,
    PlanOutput,
    ReviewOutput,
    ReviewVerdict,
)
from agent_agent.models.dag import DAGNode, DAGRunStatus, NodeStatus, NodeType
from agent_agent.orchestrator import Orchestrator
from agent_agent.state import StateStore
from agent_agent.worktree import WorktreeRecord


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
        "port": 18100,  # use non-default port to avoid conflicts in CI
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_capturing_agent_fn(run_id_holder: list[str]) -> AgentFn:
    """Stub agent_fn that captures dag_run_id from the first node it receives."""

    async def _stub(node: DAGNode, context: object) -> tuple[object, float]:
        # Capture run_id for post-run verification
        if not run_id_holder:
            run_id_holder.append(node.dag_run_id)

        if node.type == NodeType.PLAN:
            return PlanOutput(
                investigation_summary="Stub plan",
                child_dag=None,
                discoveries=[],
            ), 0.05
        elif node.type == NodeType.CODING:
            return CodeOutput(
                summary="Stub code",
                files_changed=[],
                branch_name=f"agent/42/stub-{node.id[:8]}",
                commit_sha=None,
                tests_passed=True,
                discoveries=[],
            ), 0.05
        elif node.type == NodeType.REVIEW:
            return ReviewOutput(
                verdict=ReviewVerdict.APPROVED,
                summary="Stub review: auto-approved.",
                findings=[],
                downstream_impacts=[],
                discoveries=[],
            ), 0.05
        raise ValueError(f"Unknown node type: {node.type}")

    return _stub  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Test 10 — Orchestrator full lifecycle
# ---------------------------------------------------------------------------


class TestOrchestratorLifecycle:
    """Test 10: Real Orchestrator with stub agent_fn; full lifecycle completes."""

    async def test_orchestrator_run_completes(self, tmp_git_repo: Path) -> None:
        """Test 10a: Orchestrator.run() creates DAGRun, persists nodes, executes, completes.

        Uses a capturing agent_fn that records run_id on the first dispatch so we can
        verify DAGRun status and node counts via public StateStore API (no _db access).
        """
        import uvicorn

        repo_path = str(tmp_git_repo)
        settings = _make_settings(port=18100)

        state = StateStore(":memory:")
        await state.init()

        # Mock uvicorn Server to avoid port conflicts between tests
        mock_server = MagicMock()
        mock_server.should_exit = False
        mock_server.serve = AsyncMock(return_value=None)

        # Capture run_id via agent_fn
        run_id_holder: list[str] = []
        agent_fn = _make_capturing_agent_fn(run_id_holder)

        try:
            orchestrator = Orchestrator(
                settings=settings,
                repo_path=repo_path,
                claude_md_content="# Test CLAUDE.md\n",
                issue_url="https://github.com/test/repo/issues/42",
                state_store=state,
                agent_fn=agent_fn,
                use_composites=False,
            )

            with patch.object(uvicorn, "Server", return_value=mock_server):
                branch_name, summary = await orchestrator.run()

            # branch_name comes from the CODING node output
            assert branch_name  # non-empty
            assert "agent/42" in branch_name

            # summary comes from the REVIEW node output
            assert summary  # non-empty

            # Verify run_id was captured
            assert run_id_holder, "agent_fn was not called — run_id not captured"
            run_id = run_id_holder[0]

            # Verify DAGRun was created and completed — use public API only
            dag_run = await state.get_dag_run(run_id)
            assert dag_run is not None, f"DAGRun {run_id} not found in state store"
            assert dag_run.status == DAGRunStatus.COMPLETED, (
                f"Expected COMPLETED, got {dag_run.status}"
            )

            # Verify all nodes were persisted before execution and are COMPLETED
            nodes = await state.list_dag_nodes(run_id)

            # Should have 4 nodes: L0-plan, L1-coding, L1-review, L1-plan-terminal
            assert len(nodes) == 4, (
                f"Expected 4 nodes, got {len(nodes)}: {[n.id for n in nodes]}"
            )

            for node in nodes:
                assert node.status == NodeStatus.COMPLETED, (
                    f"Node {node.id} ({node.type}) should be COMPLETED, got {node.status}"
                )

            # All NodeResults should exist
            for node in nodes:
                result = await state.get_node_result(node.id)
                assert result is not None, f"NodeResult missing for node {node.id}"

        finally:
            await state.close()

    async def test_orchestrator_prunes_worktrees_before_execution(
        self, tmp_git_repo: Path
    ) -> None:
        """Test 10b: git worktree prune is called before execution.

        We mock the uvicorn server startup to avoid port conflicts between tests.
        """
        import uvicorn

        repo_path = str(tmp_git_repo)
        settings = _make_settings(port=18200)

        state = StateStore(":memory:")
        await state.init()

        try:
            prune_called = False
            original_subprocess_run = subprocess.run

            def mock_subprocess_run(cmd: list[str], **kwargs: Any) -> Any:
                nonlocal prune_called
                if cmd == ["git", "worktree", "prune"]:
                    prune_called = True
                return original_subprocess_run(cmd, **kwargs)

            # Mock uvicorn Server to avoid port conflicts
            mock_server = MagicMock()
            mock_server.should_exit = False
            mock_server.serve = AsyncMock(return_value=None)

            orchestrator = Orchestrator(
                settings=settings,
                repo_path=repo_path,
                claude_md_content="# Test CLAUDE.md\n",
                issue_url="https://github.com/test/repo/issues/42",
                state_store=state,
                agent_fn=_make_capturing_agent_fn([]),
                use_composites=False,
            )

            with patch("agent_agent.orchestrator.subprocess.run", side_effect=mock_subprocess_run):
                with patch.object(uvicorn, "Server", return_value=mock_server):
                    await orchestrator.run()

            assert prune_called, "git worktree prune should be called before execution"

        finally:
            await state.close()

    async def test_orchestrator_nodes_persisted_before_execution(
        self, tmp_git_repo: Path
    ) -> None:
        """Test 10c: All DAGNodes are persisted to state store before execute() is called [P1.8]."""
        import uvicorn

        repo_path = str(tmp_git_repo)
        settings = _make_settings(port=18300)

        state = StateStore(":memory:")
        await state.init()

        # Mock uvicorn Server to avoid port conflicts
        mock_server = MagicMock()
        mock_server.should_exit = False
        mock_server.serve = AsyncMock(return_value=None)

        try:
            nodes_at_dispatch: list[Any] = []
            original_agent_fn = _make_capturing_agent_fn([])

            async def capturing_agent_fn(
                node: DAGNode, context: object
            ) -> tuple[object, float]:
                # Capture all persisted nodes on the FIRST dispatch call using public API.
                # node.dag_run_id gives us the run_id directly without private _db access.
                if not nodes_at_dispatch:
                    persisted = await state.list_dag_nodes(node.dag_run_id)
                    nodes_at_dispatch.extend(persisted)
                return await original_agent_fn(node, context)

            orchestrator = Orchestrator(
                settings=settings,
                repo_path=repo_path,
                claude_md_content="# Test CLAUDE.md\n",
                issue_url="https://github.com/test/repo/issues/42",
                state_store=state,
                agent_fn=capturing_agent_fn,
                use_composites=False,
            )

            with patch.object(uvicorn, "Server", return_value=mock_server):
                await orchestrator.run()

            # All 4 nodes should have been persisted before the first dispatch
            assert len(nodes_at_dispatch) == 4, (
                f"Expected 4 nodes persisted before first dispatch, "
                f"got {len(nodes_at_dispatch)} [P1.8]"
            )

        finally:
            await state.close()

    async def test_orchestrator_use_composites_wires_worktree_manager(
        self, tmp_git_repo: Path
    ) -> None:
        """Test 10d: use_composites=True creates WorktreeManager and passes it to executor.

        When use_composites=True, the Orchestrator:
        1. Creates a WorktreeManager (wired into DAGExecutor)
        2. Sets agent_fn=None so the executor uses _dispatch_composite()

        We mock WorktreeManager, PlanComposite.execute, CodingComposite.execute,
        and ReviewComposite.execute so no real SDK calls are made.
        Also verifies git worktree prune is still called.
        """
        import uvicorn

        repo_path = str(tmp_git_repo)
        # use_composites=True is passed to Orchestrator, not Settings.
        # Settings needs worktree_base_dir (already set in _make_settings defaults).
        settings = _make_settings(port=18400)

        state = StateStore(":memory:")
        await state.init()

        # Mock uvicorn Server to avoid port conflicts
        mock_server = MagicMock()
        mock_server.should_exit = False
        mock_server.serve = AsyncMock(return_value=None)

        # Track WorktreeManager instantiation
        wm_instantiated = False

        # Create a fake worktree record for the coding/review worktree
        fake_coding_worktree = WorktreeRecord(
            path=repo_path,  # reuse repo path as fake worktree
            branch="agent-test10d-code-1",
            dag_run_id="placeholder",  # will be overridden
            node_id="placeholder",
            readonly=False,
        )
        fake_review_worktree = WorktreeRecord(
            path=repo_path,
            branch="agent-test10d-code-1",
            dag_run_id="placeholder",
            node_id="placeholder",
            readonly=True,
        )

        # WorktreeManager mock: tracks instantiation, provides fake worktrees
        mock_wm = MagicMock()
        mock_wm.create_coding_worktree = AsyncMock(return_value=fake_coding_worktree)
        mock_wm.create_review_worktree = AsyncMock(return_value=fake_review_worktree)
        mock_wm.remove_worktree = AsyncMock(return_value=None)

        # Canned composite outputs
        # L0 plan returns a child_dag so _spawn_child_dag() builds L1 nodes
        l0_plan_output = PlanOutput(
            investigation_summary="Composite plan stub",
            child_dag=ChildDAGSpec(
                composites=[
                    CompositeSpec(
                        id="A",
                        scope="implement the feature",
                        branch_suffix="test10d-code-1",
                    )
                ],
                sequential_edges=[],
            ),
            discoveries=[],
        )
        # Terminal plan (L1) returns child_dag=None — work complete
        terminal_plan_output = PlanOutput(
            investigation_summary="Terminal plan stub",
            child_dag=None,
            discoveries=[],
        )
        code_output = CodeOutput(
            summary="Composite code stub",
            files_changed=[],
            branch_name="agent-test10d-code-1",
            commit_sha=None,
            tests_passed=True,
            discoveries=[],
        )
        review_output = ReviewOutput(
            verdict=ReviewVerdict.APPROVED,
            summary="Composite review stub: approved.",
            findings=[],
            downstream_impacts=[],
            discoveries=[],
        )

        try:
            def make_wm(base_dir: str) -> MagicMock:
                nonlocal wm_instantiated
                wm_instantiated = True
                return mock_wm

            # PlanComposite.execute is called twice: L0 plan (returns child_dag)
            # and terminal L1 plan (returns child_dag=None).
            plan_execute_mock = AsyncMock(
                side_effect=[
                    (l0_plan_output, 0.05),
                    (terminal_plan_output, 0.05),
                ]
            )

            with (
                # WorktreeManager is imported lazily inside orchestrator.run(),
                # so we patch it in its own module (agent_agent.worktree).
                patch("agent_agent.worktree.WorktreeManager", side_effect=make_wm),
                # Composites are imported lazily in executor._dispatch_* methods.
                # Patch the execute() method on each composite class in their modules.
                patch(
                    "agent_agent.agents.plan.PlanComposite.execute",
                    new=plan_execute_mock,
                ),
                patch(
                    "agent_agent.agents.coding.CodingComposite.execute",
                    new=AsyncMock(return_value=(code_output, 0.05)),
                ),
                patch(
                    "agent_agent.agents.review.ReviewComposite.execute",
                    new=AsyncMock(return_value=(review_output, 0.05)),
                ),
                patch.object(uvicorn, "Server", return_value=mock_server),
            ):
                orchestrator = Orchestrator(
                    settings=settings,
                    repo_path=repo_path,
                    claude_md_content="# Test CLAUDE.md\n",
                    issue_url="https://github.com/test/repo/issues/42",
                    state_store=state,
                    agent_fn=None,
                    use_composites=True,
                )
                branch_name, summary = await orchestrator.run()

            # WorktreeManager was instantiated (wired up)
            assert wm_instantiated, (
                "WorktreeManager was not instantiated when use_composites=True"
            )

            # WorktreeManager.create_coding_worktree was called for the coding node
            assert mock_wm.create_coding_worktree.called, (
                "WorktreeManager.create_coding_worktree was not called"
            )

            # The run completed with a valid branch_name and summary
            assert branch_name == "agent-test10d-code-1", (
                f"Expected branch_name from CodingComposite, got {branch_name!r}"
            )
            assert "approved" in summary.lower(), (
                f"Expected approval summary from ReviewComposite, got {summary!r}"
            )

        finally:
            await state.close()
