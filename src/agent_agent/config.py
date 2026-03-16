"""Budget configuration — all values configurable per environment (P13)."""

from __future__ import annotations

from pydantic import BaseModel

from agent_agent.models.budget import AgentType, ComplexityTier


class BudgetTierConfig(BaseModel):
    """Token ceilings per complexity tier (P1)."""
    simple_dev: int = 100_000
    simple_prod: int = 50_000
    medium_dev: int = 300_000
    medium_prod: int = 150_000
    complex_dev: int = 750_000
    complex_prod: int = 500_000

    def get(self, tier: ComplexityTier, env: str) -> int:
        key = f"{tier.value}_{env}"
        return getattr(self, key)


# Default agent-type weights (P3)
DEFAULT_WEIGHTS: dict[AgentType, float] = {
    AgentType.RESEARCH: 1.0,
    AgentType.IMPLEMENT: 2.5,
    AgentType.TEST: 0.7,
    AgentType.REVIEW: 1.2,
    AgentType.PLANNER: 0.5,
}

# Top-up priority ordering (P6): implement > review > research > test
TOP_UP_PRIORITY: list[AgentType] = [
    AgentType.IMPLEMENT,
    AgentType.REVIEW,
    AgentType.RESEARCH,
    AgentType.TEST,
]


class BudgetConfig(BaseModel):
    """Complete budget configuration."""
    tiers: BudgetTierConfig = BudgetTierConfig()
    weights: dict[AgentType, float] = DEFAULT_WEIGHTS
    reserve_fraction: float = 0.15
    topup_max_multiplier: float = 1.5
    warning_threshold: float = 0.90
