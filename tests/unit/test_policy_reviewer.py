"""Unit tests for agents/policy_review.py — PolicyReviewer."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent_agent.agents.policy_review import PolicyReviewer
from agent_agent.agents.prompts import POLICY_REVIEWER
from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.dag.executor import AgentError
from agent_agent.models.agent import (
    CodeOutput,
    PolicyCitation,
    PolicyReviewOutput,
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
            framework=None,
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


def _make_worktree(path: str = "/workspaces/.worktrees/policy-review-1") -> WorktreeRecord:
    return WorktreeRecord(
        path=path,
        branch="agent-abc12345-code-1",
        dag_run_id="run-1",
        node_id="review-node",
        readonly=True,
    )


def _make_policy_review_output(
    approved: bool = True,
    skipped: bool = False,
) -> PolicyReviewOutput:
    return PolicyReviewOutput(
        approved=approved,
        skipped=skipped,
        policy_citations=[],
        policies_evaluated=[],
    )


# ---------------------------------------------------------------------------
# Test: PolicyReviewer config
# ---------------------------------------------------------------------------


class TestPolicyReviewerConfig:
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_config_name_is_policy_reviewer(self, mock_invoke: AsyncMock) -> None:
        mock_invoke.return_value = (_make_policy_review_output(), 0.01)
        reviewer = PolicyReviewer(_make_settings(), _make_worktree(), _make_budget())
        await reviewer.execute(_make_node_context(), "run-1", "review-node")
        config = mock_invoke.call_args.kwargs["config"]
        assert config.name == "policy_reviewer"

    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_config_output_model(self, mock_invoke: AsyncMock) -> None:
        mock_invoke.return_value = (_make_policy_review_output(), 0.01)
        reviewer = PolicyReviewer(_make_settings(), _make_worktree(), _make_budget())
        await reviewer.execute(_make_node_context(), "run-1", "review-node")
        config = mock_invoke.call_args.kwargs["config"]
        assert config.output_model is PolicyReviewOutput

    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_config_max_turns(self, mock_invoke: AsyncMock) -> None:
        settings = _make_settings()
        mock_invoke.return_value = (_make_policy_review_output(), 0.01)
        reviewer = PolicyReviewer(settings, _make_worktree(), _make_budget())
        await reviewer.execute(_make_node_context(), "run-1", "review-node")
        config = mock_invoke.call_args.kwargs["config"]
        assert config.max_turns == settings.reviewer_max_turns

    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_config_tools_read_only(self, mock_invoke: AsyncMock) -> None:
        mock_invoke.return_value = (_make_policy_review_output(), 0.01)
        reviewer = PolicyReviewer(_make_settings(), _make_worktree(), _make_budget())
        await reviewer.execute(_make_node_context(), "run-1", "review-node")
        config = mock_invoke.call_args.kwargs["config"]
        assert set(config.allowed_tools) == {"Read", "Glob", "Grep", "Bash"}
        assert "Edit" not in config.allowed_tools
        assert "Write" not in config.allowed_tools

    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_system_prompt_includes_worktree_path(self, mock_invoke: AsyncMock) -> None:
        worktree_path = "/workspaces/.worktrees/policy-review-xyz"
        mock_invoke.return_value = (_make_policy_review_output(), 0.01)
        reviewer = PolicyReviewer(
            _make_settings(), _make_worktree(path=worktree_path), _make_budget()
        )
        await reviewer.execute(_make_node_context(), "run-1", "review-node")
        config = mock_invoke.call_args.kwargs["config"]
        assert worktree_path in config.system_prompt
        assert config.system_prompt == POLICY_REVIEWER.format(worktree_path=worktree_path)

    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_cwd_is_worktree_path(self, mock_invoke: AsyncMock) -> None:
        worktree_path = "/workspaces/.worktrees/policy-review-cwd"
        mock_invoke.return_value = (_make_policy_review_output(), 0.01)
        reviewer = PolicyReviewer(
            _make_settings(), _make_worktree(path=worktree_path), _make_budget()
        )
        await reviewer.execute(_make_node_context(), "run-1", "review-node")
        assert mock_invoke.call_args.kwargs["cwd"] == worktree_path


# ---------------------------------------------------------------------------
# Test: PolicyReviewOutput model
# ---------------------------------------------------------------------------


class TestPolicyReviewOutputModel:
    def test_skipped_output_valid(self) -> None:
        output = PolicyReviewOutput(
            approved=True, skipped=True, policy_citations=[], policies_evaluated=[]
        )
        assert output.type == "policy_review"
        assert output.skipped is True

    def test_violation_sets_approved_false(self) -> None:
        citation = PolicyCitation(
            policy_id="POLICY-001",
            policy_text="All functions must have type annotations.",
            location="src/foo.py:10",
            finding="Function bar() missing type annotations.",
            is_violation=True,
        )
        output = PolicyReviewOutput(
            approved=False, skipped=False,
            policy_citations=[citation], policies_evaluated=["POLICY-001"]
        )
        assert output.approved is False
        assert output.policy_citations[0].is_violation is True

    def test_policy_citation_required_fields(self) -> None:
        citation = PolicyCitation(
            policy_id="P1", policy_text="Some rule.",
            location="foo.py:1", finding="A finding.", is_violation=False,
        )
        assert citation.policy_id == "P1"

    def test_round_trips_via_json(self) -> None:
        output = PolicyReviewOutput(
            approved=True, skipped=False,
            policy_citations=[
                PolicyCitation(
                    policy_id="P2", policy_text="No module-level mutable state.",
                    location="lib/cache.py:5", finding="Uses instance-level cache.",
                    is_violation=False,
                )
            ],
            policies_evaluated=["P2"],
        )
        restored = PolicyReviewOutput.model_validate(output.model_dump())
        assert restored.policies_evaluated == ["P2"]


# ---------------------------------------------------------------------------
# Test: ReviewOutput.policy_review field
# ---------------------------------------------------------------------------


class TestReviewOutputPolicyReviewField:
    def test_policy_review_field_defaults_to_none(self) -> None:
        output = ReviewOutput(verdict=ReviewVerdict.APPROVED, summary="Looks good.")
        assert output.policy_review is None

    def test_policy_review_field_accepts_policy_review_output(self) -> None:
        policy_output = PolicyReviewOutput(
            approved=True, skipped=False, policy_citations=[], policies_evaluated=[]
        )
        output = ReviewOutput(
            verdict=ReviewVerdict.APPROVED, summary="Looks good.", policy_review=policy_output
        )
        assert output.policy_review is not None
        assert output.policy_review.approved is True


# ---------------------------------------------------------------------------
# Test: PolicyReviewer.execute()
# ---------------------------------------------------------------------------


class TestPolicyReviewerExecute:
    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_returns_policy_review_output_and_cost(self, mock_invoke: AsyncMock) -> None:
        expected = _make_policy_review_output(approved=False, skipped=False)
        mock_invoke.return_value = (expected, 0.05)
        reviewer = PolicyReviewer(_make_settings(), _make_worktree(), _make_budget())
        output, cost = await reviewer.execute(_make_node_context(), "run-1", "review-node")
        assert output is expected
        assert cost == 0.05

    @patch("agent_agent.agents.policy_review.invoke_agent", new_callable=AsyncMock)
    async def test_raises_agent_error_on_wrong_output_type(self, mock_invoke: AsyncMock) -> None:
        wrong_output = CodeOutput(
            summary="code", files_changed=[], branch_name="b", commit_sha=None
        )
        mock_invoke.return_value = (wrong_output, 0.01)
        reviewer = PolicyReviewer(_make_settings(), _make_worktree(), _make_budget())
        with pytest.raises(AgentError, match="PolicyReviewer expected PolicyReviewOutput"):
            await reviewer.execute(_make_node_context(), "run-1", "review-node")
