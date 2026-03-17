"""Test fixtures for component and e2e tests.

github_test_repo: session-scoped fixture that creates a real GitHub repo + issue
  via the GitHub API. Yields (repo_full_name, issue_number). Teardown deletes
  the repo via API. Requires GITHUB_TOKEN env var.

target_repo / repo_with_remote: local-only fixtures (no GITHUB_TOKEN needed).
  Defined in tests/component/conftest.py.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        pytest.skip("GITHUB_TOKEN not set — skipping GitHub integration test")
    return token


@pytest.fixture(scope="session")
def github_test_repo() -> tuple[str, int]:
    """Create a temporary GitHub repo + issue. Yields (full_name, issue_number).

    Session-scoped: one repo per test session, deleted in finalizer.
    """
    token = _github_token()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Create repo
    repo_name = f"agent-agent-test-{uuid.uuid4().hex[:8]}"
    with httpx.Client(base_url="https://api.github.com", headers=headers) as client:
        resp = client.post("/user/repos", json={
            "name": repo_name,
            "auto_init": True,
            "private": True,
        })
        resp.raise_for_status()
        full_name = resp.json()["full_name"]

        # Create issue
        owner, repo = full_name.split("/")
        resp = client.post(f"/repos/{full_name}/issues", json={
            "title": "Test issue for agent-agent",
            "body": "This is a test issue created by the agent-agent test suite.",
        })
        resp.raise_for_status()
        issue_number = resp.json()["number"]

    yield full_name, issue_number

    # Teardown: delete the repo
    with httpx.Client(base_url="https://api.github.com", headers=headers) as client:
        client.delete(f"/repos/{full_name}")
