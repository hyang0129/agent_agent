"""Unit tests for agents/base.py — serialization, options, error mapping."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_agent.agents.base import (
    SubAgentConfig,
    compute_sdk_backstop,
    invoke_agent,
    serialize_node_context,
)
from agent_agent.agents.tools import (
    debugger_allowed_tools,
    plan_allowed_tools,
    programmer_allowed_tools,
    reviewer_allowed_tools,
    test_designer_allowed_tools,
    test_executor_allowed_tools as te_allowed_tools,
)
from agent_agent.dag.executor import (
    AgentError,
    DeterministicError,
    ResourceExhaustionError,
    TransientError,
)
from agent_agent.models.agent import (
    FileMapping,
    PlanOutput,
)
from agent_agent.models.context import (
    AncestorContext,
    DiscoveryRecord,
    IssueContext,
    NodeContext,
    RepoMetadata,
    SharedContextView,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issue() -> IssueContext:
    return IssueContext(
        url="https://github.com/org/repo/issues/42",
        title="Fix the widget",
        body="The widget is broken.\n\nPlease fix it.",
    )


def _make_repo() -> RepoMetadata:
    return RepoMetadata(
        path="/workspaces/target",
        default_branch="main",
        language="python",
        framework="fastapi",
        claude_md="# Target CLAUDE.md\nRules here.",
    )


def _make_node_context(**kwargs: Any) -> NodeContext:
    defaults: dict[str, Any] = {
        "issue": _make_issue(),
        "repo_metadata": _make_repo(),
        "parent_outputs": {},
        "ancestor_context": AncestorContext(),
        "shared_context_view": SharedContextView(),
    }
    defaults.update(kwargs)
    return NodeContext(**defaults)


def _make_config(**kwargs: Any) -> SubAgentConfig:
    defaults: dict[str, Any] = {
        "name": "test_agent",
        "system_prompt": "You are a test agent.",
        "allowed_tools": ["Read", "Glob", "Grep"],
        "output_model": PlanOutput,
        "max_turns": 10,
    }
    defaults.update(kwargs)
    return SubAgentConfig(**defaults)


def _make_result_message(
    *,
    subtype: str = "success",
    is_error: bool = False,
    structured_output: Any = None,
    result: str | None = None,
    total_cost_usd: float | None = 0.05,
    num_turns: int = 3,
) -> Any:
    """Create a real ResultMessage (dataclass from SDK)."""
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype=subtype,
        duration_ms=1000,
        duration_api_ms=800,
        is_error=is_error,
        num_turns=num_turns,
        session_id="test-session",
        stop_reason=None,
        total_cost_usd=total_cost_usd,
        usage=None,
        result=result,
        structured_output=structured_output,
    )


# ---------------------------------------------------------------------------
# serialize_node_context tests
# ---------------------------------------------------------------------------


class TestSerializeNodeContext:
    def test_includes_issue_verbatim(self) -> None:
        """Test 1: serialize_node_context() includes issue verbatim [P5.3]."""
        ctx = _make_node_context()
        result = serialize_node_context(ctx, "programmer")
        assert "## GitHub Issue" in result
        assert ctx.issue.url in result
        assert ctx.issue.title in result
        assert ctx.issue.body in result

    def test_includes_repo_metadata_verbatim(self) -> None:
        """Test 2: serialize_node_context() includes repo_metadata verbatim [P5.3]."""
        ctx = _make_node_context()
        result = serialize_node_context(ctx, "programmer")
        assert "## Repository" in result
        assert ctx.repo_metadata.path in result
        assert ctx.repo_metadata.default_branch in result
        assert "## Target Repo CLAUDE.md" in result
        assert ctx.repo_metadata.claude_md in result

    def test_includes_parent_outputs_as_json(self) -> None:
        """Test 3: serialize_node_context() includes parent_outputs as JSON."""
        plan = PlanOutput(investigation_summary="found the bug", child_dag=None)
        ctx = _make_node_context(parent_outputs={"node-plan": plan})
        result = serialize_node_context(ctx, "programmer")
        assert "## Upstream Outputs" in result
        assert "node-plan" in result
        assert "found the bug" in result
        # Should be valid JSON in code block
        assert "```json" in result

    def test_includes_shared_context_view_discoveries(self) -> None:
        """Test 4: serialize_node_context() includes shared_context_view discoveries."""
        now = datetime.now(timezone.utc)
        discovery = FileMapping(
            path="src/widget.py", description="Main widget module", confidence=0.9
        )
        record = DiscoveryRecord(discovery=discovery, source_node_id="node-1", timestamp=now)
        scv = SharedContextView(
            summary="Widget is broken",
            file_mappings=[record],
        )
        ctx = _make_node_context(shared_context_view=scv)
        result = serialize_node_context(ctx, "programmer")
        assert "## Shared Context" in result
        assert "Widget is broken" in result
        assert "File mappings" in result
        assert "node-1" in result


# ---------------------------------------------------------------------------
# invoke_agent options tests
# ---------------------------------------------------------------------------


class TestInvokeAgentOptions:
    """Verify that invoke_agent passes correct options to the SDK."""

    @patch("agent_agent.agents.base.query")
    @patch("agent_agent.agents.base.ClaudeAgentOptions")
    async def test_uses_print_mode_and_allowed_tools(
        self, mock_options_cls: MagicMock, mock_query: MagicMock
    ) -> None:
        """invoke_agent passes --print via extra_args and allowed_tools to ClaudeAgentOptions."""
        from claude_agent_sdk import ResultMessage

        result_msg = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="s",
            stop_reason=None,
            total_cost_usd=0.01,
            usage=None,
            result=None,
            structured_output={
                "type": "plan",
                "investigation_summary": "done",
                "child_dag": None,
                "discoveries": [],
            },
        )

        async def _gen(*args: Any, **kwargs: Any) -> Any:
            yield result_msg

        mock_query.side_effect = lambda *a, **kw: _gen(*a, **kw)
        mock_options_cls.return_value = MagicMock()

        cfg = _make_config(allowed_tools=["Read", "Glob", "Grep"])
        ctx = _make_node_context()
        await invoke_agent(cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1")

        _, kwargs = mock_options_cls.call_args
        assert kwargs["extra_args"] == {"print": None}
        assert kwargs["allowed_tools"] == ["Read", "Glob", "Grep"]
        assert kwargs["permission_mode"] is None


# ---------------------------------------------------------------------------
# Allowed tool function tests
# ---------------------------------------------------------------------------


class TestAllowedToolFunctions:
    def test_plan_allowed_tools(self) -> None:
        """plan_allowed_tools: read-only tools only [P3.3]."""
        tools = set(plan_allowed_tools())
        assert tools == {"Read", "Glob", "Grep", "Bash"}
        assert "Edit" not in tools
        assert "Write" not in tools

    def test_programmer_allowed_tools(self) -> None:
        """programmer_allowed_tools: includes write tools [P3.3]."""
        tools = set(programmer_allowed_tools())
        assert tools == {"Read", "Glob", "Grep", "Write", "Edit", "Bash"}

    def test_test_designer_allowed_tools(self) -> None:
        """test_designer_allowed_tools: read-only [P3.3]."""
        tools = set(test_designer_allowed_tools())
        assert tools == {"Read", "Glob", "Grep", "Bash"}
        assert "Edit" not in tools
        assert "Write" not in tools

    def test_reviewer_allowed_tools(self) -> None:
        """reviewer_allowed_tools: read-only [P3.3]."""
        tools = set(reviewer_allowed_tools())
        assert tools == {"Read", "Glob", "Grep", "Bash"}
        assert "Edit" not in tools
        assert "Write" not in tools

    def test_debugger_same_as_programmer(self) -> None:
        """Debugger uses same tools as Programmer."""
        assert debugger_allowed_tools() == programmer_allowed_tools()

    def test_executor_tools(self) -> None:
        """test_executor_allowed_tools: read + run tests [P3.3]."""
        tools = set(te_allowed_tools())
        assert "Bash" in tools
        assert "Read" in tools


# ---------------------------------------------------------------------------
# Error mapping tests (via invoke_agent with mocked SDK)
# ---------------------------------------------------------------------------


def _make_mock_query_yielding(result_msg: Any) -> Any:
    """Create a side_effect function that returns an async generator yielding result_msg."""

    async def _query_impl(*args: Any, **kwargs: Any) -> Any:
        yield result_msg

    def side_effect(*args: Any, **kwargs: Any) -> Any:
        return _query_impl(*args, **kwargs)

    return side_effect


def _make_mock_query_empty() -> Any:
    """Create a side_effect function that returns an empty async generator."""

    async def _query_impl(*args: Any, **kwargs: Any) -> Any:
        return
        yield  # noqa: RUF100  — needed to make this an async generator

    def side_effect(*args: Any, **kwargs: Any) -> Any:
        return _query_impl(*args, **kwargs)

    return side_effect


class TestErrorMapping:
    """Tests 15-21: SDK error conditions map to correct executor exceptions."""

    @patch("agent_agent.agents.base.query")
    async def test_process_error_rate_limit_maps_to_transient(self, mock_query: MagicMock) -> None:
        """Test 15: ProcessError with rate-limit stderr -> TransientError."""
        from claude_agent_sdk import ProcessError

        mock_query.side_effect = ProcessError(
            "rate limit", exit_code=1, stderr="rate limit exceeded"
        )
        ctx = _make_node_context()
        cfg = _make_config()

        with pytest.raises(TransientError, match="transient"):
            await invoke_agent(cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1")

    @patch("agent_agent.agents.base.query")
    async def test_process_error_auth_maps_to_deterministic(self, mock_query: MagicMock) -> None:
        """Test 16: ProcessError with auth-failure stderr -> DeterministicError."""
        from claude_agent_sdk import ProcessError

        mock_query.side_effect = ProcessError("auth failure", exit_code=1, stderr="invalid api key")
        ctx = _make_node_context()
        cfg = _make_config()

        with pytest.raises(DeterministicError, match="auth"):
            await invoke_agent(cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1")

    @patch("agent_agent.agents.base.query")
    async def test_cli_connection_error_maps_to_deterministic(self, mock_query: MagicMock) -> None:
        """Test 17: CLIConnectionError -> DeterministicError."""
        from claude_agent_sdk import CLIConnectionError

        mock_query.side_effect = CLIConnectionError("connection failed")
        ctx = _make_node_context()
        cfg = _make_config()

        with pytest.raises(DeterministicError, match="CLI"):
            await invoke_agent(cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1")

    @patch("agent_agent.agents.base.query")
    async def test_budget_exceeded_maps_to_resource_exhaustion(self, mock_query: MagicMock) -> None:
        """Test 18: ResultMessage(subtype='error_max_budget_usd') -> ResourceExhaustionError."""
        result_msg = _make_result_message(subtype="error_max_budget_usd", is_error=True)
        mock_query.side_effect = _make_mock_query_yielding(result_msg)
        ctx = _make_node_context()
        cfg = _make_config()

        with pytest.raises(ResourceExhaustionError, match="budget"):
            await invoke_agent(cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1")

    @patch("agent_agent.agents.base.query")
    async def test_empty_result_maps_to_agent_error(self, mock_query: MagicMock) -> None:
        """Test 19: SDK empty result (no messages) -> AgentError."""
        mock_query.side_effect = _make_mock_query_empty()
        ctx = _make_node_context()
        cfg = _make_config()

        with pytest.raises(AgentError, match="no messages"):
            await invoke_agent(cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1")

    @patch("agent_agent.agents.base.query")
    async def test_json_decode_error_maps_to_agent_error(self, mock_query: MagicMock) -> None:
        """Test 20: SDK json.JSONDecodeError on result -> AgentError."""
        result_msg = _make_result_message(
            structured_output=None,
            result="this is not json {{{",
        )
        mock_query.side_effect = _make_mock_query_yielding(result_msg)
        ctx = _make_node_context()
        cfg = _make_config()

        with pytest.raises(AgentError, match="Failed to parse"):
            await invoke_agent(cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1")

    @patch("agent_agent.agents.base.query")
    async def test_structured_output_preferred_over_result(self, mock_query: MagicMock) -> None:
        """Test 21: ResultMessage.structured_output used when present, falls back to .result."""
        plan_data = {
            "type": "plan",
            "investigation_summary": "from structured_output",
            "child_dag": None,
            "discoveries": [],
        }
        result_msg = _make_result_message(
            structured_output=plan_data,
            result='{"type": "plan", "investigation_summary": "from result", '
            '"child_dag": null, "discoveries": []}',
        )
        mock_query.side_effect = _make_mock_query_yielding(result_msg)
        ctx = _make_node_context()
        cfg = _make_config()

        output, cost = await invoke_agent(
            cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1"
        )
        # Should use structured_output, not result
        assert isinstance(output, PlanOutput)
        assert output.investigation_summary == "from structured_output"

    @patch("agent_agent.agents.base.query")
    async def test_falls_back_to_result_when_no_structured_output(
        self, mock_query: MagicMock
    ) -> None:
        """Test 21 (fallback): Falls back to .result when structured_output is None."""
        result_msg = _make_result_message(
            structured_output=None,
            result=json.dumps(
                {
                    "type": "plan",
                    "investigation_summary": "from result fallback",
                    "child_dag": None,
                    "discoveries": [],
                }
            ),
        )
        mock_query.side_effect = _make_mock_query_yielding(result_msg)
        ctx = _make_node_context()
        cfg = _make_config()

        output, cost = await invoke_agent(
            cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1"
        )
        assert isinstance(output, PlanOutput)
        assert output.investigation_summary == "from result fallback"

    @patch("agent_agent.agents.base.query")
    async def test_no_output_maps_to_resource_exhaustion(self, mock_query: MagicMock) -> None:
        """ResultMessage with no structured_output and no result -> ResourceExhaustionError."""
        result_msg = _make_result_message(
            structured_output=None,
            result=None,
        )
        mock_query.side_effect = _make_mock_query_yielding(result_msg)
        ctx = _make_node_context()
        cfg = _make_config()

        with pytest.raises(ResourceExhaustionError, match="no output"):
            await invoke_agent(cfg, ctx, "claude-haiku-4-5-20251001", 1.0, "/tmp", "run-1", "n-1")


# ---------------------------------------------------------------------------
# compute_sdk_backstop
# ---------------------------------------------------------------------------


class TestComputeSdkBackstop:
    def test_formula_min_of_two_options(self) -> None:
        """Backstop = min(node_alloc * 2, node_alloc + 2.5% of total)."""
        # node_alloc=1.0, total=10.0 -> min(2.0, 1.25) = 1.25
        assert compute_sdk_backstop(1.0, 10.0) == 1.25
        # node_alloc=0.5, total=10.0 -> min(1.0, 0.75) = 0.75
        assert compute_sdk_backstop(0.5, 10.0) == 0.75
        # node_alloc=2.0, total=10.0 -> min(4.0, 2.25) = 2.25
        assert compute_sdk_backstop(2.0, 10.0) == 2.25
        # node_alloc=5.0, total=10.0 -> min(10.0, 5.25) = 5.25
        assert compute_sdk_backstop(5.0, 10.0) == 5.25
