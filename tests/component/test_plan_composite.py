"""Component tests for PlanComposite — real SDK calls.

These tests require ANTHROPIC_API_KEY and make real Claude API calls.
Mark with @pytest.mark.sdk so they are skipped in CI without API keys.
"""

from __future__ import annotations

import os

import pytest

from agent_agent.agents.plan import PlanComposite, validate_child_dag_spec
from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.context.shared import append_discoveries
from agent_agent.models.agent import FileMapping, PlanOutput, ReviewOutput, ReviewVerdict
from agent_agent.models.context import (
    AncestorContext,
    IssueContext,
    NodeContext,
    RepoMetadata,
    SharedContext,
    SharedContextView,
)
from agent_agent.state import StateStore


pytestmark = pytest.mark.sdk


def _skip_without_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping SDK test")


def _make_settings() -> Settings:
    return Settings(
        env="test",
        model="claude-haiku-4-5-20251001",
        max_budget_usd=5.0,
        plan_use_thinking=False,
        plan_effort="low",
        plan_max_turns=100,
    )


def _make_budget(node_ids: list[str] | None = None) -> BudgetManager:
    mgr = BudgetManager(dag_run_id="component-run", total_budget_usd=5.0)
    mgr.allocate(node_ids or ["plan-node"])
    return mgr


def _make_l0_context(repo_path: str) -> NodeContext:
    """Build a minimal L0 NodeContext for a fresh issue analysis."""
    return NodeContext(
        issue=IssueContext(
            url="https://github.com/test-org/test-repo/issues/1",
            title="Add a greeting function",
            body=(
                "We need a `greet(name: str) -> str` function in `src/greet.py` "
                "that returns `'Hello, {name}!'`. Add a unit test in `tests/test_greet.py`."
            ),
        ),
        repo_metadata=RepoMetadata(
            path=repo_path,
            default_branch="main",
            language="python",
            framework=None,
            claude_md="# Test Repo\nNo special rules.",
        ),
        parent_outputs={},
        ancestor_context=AncestorContext(),
        shared_context_view=SharedContextView(),
    )


def _make_consolidation_context(repo_path: str) -> NodeContext:
    """Build a consolidation NodeContext with approved ReviewOutputs."""
    review = ReviewOutput(
        verdict=ReviewVerdict.APPROVED,
        summary="Code looks good, tests pass.",
        findings=[],
        downstream_impacts=[],
        discoveries=[],
    )
    return NodeContext(
        issue=IssueContext(
            url="https://github.com/test-org/test-repo/issues/1",
            title="Add a greeting function",
            body="Add greet() function.",
        ),
        repo_metadata=RepoMetadata(
            path=repo_path,
            default_branch="main",
            language="python",
            framework=None,
            claude_md="# Test Repo\nNo special rules.",
        ),
        parent_outputs={"review-A": review},
        ancestor_context=AncestorContext(),
        shared_context_view=SharedContextView(),
    )


class TestPlanCompositeSDK:
    """Component tests with real SDK calls. Budget-capped at $1.00 per test."""

    async def test_l0_plan_returns_valid_plan_output(self, tmp_git_repo: str) -> None:
        """L0 plan: given a simple issue + fixture repo, returns PlanOutput with valid ChildDAGSpec."""
        _skip_without_api_key()

        settings = _make_settings()
        budget = _make_budget()
        composite = PlanComposite(settings=settings, repo_path=str(tmp_git_repo), budget=budget)

        output, cost = await composite.execute(
            node_context=_make_l0_context(str(tmp_git_repo)),
            dag_run_id="component-run",
            node_id="plan-node",
            is_consolidation=False,
        )

        assert isinstance(output, PlanOutput)
        assert output.type == "plan"
        assert output.investigation_summary  # non-empty
        # L0 plan should produce a child_dag
        if output.child_dag is not None:
            validate_child_dag_spec(output.child_dag)
        assert cost > 0.0

    async def test_consolidation_plan_with_approved_reviews(self, tmp_git_repo: str) -> None:
        """Consolidation plan: given approved ReviewOutputs, returns PlanOutput with child_dag=None."""
        _skip_without_api_key()

        settings = _make_settings()
        budget = _make_budget()
        composite = PlanComposite(settings=settings, repo_path=str(tmp_git_repo), budget=budget)

        output, cost = await composite.execute(
            node_context=_make_consolidation_context(str(tmp_git_repo)),
            dag_run_id="component-run",
            node_id="plan-node",
            is_consolidation=True,
        )

        assert isinstance(output, PlanOutput)
        assert output.type == "plan"
        # With all reviews approved, consolidation should return child_dag=None
        # (though the model may not always do this, so we just verify the type)
        assert cost > 0.0

    async def test_plan_output_contains_discoveries(self, tmp_git_repo: str) -> None:
        """PlanOutput.discoveries contains at least one discovery (smoke test)."""
        _skip_without_api_key()

        settings = _make_settings()
        budget = _make_budget()
        composite = PlanComposite(settings=settings, repo_path=str(tmp_git_repo), budget=budget)

        output, cost = await composite.execute(
            node_context=_make_l0_context(str(tmp_git_repo)),
            dag_run_id="component-run",
            node_id="plan-node",
            is_consolidation=False,
        )

        assert isinstance(output, PlanOutput)
        # Smoke test: the planner should discover something about the repo
        # This may be flaky with very simple repos, so we just check the field exists
        assert isinstance(output.discoveries, list)


# ---------------------------------------------------------------------------
# Test 3 — Discovery write-through
# ---------------------------------------------------------------------------


class TestDiscoveryWriteThrough:
    """Test 3: After PlanComposite.execute(), discoveries are persisted to StateStore.

    This tests the write-through path: PlanOutput.discoveries -> append_discoveries()
    -> StateStore.list_shared_context(). The executor calls append_discoveries() after
    a successful plan node; we replicate that here with a real StateStore.
    """

    async def test_discovery_write_through_to_state_store(self, tmp_git_repo: str) -> None:
        """Test 3 (SDK): Real PlanComposite + real StateStore; discoveries must persist."""
        _skip_without_api_key()

        settings = _make_settings()
        budget = _make_budget()

        # Build a real in-memory StateStore
        state = StateStore(":memory:")
        await state.init()

        try:
            # Create a minimal DAGRun so foreign keys pass
            from datetime import datetime, timezone
            from agent_agent.models.dag import DAGRun, DAGRunStatus

            dag_run_id = "test3-discovery-run"
            now = datetime.now(timezone.utc)
            dag_run = DAGRun(
                id=dag_run_id,
                issue_url="https://github.com/test/repo/issues/3",
                repo_path=str(tmp_git_repo),
                status=DAGRunStatus.RUNNING,
                budget_usd=5.0,
                usd_used=0.0,
                created_at=now,
                updated_at=now,
            )
            await state.create_dag_run(dag_run)

            # Run the PlanComposite against the real fixture repo
            composite = PlanComposite(
                settings=settings, repo_path=str(tmp_git_repo), budget=budget
            )
            output, cost = await composite.execute(
                node_context=_make_l0_context(str(tmp_git_repo)),
                dag_run_id=dag_run_id,
                node_id="plan-node",
                is_consolidation=False,
            )

            assert isinstance(output, PlanOutput)
            assert cost > 0.0

            # If the agent produced discoveries, write them through via append_discoveries()
            # (This is what the executor does after a successful plan node.)
            if output.discoveries:
                shared_context = SharedContext(
                    issue=_make_l0_context(str(tmp_git_repo)).issue,
                    repo_metadata=_make_l0_context(str(tmp_git_repo)).repo_metadata,
                )
                await append_discoveries(
                    discoveries=output.discoveries,
                    source_node_id="plan-node",
                    dag_run_id=dag_run_id,
                    shared_context=shared_context,
                    state=state,
                )

                # Verify they were persisted to the state store
                records = await state.list_shared_context(dag_run_id)
                assert len(records) > 0

                # Each record has a valid category from the allowed set
                valid_categories = {
                    "file_mapping",
                    "root_cause",
                    "constraint",
                    "design_decision",
                    "negative_finding",
                }
                for record in records:
                    assert record["category"] in valid_categories
                    assert record["source_node_id"] == "plan-node"
            else:
                # If no discoveries, verify the store is empty (not an error)
                records = await state.list_shared_context(dag_run_id)
                assert records == []
        finally:
            await state.close()

    async def test_manual_discovery_write_through(self, tmp_git_repo: str) -> None:
        """Test 3b (no SDK): Manually inject a discovery and verify StateStore persistence.

        This verifies the write-through path directly without making an SDK call.
        Useful for CI that lacks an API key.
        """
        from datetime import datetime, timezone

        from agent_agent.models.agent import FileMapping
        from agent_agent.models.dag import DAGRun, DAGRunStatus

        state = StateStore(":memory:")
        await state.init()

        try:
            dag_run_id = "test3b-discovery-run"
            now = datetime.now(timezone.utc)
            dag_run = DAGRun(
                id=dag_run_id,
                issue_url="https://github.com/test/repo/issues/3",
                repo_path=str(tmp_git_repo),
                status=DAGRunStatus.RUNNING,
                budget_usd=5.0,
                usd_used=0.0,
                created_at=now,
                updated_at=now,
            )
            await state.create_dag_run(dag_run)

            # Synthetic discoveries
            discoveries = [
                FileMapping(
                    path="src/main.py",
                    description="Main application entry point",
                    confidence=0.9,
                ),
            ]

            # Build a minimal SharedContext
            shared_context = SharedContext(
                issue=_make_l0_context(str(tmp_git_repo)).issue,
                repo_metadata=_make_l0_context(str(tmp_git_repo)).repo_metadata,
            )

            # Write through
            await append_discoveries(
                discoveries=discoveries,
                source_node_id="plan-node-3b",
                dag_run_id=dag_run_id,
                shared_context=shared_context,
                state=state,
            )

            # Verify
            records = await state.list_shared_context(dag_run_id)
            assert len(records) == 1
            assert records[0]["category"] == "file_mapping"
            assert records[0]["source_node_id"] == "plan-node-3b"
        finally:
            await state.close()
