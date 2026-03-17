"""FastAPI app — GET /dags/{id}/status.

L1 status only (DAG + per-node execution state from SQLite).
L2 metrics (tokens, cost, retries, budget) are available via structured logs
only — not exposed on this endpoint for MVP [P11].

Bound in-process by the Orchestrator; lifetime = run duration.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .state import StateStore

app = FastAPI(title="agent-agent", version="0.1.0")

# The StateStore instance is injected by the Orchestrator at startup.
_state: StateStore | None = None


def set_state_store(state: StateStore) -> None:
    """Inject the active StateStore. Called by Orchestrator before server start."""
    global _state  # noqa: PLW0603
    _state = state


def _get_state() -> StateStore:
    if _state is None:
        raise RuntimeError("StateStore not injected — call set_state_store() first")
    return _state


# ------------------------------------------------------------------
# Response models (L1 contract)
# ------------------------------------------------------------------


class NodeStatusResponse(BaseModel):
    id: str
    type: str
    status: str
    level: int
    composite_id: str
    worktree_path: str | None = None
    branch_name: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class DAGStatusResponse(BaseModel):
    dag_run_id: str
    issue_url: str
    repo_path: str
    status: str
    budget_usd: float
    usd_used: float
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    nodes: list[NodeStatusResponse]


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@app.get("/dags/{dag_run_id}/status", response_model=DAGStatusResponse)
async def get_dag_status(dag_run_id: str) -> DAGStatusResponse:
    """Return L1 status for a DAG run (DAG + per-node state)."""
    state = _get_state()
    dag_run = await state.get_dag_run(dag_run_id)
    if dag_run is None:
        raise HTTPException(status_code=404, detail=f"DAG run {dag_run_id!r} not found")

    nodes = await state.list_dag_nodes(dag_run_id)

    node_responses = [
        NodeStatusResponse(
            id=n.id,
            type=n.type.value,
            status=n.status.value,
            level=n.level,
            composite_id=n.composite_id,
            worktree_path=n.worktree_path,
            branch_name=n.branch_name,
            created_at=n.created_at,
            updated_at=n.updated_at,
            completed_at=n.completed_at,
        )
        for n in nodes
    ]

    return DAGStatusResponse(
        dag_run_id=dag_run.id,
        issue_url=dag_run.issue_url,
        repo_path=dag_run.repo_path,
        status=dag_run.status.value,
        budget_usd=dag_run.budget_usd,
        usd_used=dag_run.usd_used,
        created_at=dag_run.created_at,
        updated_at=dag_run.updated_at,
        completed_at=dag_run.completed_at,
        error=dag_run.error,
        nodes=node_responses,
    )
