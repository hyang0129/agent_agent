"""Component tests for WorktreeManager — real git operations against tmp_git_repo."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_agent.worktree import WorktreeManager, WorktreeMisconfiguredError, from_settings

WORKTREE_BASE = "/workspaces/.agent_agent_tests/worktrees"


# ---------------------------------------------------------------------------
# from_settings guard
# ---------------------------------------------------------------------------


def test_from_settings_raises_if_base_dir_none() -> None:
    with pytest.raises(WorktreeMisconfiguredError):
        from_settings(None)


def test_from_settings_raises_if_base_dir_empty() -> None:
    with pytest.raises(WorktreeMisconfiguredError):
        from_settings("")


def test_from_settings_returns_manager() -> None:
    mgr = from_settings(WORKTREE_BASE)
    assert isinstance(mgr, WorktreeManager)


# ---------------------------------------------------------------------------
# Coding worktree
# ---------------------------------------------------------------------------


async def test_create_coding_worktree(tmp_git_repo: Path) -> None:
    mgr = WorktreeManager(WORKTREE_BASE)
    rec = await mgr.create_coding_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="abcd1234efgh",
        node_id="node-coding-1",
        n=1,
    )

    try:
        # Path must exist under WORKTREE_BASE
        assert rec.path.startswith(WORKTREE_BASE)
        assert Path(rec.path).is_dir()

        # Branch name must follow naming convention
        assert "abcd1234" in rec.branch
        assert "code" in rec.branch
        assert "1" in rec.branch

        # readonly=False for coding worktrees
        assert rec.readonly is False

        # Metadata
        assert rec.dag_run_id == "abcd1234efgh"
        assert rec.node_id == "node-coding-1"
    finally:
        await mgr.remove_worktree(str(tmp_git_repo), rec.path)


async def test_coding_worktree_path_under_base_dir(tmp_git_repo: Path) -> None:
    mgr = WorktreeManager(WORKTREE_BASE)
    rec = await mgr.create_coding_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="run-xyz-999",
        node_id="node-c",
        n=2,
    )
    try:
        assert rec.path.startswith(WORKTREE_BASE), (
            f"Expected path under {WORKTREE_BASE}, got {rec.path}"
        )
    finally:
        await mgr.remove_worktree(str(tmp_git_repo), rec.path)


async def test_coding_worktree_branch_is_new(tmp_git_repo: Path) -> None:
    """The coding worktree must create a new branch, not check out an existing one."""
    import subprocess

    mgr = WorktreeManager(WORKTREE_BASE)
    rec = await mgr.create_coding_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="run-branchtest",
        node_id="node-b",
        n=1,
    )
    try:
        result = subprocess.run(
            ["git", "branch", "--list", rec.branch],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
        )
        assert rec.branch in result.stdout
    finally:
        await mgr.remove_worktree(str(tmp_git_repo), rec.path)


# ---------------------------------------------------------------------------
# Review worktree
# ---------------------------------------------------------------------------


async def test_create_review_worktree(tmp_git_repo: Path) -> None:
    """Review worktree must check out an existing branch, readonly=True.

    Models real flow: coding worktree is created, branch pushed, coding worktree
    removed, then review worktree checks out the same branch.
    """
    mgr = WorktreeManager(WORKTREE_BASE)

    # Create coding worktree to produce a branch, then remove it (simulating push + done)
    coding_rec = await mgr.create_coding_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="run-review-test",
        node_id="node-code",
        n=1,
    )
    branch = coding_rec.branch
    await mgr.remove_worktree(str(tmp_git_repo), coding_rec.path)

    # Now the branch exists but is not checked out anywhere → review can check it out
    review_rec = await mgr.create_review_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="run-review-test",
        node_id="node-review",
        n=1,
        existing_branch=branch,
    )
    try:
        assert review_rec.path.startswith(WORKTREE_BASE)
        assert Path(review_rec.path).is_dir()
        assert review_rec.readonly is True
        assert review_rec.branch == branch
        assert "review" in review_rec.path
    finally:
        await mgr.remove_worktree(str(tmp_git_repo), review_rec.path)


async def test_review_worktree_readonly_flag_stored(tmp_git_repo: Path) -> None:
    mgr = WorktreeManager(WORKTREE_BASE)
    coding_rec = await mgr.create_coding_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="run-flag",
        node_id="nc",
        n=3,
    )
    assert coding_rec.readonly is False

    # Remove coding worktree so branch is free for review checkout
    await mgr.remove_worktree(str(tmp_git_repo), coding_rec.path)

    review_rec = await mgr.create_review_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="run-flag",
        node_id="nr",
        n=3,
        existing_branch=coding_rec.branch,
    )
    try:
        assert review_rec.readonly is True
    finally:
        await mgr.remove_worktree(str(tmp_git_repo), review_rec.path)


# ---------------------------------------------------------------------------
# Remove worktree
# ---------------------------------------------------------------------------


async def test_remove_worktree(tmp_git_repo: Path) -> None:
    mgr = WorktreeManager(WORKTREE_BASE)
    rec = await mgr.create_coding_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="run-remove",
        node_id="n",
        n=1,
    )
    assert Path(rec.path).is_dir()

    await mgr.remove_worktree(str(tmp_git_repo), rec.path)

    assert not Path(rec.path).exists()


# ---------------------------------------------------------------------------
# Branch isolation
# ---------------------------------------------------------------------------


async def test_worktrees_have_independent_branches(tmp_git_repo: Path) -> None:
    """Two coding worktrees for the same run must be on different branches."""
    mgr = WorktreeManager(WORKTREE_BASE)
    rec1 = await mgr.create_coding_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="run-iso",
        node_id="n1",
        n=1,
    )
    rec2 = await mgr.create_coding_worktree(
        repo_path=str(tmp_git_repo),
        dag_run_id="run-iso",
        node_id="n2",
        n=2,
    )
    try:
        assert rec1.branch != rec2.branch
        assert rec1.path != rec2.path
    finally:
        await mgr.remove_worktree(str(tmp_git_repo), rec1.path)
        await mgr.remove_worktree(str(tmp_git_repo), rec2.path)
