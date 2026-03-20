"""Review composite — Reviewer and PolicyReviewer sub-agents on a read-only worktree.

Both run in parallel [P03]. Reviewer evaluates code quality; PolicyReviewer evaluates
policy compliance. Neither sees the other's concern [P3.2].
Read-only tools [P3.3] — enforced by tool selection, not filesystem perms.
"""

from __future__ import annotations

import asyncio

from ..budget import BudgetManager
from ..config import Settings
from ..dag.executor import AgentError
from ..models.agent import PolicyReviewOutput, ReviewOutput, ReviewVerdict
from ..models.context import NodeContext
from ..worktree import WorktreeRecord
from .base import SubAgentConfig, compute_sdk_backstop, invoke_agent
from .policy_review import PolicyReviewer
from .prompts import REVIEWER
from .tools import reviewer_permissions


def _merge_verdict(
    review_verdict: ReviewVerdict,
    policy_review: PolicyReviewOutput,
) -> ReviewVerdict:
    """Derive final verdict from code quality review and policy review [P3.2].

    If the policy reviewer found violations (approved=False, skipped=False),
    the final verdict is REJECTED regardless of the code quality verdict.
    If policy review was skipped (no policies in repo), the code quality verdict stands.
    """
    if not policy_review.skipped and not policy_review.approved:
        return ReviewVerdict.REJECTED
    return review_verdict


class ReviewComposite:
    """Executes a Review composite node (Reviewer + PolicyReviewer in parallel)."""

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
    ) -> tuple[ReviewOutput, float]:
        """Invoke Reviewer and PolicyReviewer in parallel, merge into ReviewOutput."""
        system_prompt = REVIEWER.format(worktree_path=self._worktree.path)

        reviewer_config = SubAgentConfig(
            name="reviewer",
            system_prompt=system_prompt,
            permissions=reviewer_permissions(),
            output_model=ReviewOutput,
            max_turns=self._settings.reviewer_max_turns,
        )

        # SDK backstop: min(node_alloc * 2, node_alloc + 2.5% of total) [HG-7]
        node_alloc = self._budget.remaining_node(node_id)
        backstop = compute_sdk_backstop(node_alloc, self._settings.max_budget_usd)

        policy_reviewer = PolicyReviewer(
            settings=self._settings,
            worktree=self._worktree,
            budget=self._budget,
        )

        # Run Reviewer and PolicyReviewer in parallel [P03]
        (raw_review_output, reviewer_cost), (policy_review_output, policy_cost) = (
            await asyncio.gather(
                invoke_agent(
                    config=reviewer_config,
                    node_context=node_context,
                    model=self._settings.model,
                    sdk_budget_backstop_usd=backstop,
                    cwd=self._worktree.path,
                    dag_run_id=dag_run_id,
                    node_id=node_id,
                ),
                policy_reviewer.execute(
                    node_context=node_context,
                    dag_run_id=dag_run_id,
                    node_id=node_id,
                ),
            )
        )

        if not isinstance(raw_review_output, ReviewOutput):
            raise AgentError(
                f"Reviewer expected ReviewOutput, got {type(raw_review_output).__name__}"
            )

        # Merge: attach policy_review and apply verdict merge rule
        merged_verdict = _merge_verdict(raw_review_output.verdict, policy_review_output)
        review_output = raw_review_output.model_copy(
            update={
                "verdict": merged_verdict,
                "policy_review": policy_review_output,
            }
        )

        total_cost = reviewer_cost + policy_cost
        return review_output, total_cost
