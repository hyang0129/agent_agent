"""Pydantic models for the 3-tier budget system.

Tier 1 — DAG-level:  immutable hard ceiling per run.
Tier 2 — Node-level: weighted initial allocation + dynamic reallocation.
Tier 3 — Request-level: per-API-call max_tokens enforcement.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AgentType(str, Enum):
    RESEARCH = "research"
    IMPLEMENT = "implement"
    TEST = "test"
    REVIEW = "review"
    PLANNER = "planner"


class ComplexityTier(str, Enum):
    SIMPLE = "simple"    # 1-2 nodes
    MEDIUM = "medium"    # 3-5 nodes
    COMPLEX = "complex"  # 6-12 nodes


class BudgetEventType(str, Enum):
    INITIAL_ALLOCATION = "initial_allocation"
    TOP_UP = "top_up"
    RECLAIM = "reclaim"
    EXHAUSTION = "exhaustion"


class NodeBudgetStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"        # ≥90% used
    EXCEEDED = "exceeded"      # ≥100% used


# ---------------------------------------------------------------------------
# Token tracking
# ---------------------------------------------------------------------------

class TokenUsage(BaseModel):
    """Token counts from a single API response."""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
        )


# ---------------------------------------------------------------------------
# Budget allocation (per node)
# ---------------------------------------------------------------------------

class BudgetAllocation(BaseModel):
    """Tracks a single node's budget allocation and usage."""
    node_id: str
    agent_type: AgentType
    initial_tokens: int
    top_ups: int = 0
    tokens_used: int = 0

    @property
    def current_limit(self) -> int:
        return self.initial_tokens + self.top_ups

    @property
    def remaining(self) -> int:
        return max(0, self.current_limit - self.tokens_used)

    @property
    def utilization(self) -> float:
        if self.current_limit == 0:
            return 0.0
        return self.tokens_used / self.current_limit

    @property
    def status(self) -> NodeBudgetStatus:
        if self.utilization >= 1.0:
            return NodeBudgetStatus.EXCEEDED
        if self.utilization >= 0.9:
            return NodeBudgetStatus.WARNING
        return NodeBudgetStatus.OK

    def max_tokens_for_request(self, model_max: int = 8192) -> int:
        """Tier 3: per-request max_tokens (P11)."""
        return min(model_max, self.remaining)


# ---------------------------------------------------------------------------
# Budget events (audit trail — P7)
# ---------------------------------------------------------------------------

class BudgetEvent(BaseModel):
    """Immutable record of a budget state change."""
    dag_run_id: str
    node_id: str | None = None
    event_type: BudgetEventType
    tokens_before: int
    tokens_after: int
    reserve_before: int
    reserve_after: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Budget report (P10)
# ---------------------------------------------------------------------------

class NodeBudgetSummary(BaseModel):
    node_id: str
    agent_type: AgentType
    allocated: int
    used: int
    status: Literal["completed", "budget_exceeded", "skipped", "failed"]


class BudgetReport(BaseModel):
    """Structured report emitted on DAG completion or budget exhaustion (P10)."""
    dag_run_id: str
    complexity_tier: ComplexityTier
    dag_budget: int
    total_used: int
    reserve_remaining: int
    nodes: list[NodeBudgetSummary]
    exhausted: bool = False

    @property
    def utilization(self) -> float:
        if self.dag_budget == 0:
            return 0.0
        return self.total_used / self.dag_budget
