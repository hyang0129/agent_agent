"""3-tier budget manager.

Tier 1 — DAG-level:    immutable hard ceiling, set at DAG creation (P1).
Tier 2 — Node-level:   weighted allocation + dynamic reallocation (P2-P6).
Tier 3 — Request-level: per-call max_tokens enforcement (P11).

All state changes are recorded as BudgetEvents for audit (P7).
"""

from __future__ import annotations

from agent_agent.config import TOP_UP_PRIORITY, BudgetConfig
from agent_agent.models.budget import (
    AgentType,
    BudgetAllocation,
    BudgetEvent,
    BudgetEventType,
    BudgetReport,
    ComplexityTier,
    NodeBudgetSummary,
    NodeBudgetStatus,
    TokenUsage,
)


class BudgetExceeded(Exception):
    """Raised when a budget limit is hit."""

    def __init__(self, *, scope: str, used: int, limit: int) -> None:
        self.scope = scope
        self.used = used
        self.limit = limit
        super().__init__(f"{scope} budget exceeded: {used}/{limit} tokens")


class BudgetManager:
    """Manages the 3-tier budget for a single DAG run.

    Usage::

        mgr = BudgetManager(dag_run_id="run-1", tier=ComplexityTier.MEDIUM, env="dev")
        mgr.allocate_nodes([
            ("node-1", AgentType.RESEARCH),
            ("node-2", AgentType.IMPLEMENT),
            ("node-3", AgentType.TEST),
        ])

        # Before each API call (tier 3):
        max_tok = mgr.max_tokens_for_request("node-2", model_max=8192)

        # After each API call:
        mgr.record_usage("node-2", TokenUsage(input_tokens=500, output_tokens=300))

        # When a node finishes:
        mgr.complete_node("node-1")

        # Before dispatching a node, optionally top up from reserve:
        mgr.try_top_up("node-2")
    """

    def __init__(
        self,
        dag_run_id: str,
        tier: ComplexityTier,
        env: str = "dev",
        config: BudgetConfig | None = None,
    ) -> None:
        self._config = config or BudgetConfig()
        self.dag_run_id = dag_run_id
        self.tier = tier
        self.env = env

        # Tier 1: immutable DAG-level ceiling (P1)
        self.dag_budget: int = self._config.tiers.get(tier, env)
        self.dag_used: int = 0

        # Reserve pool (P2)
        self.reserve: int = 0

        # Tier 2: node allocations
        self._nodes: dict[str, BudgetAllocation] = {}

        # Audit trail (P7)
        self.events: list[BudgetEvent] = []

    # ------------------------------------------------------------------
    # Tier 1+2: Allocation
    # ------------------------------------------------------------------

    def allocate_nodes(self, nodes: list[tuple[str, AgentType]]) -> None:
        """Compute initial weighted allocations for all nodes (P2, P3).

        Must be called exactly once before any usage recording.
        """
        if self._nodes:
            raise RuntimeError("Nodes already allocated")

        allocable = int(self.dag_budget * (1 - self._config.reserve_fraction))
        self.reserve = self.dag_budget - allocable

        # Weighted distribution (P3)
        total_weight = sum(self._config.weights[agent_type] for _, agent_type in nodes)

        for node_id, agent_type in nodes:
            weight = self._config.weights[agent_type]
            allocation = int((weight / total_weight) * allocable)
            self._nodes[node_id] = BudgetAllocation(
                node_id=node_id,
                agent_type=agent_type,
                initial_tokens=allocation,
            )
            self._emit(
                node_id=node_id,
                event_type=BudgetEventType.INITIAL_ALLOCATION,
                tokens_before=0,
                tokens_after=allocation,
            )

    # ------------------------------------------------------------------
    # Tier 2: Dynamic reallocation
    # ------------------------------------------------------------------

    def try_top_up(self, node_id: str) -> int:
        """Attempt to augment a node's budget from the reserve pool (P5, P6).

        Returns the number of tokens added (may be 0).
        """
        alloc = self._get_node(node_id)
        max_topup_cap = int(
            alloc.initial_tokens * (self._config.topup_max_multiplier - 1)
        )
        already_topped = alloc.top_ups
        headroom = max_topup_cap - already_topped

        if headroom <= 0 or self.reserve <= 0:
            return 0

        granted = min(headroom, self.reserve)
        old_reserve = self.reserve
        self.reserve -= granted
        alloc.top_ups += granted

        self._emit(
            node_id=node_id,
            event_type=BudgetEventType.TOP_UP,
            tokens_before=alloc.current_limit - granted,
            tokens_after=alloc.current_limit,
            reserve_before=old_reserve,
        )
        return granted

    def try_top_up_by_priority(self, eligible_node_ids: list[str]) -> None:
        """Top up eligible nodes in priority order (P6)."""
        by_priority: list[str] = []
        for agent_type in TOP_UP_PRIORITY:
            for nid in eligible_node_ids:
                if self._nodes[nid].agent_type == agent_type and nid not in by_priority:
                    by_priority.append(nid)
        # Include planner last (not in TOP_UP_PRIORITY list)
        for nid in eligible_node_ids:
            if nid not in by_priority:
                by_priority.append(nid)

        for nid in by_priority:
            if self.reserve <= 0:
                break
            self.try_top_up(nid)

    def complete_node(self, node_id: str) -> int:
        """Reclaim unspent tokens from a completed node (P4).

        Returns the number of tokens reclaimed.
        """
        alloc = self._get_node(node_id)
        unspent = alloc.remaining
        if unspent <= 0:
            return 0

        old_reserve = self.reserve
        self.reserve += unspent
        # Zero out remaining by setting top_ups so current_limit == tokens_used
        alloc.top_ups = alloc.tokens_used - alloc.initial_tokens

        self._emit(
            node_id=node_id,
            event_type=BudgetEventType.RECLAIM,
            tokens_before=alloc.tokens_used + unspent,
            tokens_after=alloc.tokens_used,
            reserve_before=old_reserve,
        )
        return unspent

    # ------------------------------------------------------------------
    # Tier 3: Per-request enforcement
    # ------------------------------------------------------------------

    def max_tokens_for_request(self, node_id: str, model_max: int = 8192) -> int:
        """Compute max_tokens for the next API call (P11).

        Also checks DAG-level remaining budget.
        """
        alloc = self._get_node(node_id)
        dag_remaining = self.dag_budget - self.dag_used
        return max(0, min(model_max, alloc.remaining, dag_remaining))

    # ------------------------------------------------------------------
    # Usage recording
    # ------------------------------------------------------------------

    def record_usage(self, node_id: str, usage: TokenUsage) -> NodeBudgetStatus:
        """Record token usage from an API response.

        Returns the node's budget status after recording.
        Raises BudgetExceeded if the DAG budget is exhausted (P9).
        """
        alloc = self._get_node(node_id)
        alloc.tokens_used += usage.total
        self.dag_used += usage.total

        # Node-level exhaustion (P8)
        if alloc.status == NodeBudgetStatus.EXCEEDED:
            self._emit(
                node_id=node_id,
                event_type=BudgetEventType.EXHAUSTION,
                tokens_before=alloc.tokens_used - usage.total,
                tokens_after=alloc.tokens_used,
            )

        # DAG-level exhaustion (P9)
        if self.dag_used >= self.dag_budget:
            raise BudgetExceeded(
                scope="DAG",
                used=self.dag_used,
                limit=self.dag_budget,
            )

        return alloc.status

    # ------------------------------------------------------------------
    # Reporting (P10)
    # ------------------------------------------------------------------

    def report(self, node_statuses: dict[str, str] | None = None) -> BudgetReport:
        """Generate a structured budget report."""
        statuses = node_statuses or {}
        summaries = []
        for nid, alloc in self._nodes.items():
            status_str = statuses.get(nid)
            if status_str is None:
                status_str = (
                    "budget_exceeded"
                    if alloc.status == NodeBudgetStatus.EXCEEDED
                    else "completed"
                )
            summaries.append(
                NodeBudgetSummary(
                    node_id=nid,
                    agent_type=alloc.agent_type,
                    allocated=alloc.current_limit,
                    used=alloc.tokens_used,
                    status=status_str,
                )
            )

        return BudgetReport(
            dag_run_id=self.dag_run_id,
            complexity_tier=self.tier,
            dag_budget=self.dag_budget,
            total_used=self.dag_used,
            reserve_remaining=self.reserve,
            nodes=summaries,
            exhausted=self.dag_used >= self.dag_budget,
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> BudgetAllocation:
        return self._get_node(node_id)

    @property
    def dag_remaining(self) -> int:
        return max(0, self.dag_budget - self.dag_used)

    @property
    def node_ids(self) -> list[str]:
        return list(self._nodes.keys())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_node(self, node_id: str) -> BudgetAllocation:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise KeyError(f"Unknown node: {node_id}")

    def _emit(
        self,
        *,
        event_type: BudgetEventType,
        tokens_before: int,
        tokens_after: int,
        node_id: str | None = None,
        reserve_before: int | None = None,
    ) -> None:
        self.events.append(
            BudgetEvent(
                dag_run_id=self.dag_run_id,
                node_id=node_id,
                event_type=event_type,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                reserve_before=reserve_before if reserve_before is not None else self.reserve,
                reserve_after=self.reserve,
            )
        )
