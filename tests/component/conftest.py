"""Component test fixtures.

tmp_git_repo(tmp_path): creates a real git repo with an initial commit.
Sets AGENT_AGENT_WORKTREE_BASE_DIR so WorktreeManager calls land at
/workspaces/.agent_agent_tests/worktrees/ — never inside the agent_agent git tree.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


WORKTREE_BASE = "/workspaces/.agent_agent_tests/worktrees"


@pytest.fixture(autouse=True)
def set_worktree_base_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure all WorktreeManager calls use the test base directory."""
    monkeypatch.setenv("AGENT_AGENT_WORKTREE_BASE_DIR", WORKTREE_BASE)


@pytest.fixture()
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one committed file in tmp_path.

    Returns the repo root Path.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"}

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    git("init", "-b", "main")
    git("config", "user.email", "t@t.com")
    git("config", "user.name", "Test")
    (repo / "README.md").write_text("# test repo\n")
    git("add", "README.md")
    git("commit", "-m", "initial commit")

    return repo
