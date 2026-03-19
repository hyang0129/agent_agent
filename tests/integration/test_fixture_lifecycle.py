"""Tests for the fixture create/push/issue/teardown machinery.

Uses test_data/schema.json (not the main evaluation catalog) so that infrastructure
tests are isolated from changes to the agent evaluation fixture catalog.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from tests.integration.conftest import load_all_fixtures


_LIFECYCLE_FIXTURES = load_all_fixtures(Path(__file__).parent / "test_data")


@pytest.mark.integration
@pytest.mark.parametrize(
    "fixture_repo",
    _LIFECYCLE_FIXTURES[:1],  # first entry only; empty list = test not collected
    indirect=True,
    ids=lambda m: m.fixture_id,
)
def test_fixture_lifecycle(fixture_repo: tuple[str, int]) -> None:
    repo_url, issue_number = fixture_repo
    assert repo_url.startswith("https://github.com/")
    assert issue_number >= 1

    repo_path = repo_url.removeprefix("https://github.com/")

    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.get(f"https://api.github.com/repos/{repo_path}", headers=headers)
    assert r.status_code == 200

    r2 = httpx.get(f"https://api.github.com/repos/{repo_path}/issues/{issue_number}", headers=headers)
    assert r2.status_code == 200
