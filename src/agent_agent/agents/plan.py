"""Plan composite — ResearchPlannerOrchestrator sub-agent.

Two invocation modes:
  L0 (analysis): reads issue + repo, produces ChildDAGSpec
  Consolidation: receives all (CodeOutput, ReviewOutput) pairs, decides next steps

Uses extended reasoning (thinking enabled) [P10.11].
Read-only tools only [P3.3].
cwd = primary checkout (--repo path) — no worktree needed for Plan composite.
"""

from __future__ import annotations

import structlog

from ..budget import BudgetManager
from ..config import Settings
from ..dag.executor import AgentError
from ..models.agent import ChildDAGSpec, PlanOutput
from ..models.context import NodeContext
from .base import SubAgentConfig, compute_sdk_backstop, invoke_agent
from .prompts import CONSOLIDATION_PLANNER, RESEARCH_PLANNER_ORCHESTRATOR
from .tools import plan_permissions

_logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# ChildDAGSpec validation
# ---------------------------------------------------------------------------


def validate_child_dag_spec(spec: ChildDAGSpec) -> None:
    """Validate ChildDAGSpec per P02 rules. Raises ValueError on violation."""
    n = len(spec.composites)
    if n >= 8:
        raise ValueError(f"P02 violation: {n} composites (max 7; 8+ rejected)")
    if n >= 6 and not spec.justification:
        raise ValueError(f"P02 violation: {n} composites without justification")
    if n < 1:
        raise ValueError("ChildDAGSpec must have at least 1 composite")

    ids = {c.id for c in spec.composites}
    suffixes = [c.branch_suffix for c in spec.composites]
    if len(set(suffixes)) != len(suffixes):
        raise ValueError("Duplicate branch_suffix in ChildDAGSpec")

    for edge in spec.sequential_edges:
        if edge.from_composite_id not in ids:
            raise ValueError(f"Unknown from_composite_id: {edge.from_composite_id}")
        if edge.to_composite_id not in ids:
            raise ValueError(f"Unknown to_composite_id: {edge.to_composite_id}")


# ---------------------------------------------------------------------------
# PlanComposite
# ---------------------------------------------------------------------------


class PlanComposite:
    """Executes a Plan composite node (single ResearchPlannerOrchestrator invocation)."""

    def __init__(self, settings: Settings, repo_path: str, budget: BudgetManager) -> None:
        self._settings = settings
        self._repo_path = repo_path
        self._budget = budget

    async def execute(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        is_consolidation: bool,
    ) -> tuple[PlanOutput, float]:
        """Invoke ResearchPlannerOrchestrator and return (PlanOutput, cost_usd).

        Args:
            is_consolidation: True for terminal Plan composites (post-review);
                             False for L0 analysis.
        """
        system_prompt = CONSOLIDATION_PLANNER if is_consolidation else RESEARCH_PLANNER_ORCHESTRATOR

        config = SubAgentConfig(
            name="research_planner_orchestrator",
            system_prompt=system_prompt,
            permissions=plan_permissions(),
            output_model=PlanOutput,
            max_turns=self._settings.plan_max_turns,
            use_thinking=self._settings.plan_use_thinking,
            thinking_budget_tokens=self._settings.plan_thinking_budget_tokens,
            effort=self._settings.plan_effort,
        )

        # SDK backstop: min(node_alloc * 2, node_alloc + 2.5% of total) [HG-7]
        node_alloc = self._budget.remaining_node(node_id)
        backstop = compute_sdk_backstop(node_alloc, self._settings.max_budget_usd)

        output, cost = await invoke_agent(
            config=config,
            node_context=node_context,
            model=self._settings.model,
            sdk_budget_backstop_usd=backstop,
            cwd=self._repo_path,
            dag_run_id=dag_run_id,
            node_id=node_id,
        )

        if not isinstance(output, PlanOutput):
            raise AgentError(f"PlanComposite expected PlanOutput, got {type(output).__name__}")
        return output, cost
