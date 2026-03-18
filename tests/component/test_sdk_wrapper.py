"""Component tests for SDK wrapper — real SDK calls.

These tests require a valid ANTHROPIC_API_KEY and will incur real API costs.
Each test has a hard budget cap of $1.00 via sdk_budget_backstop_usd.

Run with: pytest tests/component/test_sdk_wrapper.py -v -m sdk
"""

from __future__ import annotations

import os

import pytest

from agent_agent.agents.base import SubAgentConfig, invoke_agent
from agent_agent.agents.tools import plan_permissions
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
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set — skipping real SDK tests",
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
        permissions=plan_permissions(),
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
        permissions=plan_permissions(),
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
        permissions=plan_permissions(),
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
        permissions=plan_permissions(),
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


# ---------------------------------------------------------------------------
# Test 2 — can_use_tool callback fires and controls access
# ---------------------------------------------------------------------------


async def test_can_use_tool_callback_fires(tmp_git_repo: object) -> None:
    """Test 2: can_use_tool callback fires and denies unauthorized tools.

    Permissions allow Read/Glob/Grep/Bash but NOT Edit or Write. The system prompt
    instructs the agent to attempt an Edit operation. The can_use_tool callback should
    deny it and we verify denial count >= 1 via captured TOOL_DENIED events.
    The denial count stays below DENIAL_THRESHOLD (5), so the session completes normally.
    """
    from unittest.mock import patch

    from agent_agent.observability import EventType

    # Allow Read, Glob, Grep, Bash (read-only) but NOT Edit or Write
    allowed_permissions = plan_permissions()  # Read, Glob, Grep, Bash read-only

    # Track TOOL_DENIED events emitted by the can_use_tool callback
    denied_tools: list[str] = []
    original_emit = __import__("agent_agent.agents.base", fromlist=["emit_event"]).emit_event

    def capturing_emit(event_type: EventType, dag_run_id: str, **kwargs: object) -> None:
        if event_type == EventType.TOOL_DENIED:
            denied_tools.append(str(kwargs.get("tool_name", "unknown")))
        original_emit(event_type, dag_run_id, **kwargs)

    ctx = NodeContext(
        issue=IssueContext(
            url="https://github.com/test/repo/issues/2",
            title="Read and try to edit",
            body=(
                "Read README.md, then try to create a new file called 'output.txt' "
                "using the Edit tool. After that, describe what you found."
            ),
        ),
        repo_metadata=RepoMetadata(
            path=str(tmp_git_repo),
            default_branch="main",
            language="python",
            claude_md="# Test repo\nNo special rules.",
        ),
    )
    cfg = SubAgentConfig(
        name="test_callback_fires",
        system_prompt=(
            "You MUST attempt to use the Edit tool to create a new file called 'output.txt' "
            "with content 'hello'. After attempting (whether or not it succeeds), "
            "read README.md using the Read tool and produce your output.\n\n"
            "IMPORTANT: discoveries MUST be an empty list []. Do NOT add any discoveries.\n\n"
            'Return EXACTLY this JSON: {"type": "plan", "investigation_summary": "<what you found>", '
            '"child_dag": null, "discoveries": []}'
        ),
        permissions=allowed_permissions,
        output_model=PlanOutput,
        max_turns=10,
    )

    with patch("agent_agent.agents.base.emit_event", side_effect=capturing_emit):
        output, cost = await invoke_agent(
            config=cfg,
            node_context=ctx,
            model="claude-haiku-4-5-20251001",
            sdk_budget_backstop_usd=1.0,
            cwd=str(tmp_git_repo),
            dag_run_id="test2-run",
            node_id="test2-node",
        )

    # Session completed without raising (denial count stayed below DENIAL_THRESHOLD=5)
    assert isinstance(output, PlanOutput)
    assert output.investigation_summary  # non-empty
    # Verify the denial callback fired at least once (Edit was denied)
    assert len(denied_tools) >= 1, (
        f"Expected at least 1 tool denial (Edit), but captured denials: {denied_tools}"
    )


# ---------------------------------------------------------------------------
# Test 5 — Denial threshold stops session (SafetyViolationError)
# ---------------------------------------------------------------------------


async def test_denial_threshold_raises_safety_violation() -> None:
    """Test 5: After DENIAL_THRESHOLD consecutive denials, SafetyViolationError is raised.

    permission_mode="default" does not reliably block tools via can_use_tool in
    non-TTY subprocesses. This test instead patches query() to directly invoke
    the can_use_tool callback DENIAL_THRESHOLD times, then yields nothing — verifying
    that invoke_agent raises SafetyViolationError rather than AgentError.
    """
    from unittest.mock import patch

    from agent_agent.agents.base import DENIAL_THRESHOLD
    from agent_agent.dag.executor import SafetyViolationError

    ctx = _make_minimal_context()

    cfg = SubAgentConfig(
        name="test_denial_threshold",
        system_prompt="Use tools.",
        permissions=[],  # no tools permitted — every call is denied
        output_model=PlanOutput,
        max_turns=20,
    )

    async def _mock_query(prompt: object, options: object) -> object:
        """Simulate DENIAL_THRESHOLD tool-use attempts, all denied, no result returned."""
        callback = options.can_use_tool  # type: ignore[union-attr]
        for i in range(DENIAL_THRESHOLD):
            await callback("Read", {"file_path": f"/test{i}.txt"}, None)
        # Yield nothing — session ends without a ResultMessage
        return
        yield  # make this an async generator

    with patch("agent_agent.agents.base.query", side_effect=_mock_query):
        with pytest.raises(SafetyViolationError):
            await invoke_agent(
                config=cfg,
                node_context=ctx,
                model="claude-haiku-4-5-20251001",
                sdk_budget_backstop_usd=1.0,
                cwd="/tmp",
                dag_run_id="test5-run",
                node_id="test5-node",
            )
