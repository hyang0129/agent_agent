"""Tests for the 3-tier budget system."""

from __future__ import annotations

import pytest

from agent_agent.budget import BudgetExceeded, BudgetManager
from agent_agent.config import BudgetConfig
from agent_agent.models.budget import (
    AgentType,
    BudgetEventType,
    ComplexityTier,
    NodeBudgetStatus,
    TokenUsage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_manager(env: str = "dev") -> BudgetManager:
    """A manager with 3 nodes: research, implement, test."""
    mgr = BudgetManager(dag_run_id="test-run", tier=ComplexityTier.SIMPLE, env=env)
    mgr.allocate_nodes([
        ("research-1", AgentType.RESEARCH),
        ("impl-1", AgentType.IMPLEMENT),
        ("test-1", AgentType.TEST),
    ])
    return mgr


# ---------------------------------------------------------------------------
# Tier 1: DAG-level budget
# ---------------------------------------------------------------------------

class TestDAGBudget:
    def test_dev_budget_is_correct(self):
        mgr = BudgetManager(dag_run_id="r", tier=ComplexityTier.SIMPLE, env="dev")
        assert mgr.dag_budget == 100_000

    def test_prod_budget_is_correct(self):
        mgr = BudgetManager(dag_run_id="r", tier=ComplexityTier.SIMPLE, env="prod")
        assert mgr.dag_budget == 50_000

    def test_medium_tier(self):
        mgr = BudgetManager(dag_run_id="r", tier=ComplexityTier.MEDIUM, env="prod")
        assert mgr.dag_budget == 150_000

    def test_complex_tier(self):
        mgr = BudgetManager(dag_run_id="r", tier=ComplexityTier.COMPLEX, env="dev")
        assert mgr.dag_budget == 750_000

    def test_dag_budget_is_immutable_by_usage(self):
        mgr = _simple_manager()
        original = mgr.dag_budget
        mgr.record_usage("research-1", TokenUsage(input_tokens=1000, output_tokens=100))
        assert mgr.dag_budget == original

    def test_dag_exhaustion_raises(self):
        config = BudgetConfig()
        config.tiers.simple_dev = 500  # tiny budget
        mgr = BudgetManager(dag_run_id="r", tier=ComplexityTier.SIMPLE, env="dev", config=config)
        mgr.allocate_nodes([("n1", AgentType.RESEARCH)])

        with pytest.raises(BudgetExceeded, match="DAG"):
            mgr.record_usage("n1", TokenUsage(input_tokens=400, output_tokens=200))


# ---------------------------------------------------------------------------
# Tier 2: Node-level allocation
# ---------------------------------------------------------------------------

class TestNodeAllocation:
    def test_reserve_is_15_percent(self):
        mgr = _simple_manager()
        total_allocated = sum(n.current_limit for n in mgr._nodes.values())
        # int() truncation can lose up to (num_nodes - 1) tokens
        assert mgr.reserve + total_allocated <= mgr.dag_budget
        assert mgr.dag_budget - (mgr.reserve + total_allocated) < len(mgr._nodes)
        assert mgr.reserve == int(mgr.dag_budget * 0.15)

    def test_implement_gets_largest_share(self):
        mgr = _simple_manager()
        impl = mgr.get_node("impl-1")
        research = mgr.get_node("research-1")
        test = mgr.get_node("test-1")
        assert impl.initial_tokens > research.initial_tokens
        assert impl.initial_tokens > test.initial_tokens

    def test_weighted_proportions(self):
        mgr = _simple_manager()
        # weights: research=1.0, implement=2.5, test=0.7  → total=4.2
        allocable = int(mgr.dag_budget * 0.85)
        impl = mgr.get_node("impl-1")
        expected = int((2.5 / 4.2) * allocable)
        assert impl.initial_tokens == expected

    def test_allocate_twice_raises(self):
        mgr = _simple_manager()
        with pytest.raises(RuntimeError, match="already allocated"):
            mgr.allocate_nodes([("extra", AgentType.TEST)])

    def test_unknown_node_raises(self):
        mgr = _simple_manager()
        with pytest.raises(KeyError, match="Unknown node"):
            mgr.record_usage("nonexistent", TokenUsage(input_tokens=1))


# ---------------------------------------------------------------------------
# Tier 2: Dynamic reallocation
# ---------------------------------------------------------------------------

class TestDynamicReallocation:
    def test_complete_node_reclaims_to_reserve(self):
        mgr = _simple_manager()
        # Use only half the research budget
        alloc = mgr.get_node("research-1")
        half = alloc.initial_tokens // 2
        mgr.record_usage("research-1", TokenUsage(input_tokens=half))

        old_reserve = mgr.reserve
        reclaimed = mgr.complete_node("research-1")
        assert reclaimed == alloc.initial_tokens - half
        assert mgr.reserve == old_reserve + reclaimed

    def test_complete_fully_used_node_reclaims_nothing(self):
        mgr = _simple_manager()
        alloc = mgr.get_node("research-1")
        mgr.record_usage("research-1", TokenUsage(input_tokens=alloc.initial_tokens))
        assert mgr.complete_node("research-1") == 0

    def test_top_up_from_reserve(self):
        mgr = _simple_manager()
        impl = mgr.get_node("impl-1")
        old_limit = impl.current_limit
        added = mgr.try_top_up("impl-1")
        assert added > 0
        assert impl.current_limit == old_limit + added
        assert impl.current_limit <= int(impl.initial_tokens * 1.5)

    def test_top_up_capped_at_1_5x(self):
        mgr = _simple_manager()
        impl = mgr.get_node("impl-1")
        mgr.try_top_up("impl-1")
        mgr.try_top_up("impl-1")  # second attempt
        assert impl.current_limit <= int(impl.initial_tokens * 1.5)

    def test_top_up_priority_order(self):
        mgr = _simple_manager()
        # Empty the reserve first, then refill
        initial_reserve = mgr.reserve
        # Complete research (reclaims tokens to reserve)
        mgr.record_usage("research-1", TokenUsage(input_tokens=100))
        mgr.complete_node("research-1")

        # Now top up by priority — implement should get tokens first
        impl_before = mgr.get_node("impl-1").current_limit
        mgr.try_top_up_by_priority(["test-1", "impl-1"])
        impl_after = mgr.get_node("impl-1").current_limit
        assert impl_after > impl_before

    def test_top_up_with_empty_reserve(self):
        config = BudgetConfig()
        config.reserve_fraction = 0.0
        mgr = BudgetManager(dag_run_id="r", tier=ComplexityTier.SIMPLE, env="dev", config=config)
        mgr.allocate_nodes([("n1", AgentType.IMPLEMENT)])
        assert mgr.try_top_up("n1") == 0


# ---------------------------------------------------------------------------
# Tier 3: Per-request max_tokens
# ---------------------------------------------------------------------------

class TestRequestLevel:
    def test_max_tokens_respects_model_max(self):
        mgr = _simple_manager()
        assert mgr.max_tokens_for_request("impl-1", model_max=512) <= 512

    def test_max_tokens_respects_node_remaining(self):
        mgr = _simple_manager()
        alloc = mgr.get_node("impl-1")
        # Use almost all budget
        mgr.record_usage("impl-1", TokenUsage(output_tokens=alloc.current_limit - 100))
        assert mgr.max_tokens_for_request("impl-1", model_max=8192) == 100

    def test_max_tokens_respects_dag_remaining(self):
        config = BudgetConfig()
        config.tiers.simple_dev = 10_000
        config.reserve_fraction = 0.0  # all tokens allocable
        mgr = BudgetManager(dag_run_id="r", tier=ComplexityTier.SIMPLE, env="dev", config=config)
        mgr.allocate_nodes([("n1", AgentType.IMPLEMENT)])
        # Node gets all 10k. Use 9900 → 100 remaining at both DAG and node level.
        mgr.record_usage("n1", TokenUsage(output_tokens=9_900))
        assert mgr.max_tokens_for_request("n1", model_max=8192) == 100

    def test_max_tokens_zero_when_exhausted(self):
        mgr = _simple_manager()
        alloc = mgr.get_node("test-1")
        mgr.record_usage("test-1", TokenUsage(output_tokens=alloc.current_limit))
        assert mgr.max_tokens_for_request("test-1") == 0


# ---------------------------------------------------------------------------
# Node budget status (P8)
# ---------------------------------------------------------------------------

class TestNodeStatus:
    def test_ok_status(self):
        mgr = _simple_manager()
        assert mgr.get_node("impl-1").status == NodeBudgetStatus.OK

    def test_warning_at_90_percent(self):
        mgr = _simple_manager()
        alloc = mgr.get_node("impl-1")
        usage = int(alloc.current_limit * 0.91)
        mgr.record_usage("impl-1", TokenUsage(output_tokens=usage))
        assert alloc.status == NodeBudgetStatus.WARNING

    def test_exceeded_at_100_percent(self):
        mgr = _simple_manager()
        alloc = mgr.get_node("impl-1")
        mgr.record_usage("impl-1", TokenUsage(output_tokens=alloc.current_limit))
        assert alloc.status == NodeBudgetStatus.EXCEEDED


# ---------------------------------------------------------------------------
# Audit trail (P7)
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_initial_allocation_events(self):
        mgr = _simple_manager()
        alloc_events = [e for e in mgr.events if e.event_type == BudgetEventType.INITIAL_ALLOCATION]
        assert len(alloc_events) == 3

    def test_reclaim_event(self):
        mgr = _simple_manager()
        mgr.record_usage("research-1", TokenUsage(input_tokens=100))
        mgr.complete_node("research-1")
        reclaim_events = [e for e in mgr.events if e.event_type == BudgetEventType.RECLAIM]
        assert len(reclaim_events) == 1

    def test_top_up_event(self):
        mgr = _simple_manager()
        mgr.try_top_up("impl-1")
        topup_events = [e for e in mgr.events if e.event_type == BudgetEventType.TOP_UP]
        assert len(topup_events) == 1

    def test_exhaustion_event(self):
        config = BudgetConfig()
        config.tiers.simple_dev = 100_000
        mgr = BudgetManager(dag_run_id="r", tier=ComplexityTier.SIMPLE, env="dev", config=config)
        mgr.allocate_nodes([("n1", AgentType.RESEARCH)])
        alloc = mgr.get_node("n1")
        mgr.record_usage("n1", TokenUsage(input_tokens=alloc.current_limit + 1))
        exhaustion = [e for e in mgr.events if e.event_type == BudgetEventType.EXHAUSTION]
        assert len(exhaustion) == 1


# ---------------------------------------------------------------------------
# Report (P10)
# ---------------------------------------------------------------------------

class TestReport:
    def test_report_structure(self):
        mgr = _simple_manager()
        mgr.record_usage("research-1", TokenUsage(input_tokens=500, output_tokens=50))
        report = mgr.report({"research-1": "completed", "impl-1": "completed", "test-1": "skipped"})
        assert report.dag_run_id == "test-run"
        assert report.complexity_tier == ComplexityTier.SIMPLE
        assert report.total_used == 550
        assert len(report.nodes) == 3

    def test_report_utilization(self):
        mgr = _simple_manager()
        mgr.record_usage("impl-1", TokenUsage(output_tokens=50_000))
        report = mgr.report()
        assert 0 < report.utilization < 1


# ---------------------------------------------------------------------------
# TokenUsage model
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_total(self):
        u = TokenUsage(input_tokens=100, output_tokens=50, cached_tokens=80)
        assert u.total == 150  # cached_tokens not in total

    def test_add(self):
        a = TokenUsage(input_tokens=100, output_tokens=50, cached_tokens=10)
        b = TokenUsage(input_tokens=200, output_tokens=100, cached_tokens=20)
        c = a + b
        assert c.input_tokens == 300
        assert c.output_tokens == 150
        assert c.cached_tokens == 30
