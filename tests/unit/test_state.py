"""Tests for the SQLite state store — CRUD round-trips against :memory:."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from agent_agent.models.budget import BudgetEvent, BudgetEventType
from agent_agent.models.dag import (
    DAGNode,
    DAGRun,
    DAGRunStatus,
    ExecutionMeta,
    NodeResult,
    NodeStatus,
    NodeType,
)
from agent_agent.models.escalation import EscalationRecord, EscalationSeverity
from agent_agent.models.agent import PlanOutput
from agent_agent.state import StateStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run(run_id: str = "run-1") -> DAGRun:
    now = _now()
    return DAGRun(
        id=run_id,
        issue_url="https://github.com/x/y/issues/1",
        repo_path="/tmp/repo",
        budget_usd=5.0,
        created_at=now,
        updated_at=now,
    )


def _node(run_id: str = "run-1", node_id: str = "node-1") -> DAGNode:
    now = _now()
    return DAGNode(
        id=node_id,
        dag_run_id=run_id,
        type=NodeType.PLAN,
        level=0,
        composite_id="A",
        created_at=now,
        updated_at=now,
    )


@pytest_asyncio.fixture
async def store() -> StateStore:
    s = StateStore(":memory:")
    await s.init()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# dag_runs
# ---------------------------------------------------------------------------

class TestDAGRuns:
    @pytest.mark.asyncio
    async def test_create_and_get(self, store: StateStore):
        run = _run()
        await store.create_dag_run(run)
        fetched = await store.get_dag_run("run-1")
        assert fetched is not None
        assert fetched.id == "run-1"
        assert fetched.status == DAGRunStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store: StateStore):
        assert await store.get_dag_run("nonexistent") is None

    @pytest.mark.asyncio
    async def test_update_status(self, store: StateStore):
        await store.create_dag_run(_run())
        await store.update_dag_run_status("run-1", "completed")
        fetched = await store.get_dag_run("run-1")
        assert fetched is not None
        assert fetched.status == DAGRunStatus.COMPLETED
        assert fetched.completed_at is not None

    @pytest.mark.asyncio
    async def test_increment_usd(self, store: StateStore):
        await store.create_dag_run(_run())
        await store.increment_usd_used("run-1", 0.042)
        await store.increment_usd_used("run-1", 0.018)
        fetched = await store.get_dag_run("run-1")
        assert fetched is not None
        assert fetched.usd_used == pytest.approx(0.06)


# ---------------------------------------------------------------------------
# dag_nodes
# ---------------------------------------------------------------------------

class TestDAGNodes:
    @pytest.mark.asyncio
    async def test_create_and_get(self, store: StateStore):
        await store.create_dag_run(_run())
        node = _node()
        await store.create_dag_node(node)
        fetched = await store.get_dag_node("node-1")
        assert fetched is not None
        assert fetched.type == NodeType.PLAN
        assert fetched.parent_node_ids == []

    @pytest.mark.asyncio
    async def test_list_nodes(self, store: StateStore):
        await store.create_dag_run(_run())
        await store.create_dag_node(_node(node_id="n1"))
        await store.create_dag_node(_node(node_id="n2"))
        nodes = await store.list_dag_nodes("run-1")
        assert len(nodes) == 2

    @pytest.mark.asyncio
    async def test_update_status_sets_completed_at(self, store: StateStore):
        await store.create_dag_run(_run())
        await store.create_dag_node(_node())
        await store.update_dag_node_status("node-1", "completed")
        fetched = await store.get_dag_node("node-1")
        assert fetched is not None
        assert fetched.status == NodeStatus.COMPLETED
        assert fetched.completed_at is not None

    @pytest.mark.asyncio
    async def test_update_sets_branch_name(self, store: StateStore):
        await store.create_dag_run(_run())
        await store.create_dag_node(_node())
        await store.update_dag_node_status("node-1", "running", branch_name="agent/1/fix")
        fetched = await store.get_dag_node("node-1")
        assert fetched is not None
        assert fetched.branch_name == "agent/1/fix"


# ---------------------------------------------------------------------------
# node_results
# ---------------------------------------------------------------------------

class TestNodeResults:
    @pytest.mark.asyncio
    async def test_save_and_get(self, store: StateStore):
        await store.create_dag_run(_run())
        await store.create_dag_node(_node())
        now = _now()
        result = NodeResult(
            node_id="node-1",
            dag_run_id="run-1",
            output=PlanOutput(investigation_summary="done"),
            meta=ExecutionMeta(
                started_at=now,
                completed_at=now,
                input_tokens=100,
                output_tokens=50,
            ),
        )
        await store.save_node_result(result)
        fetched = await store.get_node_result("node-1")
        assert fetched is not None
        assert isinstance(fetched.output, PlanOutput)
        assert fetched.output.investigation_summary == "done"
        assert fetched.meta.input_tokens == 100

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store: StateStore):
        assert await store.get_node_result("nonexistent") is None


# ---------------------------------------------------------------------------
# shared_context  (append-only)
# ---------------------------------------------------------------------------

class TestSharedContext:
    @pytest.mark.asyncio
    async def test_append_and_list(self, store: StateStore):
        await store.create_dag_run(_run())
        await store.append_shared_context(
            str(uuid.uuid4()), "run-1", "node-1", "file_mapping",
            {"path": "src/auth.py", "description": "token validation"}
        )
        entries = await store.list_shared_context("run-1")
        assert len(entries) == 1
        assert entries[0]["category"] == "file_mapping"

    @pytest.mark.asyncio
    async def test_multiple_entries_ordered_by_timestamp(self, store: StateStore):
        await store.create_dag_run(_run())
        for i in range(3):
            await store.append_shared_context(
                str(uuid.uuid4()), "run-1", "node-1", "constraint", {"n": i}
            )
        entries = await store.list_shared_context("run-1")
        assert len(entries) == 3


# ---------------------------------------------------------------------------
# budget_events
# ---------------------------------------------------------------------------

class TestBudgetEvents:
    @pytest.mark.asyncio
    async def test_append_and_list(self, store: StateStore):
        await store.create_dag_run(_run())
        event = BudgetEvent(
            id=str(uuid.uuid4()),
            dag_run_id="run-1",
            node_id="node-1",
            event_type=BudgetEventType.INITIAL_ALLOCATION,
            usd_before=5.0,
            usd_after=5.0,
            reason="equal split",
            timestamp=_now(),
        )
        await store.append_budget_event(event)
        events = await store.list_budget_events("run-1")
        assert len(events) == 1
        assert events[0].event_type == BudgetEventType.INITIAL_ALLOCATION


# ---------------------------------------------------------------------------
# escalations
# ---------------------------------------------------------------------------

class TestEscalations:
    @pytest.mark.asyncio
    async def test_create_and_get(self, store: StateStore):
        await store.create_dag_run(_run())
        record = EscalationRecord(
            id=str(uuid.uuid4()),
            dag_run_id="run-1",
            node_id="node-1",
            severity=EscalationSeverity.HIGH,
            trigger="cycle cap exhausted",
            message='{"attempt_history": []}',
            created_at=_now(),
        )
        await store.create_escalation(record)
        fetched = await store.get_escalation(record.id)
        assert fetched is not None
        assert fetched.severity == EscalationSeverity.HIGH

    @pytest.mark.asyncio
    async def test_list_escalations(self, store: StateStore):
        await store.create_dag_run(_run())
        for _ in range(2):
            await store.create_escalation(EscalationRecord(
                id=str(uuid.uuid4()),
                dag_run_id="run-1",
                node_id=None,
                severity=EscalationSeverity.MEDIUM,
                trigger="budget exhausted",
                message="{}",
                created_at=_now(),
            ))
        records = await store.list_escalations("run-1")
        assert len(records) == 2
