"""Component test fixtures.

tmp_git_repo(tmp_path): creates a real git repo with an initial commit.
Sets AGENT_AGENT_WORKTREE_BASE_DIR so WorktreeManager calls land at
/workspaces/.agent_agent_tests/worktrees/ — never inside the agent_agent git tree.

github_test_repo: session-scoped fixture for live GitHub API tests (requires GITHUB_TOKEN).
"""
from __future__ import annotations

import os
import subprocess
import uuid
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


# ------------------------------------------------------------------
# GitHub test repo (session-scoped, requires GITHUB_TOKEN)
# ------------------------------------------------------------------


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        pytest.skip("GITHUB_TOKEN not set — skipping GitHub integration test")
    return token


@pytest.fixture(scope="session")
def github_test_repo() -> tuple[str, int]:
    """Create a temporary GitHub repo + issue. Yields (full_name, issue_number).

    Session-scoped: one repo per test session, deleted in finalizer.
    Requires GITHUB_TOKEN env var.
    """
    import httpx

    token = _github_token()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    repo_name = f"agent-agent-test-{uuid.uuid4().hex[:8]}"
    with httpx.Client(base_url="https://api.github.com", headers=headers) as client:
        resp = client.post("/user/repos", json={
            "name": repo_name,
            "auto_init": True,
            "private": True,
        })
        resp.raise_for_status()
        full_name = resp.json()["full_name"]

        resp = client.post(f"/repos/{full_name}/issues", json={
            "title": "Test issue for agent-agent",
            "body": "This is a test issue created by the agent-agent test suite.",
        })
        resp.raise_for_status()
        issue_number = resp.json()["number"]

    yield full_name, issue_number  # type: ignore[misc]

    with httpx.Client(base_url="https://api.github.com", headers=headers) as client:
        client.delete(f"/repos/{full_name}")
