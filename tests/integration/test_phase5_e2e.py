"""Phase 5 end-to-end integration tests against real GitHub fixture repos.

Tests the full orchestrator (use_composites=True) across all complexity tiers.
Parametrized over the approved fixture catalog at tests/fixtures/*.json.

Each test:
  1. fixture_repo creates an ephemeral GitHub repo at the pre-fix base_sha + posts the issue.
  2. We clone the ephemeral repo locally (with auth token for push access).
  3. Orchestrator runs Plan → Coding → Review against the local clone.
  4. Assertions: branch pushed to GitHub, DAGRun COMPLETED, real API spend occurred.

Markers:
  integration — requires GITHUB_TOKEN
  sdk         — requires claude CLI auth (Max plan); unset CLAUDECODE before running

Run all tiers:
    set -a && source .env && set +a
    unset CLAUDECODE
    pytest tests/integration/test_phase5_e2e.py -m "integration and sdk" -v

Run by tier:
    pytest tests/integration/test_phase5_e2e.py -k easy -m "integration and sdk" -v
    pytest tests/integration/test_phase5_e2e.py -k medium -m "integration and sdk" -v -n 2
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import uvicorn

from agent_agent.config import Settings
from agent_agent.models.context import IssueContext
from agent_agent.models.dag import DAGRunStatus
from agent_agent.orchestrator import Orchestrator
from agent_agent.state import StateStore
from tests.integration.conftest import load_all_fixtures


_EASY_FIXTURES = [f for f in load_all_fixtures() if f.complexity == "easy"]
_MEDIUM_FIXTURES = [f for f in load_all_fixtures() if f.complexity == "medium"]

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "agent-agent-test",
    "GIT_AUTHOR_EMAIL": "agent-agent-test@localhost",
    "GIT_COMMITTER_NAME": "agent-agent-test",
    "GIT_COMMITTER_EMAIL": "agent-agent-test@localhost",
}

_ARTIFACT_BASE = Path("/workspaces/.agent_agent_tests/runs")


def _get_free_port() -> int:
    """Return an ephemeral free TCP port (uvicorn is mocked so it is never bound)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _make_settings(worktree_base: str, port: int) -> Settings:
    return Settings(
        env="test",
        max_budget_usd=3.0,
        git_push_enabled=True,
        dry_run_github=False,  # enable real PR creation on the ephemeral repo
        worktree_base_dir=worktree_base,
        port=port,
        plan_use_thinking=False,
        plan_thinking_budget_tokens=0,
        plan_max_turns=100,
        programmer_max_turns=100,
        tester_max_turns=100,
        debugger_max_turns=100,
        reviewer_max_turns=100,
    )


def _fetch_issue(repo_path: str, issue_number: int) -> dict[str, str]:
    """Fetch the posted issue's title + body from the GitHub API.

    Uses GITHUB_TOKEN from the environment (injected by devcontainer).
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.get(
        f"https://api.github.com/repos/{repo_path}/issues/{issue_number}",
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return {"title": data["title"], "body": data.get("body") or ""}


@pytest.fixture()
def run_artifact_dir(
    request: pytest.FixtureRequest,
    session_id: str,
) -> Path:
    """Return a persistent per-test artifact directory under /workspaces/.agent_agent_tests/runs/.

    Path: /workspaces/.agent_agent_tests/runs/<session_id>/<fixture_id>/
    Never cleaned up — survives the pytest session for post-run inspection.
    With xdist, session_id is per-worker (each worker has its own UUID).
    """
    param_id = re.sub(r".*\[(.+)\]$", r"\1", request.node.name)
    artifact_dir = _ARTIFACT_BASE / session_id / param_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


@pytest.mark.integration
@pytest.mark.sdk
@pytest.mark.timeout(1200)  # 20 min — full Plan+Coding+Review pipeline can take 10-15 min
@pytest.mark.parametrize(
    "fixture_repo",
    _EASY_FIXTURES,
    indirect=True,
    ids=lambda m: m.fixture_id,
)
async def test_phase5_schema_issue_resolution(
    fixture_repo: tuple[str, int],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_artifact_dir: Path,
) -> None:
    """Phase 5 gate: resolve a real schema issue end-to-end.

    Assertions:
    A1. orchestrator.run() returns non-empty (branch_name, summary)
    A2. branch_name starts with 'agent' (e.g. 'agent-<run_id[:8]>-code-1')
    A3. Branch is visible on the ephemeral GitHub remote (push succeeded)
    A4. DAGRun.status == COMPLETED
    A5. DAGRun.usd_used is between 0 and max_budget_usd (real SDK calls were made)
    """
    repo_url, issue_number = fixture_repo
    repo_path_part = repo_url.removeprefix("https://github.com/")

    # Clone using plain HTTPS — the devcontainer credential helper handles auth.
    clone_dir = run_artifact_dir / "repo"
    subprocess.run(
        ["git", "clone", repo_url, str(clone_dir)],
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    for cfg_key, cfg_val in [("user.email", "agent@agent-agent"), ("user.name", "Agent Agent")]:
        subprocess.run(
            ["git", "config", cfg_key, cfg_val],
            cwd=clone_dir,
            check=True,
            capture_output=True,
        )

    # Fetch the issue that fixture_repo posted onto the ephemeral repo.
    issue_data = _fetch_issue(repo_path_part, issue_number)
    issue_url = f"{repo_url}/issues/{issue_number}"
    issue_ctx = IssueContext(
        url=issue_url,
        title=issue_data["title"],
        body=issue_data["body"],
    )

    claude_md = (clone_dir / "CLAUDE.md").read_text() if (clone_dir / "CLAUDE.md").exists() else ""

    worktree_base = str(run_artifact_dir / "worktrees")
    Path(worktree_base).mkdir(parents=True, exist_ok=True)

    settings = _make_settings(
        worktree_base=worktree_base,
        port=_get_free_port(),
    )

    state_store = StateStore(str(run_artifact_dir / "state.db"))
    await state_store.init()

    # Capture run_id without having to list all dag runs.
    run_id_holder: list[str] = []
    _orig_create = state_store.create_dag_run

    async def _capturing_create(run: Any) -> None:
        run_id_holder.append(run.id)
        return await _orig_create(run)

    state_store.create_dag_run = _capturing_create  # type: ignore[method-assign]

    # Mock uvicorn server to avoid port binding in tests (not under test here).
    # Patch at fixture scope so the mock stays active through the finally-cleanup
    # inside orchestrator._start_server (server.should_exit = True).
    mock_server = MagicMock()
    mock_server.should_exit = False
    mock_server.serve = AsyncMock(return_value=None)
    monkeypatch.setattr(uvicorn, "Server", lambda _cfg: mock_server)

    try:
        orchestrator = Orchestrator(
            settings=settings,
            repo_path=str(clone_dir),
            claude_md_content=claude_md,
            issue_url=issue_url,
            state_store=state_store,
            use_composites=True,
            issue_context=issue_ctx,
        )
        branch_name, summary = await orchestrator.run()

        # A1: non-empty return values
        assert branch_name, f"empty branch_name; summary={summary!r}"
        assert summary, f"empty summary; branch={branch_name!r}"

        # A2: branch naming convention
        assert branch_name.startswith("agent"), f"branch {branch_name!r} must start with 'agent'"

        # A3: branch visible on the GitHub remote (push succeeded)
        remote_heads = subprocess.run(
            ["git", "ls-remote", "--heads", repo_url],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert any(
            segment in remote_heads for segment in [branch_name, branch_name.split("/")[-1]]
        ), f"Branch {branch_name!r} not found on remote.\nRemote heads:\n{remote_heads}"

        # A4: DAGRun completed cleanly
        assert run_id_holder, "create_dag_run was never called"
        dag_run = await state_store.get_dag_run(run_id_holder[0])
        assert dag_run is not None
        assert dag_run.status == DAGRunStatus.COMPLETED, f"Expected COMPLETED, got {dag_run.status}"

        # A5: real spend — not a stub run
        assert 0 < dag_run.usd_used <= settings.max_budget_usd, (
            f"usd_used={dag_run.usd_used!r} out of range (0, {settings.max_budget_usd}]"
        )

        # A6: PR was opened on the ephemeral GitHub remote
        token = os.environ.get("GITHUB_TOKEN", "")
        headers = {"X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        prs = httpx.get(
            f"https://api.github.com/repos/{repo_path_part}/pulls",
            params={"state": "open", "head": branch_name},
            headers=headers,
            timeout=30,
        )
        prs.raise_for_status()
        assert prs.json(), f"No open PR found for branch {branch_name!r} on {repo_path_part}"

    finally:
        await state_store.close()


@pytest.mark.integration
@pytest.mark.sdk
@pytest.mark.timeout(1800)  # 30 min — medium fixtures require reading 2-4 files, more turns
@pytest.mark.parametrize(
    "fixture_repo",
    _MEDIUM_FIXTURES,
    indirect=True,
    ids=lambda m: m.fixture_id,
)
async def test_phase5_medium_issue_resolution(
    fixture_repo: tuple[str, int],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_artifact_dir: Path,
) -> None:
    """Phase 5 medium-tier gate: resolve a medium-complexity issue end-to-end.

    Same assertions as the easy-tier test; separated to apply a longer timeout
    appropriate for issues requiring 3-5 file changes and 30-100 LOC delta.
    """
    repo_url, issue_number = fixture_repo
    repo_path_part = repo_url.removeprefix("https://github.com/")

    clone_dir = run_artifact_dir / "repo"
    subprocess.run(
        ["git", "clone", repo_url, str(clone_dir)],
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    for cfg_key, cfg_val in [("user.email", "agent@agent-agent"), ("user.name", "Agent Agent")]:
        subprocess.run(
            ["git", "config", cfg_key, cfg_val],
            cwd=clone_dir,
            check=True,
            capture_output=True,
        )

    issue_data = _fetch_issue(repo_path_part, issue_number)
    issue_url = f"{repo_url}/issues/{issue_number}"
    issue_ctx = IssueContext(
        url=issue_url,
        title=issue_data["title"],
        body=issue_data["body"],
    )

    claude_md = (clone_dir / "CLAUDE.md").read_text() if (clone_dir / "CLAUDE.md").exists() else ""

    worktree_base = str(run_artifact_dir / "worktrees")
    Path(worktree_base).mkdir(parents=True, exist_ok=True)

    settings = _make_settings(worktree_base=worktree_base, port=_get_free_port())

    state_store = StateStore(str(run_artifact_dir / "state.db"))
    await state_store.init()

    run_id_holder: list[str] = []
    _orig_create = state_store.create_dag_run

    async def _capturing_create(run: Any) -> None:
        run_id_holder.append(run.id)
        return await _orig_create(run)

    state_store.create_dag_run = _capturing_create  # type: ignore[method-assign]

    mock_server = MagicMock()
    mock_server.should_exit = False
    mock_server.serve = AsyncMock(return_value=None)
    monkeypatch.setattr(uvicorn, "Server", lambda _cfg: mock_server)

    try:
        orchestrator = Orchestrator(
            settings=settings,
            repo_path=str(clone_dir),
            claude_md_content=claude_md,
            issue_url=issue_url,
            state_store=state_store,
            use_composites=True,
            issue_context=issue_ctx,
        )
        branch_name, summary = await orchestrator.run()

        assert branch_name, f"empty branch_name; summary={summary!r}"
        assert summary, f"empty summary; branch={branch_name!r}"
        assert branch_name.startswith("agent"), f"branch {branch_name!r} must start with 'agent'"

        remote_heads = subprocess.run(
            ["git", "ls-remote", "--heads", repo_url],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert any(
            segment in remote_heads for segment in [branch_name, branch_name.split("/")[-1]]
        ), f"Branch {branch_name!r} not found on remote.\nRemote heads:\n{remote_heads}"

        assert run_id_holder, "create_dag_run was never called"
        dag_run = await state_store.get_dag_run(run_id_holder[0])
        assert dag_run is not None
        assert dag_run.status == DAGRunStatus.COMPLETED, f"Expected COMPLETED, got {dag_run.status}"
        assert 0 < dag_run.usd_used <= settings.max_budget_usd, (
            f"usd_used={dag_run.usd_used!r} out of range (0, {settings.max_budget_usd}]"
        )

        token = os.environ.get("GITHUB_TOKEN", "")
        headers = {"X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        prs = httpx.get(
            f"https://api.github.com/repos/{repo_path_part}/pulls",
            params={"state": "open", "head": branch_name},
            headers=headers,
            timeout=30,
        )
        prs.raise_for_status()
        assert prs.json(), f"No open PR found for branch {branch_name!r} on {repo_path_part}"

    finally:
        await state_store.close()
