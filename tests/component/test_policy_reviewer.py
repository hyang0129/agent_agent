"""Component tests for PolicyReviewer — real SDK calls (Mode 1 arbitrary policy).

Marked @pytest.mark.sdk — requires the claude CLI and unset CLAUDECODE.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_agent.agents.policy_review import PolicyReviewer
from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.models.agent import PolicyReviewOutput
from agent_agent.models.context import (
    AncestorContext,
    IssueContext,
    NodeContext,
    RepoMetadata,
    SharedContextView,
)
from agent_agent.worktree import WorktreeRecord


pytestmark = pytest.mark.sdk


def _skip_without_claude_cli() -> None:
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not found — skipping SDK test")


def _make_settings() -> Settings:
    return Settings(env="test", model="claude-haiku-4-5-20251001", max_budget_usd=5.0)


def _make_budget(node_ids: list[str] | None = None) -> BudgetManager:
    mgr = BudgetManager(dag_run_id="policy-component-run", total_budget_usd=5.0)
    mgr.allocate(node_ids or ["review-node"])
    return mgr


def _git(*args: str, cwd: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture()
def repo_with_policy_violation(tmp_git_repo: Path, tmp_path: Path) -> tuple[Path, WorktreeRecord, str]:
    """Repo with CLAUDE.md policy that the committed diff violates (no type annotations)."""
    repo = tmp_git_repo
    branch = "agent-policy-violation-1"

    _git("checkout", "-b", branch, cwd=repo)

    claude_md = repo / "CLAUDE.md"
    claude_md.write_text(
        "# Test Repo\n\n"
        "## Policies\n\n"
        "POLICY-TA-001: All new public functions must have complete type annotations "
        "(parameter types and return type). Functions without type annotations will be "
        "rejected in code review.\n"
    )
    _git("add", "CLAUDE.md", cwd=repo)

    src_dir = repo / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "utils.py").write_text(
        "def process(data, multiplier):\n"
        "    \"\"\"Process data by multiplier.\"\"\"\n"
        "    return data * multiplier\n"
    )
    _git("add", "src/utils.py", cwd=repo)
    _git("commit", "-m", "Add process function without type annotations", cwd=repo)
    _git("checkout", "main", cwd=repo)

    worktree_path = tmp_path / "review-wt-violation"
    _git("worktree", "add", str(worktree_path), branch, cwd=repo)

    record = WorktreeRecord(
        path=str(worktree_path),
        branch=branch,
        dag_run_id="policy-component-run",
        node_id="review-node",
        readonly=True,
    )
    return repo, record, "POLICY-TA-001"


@pytest.fixture()
def repo_without_policy(tmp_git_repo: Path, tmp_path: Path) -> tuple[Path, WorktreeRecord]:
    """Repo with no CLAUDE.md — PolicyReviewer should return skipped=True."""
    repo = tmp_git_repo
    branch = "agent-no-policy-1"

    _git("checkout", "-b", branch, cwd=repo)

    src_dir = repo / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "greet.py").write_text(
        "def greet(name: str) -> str:\n"
        "    return f'Hello, {name}!'\n"
    )
    _git("add", "src/greet.py", cwd=repo)
    _git("commit", "-m", "Add greet function", cwd=repo)
    _git("checkout", "main", cwd=repo)

    worktree_path = tmp_path / "review-wt-no-policy"
    _git("worktree", "add", str(worktree_path), branch, cwd=repo)

    record = WorktreeRecord(
        path=str(worktree_path),
        branch=branch,
        dag_run_id="policy-component-run",
        node_id="review-node",
        readonly=True,
    )
    return repo, record


def _make_context(repo_path: str, claude_md: str = "# Test Repo") -> NodeContext:
    return NodeContext(
        issue=IssueContext(
            url="https://github.com/test-org/test-repo/issues/1",
            title="Add utility function",
            body="Add a process() function to src/utils.py.",
        ),
        repo_metadata=RepoMetadata(
            path=repo_path,
            default_branch="main",
            language="python",
            framework=None,
            claude_md=claude_md,
        ),
        parent_outputs={},
        ancestor_context=AncestorContext(),
        shared_context_view=SharedContextView(),
    )


class TestPolicyReviewerSDKSkip:
    async def test_skips_when_no_claude_md(
        self, repo_without_policy: tuple[Path, WorktreeRecord]
    ) -> None:
        _skip_without_claude_cli()
        repo, worktree = repo_without_policy
        reviewer = PolicyReviewer(_make_settings(), worktree, _make_budget())
        output, cost = await reviewer.execute(
            _make_context(str(repo)), "policy-component-run", "review-node"
        )
        assert isinstance(output, PolicyReviewOutput)
        assert output.skipped is True
        assert output.approved is True
        assert output.policy_citations == []
        assert cost > 0.0


class TestPolicyReviewerSDKViolation:
    async def test_detects_type_annotation_violation(
        self, repo_with_policy_violation: tuple[Path, WorktreeRecord, str]
    ) -> None:
        _skip_without_claude_cli()
        repo, worktree, _ = repo_with_policy_violation
        claude_md = (
            "# Test Repo\n\n## Policies\n\n"
            "POLICY-TA-001: All new public functions must have complete type annotations "
            "(parameter types and return type). Functions without type annotations will be "
            "rejected in code review.\n"
        )
        reviewer = PolicyReviewer(_make_settings(), worktree, _make_budget())
        output, cost = await reviewer.execute(
            _make_context(str(repo), claude_md), "policy-component-run", "review-node"
        )
        assert isinstance(output, PolicyReviewOutput)
        assert output.skipped is False
        assert output.approved is False, f"Expected violation detected. Citations: {output.policy_citations}"
        assert any(c.is_violation for c in output.policy_citations)
        assert cost > 0.0


class TestPolicyReviewerSDKOutputStructure:
    async def test_output_is_valid_policy_review_output(
        self, repo_with_policy_violation: tuple[Path, WorktreeRecord, str]
    ) -> None:
        _skip_without_claude_cli()
        repo, worktree, _ = repo_with_policy_violation
        claude_md = (
            "# Test Repo\n\n## Policies\n\n"
            "POLICY-TA-001: All new public functions must have complete type annotations "
            "(parameter types and return type). Functions without type annotations will be "
            "rejected in code review.\n"
        )
        reviewer = PolicyReviewer(_make_settings(), worktree, _make_budget())
        output, cost = await reviewer.execute(
            _make_context(str(repo), claude_md), "policy-component-run", "review-node"
        )
        assert isinstance(output, PolicyReviewOutput)
        assert output.type == "policy_review"
        assert isinstance(output.approved, bool)
        assert isinstance(output.skipped, bool)
        assert isinstance(output.policy_citations, list)
        assert cost > 0.0
