"""GitHub client component tests — requires GITHUB_TOKEN.

Marked @pytest.mark.github so they can be skipped: `pytest -m "not github"`.

Tests:
  - Issue fetch: assert IssueContext fields populated from real response
  - DRY_RUN_GITHUB guard: assert write methods raise when dry_run=True
"""
from __future__ import annotations

import pytest

from agent_agent.github.client import GitHubClient, parse_issue_url


@pytest.mark.github
class TestGitHubClientLive:
    """Live GitHub API tests — requires GITHUB_TOKEN and github_test_repo fixture."""

    async def test_fetch_issue_populates_context(
        self, github_test_repo: tuple[str, int]
    ) -> None:
        full_name, issue_number = github_test_repo
        owner, repo = full_name.split("/")

        async with GitHubClient(dry_run=True) as client:
            ctx = await client.fetch_issue(owner, repo, issue_number)

        assert ctx.url  # non-empty
        assert ctx.title == "Test issue for agent-agent"
        assert "test issue" in ctx.body.lower()

    async def test_dry_run_blocks_write(
        self, github_test_repo: tuple[str, int]
    ) -> None:
        full_name, _ = github_test_repo
        owner, repo = full_name.split("/")

        async with GitHubClient(dry_run=True) as client:
            with pytest.raises(RuntimeError, match="DRY_RUN_GITHUB is True"):
                await client.create_branch(owner, repo, "test-branch", "abc123")


class TestGitHubClientUnit:
    """Unit tests for the GitHub client — no GITHUB_TOKEN needed."""

    def test_parse_issue_url_valid(self) -> None:
        owner, repo, number = parse_issue_url(
            "https://github.com/anthropics/claude-code/issues/42"
        )
        assert owner == "anthropics"
        assert repo == "claude-code"
        assert number == 42

    def test_parse_issue_url_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            parse_issue_url("https://example.com/not-a-github-url")

    def test_branch_protection_rejects_main(self) -> None:
        with pytest.raises(ValueError, match="protected branch"):
            GitHubClient._check_branch_protection("main")

    def test_branch_protection_rejects_master(self) -> None:
        with pytest.raises(ValueError, match="protected branch"):
            GitHubClient._check_branch_protection("master")

    def test_branch_protection_rejects_production(self) -> None:
        with pytest.raises(ValueError, match="protected branch"):
            GitHubClient._check_branch_protection("production")

    def test_branch_protection_allows_feature(self) -> None:
        GitHubClient._check_branch_protection("agent/42/fix-bug")  # should not raise
