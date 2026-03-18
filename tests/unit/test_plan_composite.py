"""Unit tests for agents/plan.py — validate_child_dag_spec and PlanComposite."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent_agent.agents.plan import PlanComposite, validate_child_dag_spec
from agent_agent.agents.prompts import CONSOLIDATION_PLANNER, RESEARCH_PLANNER_ORCHESTRATOR
from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.models.agent import (
    ChildDAGSpec,
    CompositeSpec,
    PlanOutput,
    SequentialEdge,
)
from agent_agent.models.context import (
    AncestorContext,
    IssueContext,
    NodeContext,
    RepoMetadata,
    SharedContextView,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_composites(n: int) -> list[CompositeSpec]:
    """Create n CompositeSpec objects with unique ids and branch_suffixes."""
    return [
        CompositeSpec(id=chr(65 + i), scope=f"scope-{i}", branch_suffix=f"branch-{i}")
        for i in range(n)
    ]


def _make_spec(
    n: int = 3,
    justification: str | None = None,
    edges: list[SequentialEdge] | None = None,
    composites: list[CompositeSpec] | None = None,
) -> ChildDAGSpec:
    return ChildDAGSpec(
        composites=composites if composites is not None else _make_composites(n),
        sequential_edges=edges or [],
        justification=justification,
    )


def _make_node_context() -> NodeContext:
    return NodeContext(
        issue=IssueContext(
            url="https://github.com/org/repo/issues/1",
            title="Test issue",
            body="Fix the bug.",
        ),
        repo_metadata=RepoMetadata(
            path="/workspaces/target",
            default_branch="main",
            language="python",
            framework="fastapi",
            claude_md="# CLAUDE.md",
        ),
        parent_outputs={},
        ancestor_context=AncestorContext(),
        shared_context_view=SharedContextView(),
    )


def _make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "env": "test",
        "model": "claude-haiku-4-5-20251001",
        "max_budget_usd": 5.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_budget(total: float = 5.0, node_ids: list[str] | None = None) -> BudgetManager:
    mgr = BudgetManager(dag_run_id="run-1", total_budget_usd=total)
    mgr.allocate(node_ids or ["plan-node"])
    return mgr


# ---------------------------------------------------------------------------
# validate_child_dag_spec tests
# ---------------------------------------------------------------------------


class TestValidateChildDagSpec:
    def test_accepts_2_to_5_composites_without_justification(self) -> None:
        """Test 1: 2-5 composites with no justification is valid."""
        for n in (2, 3, 4, 5):
            spec = _make_spec(n=n)
            validate_child_dag_spec(spec)  # should not raise

    def test_requires_justification_for_6_to_7_composites(self) -> None:
        """Test 2: 6-7 composites without justification raises ValueError."""
        for n in (6, 7):
            spec = _make_spec(n=n)
            with pytest.raises(ValueError, match="without justification"):
                validate_child_dag_spec(spec)

        # With justification, 6-7 should pass
        for n in (6, 7):
            spec = _make_spec(n=n, justification="Complex issue requires more branches")
            validate_child_dag_spec(spec)  # should not raise

    def test_rejects_8_plus_composites(self) -> None:
        """Test 3: 8+ composites always rejected regardless of justification."""
        spec = _make_spec(n=8, justification="Even with justification")
        with pytest.raises(ValueError, match=r"8\+ rejected"):
            validate_child_dag_spec(spec)

        spec = _make_spec(n=10)
        with pytest.raises(ValueError, match=r"8\+ rejected"):
            validate_child_dag_spec(spec)

    def test_rejects_duplicate_branch_suffix(self) -> None:
        """Test 4: Duplicate branch_suffix raises ValueError."""
        composites = [
            CompositeSpec(id="A", scope="scope-a", branch_suffix="fix-widget"),
            CompositeSpec(id="B", scope="scope-b", branch_suffix="fix-widget"),
        ]
        spec = _make_spec(composites=composites)
        with pytest.raises(ValueError, match="Duplicate branch_suffix"):
            validate_child_dag_spec(spec)

    def test_rejects_unknown_edge_references(self) -> None:
        """Test 5: Unknown from/to composite IDs in edges raises ValueError."""
        composites = _make_composites(3)
        bad_edge_from = SequentialEdge(from_composite_id="X", to_composite_id="A")
        with pytest.raises(ValueError, match="Unknown from_composite_id"):
            validate_child_dag_spec(_make_spec(composites=composites, edges=[bad_edge_from]))

        bad_edge_to = SequentialEdge(from_composite_id="A", to_composite_id="Z")
        with pytest.raises(ValueError, match="Unknown to_composite_id"):
            validate_child_dag_spec(_make_spec(composites=composites, edges=[bad_edge_to]))

    def test_rejects_zero_composites(self) -> None:
        """Edge case: 0 composites should be rejected."""
        spec = _make_spec(composites=[])
        with pytest.raises(ValueError, match="at least 1"):
            validate_child_dag_spec(spec)

    def test_accepts_valid_edges(self) -> None:
        """Valid edges referencing known composite IDs should pass."""
        composites = _make_composites(3)
        edges = [SequentialEdge(from_composite_id="A", to_composite_id="B")]
        spec = _make_spec(composites=composites, edges=edges)
        validate_child_dag_spec(spec)  # should not raise


# ---------------------------------------------------------------------------
# PlanComposite tests (mock invoke_agent)
# ---------------------------------------------------------------------------


class TestPlanCompositeExecute:
    """Tests 6-9: PlanComposite.execute() passes correct config to invoke_agent."""

    @patch("agent_agent.agents.plan.invoke_agent", new_callable=AsyncMock)
    async def test_l0_uses_research_planner_prompt(self, mock_invoke: AsyncMock) -> None:
        """Test 6: is_consolidation=False uses RESEARCH_PLANNER_ORCHESTRATOR prompt."""
        plan_output = PlanOutput(investigation_summary="found it", child_dag=None)
        mock_invoke.return_value = (plan_output, 0.05)

        composite = PlanComposite(
            settings=_make_settings(),
            repo_path="/workspaces/target",
            budget=_make_budget(),
        )
        result, cost = await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="plan-node",
            is_consolidation=False,
        )

        assert result is plan_output
        config = mock_invoke.call_args.kwargs["config"]
        assert config.system_prompt == RESEARCH_PLANNER_ORCHESTRATOR

    @patch("agent_agent.agents.plan.invoke_agent", new_callable=AsyncMock)
    async def test_consolidation_uses_consolidation_planner_prompt(
        self, mock_invoke: AsyncMock
    ) -> None:
        """Test 7: is_consolidation=True uses CONSOLIDATION_PLANNER prompt."""
        plan_output = PlanOutput(investigation_summary="all approved", child_dag=None)
        mock_invoke.return_value = (plan_output, 0.03)

        composite = PlanComposite(
            settings=_make_settings(),
            repo_path="/workspaces/target",
            budget=_make_budget(),
        )
        result, cost = await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="plan-node",
            is_consolidation=True,
        )

        assert result is plan_output
        config = mock_invoke.call_args.kwargs["config"]
        assert config.system_prompt == CONSOLIDATION_PLANNER

    @patch("agent_agent.agents.plan.invoke_agent", new_callable=AsyncMock)
    async def test_config_max_turns_and_thinking(self, mock_invoke: AsyncMock) -> None:
        """Test 8: PlanComposite config has max_turns=50, use_thinking=True [P10.3, P10.11]."""
        plan_output = PlanOutput(investigation_summary="done", child_dag=None)
        mock_invoke.return_value = (plan_output, 0.04)

        composite = PlanComposite(
            settings=_make_settings(),
            repo_path="/workspaces/target",
            budget=_make_budget(),
        )
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="plan-node",
            is_consolidation=False,
        )

        config = mock_invoke.call_args.kwargs["config"]
        assert config.max_turns == _make_settings().plan_max_turns
        assert config.use_thinking is True
        assert config.thinking_budget_tokens == 10000
        assert config.effort == "high"

    @patch("agent_agent.agents.plan.invoke_agent", new_callable=AsyncMock)
    async def test_allowed_tools_read_only(self, mock_invoke: AsyncMock) -> None:
        """Test 9: PlanComposite allowed_tools are read-only tools only [P3.3]."""
        plan_output = PlanOutput(investigation_summary="done", child_dag=None)
        mock_invoke.return_value = (plan_output, 0.02)

        composite = PlanComposite(
            settings=_make_settings(),
            repo_path="/workspaces/target",
            budget=_make_budget(),
        )
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="plan-node",
            is_consolidation=False,
        )

        config = mock_invoke.call_args.kwargs["config"]
        all_tools = set(config.allowed_tools)
        # Read-only: Read, Glob, Grep, Bash
        assert all_tools == {"Read", "Glob", "Grep", "Bash"}
        # No write tools
        assert "Edit" not in all_tools
        assert "Write" not in all_tools

    @patch("agent_agent.agents.plan.invoke_agent", new_callable=AsyncMock)
    async def test_raises_agent_error_on_wrong_output_type(self, mock_invoke: AsyncMock) -> None:
        """PlanComposite raises AgentError if invoke_agent returns non-PlanOutput."""
        from agent_agent.dag.executor import AgentError
        from agent_agent.models.agent import CodeOutput

        wrong_output = CodeOutput(
            summary="code", files_changed=[], branch_name="b", commit_sha=None
        )
        mock_invoke.return_value = (wrong_output, 0.01)

        composite = PlanComposite(
            settings=_make_settings(),
            repo_path="/workspaces/target",
            budget=_make_budget(),
        )
        with pytest.raises(AgentError, match="expected PlanOutput"):
            await composite.execute(
                node_context=_make_node_context(),
                dag_run_id="run-1",
                node_id="plan-node",
                is_consolidation=False,
            )

    @patch("agent_agent.agents.plan.invoke_agent", new_callable=AsyncMock)
    async def test_passes_correct_model_and_cwd(self, mock_invoke: AsyncMock) -> None:
        """PlanComposite passes settings.model and repo_path as cwd."""
        plan_output = PlanOutput(investigation_summary="done", child_dag=None)
        mock_invoke.return_value = (plan_output, 0.01)

        composite = PlanComposite(
            settings=_make_settings(model="claude-sonnet-4-6"),
            repo_path="/workspaces/my-repo",
            budget=_make_budget(),
        )
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="plan-node",
            is_consolidation=False,
        )

        assert mock_invoke.call_args.kwargs["model"] == "claude-sonnet-4-6"
        assert mock_invoke.call_args.kwargs["cwd"] == "/workspaces/my-repo"

    @patch("agent_agent.agents.plan.invoke_agent", new_callable=AsyncMock)
    async def test_config_name_is_research_planner(self, mock_invoke: AsyncMock) -> None:
        """PlanComposite config.name is 'research_planner_orchestrator'."""
        plan_output = PlanOutput(investigation_summary="done", child_dag=None)
        mock_invoke.return_value = (plan_output, 0.01)

        composite = PlanComposite(
            settings=_make_settings(),
            repo_path="/workspaces/target",
            budget=_make_budget(),
        )
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="plan-node",
            is_consolidation=False,
        )

        config = mock_invoke.call_args.kwargs["config"]
        assert config.name == "research_planner_orchestrator"
        assert config.output_model is PlanOutput
