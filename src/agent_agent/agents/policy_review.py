"""PolicyReviewer sub-agent — policy compliance evaluation on a read-only worktree.

Runs in parallel with Reviewer inside ReviewComposite [P03].
Evaluates policy compliance only; never evaluates code quality [P3.2].
Read-only tools [P3.3] — enforced by tool selection, not filesystem perms.
"""

from __future__ import annotations

from ..budget import BudgetManager
from ..config import Settings
from ..dag.executor import AgentError
from ..models.agent import PolicyReviewOutput
from ..models.context import NodeContext
from ..worktree import WorktreeRecord
from .base import SubAgentConfig, compute_sdk_backstop, invoke_agent
from .prompts import POLICY_REVIEWER
from .tools import policy_reviewer_allowed_tools


class PolicyReviewer:
    """Executes PolicyReviewer sub-agent on a read-only worktree.

    Runs in parallel with Reviewer inside ReviewComposite.execute().
    Returns PolicyReviewOutput with per-policy citations and an approved verdict.
    """

    def __init__(
        self,
        settings: Settings,
        worktree: WorktreeRecord,
        budget: BudgetManager,
    ) -> None:
        self._settings = settings
        self._worktree = worktree
        self._budget = budget

    async def execute(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
    ) -> tuple[PolicyReviewOutput, float]:
        """Invoke PolicyReviewer and return (PolicyReviewOutput, cost_usd)."""
        system_prompt = POLICY_REVIEWER.format(worktree_path=self._worktree.path)

        config = SubAgentConfig(
            name="policy_reviewer",
            system_prompt=system_prompt,
            allowed_tools=policy_reviewer_allowed_tools(),
            output_model=PolicyReviewOutput,
            max_turns=self._settings.reviewer_max_turns,
        )

        # SDK backstop: min(node_alloc * 2, node_alloc + 2.5% of total) [HG-7]
        node_alloc = self._budget.remaining_node(node_id)
        backstop = compute_sdk_backstop(node_alloc, self._settings.max_budget_usd)

        output, cost = await invoke_agent(
            config=config,
            node_context=node_context,
            model=self._settings.model,
            sdk_budget_backstop_usd=backstop,
            cwd=self._worktree.path,
            dag_run_id=dag_run_id,
            node_id=node_id,
        )

        if not isinstance(output, PolicyReviewOutput):
            raise AgentError(
                f"PolicyReviewer expected PolicyReviewOutput, got {type(output).__name__}"
            )
        return output, cost
