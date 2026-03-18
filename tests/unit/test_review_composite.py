"""Unit tests for agents/review.py — ReviewComposite."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent_agent.agents.prompts import REVIEWER
from agent_agent.agents.review import ReviewComposite
from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.dag.executor import AgentError
from agent_agent.models.agent import (
    CodeOutput,
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReviewCompositeConfig:
    """Test 1: ReviewComposite config has max_turns=20 and read-only tools."""

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    async def test_config_max_turns_and_defaults(self, mock_invoke: AsyncMock) -> None:
        """ReviewComposite config: max_turns=20, use_thinking=False [P10.3]."""
        review_output = ReviewOutput(
            verdict=ReviewVerdict.APPROVED,
            summary="Looks good.",
            findings=[],
            downstream_impacts=[],
        )
        mock_invoke.return_value = (review_output, 0.03)

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
    async def test_returns_review_output_and_cost(self, mock_invoke: AsyncMock) -> None:
        review_output = ReviewOutput(
            verdict=ReviewVerdict.NEEDS_REWORK,
            summary="Needs fixes.",
            findings=[
                ReviewFinding(severity="major", description="Missing error handling"),
            ],
            downstream_impacts=["May affect module B"],
        )
        mock_invoke.return_value = (review_output, 0.07)

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

        assert result is review_output
        assert cost == 0.07

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    async def test_raises_agent_error_on_wrong_output_type(self, mock_invoke: AsyncMock) -> None:
        """ReviewComposite raises AgentError if invoke_agent returns non-ReviewOutput."""
        wrong_output = CodeOutput(
            summary="code", files_changed=[], branch_name="b", commit_sha=None
        )
        mock_invoke.return_value = (wrong_output, 0.01)

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
    async def test_system_prompt_includes_worktree_path(self, mock_invoke: AsyncMock) -> None:
        worktree_path = "/workspaces/.worktrees/review-xyz-42"
        review_output = ReviewOutput(
            verdict=ReviewVerdict.APPROVED,
            summary="All good.",
        )
        mock_invoke.return_value = (review_output, 0.02)

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
        # Verify it matches the REVIEWER template with worktree_path formatted in
        expected_prompt = REVIEWER.format(worktree_path=worktree_path)
        assert config.system_prompt == expected_prompt

    @patch("agent_agent.agents.review.invoke_agent", new_callable=AsyncMock)
    async def test_cwd_is_worktree_path(self, mock_invoke: AsyncMock) -> None:
        """ReviewComposite passes worktree.path as cwd."""
        worktree_path = "/workspaces/.worktrees/review-cwd-1"
        review_output = ReviewOutput(
            verdict=ReviewVerdict.APPROVED,
            summary="Fine.",
        )
        mock_invoke.return_value = (review_output, 0.01)

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
    async def test_allowed_tools_read_only(self, mock_invoke: AsyncMock) -> None:
        """Reviewer has only read-only tools: Read, Glob, Grep, Bash."""
        review_output = ReviewOutput(
            verdict=ReviewVerdict.APPROVED,
            summary="Good.",
        )
        mock_invoke.return_value = (review_output, 0.01)

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
