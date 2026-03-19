"""Async GitHub client — issue read + guarded write operations.

Read operations:
  - fetch_issue: GET /repos/{owner}/{repo}/issues/{number} → IssueContext

Write operations (all guarded by DRY_RUN_GITHUB):
  - create_branch: stub for Phase 4 wiring
  - create_pull_request: open a PR from branch → base_branch

Branch protection check:
  - raises NotImplementedError in Phase 3 — no write operations exist yet
    to exercise it; real implementation wired in Phase 4
"""

from __future__ import annotations

import os
import re

import httpx
import structlog

from ..models.context import IssueContext

_logger = structlog.get_logger(__name__)

_ISSUE_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)"
)

_PROTECTED_BRANCHES = frozenset({"main", "master", "production"})


def parse_issue_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub issue URL into (owner, repo, issue_number).

    Raises ValueError if the URL does not match the expected pattern.
    """
    m = _ISSUE_URL_RE.match(url)
    if not m:
        raise ValueError(
            f"Invalid GitHub issue URL: {url!r}. "
            "Expected format: https://github.com/<owner>/<repo>/issues/<number>"
        )
    return m.group("owner"), m.group("repo"), int(m.group("number"))


class GitHubClient:
    """Async GitHub API client.

    All write methods are guarded by dry_run. When dry_run=True (the default),
    write methods raise RuntimeError instead of hitting the API.
    """

    def __init__(self, *, dry_run: bool = True, token: str | None = None) -> None:
        self.dry_run = dry_run
        self._token = token or os.environ.get("GITHUB_TOKEN", "")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> GitHubClient:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=headers,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GitHubClient not entered — use `async with` context manager")
        return self._client

    # ------------------------------------------------------------------
    # Read operations (no guard)
    # ------------------------------------------------------------------

    async def fetch_issue(self, owner: str, repo: str, number: int) -> IssueContext:
        """Fetch a GitHub issue and return an IssueContext.

        Raises httpx.HTTPStatusError on API errors.
        """
        resp = await self._http.get(f"/repos/{owner}/{repo}/issues/{number}")
        resp.raise_for_status()
        data = resp.json()

        _logger.info(
            "github.issue_fetched",
            owner=owner,
            repo=repo,
            number=number,
            title=data.get("title", ""),
        )

        return IssueContext(
            url=data.get("html_url", f"https://github.com/{owner}/{repo}/issues/{number}"),
            title=data.get("title", ""),
            body=data.get("body", "") or "",
        )

    # ------------------------------------------------------------------
    # Write operations (guarded by dry_run)
    # ------------------------------------------------------------------

    def _guard_write(self, operation: str) -> None:
        """Raise if dry_run is enabled. All write methods call this first."""
        if self.dry_run:
            raise RuntimeError(
                f"GitHubClient.{operation}() blocked: DRY_RUN_GITHUB is True. "
                "Set AGENT_AGENT_DRY_RUN_GITHUB=false to enable GitHub writes."
            )

    async def create_branch(self, owner: str, repo: str, branch: str, sha: str) -> None:
        """Create a branch on GitHub. Guarded by dry_run.

        Phase 3 stub — exercises the DRY_RUN guard path.
        Real implementation wired in Phase 4.
        """
        self._guard_write("create_branch")
        self._check_branch_protection(branch)
        resp = await self._http.post(
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        resp.raise_for_status()
        _logger.info("github.branch_created", owner=owner, repo=repo, branch=branch)

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> str:
        """Open a pull request from head → base. Guarded by dry_run.

        Returns the URL of the created PR.

        Raises RuntimeError if dry_run=True.
        Raises httpx.HTTPStatusError on API errors.
        """
        self._guard_write("create_pull_request")
        resp = await self._http.post(
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        pr_url: str = data.get("html_url", "")
        _logger.info(
            "github.pr_created",
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            pr_url=pr_url,
        )
        return pr_url

    # ------------------------------------------------------------------
    # Branch protection check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_branch_protection(branch: str) -> None:
        """Reject pushes to protected branch names.

        Raises ValueError if the branch is in the protection blocklist.
        """
        if branch in _PROTECTED_BRANCHES:
            raise ValueError(
                f"Refusing to operate on protected branch {branch!r}. "
                f"Protected branches: {sorted(_PROTECTED_BRANCHES)}"
            )
