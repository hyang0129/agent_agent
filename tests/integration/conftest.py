"""Integration test fixtures for ephemeral GitHub repo lifecycle.

Provides:
  - FixtureMeta: Pydantic v2 model for fixture catalog entries
  - load_all_fixtures(): reads all *.json files from a directory
  - _FixtureBotClient: synchronous httpx wrapper for bot account operations
  - session_id: session-scoped session identifier fixture
  - _session_cleanup: session-scoped autouse fixture for bulk teardown
  - fixture_repo: function-scoped fixture that creates an ephemeral GitHub repo

Requires AGENT_AGENT_FIXTURE_BOT_TOKEN env var; tests are skipped if absent.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
import warnings
from pathlib import Path
from typing import Generator, Literal

import httpx
import pytest
from pydantic import BaseModel


logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_API_VERSION = "2022-11-28"

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture-bot",
    "GIT_AUTHOR_EMAIL": "fixture-bot@localhost",
    "GIT_COMMITTER_NAME": "fixture-bot",
    "GIT_COMMITTER_EMAIL": "fixture-bot@localhost",
}


class FixtureMeta(BaseModel):
    fixture_id: str
    complexity: Literal["easy", "medium", "hard"]
    upstream: str
    base_sha: str
    license: str
    pr_number: int
    issue_number: int
    issue_title: str
    issue_body: str
    synthetic_issue: bool
    merged_from: list[int]


def load_all_fixtures(
    catalog_dir: Path = Path(__file__).parent.parent / "fixtures",
) -> list[FixtureMeta]:
    """Read all *.json files from catalog_dir and return a flat list of FixtureMeta."""
    fixtures: list[FixtureMeta] = []
    for json_file in sorted(catalog_dir.glob("*.json")):
        if json_file.name in ("conftest.json", "__init__.json"):
            continue
        records = json.loads(json_file.read_text())
        for record in records:
            fixtures.append(FixtureMeta.model_validate(record))
    return fixtures


class _FixtureBotClient:
    """Synchronous httpx.Client wrapper for bot account GitHub operations."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._username: str | None = None
        self._client = httpx.Client(
            base_url=_GITHUB_API,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": _GITHUB_API_VERSION,
            },
        )

    def __enter__(self) -> "_FixtureBotClient":
        return self

    def __exit__(self, *args: object) -> None:
        self._client.close()

    def get_bot_username(self) -> str:
        """Return the login of the authenticated bot account; cached after first call."""
        if self._username is None:
            resp = self._client.get("/user")
            resp.raise_for_status()
            self._username = resp.json()["login"]
        return self._username

    def create_repo(self, repo_name: str, description: str) -> str:
        """Create a public repo under the bot account. Returns '<username>/<repo_name>'."""
        resp = self._client.post(
            "/user/repos",
            json={
                "name": repo_name,
                "description": description,
                "private": False,
                "auto_init": False,
            },
        )
        resp.raise_for_status()
        return resp.json()["full_name"]

    def post_issue(self, full_repo: str, title: str, body: str) -> int:
        """Open an issue on full_repo. Returns the issue number."""
        resp = self._client.post(
            f"/repos/{full_repo}/issues",
            json={"title": title, "body": body},
        )
        resp.raise_for_status()
        return int(resp.json()["number"])

    def delete_repo(self, full_repo: str) -> None:
        """Delete a repo. Logs a warning on failure; does not raise."""
        try:
            resp = self._client.delete(f"/repos/{full_repo}")
            resp.raise_for_status()
        except Exception as exc:
            warnings.warn(f"Failed to delete repo {full_repo}: {exc}", stacklevel=2)

    def list_repos_with_prefix(self, prefix: str) -> list[str]:
        """Return full repo names on the bot account whose name starts with prefix."""
        username = self.get_bot_username()
        results: list[str] = []
        page = 1
        while True:
            resp = self._client.get(
                f"/users/{username}/repos",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for repo in batch:
                if repo["name"].startswith(prefix):
                    results.append(repo["full_name"])
            if len(batch) < 100:
                break
            page += 1
        return results


def pytest_configure(config: pytest.Config) -> None:
    """Register the integration marker."""
    config.addinivalue_line(
        "markers",
        "integration: end-to-end tests requiring AGENT_AGENT_FIXTURE_BOT_TOKEN + GITHUB_TOKEN + ANTHROPIC_API_KEY",
    )


@pytest.fixture(scope="session")
def session_id() -> str:
    """Return a short unique identifier for this pytest session."""
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="session", autouse=True)
def _session_cleanup(session_id: str) -> Generator[None, None, None]:
    """Session-level safety net: delete any bot repos matching aaf-<session_id>- on teardown.

    Does nothing if AGENT_AGENT_FIXTURE_BOT_TOKEN is absent (no repos were created).
    Token absence is enforced by fixture_repo, not here, so non-integration tests are unaffected.
    """
    yield

    token = os.environ.get("AGENT_AGENT_FIXTURE_BOT_TOKEN", "")
    if not token:
        return

    prefix = f"aaf-{session_id}-"
    with _FixtureBotClient(token) as client:
        stale_repos = client.list_repos_with_prefix(prefix)
        for full_repo in stale_repos:
            client.delete_repo(full_repo)


@pytest.fixture()
def fixture_repo(
    request: pytest.FixtureRequest,
    session_id: str,
    tmp_path: Path,
) -> Generator[tuple[str, int], None, None]:
    """Create an ephemeral GitHub repo from a FixtureMeta, push squashed history, open issue.

    Parametrize with indirect=True, passing a FixtureMeta as request.param.
    Yields (repo_url, issue_number). Deletes the repo on teardown.
    """
    token = os.environ.get("AGENT_AGENT_FIXTURE_BOT_TOKEN", "")
    if not token:
        pytest.skip("AGENT_AGENT_FIXTURE_BOT_TOKEN not set — skipping integration test")

    if not hasattr(request, "param"):
        pytest.fail("fixture_repo must be used with indirect=True")
    meta: FixtureMeta = request.param
    repo_name = f"aaf-{session_id}-{meta.fixture_id}"

    with _FixtureBotClient(token) as client:
        full_repo = client.create_repo(
            repo_name=repo_name,
            description=f"Ephemeral test fixture: {meta.fixture_id}",
        )

        def teardown() -> None:
            with _FixtureBotClient(token) as teardown_client:
                teardown_client.delete_repo(full_repo)

        request.addfinalizer(teardown)

        clone_dir = tmp_path / "clone"

        def _git(*args: str) -> None:
            subprocess.run(
                ["git", *args],
                check=True,
                capture_output=True,
                env=_GIT_ENV,
            )

        def _git_in(path: Path, *args: str) -> None:
            subprocess.run(
                ["git", "-C", str(path), *args],
                check=True,
                capture_output=True,
                env=_GIT_ENV,
            )

        _git("clone", meta.upstream, str(clone_dir))
        _git_in(clone_dir, "checkout", meta.base_sha)
        shutil.rmtree(clone_dir / ".git")
        _git_in(clone_dir, "init")
        _git_in(clone_dir, "add", ".")
        _git_in(clone_dir, "commit", "-m", "fixture: initial state")
        remote_url = f"https://x-access-token:{token}@github.com/{full_repo}"
        _git_in(clone_dir, "remote", "add", "origin", remote_url)
        _git_in(clone_dir, "push", "-u", "origin", "HEAD:main")

        issue_number = client.post_issue(
            full_repo=full_repo,
            title=meta.issue_title,
            body=meta.issue_body,
        )

    bot_username = full_repo.split("/")[0]
    repo_url = f"https://github.com/{bot_username}/{repo_name}"

    yield repo_url, issue_number
