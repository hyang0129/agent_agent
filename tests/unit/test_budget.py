"""Tests for BudgetManager.  [P7]"""

from __future__ import annotations

import pytest

from agent_agent.budget import BudgetManager
from agent_agent.models.budget import BudgetEventType


def _mgr(total: float = 5.0, nodes: list[str] | None = None) -> BudgetManager:
    mgr = BudgetManager(dag_run_id="run-1", total_budget_usd=total)
    mgr.allocate(nodes or ["plan-1", "code-A", "review-A"])
    return mgr


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


class TestAllocation:
    def test_equal_split(self):
        mgr = _mgr(total=3.0, nodes=["a", "b", "c"])
        assert mgr.remaining_node("a") == pytest.approx(1.0)
        assert mgr.remaining_node("b") == pytest.approx(1.0)
        assert mgr.remaining_node("c") == pytest.approx(1.0)

    def test_single_node_gets_full_budget(self):
        mgr = BudgetManager(dag_run_id="r", total_budget_usd=5.0)
        mgr.allocate(["only-node"])
        assert mgr.remaining_node("only-node") == pytest.approx(5.0)

    def test_allocate_twice_raises(self):
        mgr = _mgr()
        with pytest.raises(RuntimeError, match="already allocated"):
            mgr.allocate(["extra"])

    def test_empty_allocation_is_noop(self):
        mgr = BudgetManager(dag_run_id="r", total_budget_usd=5.0)
        mgr.allocate([])  # should not raise
        assert mgr.remaining_dag() == pytest.approx(5.0)

    def test_initial_allocation_events_logged(self):
        mgr = _mgr(nodes=["a", "b"])
        alloc_events = [e for e in mgr.events if e.event_type == BudgetEventType.INITIAL_ALLOCATION]
        assert len(alloc_events) == 2


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------


class TestUsageTracking:
    def test_record_usage_reduces_remaining(self):
        mgr = _mgr(total=3.0, nodes=["a", "b", "c"])
        mgr.record_usage("a", 0.5)
        assert mgr.remaining_node("a") == pytest.approx(0.5)

    def test_dag_remaining_decreases_across_nodes(self):
        mgr = _mgr(total=3.0, nodes=["a", "b", "c"])
        mgr.record_usage("a", 0.3)
        mgr.record_usage("b", 0.2)
        assert mgr.remaining_dag() == pytest.approx(2.5)

    def test_unknown_node_raises(self):
        mgr = _mgr()
        with pytest.raises(KeyError, match="Unknown node"):
            mgr.record_usage("nonexistent", 0.1)

    def test_usage_event_logged(self):
        mgr = _mgr(nodes=["a"])
        mgr.record_usage("a", 0.01)
        usage_events = [e for e in mgr.events if e.event_type == BudgetEventType.USAGE]
        assert len(usage_events) == 1
        assert usage_events[0].usd_after == pytest.approx(0.01)

    def test_remaining_node_goes_negative_on_overrun(self):
        mgr = BudgetManager(dag_run_id="r", total_budget_usd=1.0)
        mgr.allocate(["n"])
        mgr.record_usage("n", 2.0)  # over-run
        assert mgr.remaining_node("n") == pytest.approx(-1.0)

    def test_remaining_dag_goes_negative_on_overrun(self):
        mgr = BudgetManager(dag_run_id="r", total_budget_usd=1.0)
        mgr.allocate(["n"])
        mgr.record_usage("n", 1.5)
        assert mgr.remaining_dag() == pytest.approx(-0.5)


# ---------------------------------------------------------------------------
# SharedContextView cap  [P5, P7]
# ---------------------------------------------------------------------------


class TestSharedContextCap:
    def test_cap_is_25_percent_of_allocation(self):
        mgr = BudgetManager(dag_run_id="r", total_budget_usd=4.0)
        mgr.allocate(["n"])  # n gets $4.00
        assert mgr.shared_context_cap("n") == pytest.approx(1.0)

    def test_cap_for_equal_split(self):
        mgr = _mgr(total=4.0, nodes=["a", "b", "c", "d"])
        # each gets $1.00; cap = $0.25
        assert mgr.shared_context_cap("a") == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Pause threshold  [P7]
# ---------------------------------------------------------------------------


class TestPauseThreshold:
    def test_not_paused_when_budget_healthy(self):
        mgr = _mgr(total=5.0)
        assert mgr.should_pause() is False

    def test_paused_at_5_percent(self):
        mgr = _mgr(total=5.0, nodes=["n"])
        mgr.record_usage("n", 4.76)  # ~4.8% remaining → below threshold
        assert mgr.should_pause() is True

    def test_exactly_at_threshold_is_paused(self):
        mgr = _mgr(total=5.0, nodes=["n"])
        mgr.record_usage("n", 4.75)  # exactly 5% remaining ($0.25)
        assert mgr.should_pause() is True

    def test_is_over_budget_false_when_within(self):
        mgr = _mgr(total=5.0, nodes=["n"])
        mgr.record_usage("n", 4.99)
        assert mgr.is_over_budget() is False

    def test_is_over_budget_true_when_exceeded(self):
        mgr = _mgr(total=5.0, nodes=["n"])
        mgr.record_usage("n", 5.01)
        assert mgr.is_over_budget() is True

    def test_nodes_can_complete_after_going_over(self):
        # Overrun is allowed — no exception raised
        mgr = _mgr(total=5.0, nodes=["n"])
        mgr.record_usage("n", 8.0)
        assert mgr.remaining_dag() == pytest.approx(-3.0)


# ---------------------------------------------------------------------------
# Utilization
# ---------------------------------------------------------------------------


class TestUtilization:
    def test_zero_utilization_on_init(self):
        mgr = _mgr()
        assert mgr.utilization() == 0.0

    def test_full_utilization(self):
        mgr = BudgetManager(dag_run_id="r", total_budget_usd=5.0)
        mgr.allocate(["n"])
        mgr.record_usage("n", 5.0)
        assert mgr.utilization() == pytest.approx(1.0)

    def test_partial_utilization(self):
        mgr = _mgr(total=5.0, nodes=["a"])
        mgr.record_usage("a", 1.25)
        assert mgr.utilization() == pytest.approx(0.25)

    def test_utilization_exceeds_one_on_overrun(self):
        mgr = BudgetManager(dag_run_id="r", total_budget_usd=5.0)
        mgr.allocate(["n"])
        mgr.record_usage("n", 7.5)
        assert mgr.utilization() == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Pause event
# ---------------------------------------------------------------------------


class TestPauseEvent:
    def test_record_pause_logs_event(self):
        mgr = _mgr(nodes=["a"])
        mgr.record_pause()
        pause_events = [e for e in mgr.events if e.event_type == BudgetEventType.PAUSE]
        assert len(pause_events) == 1


# ---------------------------------------------------------------------------
# drain_events
# ---------------------------------------------------------------------------


class TestDrainEvents:
    def test_drain_returns_events_and_clears(self):
        mgr = _mgr(nodes=["a"])
        assert len(mgr.events) > 0
        drained = mgr.drain_events()
        assert len(drained) > 0
        assert len(mgr.events) == 0

    def test_drain_twice_returns_only_new_events(self):
        mgr = _mgr(nodes=["a"])
        mgr.drain_events()  # clear allocation events
        mgr.record_usage("a", 0.01)
        drained = mgr.drain_events()
        assert len(drained) == 1
        assert drained[0].event_type == BudgetEventType.USAGE
