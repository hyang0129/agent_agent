"""Unit tests for agents/review.py — ReviewComposite."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent_agent.agents.prompts import REVIEWER
from agent_agent.agents.review import ReviewComposite, _merge_verdict
from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.dag.executor import AgentError
from agent_agent.models.agent import (
    CodeOutput,
    PolicyCitation,
    PolicyReviewOutput,
    ReviewFinding,
    ReviewOutput,
    ReviewVerdict,
)
from agent_agent.models.context import (
    AncestorContext,
    IssueContext,
    NodeContext,
    RepoMetadata,
    SharedContextView,
)
from agent_agent.worktree import WorktreeRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node_context() -> NodeContext:
    return NodeContext(
        issue=IssueContext(
            url="https://github.com/org/repo/issues/1",
            title="Test issue",
            body="Fix the bug.",
        ),
        repo_metadata=RepoMetadata(
            path="/workspaces/target",
            default_branch="main",
            language="python",
            framework="fastapi",
            claude_md="# CLAUDE.md",
        ),
        parent_outputs={},
        ancestor_context=AncestorContext(),
        shared_context_view=SharedContextView(),
    )


def _make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "env": "test",
        "model": "claude-haiku-4-5-20251001",
        "max_budget_usd": 5.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_budget(total: float = 5.0, node_ids: list[str] | None = None) -> BudgetManager:
    mgr = BudgetManager(dag_run_id="run-1", total_budget_usd=total)
    mgr.allocate(node_ids or ["review-node"])
    return mgr


def _make_worktree(path: str = "/workspaces/.worktrees/review-abc-1") -> WorktreeRecord:
    return WorktreeRecord(
        path=path,
        branch="agent-abc12345-code-1",
        dag_run_id="run-1",
        node_id="review-node",
        readonly=True,
    )


def _make_review_output(verdict: ReviewVerdict = ReviewVerdict.APPROVED) -> ReviewOutput:
    return ReviewOutput(
        verdict=verdict,
        summary="Looks good.",
        findings=[],
        downstream_impacts=[],
    )


def _make_policy_review_output(approved: bool = True, skipped: bool = False) -> PolicyReviewOutput:
    return PolicyReviewOutput(
        approved=approved,
        skipped=skipped,
        policy_citations=[],
        policies_evaluated=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReviewCompositeConfig:
    """Test 1: ReviewComposite config has max_turns and read-only tools."""

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_config_max_turns_and_defaults(
        self, mock_policy_invoke: AsyncMock, mock_invoke: AsyncMock
    ) -> None:
        """ReviewComposite config: use_thinking=False [P10.3]."""
        mock_invoke.return_value = (_make_review_output(), 0.03)
        mock_policy_invoke.return_value = (_make_policy_review_output(), 0.01)

        composite = ReviewComposite(
            settings=_make_settings(),
            worktree=_make_worktree(),
            budget=_make_budget(),
        )
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="review-node",
        )

        config = mock_invoke.call_args.kwargs["config"]
        assert config.max_turns == _make_settings().reviewer_max_turns
        assert config.use_thinking is False
        assert config.name == "reviewer"
        assert config.output_model is ReviewOutput


class TestReviewCompositeExecute:
    """Test 2: ReviewComposite.execute() returns (ReviewOutput, cost_usd)."""

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_returns_review_output_and_cost(
        self, mock_policy_invoke: AsyncMock, mock_invoke: AsyncMock
    ) -> None:
        review_output = ReviewOutput(
            verdict=ReviewVerdict.NEEDS_REWORK,
            summary="Needs fixes.",
            findings=[
                ReviewFinding(severity="major", description="Missing error handling"),
            ],
            downstream_impacts=["May affect module B"],
        )
        mock_invoke.return_value = (review_output, 0.07)
        mock_policy_invoke.return_value = (_make_policy_review_output(), 0.02)

        composite = ReviewComposite(
            settings=_make_settings(),
            worktree=_make_worktree(),
            budget=_make_budget(),
        )
        result, cost = await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="review-node",
        )

        assert isinstance(result, ReviewOutput)
        assert result.verdict == ReviewVerdict.NEEDS_REWORK
        assert cost == pytest.approx(0.09)

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_raises_agent_error_on_wrong_output_type(
        self, mock_policy_invoke: AsyncMock, mock_invoke: AsyncMock
    ) -> None:
        """ReviewComposite raises AgentError if invoke_agent returns non-ReviewOutput."""
        wrong_output = CodeOutput(
            summary="code", files_changed=[], branch_name="b", commit_sha=None
        )
        mock_invoke.return_value = (wrong_output, 0.01)
        mock_policy_invoke.return_value = (_make_policy_review_output(), 0.01)

        composite = ReviewComposite(
            settings=_make_settings(),
            worktree=_make_worktree(),
            budget=_make_budget(),
        )
        with pytest.raises(AgentError, match="expected ReviewOutput"):
            await composite.execute(
                node_context=_make_node_context(),
                dag_run_id="run-1",
                node_id="review-node",
            )


class TestReviewCompositePrompt:
    """Test 3: System prompt includes worktree path."""

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_system_prompt_includes_worktree_path(
        self, mock_policy_invoke: AsyncMock, mock_invoke: AsyncMock
    ) -> None:
        worktree_path = "/workspaces/.worktrees/review-xyz-42"
        mock_invoke.return_value = (_make_review_output(), 0.02)
        mock_policy_invoke.return_value = (_make_policy_review_output(), 0.01)

        composite = ReviewComposite(
            settings=_make_settings(),
            worktree=_make_worktree(path=worktree_path),
            budget=_make_budget(),
        )
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="review-node",
        )

        config = mock_invoke.call_args.kwargs["config"]
        assert worktree_path in config.system_prompt
        expected_prompt = REVIEWER.format(worktree_path=worktree_path)
        assert config.system_prompt == expected_prompt

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_cwd_is_worktree_path(
        self, mock_policy_invoke: AsyncMock, mock_invoke: AsyncMock
    ) -> None:
        """ReviewComposite passes worktree.path as cwd."""
        worktree_path = "/workspaces/.worktrees/review-cwd-1"
        mock_invoke.return_value = (_make_review_output(), 0.01)
        mock_policy_invoke.return_value = (_make_policy_review_output(), 0.01)

        composite = ReviewComposite(
            settings=_make_settings(model="claude-sonnet-4-6"),
            worktree=_make_worktree(path=worktree_path),
            budget=_make_budget(),
        )
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="review-node",
        )

        assert mock_invoke.call_args.kwargs["cwd"] == worktree_path
        assert mock_invoke.call_args.kwargs["model"] == "claude-sonnet-4-6"


class TestReviewerPermissions:
    """Test 4: Reviewer allowed_tools — no write tools."""

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_allowed_tools_read_only(
        self, mock_policy_invoke: AsyncMock, mock_invoke: AsyncMock
    ) -> None:
        """Reviewer has only read-only tools: Read, Glob, Grep, Bash."""
        mock_invoke.return_value = (_make_review_output(), 0.01)
        mock_policy_invoke.return_value = (_make_policy_review_output(), 0.01)

        composite = ReviewComposite(
            settings=_make_settings(),
            worktree=_make_worktree(),
            budget=_make_budget(),
        )
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="review-node",
        )

        config = mock_invoke.call_args.kwargs["config"]
        all_tools = set(config.allowed_tools)

        assert all_tools == {"Read", "Glob", "Grep", "Bash"}
        assert "Edit" not in all_tools
        assert "Write" not in all_tools


class TestReviewCompositeParallel:
    """Test 5: Both Reviewer and PolicyReviewer invoked in same execute() call."""

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_both_agents_invoked(
        self, mock_policy_invoke: AsyncMock, mock_invoke: AsyncMock
    ) -> None:
        mock_invoke.return_value = (_make_review_output(), 0.03)
        mock_policy_invoke.return_value = (_make_policy_review_output(), 0.01)

        composite = ReviewComposite(
            settings=_make_settings(),
            worktree=_make_worktree(),
            budget=_make_budget(),
        )
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="review-node",
        )

        assert mock_invoke.call_count == 1
        assert mock_policy_invoke.call_count == 1

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_total_cost_is_sum(
        self, mock_policy_invoke: AsyncMock, mock_invoke: AsyncMock
    ) -> None:
        mock_invoke.return_value = (_make_review_output(), 0.07)
        mock_policy_invoke.return_value = (_make_policy_review_output(), 0.03)

        composite = ReviewComposite(
            settings=_make_settings(),
            worktree=_make_worktree(),
            budget=_make_budget(),
        )
        _, cost = await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="review-node",
        )

        assert cost == pytest.approx(0.10)


class TestReviewCompositeMerge:
    """Test 6: Verdict merge — policy violations force REJECTED."""

    def test_merge_verdict_policy_violation_forces_rejected(self) -> None:
        policy_output = PolicyReviewOutput(
            approved=False,
            skipped=False,
            policy_citations=[
                PolicyCitation(
                    policy_id="POLICY-001",
                    policy_text="No bare except.",
                    location="src/foo.py:42",
                    finding="bare except clause found",
                    is_violation=True,
                )
            ],
            policies_evaluated=["POLICY-001"],
        )
        assert _merge_verdict(ReviewVerdict.APPROVED, policy_output) == ReviewVerdict.REJECTED
        assert _merge_verdict(ReviewVerdict.NEEDS_REWORK, policy_output) == ReviewVerdict.REJECTED

    def test_merge_verdict_skipped_does_not_change_verdict(self) -> None:
        policy_output = PolicyReviewOutput(
            approved=True,
            skipped=True,
            policy_citations=[],
            policies_evaluated=[],
        )
        assert _merge_verdict(ReviewVerdict.APPROVED, policy_output) == ReviewVerdict.APPROVED
        assert _merge_verdict(ReviewVerdict.NEEDS_REWORK, policy_output) == ReviewVerdict.NEEDS_REWORK

    def test_merge_verdict_policy_approved_preserves_verdict(self) -> None:
        policy_output = PolicyReviewOutput(
            approved=True,
            skipped=False,
            policy_citations=[],
            policies_evaluated=[],
        )
        assert _merge_verdict(ReviewVerdict.APPROVED, policy_output) == ReviewVerdict.APPROVED
        assert _merge_verdict(ReviewVerdict.NEEDS_REWORK, policy_output) == ReviewVerdict.NEEDS_REWORK

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_policy_violation_sets_verdict_to_rejected_in_execute(
        self, mock_policy_invoke: AsyncMock, mock_invoke: AsyncMock
    ) -> None:
        mock_invoke.return_value = (_make_review_output(ReviewVerdict.APPROVED), 0.03)
        mock_policy_invoke.return_value = (
            PolicyReviewOutput(
                approved=False,
                skipped=False,
                policy_citations=[
                    PolicyCitation(
                        policy_id="P1",
                        policy_text="No bare except.",
                        location="foo.py:1",
                        finding="violation",
                        is_violation=True,
                    )
                ],
                policies_evaluated=["P1"],
            ),
            0.02,
        )

        composite = ReviewComposite(
            settings=_make_settings(),
            worktree=_make_worktree(),
            budget=_make_budget(),
        )
        result, _ = await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="review-node",
        )

        assert result.verdict == ReviewVerdict.REJECTED
        assert result.policy_review is not None
        assert result.policy_review.approved is False
