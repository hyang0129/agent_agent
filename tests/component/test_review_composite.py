"""Component tests for ReviewComposite — real SDK calls.

These tests require the claude CLI to be installed and authenticated (Max plan).
Mark with @pytest.mark.sdk so they are skipped in CI without claude CLI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_agent.agents.review import ReviewComposite
from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
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


pytestmark = pytest.mark.sdk


def _skip_without_claude_cli() -> None:
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not found — skipping SDK test")


def _make_settings() -> Settings:
    return Settings(
        env="test",
        model="claude-haiku-4-5-20251001",
        max_budget_usd=5.0,
    )


def _make_budget(node_ids: list[str] | None = None) -> BudgetManager:
    mgr = BudgetManager(dag_run_id="component-run", total_budget_usd=5.0)
    mgr.allocate(node_ids or ["review-node"])
    return mgr


def _make_review_context(repo_path: str) -> NodeContext:
    """Build a NodeContext for a review with a CodeOutput parent."""
    code_output = CodeOutput(
        summary="Added greet() function in src/greet.py",
        files_changed=["src/greet.py"],
        branch_name="agent-abc12345-code-1",
        commit_sha="abc1234",
        tests_passed=None,
    )
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
        parent_outputs={"code-A": code_output},
        ancestor_context=AncestorContext(),
        shared_context_view=SharedContextView(),
    )


@pytest.fixture()
def repo_with_branch(tmp_git_repo: Path) -> tuple[Path, str]:
    """Create a branch with committed code changes on the tmp_git_repo.

    Returns (repo_path, branch_name).
    """
    repo = tmp_git_repo
    branch = "agent-abc12345-code-1"

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    git("checkout", "-b", branch)

    # Add a simple Python file
    src_dir = repo / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "greet.py").write_text(
        'def greet(name: str) -> str:\n    return f"Hello, {name}!"\n'
    )
    git("add", "src/greet.py")
    git("commit", "-m", "Add greet function")

    # Switch back to main so the branch is not checked out in the main worktree.
    # git worktree add rejects branches already checked out elsewhere.
    git("checkout", "main")

    return repo, branch


@pytest.fixture()
def review_worktree(repo_with_branch: tuple[Path, str], tmp_path: Path) -> WorktreeRecord:
    """Create a review worktree from the branch with code changes."""
    repo, branch = repo_with_branch

    worktree_path = tmp_path / "review-wt"

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }

    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), branch],
        cwd=repo,
        check=True,
        capture_output=True,
        env=env,
    )

    return WorktreeRecord(
        path=str(worktree_path),
        branch=branch,
        dag_run_id="component-run",
        node_id="review-node",
        readonly=True,
    )


class TestReviewCompositeSDK:
    """Component tests with real SDK calls. Budget-capped at $1.00 per test."""

    async def test_reviewer_returns_valid_review_output(
        self, repo_with_branch: tuple[Path, str], review_worktree: WorktreeRecord
    ) -> None:
        """Given a branch with real code changes, Reviewer returns valid ReviewOutput."""
        _skip_without_claude_cli()

        repo, _branch = repo_with_branch
        settings = _make_settings()
        budget = _make_budget()
        composite = ReviewComposite(
            settings=settings,
            worktree=review_worktree,
            budget=budget,
        )

        output, cost = await composite.execute(
            node_context=_make_review_context(str(repo)),
            dag_run_id="component-run",
            node_id="review-node",
        )

        assert isinstance(output, ReviewOutput)
        assert output.type == "review"
        assert output.summary  # non-empty
        assert cost > 0.0

    async def test_review_verdict_is_valid_enum(
        self, repo_with_branch: tuple[Path, str], review_worktree: WorktreeRecord
    ) -> None:
        """ReviewOutput.verdict is one of: approved, needs_rework, rejected."""
        _skip_without_claude_cli()

        repo, _branch = repo_with_branch
        settings = _make_settings()
        budget = _make_budget()
        composite = ReviewComposite(
            settings=settings,
            worktree=review_worktree,
            budget=budget,
        )

        output, cost = await composite.execute(
            node_context=_make_review_context(str(repo)),
            dag_run_id="component-run",
            node_id="review-node",
        )

        assert isinstance(output, ReviewOutput)
        assert output.verdict in (
            ReviewVerdict.APPROVED,
            ReviewVerdict.NEEDS_REWORK,
            ReviewVerdict.REJECTED,
        )

    async def test_review_findings_are_review_finding_objects(
        self, repo_with_branch: tuple[Path, str], review_worktree: WorktreeRecord
    ) -> None:
        """ReviewOutput.findings is a list of ReviewFinding objects."""
        _skip_without_claude_cli()

        repo, _branch = repo_with_branch
        settings = _make_settings()
        budget = _make_budget()
        composite = ReviewComposite(
            settings=settings,
            worktree=review_worktree,
            budget=budget,
        )

        output, cost = await composite.execute(
            node_context=_make_review_context(str(repo)),
            dag_run_id="component-run",
            node_id="review-node",
        )

        assert isinstance(output, ReviewOutput)
        assert isinstance(output.findings, list)
        for finding in output.findings:
            assert isinstance(finding, ReviewFinding)
            assert finding.severity in ("critical", "major", "minor")
            assert finding.description  # non-empty


# ---------------------------------------------------------------------------
# Test 4 — ReviewComposite cannot use write tools (no SDK call required)
# ---------------------------------------------------------------------------


class TestReviewPermissions:
    """Test 4: Verify reviewer_permissions() excludes write tools.

    The readonly=True flag on a WorktreeRecord signals that review_permissions()
    is used. This test verifies the permission set directly — no SDK call needed.
    We also confirm that the set of allowed tools for a reviewer excludes
    Edit, Write, and any Bash write patterns.
    """

    def test_reviewer_permissions_exclude_write_tools(self) -> None:
        """Test 4 (non-SDK): reviewer_permissions() must NOT include Edit or Write."""
        from agent_agent.agents.tools import reviewer_permissions

        perms = reviewer_permissions(worktree_root="/tmp/test-worktree")

        # Collect all SDK tool names the reviewer is allowed to call
        all_tool_names: set[str] = set()
        for perm in perms:
            all_tool_names.update(perm.sdk_tool_names)

        # Edit and Write must NOT be in the reviewer's tool set
        assert "Edit" not in all_tool_names, (
            "Reviewer must not have Edit permission (read-only composites)"
        )
        assert "Write" not in all_tool_names, (
            "Reviewer must not have Write permission (read-only composites)"
        )

    def test_reviewer_permissions_include_read_tools(self) -> None:
        """Test 4b (non-SDK): reviewer_permissions() must include Read, Glob, Grep."""
        from agent_agent.agents.tools import reviewer_permissions

        perms = reviewer_permissions(worktree_root="/tmp/test-worktree")

        all_tool_names: set[str] = set()
        for perm in perms:
            all_tool_names.update(perm.sdk_tool_names)

        # Read, Glob, Grep must be present for the reviewer to examine the code
        assert "Read" in all_tool_names
        assert "Glob" in all_tool_names
        assert "Grep" in all_tool_names

    def test_reviewer_permissions_bash_is_read_only(self) -> None:
        """Test 4c (non-SDK): reviewer Bash permission has validate_args set (read-only guard)."""
        from agent_agent.agents.tools import reviewer_permissions

        perms = reviewer_permissions(worktree_root="/tmp/test-worktree")

        bash_perms = [p for p in perms if "Bash" in p.sdk_tool_names]
        assert bash_perms, "Reviewer should have Bash permission for read-only commands"

        # The Bash permission must have a validate_args callback to enforce read-only
        for perm in bash_perms:
            assert perm.validate_args is not None, (
                "Reviewer Bash permission must have validate_args (read-only enforcement)"
            )
            # Verify the validator blocks write operations
            assert perm.validate_args("Bash", {"command": "git commit -m test"}) is False
            assert perm.validate_args("Bash", {"command": "ls -la"}) is True
