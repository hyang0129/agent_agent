"""Mode 2 integrated policy tests — full orchestrator run against synthetic fixture repos.

Each fixture is a synthetic repo built from scratch with:
  - A CLAUDE.md containing a clear, checkable policy
  - A GitHub issue asking for a change that could either comply or violate the policy

The orchestrator runs Plan → Coding → Review (with PolicyReviewer in parallel).
The oracle is three-part:
  B1: orchestrator returns non-empty (branch_name, summary)
  B2: branch visible on remote (push succeeded)
  B3: DAGRun.status == COMPLETED
  B4: usd_used in range (real SDK calls were made)
  B5: policy_review.approved == True (PolicyReviewer approved the diff)
  B6: diff does NOT match violation_regex (agent avoided the forbidden pattern)
  B7: diff DOES match compliance_regex (agent used the prescribed pattern)

B5 is retrieved from the review node's NodeResult in the StateStore.
B6–B7 are checked against the git diff of the resolved branch vs. main.

Markers:
  integration — requires GITHUB_TOKEN
  sdk         — requires claude CLI auth (Max plan); unset CLAUDECODE before running

Run all Mode 2 tests:
    set -a && source .env && set +a
    unset CLAUDECODE
    pytest tests/integration/test_policy_integrated.py -m "integration and sdk" -v
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import structlog
import uvicorn

from agent_agent.config import Settings
from agent_agent.models.agent import ReviewOutput
from agent_agent.models.context import IssueContext
from agent_agent.models.dag import DAGRunStatus, NodeType
from agent_agent.orchestrator import Orchestrator
from agent_agent.state import StateStore
from tests.integration.conftest import (
    PolicyIntegratedFixtureMeta,
    load_policy_integrated_fixtures,
)


_logger = structlog.get_logger(__name__)

_POLICY_INTEGRATED_FIXTURES = load_policy_integrated_fixtures()

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "agent-agent-test",
    "GIT_AUTHOR_EMAIL": "agent-agent-test@localhost",
    "GIT_COMMITTER_NAME": "agent-agent-test",
    "GIT_COMMITTER_EMAIL": "agent-agent-test@localhost",
}

_ARTIFACT_BASE = Path("/workspaces/.agent_agent_tests/runs")


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _make_settings(worktree_base: str, port: int) -> Settings:
    return Settings(
        env="test",
        max_budget_usd=3.0,
        git_push_enabled=True,
        dry_run_github=False,
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
    param_id = re.sub(r".*\[(.+)\]$", r"\1", request.node.name)
    artifact_dir = _ARTIFACT_BASE / session_id / param_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


async def _run_policy_integrated_test(
    policy_fixture_repo: tuple[str, int],
    meta: PolicyIntegratedFixtureMeta,
    run_artifact_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full orchestrator run with three-part policy oracle.

    Assertions:
    B1. orchestrator.run() returns non-empty (branch_name, summary)
    B2. branch_name starts with 'agent'
    B3. Branch is visible on the ephemeral GitHub remote (push succeeded)
    B4. DAGRun.status == COMPLETED
    B5. usd_used is between 0 and max_budget_usd (real SDK calls were made)
    B6. policy_review.approved == True (from the review node's NodeResult)
    B7. diff does NOT match violation_regex
    B8. diff DOES match compliance_regex
    """
    repo_url, issue_number = policy_fixture_repo
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

    settings = _make_settings(
        worktree_base=worktree_base,
        port=_get_free_port(),
    )

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

        # B1: non-empty return values
        assert branch_name, f"empty branch_name; summary={summary!r}"
        assert summary, f"empty summary; branch={branch_name!r}"

        # B2: branch naming convention
        assert branch_name.startswith("agent"), f"branch {branch_name!r} must start with 'agent'"

        # B3: branch visible on the GitHub remote
        remote_heads = subprocess.run(
            ["git", "ls-remote", "--heads", repo_url],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert any(
            segment in remote_heads for segment in [branch_name, branch_name.split("/")[-1]]
        ), f"Branch {branch_name!r} not found on remote.\nRemote heads:\n{remote_heads}"

        # B4: DAGRun completed
        assert run_id_holder, "create_dag_run was never called"
        dag_run = await state_store.get_dag_run(run_id_holder[0])
        assert dag_run is not None
        assert dag_run.status == DAGRunStatus.COMPLETED, (
            f"Expected COMPLETED, got {dag_run.status}"
        )

        # B5: real spend
        assert 0 < dag_run.usd_used <= settings.max_budget_usd, (
            f"usd_used={dag_run.usd_used!r} out of range (0, {settings.max_budget_usd}]"
        )

        # B6: Final policy review must be approved.
        # Sort review nodes by level so we evaluate only the last rework level's
        # verdict as the canonical outcome.  Earlier levels may be rejected (that
        # is the expected first step of a rework cycle) — asserting all() would
        # wrongly fail when rework occurs.
        nodes = await state_store.list_dag_nodes(run_id_holder[0])
        review_nodes = [n for n in nodes if n.type == NodeType.REVIEW]
        assert review_nodes, "No REVIEW nodes found in DAGRun"

        final_level = max(n.level for n in review_nodes)
        final_level_reviews = [n for n in review_nodes if n.level == final_level]

        final_policy_approved: list[bool] = []
        for review_node in final_level_reviews:
            node_result = await state_store.get_node_result(review_node.id)
            if node_result is None:
                continue
            output = node_result.output
            if isinstance(output, ReviewOutput) and output.policy_review is not None:
                final_policy_approved.append(output.policy_review.approved)

        assert final_policy_approved, (
            "No final-level review nodes had a populated policy_review field"
        )
        assert all(final_policy_approved), (
            f"Final PolicyReviewer rejected the diff: approved={final_policy_approved}"
        )

        # Rework cycle detection and verification.
        # If multiple review levels exist, the rework cycle occurred and we must
        # verify steps 1 (non-compliant first attempt) and 2 (policy rejection).
        review_levels = sorted(set(n.level for n in review_nodes))
        rework_cycle_occurred = len(review_levels) > 1

        if rework_cycle_occurred:
            # B6a: At least one intermediate review must have been rejected
            # (step 1: agent built non-compliant solution; step 2: policy reviewer flagged it)
            intermediate_reviews = [n for n in review_nodes if n.level < final_level]
            rejected_intermediate: list[str] = []
            for rn in intermediate_reviews:
                rn_result = await state_store.get_node_result(rn.id)
                if rn_result is None:
                    continue
                rn_output = rn_result.output
                if isinstance(rn_output, ReviewOutput) and rn_output.policy_review is not None:
                    if not rn_output.policy_review.approved:
                        rejected_intermediate.append(rn.id)
            assert rejected_intermediate, (
                "Rework cycle detected (multiple review levels) but no intermediate "
                "policy rejection found — expected at least one rejected intermediate review"
            )
            # B6b: Nested DAG was spawned (already verified by rework_cycle_occurred)
            _logger.info(
                "policy_integrated.rework_cycle_verified",
                fixture_id=meta.fixture_id,
                review_levels=review_levels,
                intermediate_rejected=rejected_intermediate,
            )
        elif meta.expect_rework_cycle:
            # Fixture expected a rework cycle but agent complied on first try.
            # This is acceptable — LLM behavior is non-deterministic.  Emit a
            # warning so CI surfaces it without failing the run.
            warnings.warn(
                f"Fixture {meta.fixture_id!r} sets expect_rework_cycle=True, but the "
                "agent produced a compliant solution on the first attempt.  "
                "The rework cycle mechanism was not exercised by this run.",
                stacklevel=2,
            )

        # B7–B8: diff-based checks against committed branch
        # Fetch and check the diff of the resolved branch vs. main
        subprocess.run(
            ["git", "fetch", "--all"],
            cwd=clone_dir,
            check=True,
            capture_output=True,
            env=_GIT_ENV,
        )
        branch_short = branch_name.split("/")[-1]
        diff_result = subprocess.run(
            ["git", "diff", "origin/main", f"origin/{branch_short}"],
            cwd=clone_dir,
            capture_output=True,
            text=True,
            env=_GIT_ENV,
        )
        if diff_result.returncode != 0 or not diff_result.stdout:
            # Try without origin/ prefix
            diff_result = subprocess.run(
                ["git", "diff", "main", branch_name],
                cwd=clone_dir,
                capture_output=True,
                text=True,
                env=_GIT_ENV,
            )
        diff_text = diff_result.stdout

        # B7: violation pattern must NOT appear in added lines of the diff
        added_lines = "\n".join(
            line[1:] for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
        violation_match = re.search(meta.violation_regex, added_lines)
        assert violation_match is None, (
            f"Diff contains forbidden pattern {meta.violation_regex!r}: "
            f"matched {violation_match.group()!r} in added lines"
        )

        # B8: compliance pattern MUST appear in added lines of the diff
        compliance_match = re.search(meta.compliance_regex, added_lines)
        assert compliance_match is not None, (
            f"Diff does not contain required pattern {meta.compliance_regex!r}.\n"
            f"Added lines:\n{added_lines[:2000]}"
        )

    finally:
        await state_store.close()


@pytest.mark.integration
@pytest.mark.sdk
@pytest.mark.timeout(1200)  # 20 min — synthetic repos are small; Plan+Coding+Review pipeline
@pytest.mark.parametrize(
    "policy_fixture_repo",
    _POLICY_INTEGRATED_FIXTURES,
    indirect=True,
    ids=lambda m: m.fixture_id,
)
async def test_policy_integrated(
    policy_fixture_repo: tuple[str, int],
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    run_artifact_dir: Path,
) -> None:
    """Mode 2 oracle: full orchestrator run respects committed CLAUDE.md policy.

    Each fixture provides a synthetic repo with a committed policy in CLAUDE.md
    and an issue that could be resolved compliantly or with a policy violation.
    The test asserts that the orchestrator resolves the issue in a compliant way.
    """
    # Look up the meta from the parametrize list by fixture_id
    fixture_id = re.sub(r".*\[(.+)\]$", r"\1", request.node.name)
    meta_list = [m for m in _POLICY_INTEGRATED_FIXTURES if m.fixture_id == fixture_id]
    assert meta_list, f"No PolicyIntegratedFixtureMeta found for fixture_id={fixture_id!r}"
    meta = meta_list[0]

    await _run_policy_integrated_test(
        policy_fixture_repo=policy_fixture_repo,
        meta=meta,
        run_artifact_dir=run_artifact_dir,
        monkeypatch=monkeypatch,
    )
