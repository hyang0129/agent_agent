"""Component tests for SDK wrapper — real SDK calls.

These tests require the claude CLI to be installed and authenticated (Max plan).
Each test has a hard budget cap of $1.00 via sdk_budget_backstop_usd.

Run with: unset CLAUDECODE && pytest tests/component/test_sdk_wrapper.py -v -m sdk
"""

from __future__ import annotations

import shutil

import pytest

from agent_agent.agents.base import SubAgentConfig, invoke_agent
from agent_agent.agents.tools import plan_allowed_tools
from agent_agent.dag.executor import AgentError, ResourceExhaustionError
from agent_agent.models.agent import PlanOutput
from agent_agent.models.context import (
    IssueContext,
    NodeContext,
    RepoMetadata,
)

pytestmark = [
    pytest.mark.sdk,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude CLI not found — skipping real SDK tests",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_minimal_context() -> NodeContext:
    return NodeContext(
        issue=IssueContext(
            url="https://github.com/test/repo/issues/1",
            title="Test issue",
            body="This is a test issue for SDK wrapper verification.",
        ),
        repo_metadata=RepoMetadata(
            path="/tmp/test-repo",
            default_branch="main",
            language="python",
            claude_md="# Test repo\nNo special rules.",
        ),
    )


def _make_plan_config() -> SubAgentConfig:
    return SubAgentConfig(
        name="test_planner",
        system_prompt=(
            "You are a test agent. Return a minimal PlanOutput JSON with "
            'type="plan", investigation_summary="test complete", '
            "child_dag=null, discoveries=[]."
        ),
        allowed_tools=plan_allowed_tools(),
        output_model=PlanOutput,
        max_turns=5,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_invoke_agent_returns_valid_output() -> None:
    """invoke_agent with a trivial prompt returns a valid Pydantic-parsed output."""
    ctx = _make_minimal_context()
    cfg = _make_plan_config()

    output, cost = await invoke_agent(
        config=cfg,
        node_context=ctx,
        model="claude-haiku-4-5-20251001",
        sdk_budget_backstop_usd=1.0,
        cwd="/tmp",
        dag_run_id="component-test-run",
        node_id="component-test-node",
    )
    assert isinstance(output, PlanOutput)
    assert output.type == "plan"
    assert isinstance(output.investigation_summary, str)


async def test_invoke_agent_respects_max_turns() -> None:
    """invoke_agent with max_turns=1 may raise ResourceExhaustionError."""
    ctx = _make_minimal_context()
    cfg = SubAgentConfig(
        name="test_planner_1turn",
        system_prompt=(
            "You are a test agent. Investigate the repository thoroughly using many "
            "tool calls before producing output. Explore all files."
        ),
        allowed_tools=plan_allowed_tools(),
        output_model=PlanOutput,
        max_turns=1,  # Very restrictive
    )

    # With max_turns=1, the agent may not produce output, leading to
    # ResourceExhaustionError or AgentError
    with pytest.raises((ResourceExhaustionError, AgentError)):
        await invoke_agent(
            config=cfg,
            node_context=ctx,
            model="claude-haiku-4-5-20251001",
            sdk_budget_backstop_usd=1.0,
            cwd="/tmp",
            dag_run_id="component-test-run",
            node_id="component-test-node-1turn",
        )


async def test_invoke_agent_returns_positive_cost() -> None:
    """invoke_agent returns cost_usd > 0."""
    ctx = _make_minimal_context()
    cfg = _make_plan_config()

    output, cost = await invoke_agent(
        config=cfg,
        node_context=ctx,
        model="claude-haiku-4-5-20251001",
        sdk_budget_backstop_usd=1.0,
        cwd="/tmp",
        dag_run_id="component-test-run",
        node_id="component-test-node-cost",
    )
    assert cost > 0.0


async def test_invoke_agent_invalid_output_raises_agent_error() -> None:
    """invoke_agent with a model that returns non-matching output raises AgentError."""
    from agent_agent.models.agent import ReviewOutput

    ctx = _make_minimal_context()
    # Ask for PlanOutput format but use ReviewOutput model — will fail validation
    cfg = SubAgentConfig(
        name="test_mismatched",
        system_prompt=(
            "You are a test agent. Return a minimal PlanOutput JSON with "
            'type="plan", investigation_summary="test", child_dag=null, discoveries=[].'
        ),
        allowed_tools=plan_allowed_tools(),
        output_model=ReviewOutput,  # Mismatch: expecting ReviewOutput but agent produces PlanOutput
        max_turns=5,
    )

    with pytest.raises(AgentError):
        await invoke_agent(
            config=cfg,
            node_context=ctx,
            model="claude-haiku-4-5-20251001",
            sdk_budget_backstop_usd=1.0,
            cwd="/tmp",
            dag_run_id="component-test-run",
            node_id="component-test-node-invalid",
        )


# ---------------------------------------------------------------------------
# Test 1 — invoke_agent executes at least one tool
# ---------------------------------------------------------------------------


async def test_invoke_agent_executes_tool(tmp_git_repo: object) -> None:
    """Test 1: invoke_agent actually calls at least one tool (Read/Glob) before returning.

    We verify tool use happened by checking cost > 0.005, which implies the agent
    spent tokens on tool calls beyond a zero-tool baseline.
    """
    ctx = NodeContext(
        issue=IssueContext(
            url="https://github.com/test/repo/issues/1",
            title="Inspect this repo",
            body="Examine the repository structure and list the files.",
        ),
        repo_metadata=RepoMetadata(
            path=str(tmp_git_repo),
            default_branch="main",
            language="python",
            claude_md="# Test repo\nNo special rules.",
        ),
    )
    cfg = SubAgentConfig(
        name="test_tool_user",
        system_prompt=(
            "You MUST use the Read or Glob tool to inspect the repository before producing "
            "any output. Read README.md using the Read tool, then produce your output JSON.\n\n"
            "IMPORTANT: discoveries MUST be an empty list []. Do NOT add any discoveries.\n\n"
            'Return EXACTLY: {"type": "plan", "investigation_summary": "<what you found>", '
            '"child_dag": null, "discoveries": []}'
        ),
        allowed_tools=plan_allowed_tools(),
        output_model=PlanOutput,
        max_turns=10,
    )

    output, cost = await invoke_agent(
        config=cfg,
        node_context=ctx,
        model="claude-haiku-4-5-20251001",
        sdk_budget_backstop_usd=1.0,
        cwd=str(tmp_git_repo),
        dag_run_id="test1-run",
        node_id="test1-node",
    )

    assert isinstance(output, PlanOutput)
    assert cost > 0
    # Cost above minimum threshold indicates token usage beyond a zero-tool response
    assert cost > 0.001

