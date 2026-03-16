"""BudgetManager — top-down USD budget allocation and tracking.

MVP stub: equal split across all nodes.
Enforces the 25% SharedContextView cap and 5% pause threshold.  [P7]

Pause semantics: nodes are never interrupted mid-execution. After a node
completes and its cost is recorded, the executor calls should_pause() to
decide whether to dispatch further nodes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .models.budget import BudgetEvent, BudgetEventType


class BudgetManager:
    """Manages USD budget for a single DAG run.

    Usage:
        mgr = BudgetManager(dag_run_id="...", total_budget_usd=5.0)
        mgr.allocate(["node-plan", "node-code-A", "node-review-A"])
        mgr.record_usage("node-code-A", 0.042)
        if mgr.should_pause():
            mgr.record_pause()
    """

    def __init__(self, dag_run_id: str, total_budget_usd: float) -> None:
        self.dag_run_id = dag_run_id
        self.total_budget_usd = total_budget_usd
        self._allocations: dict[str, float] = {}   # node_id -> allocated USD
        self._used: dict[str, float] = {}           # node_id -> USD consumed
        self.events: list[BudgetEvent] = []

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------

    def allocate(self, node_ids: list[str]) -> None:
        """MVP stub: split budget equally across all nodes."""
        if self._allocations:
            raise RuntimeError("Budget already allocated for this run.")
        if not node_ids:
            return
        per_node = self.total_budget_usd / len(node_ids)
        for node_id in node_ids:
            self._allocations[node_id] = per_node
            self._used[node_id] = 0.0
            self._log(
                node_id,
                BudgetEventType.INITIAL_ALLOCATION,
                self.total_budget_usd,
                self.total_budget_usd,
                f"equal split: ${per_node:.4f} per node ({len(node_ids)} nodes)",
            )

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    def record_usage(self, node_id: str, usd: float) -> None:
        """Record actual cost for a completed node. May push past allocation."""
        if node_id not in self._used:
            raise KeyError(f"Unknown node: {node_id!r} — call allocate() first.")
        before = self._used[node_id]
        self._used[node_id] += usd
        self._log(node_id, BudgetEventType.USAGE, before, self._used[node_id],
                  f"recorded ${usd:.6f}")

    # ------------------------------------------------------------------
    # Caps and thresholds  [P7]
    # ------------------------------------------------------------------

    def shared_context_cap(self, node_id: str) -> float:
        """Maximum USD budget for the SharedContextView for this node (25% of allocation)."""
        alloc = self._allocations.get(node_id, self.total_budget_usd)
        return alloc * 0.25

    def should_pause(self) -> bool:
        """Return True if the DAG should pause before dispatching the next node.

        Triggered when remaining DAG budget falls to or below 5% of total.
        Check this after recording each node's usage. The current node is
        always allowed to finish; only future dispatches are blocked.  [P7]
        """
        return self.remaining_dag() <= self.total_budget_usd * 0.05

    def is_over_budget(self) -> bool:
        """Return True if total spend has exceeded the budget."""
        return self.remaining_dag() < 0.0

    # ------------------------------------------------------------------
    # Remaining budget queries
    # ------------------------------------------------------------------

    def remaining_dag(self) -> float:
        """Remaining DAG budget in USD. May be negative if over budget."""
        return self.total_budget_usd - sum(self._used.values())

    def remaining_node(self, node_id: str) -> float:
        """Remaining allocation for a node in USD. May be negative if over-run."""
        alloc = self._allocations.get(node_id, 0.0)
        used = self._used.get(node_id, 0.0)
        return alloc - used

    def utilization(self) -> float:
        """Fraction of total budget consumed (0.0–1.0+, may exceed 1.0)."""
        total_used = sum(self._used.values())
        return total_used / self.total_budget_usd if self.total_budget_usd else 0.0

    # ------------------------------------------------------------------
    # Pause helper
    # ------------------------------------------------------------------

    def drain_events(self) -> list[BudgetEvent]:
        """Return all pending events and clear the internal list.

        The executor calls this after each node to flush events to StateStore.
        """
        events, self.events = self.events, []
        return events

    def record_pause(self) -> None:
        """Record that the DAG was paused due to budget threshold."""
        remaining = self.remaining_dag()
        self._log(None, BudgetEventType.PAUSE,
                  remaining + sum(self._used.values()),   # i.e. total_budget_usd
                  remaining,
                  f"DAG paused: remaining ${remaining:.4f} ≤ 5% threshold "
                  f"(${self.total_budget_usd * 0.05:.4f})")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _log(
        self,
        node_id: str | None,
        event_type: BudgetEventType,
        usd_before: float,
        usd_after: float,
        reason: str,
    ) -> None:
        self.events.append(
            BudgetEvent(
                id=str(uuid.uuid4()),
                dag_run_id=self.dag_run_id,
                node_id=node_id,
                event_type=event_type,
                usd_before=usd_before,
                usd_after=usd_after,
                reason=reason,
                timestamp=datetime.now(timezone.utc),
            )
        )
