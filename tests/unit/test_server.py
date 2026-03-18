"""Server unit tests — status endpoint against :memory: state.

Tests:
  - Valid dag_run_id → response includes DAG status + per-node status list
  - Unknown dag_run_id → 404
  - Response shape matches L1 contract (no L2 metrics fields)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from agent_agent.models.dag import DAGNode, DAGRun, DAGRunStatus, NodeStatus, NodeType
from agent_agent.server import app, set_state_store
from agent_agent.state import StateStore


@pytest.fixture()
async def state_store() -> StateStore:
    store = StateStore(":memory:")
    await store.init()
    set_state_store(store)
    yield store  # type: ignore[misc]
    await store.close()


@pytest.fixture()
async def client(state_store: StateStore) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c  # type: ignore[misc]


@pytest.fixture()
async def sample_run(state_store: StateStore) -> DAGRun:
    now = datetime.now(timezone.utc)
    run = DAGRun(
        id="test-run-001",
        issue_url="https://github.com/test/repo/issues/1",
        repo_path="/tmp/fake-repo",
        status=DAGRunStatus.COMPLETED,
        budget_usd=5.0,
        usd_used=0.5,
        created_at=now,
        updated_at=now,
    )
    await state_store.create_dag_run(run)

    nodes = [
        DAGNode(
            id="test-run-001-l0-plan",
            dag_run_id="test-run-001",
            type=NodeType.PLAN,
            status=NodeStatus.COMPLETED,
            level=0,
            composite_id="L0",
            created_at=now,
            updated_at=now,
        ),
        DAGNode(
            id="test-run-001-l1-coding",
            dag_run_id="test-run-001",
            type=NodeType.CODING,
            status=NodeStatus.COMPLETED,
            level=1,
            composite_id="A",
            parent_node_ids=["test-run-001-l0-plan"],
            created_at=now,
            updated_at=now,
        ),
    ]
    for n in nodes:
        await state_store.create_dag_node(n)

    return run


class TestDAGStatusEndpoint:
    async def test_valid_run_returns_status(self, client: AsyncClient, sample_run: DAGRun) -> None:
        resp = await client.get(f"/dags/{sample_run.id}/status")
        assert resp.status_code == 200

        data = resp.json()
        assert data["dag_run_id"] == sample_run.id
        assert data["status"] == "completed"
        assert data["issue_url"] == sample_run.issue_url
        assert data["budget_usd"] == 5.0
        assert data["usd_used"] == 0.5

        # Per-node status list present [P11 L1]
        assert "nodes" in data
        assert len(data["nodes"]) == 2

        node_ids = {n["id"] for n in data["nodes"]}
        assert "test-run-001-l0-plan" in node_ids
        assert "test-run-001-l1-coding" in node_ids

        # Each node has required L1 fields
        for node in data["nodes"]:
            assert "id" in node
            assert "type" in node
            assert "status" in node
            assert "level" in node
            assert "composite_id" in node

    async def test_unknown_run_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/dags/nonexistent-run/status")
        assert resp.status_code == 404

    async def test_response_has_no_l2_metrics(
        self, client: AsyncClient, sample_run: DAGRun
    ) -> None:
        """L1 contract: no L2 metrics fields exposed on this endpoint for MVP."""
        resp = await client.get(f"/dags/{sample_run.id}/status")
        data = resp.json()

        # These L2 fields should NOT be present in the L1 response
        assert "total_tokens_used" not in data
        assert "estimated_cost_usd" not in data
        assert "token_budget" not in data

        for node in data["nodes"]:
            assert "input_tokens" not in node
            assert "output_tokens" not in node
            assert "retry_count" not in node
