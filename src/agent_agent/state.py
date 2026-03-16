"""SQLite state store — async CRUD for all 6 tables.

All persistent state lives here; agents and composites are stateless.
Uses aiosqlite. One database per environment (data/dev.db, data/prod.db, :memory: for test).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from .models.budget import BudgetEvent
from .models.dag import DAGNode, DAGRun, NodeResult
from .models.escalation import EscalationRecord

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS dag_runs (
    id          TEXT PRIMARY KEY,
    issue_url   TEXT NOT NULL,
    repo_path   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    budget_usd  REAL NOT NULL,
    usd_used    REAL NOT NULL DEFAULT 0.0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    completed_at TEXT,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS dag_nodes (
    id              TEXT PRIMARY KEY,
    dag_run_id      TEXT NOT NULL REFERENCES dag_runs(id),
    type            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    level           INTEGER NOT NULL,
    composite_id    TEXT NOT NULL,
    parent_node_ids TEXT NOT NULL DEFAULT '[]',
    child_node_ids  TEXT NOT NULL DEFAULT '[]',
    worktree_path   TEXT,
    branch_name     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS node_results (
    node_id     TEXT PRIMARY KEY REFERENCES dag_nodes(id),
    dag_run_id  TEXT NOT NULL REFERENCES dag_runs(id),
    output      TEXT NOT NULL,
    meta        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_context (
    id              TEXT PRIMARY KEY,
    dag_run_id      TEXT NOT NULL REFERENCES dag_runs(id),
    source_node_id  TEXT NOT NULL,
    category        TEXT NOT NULL,
    data            TEXT NOT NULL,
    timestamp       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budget_events (
    id              TEXT PRIMARY KEY,
    dag_run_id      TEXT NOT NULL REFERENCES dag_runs(id),
    node_id         TEXT,
    event_type      TEXT NOT NULL,
    usd_before      REAL NOT NULL,
    usd_after       REAL NOT NULL,
    reason          TEXT NOT NULL,
    timestamp       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS escalations (
    id          TEXT PRIMARY KEY,
    dag_run_id  TEXT NOT NULL REFERENCES dag_runs(id),
    node_id     TEXT,
    severity    TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    message     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    resolution  TEXT,
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    """Async SQLite state store. Call `await store.init()` before use."""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._db_url != ":memory:":
            Path(self._db_url).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_url)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("StateStore not initialised — call await store.init() first")
        return self._conn

    # ------------------------------------------------------------------
    # dag_runs
    # ------------------------------------------------------------------

    async def create_dag_run(self, run: DAGRun) -> None:
        await self._db.execute(
            """INSERT INTO dag_runs
               (id, issue_url, repo_path, status, budget_usd, usd_used,
                created_at, updated_at, completed_at, error)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (run.id, run.issue_url, run.repo_path, run.status.value,
             run.budget_usd, run.usd_used,
             run.created_at.isoformat(), run.updated_at.isoformat(),
             run.completed_at.isoformat() if run.completed_at else None,
             run.error),
        )
        await self._db.commit()

    async def get_dag_run(self, run_id: str) -> DAGRun | None:
        async with self._db.execute(
            "SELECT * FROM dag_runs WHERE id=?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return DAGRun.model_validate(dict(row))

    async def update_dag_run_status(
        self, run_id: str, status: str, error: str | None = None
    ) -> None:
        completed_at = _now() if status in ("completed", "failed", "escalated") else None
        await self._db.execute(
            """UPDATE dag_runs SET status=?, error=?, updated_at=?, completed_at=?
               WHERE id=?""",
            (status, error, _now(), completed_at, run_id),
        )
        await self._db.commit()

    async def increment_usd_used(self, run_id: str, usd: float) -> None:
        await self._db.execute(
            "UPDATE dag_runs SET usd_used=usd_used+?, updated_at=? WHERE id=?",
            (usd, _now(), run_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # dag_nodes
    # ------------------------------------------------------------------

    async def create_dag_node(self, node: DAGNode) -> None:
        await self._db.execute(
            """INSERT INTO dag_nodes
               (id, dag_run_id, type, status, level, composite_id,
                parent_node_ids, child_node_ids, worktree_path, branch_name,
                created_at, updated_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (node.id, node.dag_run_id, node.type.value, node.status.value,
             node.level, node.composite_id,
             json.dumps(node.parent_node_ids), json.dumps(node.child_node_ids),
             node.worktree_path, node.branch_name,
             node.created_at.isoformat(), node.updated_at.isoformat(),
             node.completed_at.isoformat() if node.completed_at else None),
        )
        await self._db.commit()

    async def get_dag_node(self, node_id: str) -> DAGNode | None:
        async with self._db.execute(
            "SELECT * FROM dag_nodes WHERE id=?", (node_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["parent_node_ids"] = json.loads(d["parent_node_ids"])
        d["child_node_ids"] = json.loads(d["child_node_ids"])
        return DAGNode.model_validate(d)

    async def list_dag_nodes(self, run_id: str) -> list[DAGNode]:
        async with self._db.execute(
            "SELECT * FROM dag_nodes WHERE dag_run_id=? ORDER BY level, id",
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
        nodes = []
        for row in rows:
            d = dict(row)
            d["parent_node_ids"] = json.loads(d["parent_node_ids"])
            d["child_node_ids"] = json.loads(d["child_node_ids"])
            nodes.append(DAGNode.model_validate(d))
        return nodes

    async def update_dag_node_status(
        self,
        node_id: str,
        status: str,
        worktree_path: str | None = None,
        branch_name: str | None = None,
    ) -> None:
        completed_at = _now() if status in ("completed", "failed", "skipped") else None
        await self._db.execute(
            """UPDATE dag_nodes
               SET status=?, updated_at=?, completed_at=?,
                   worktree_path=COALESCE(?, worktree_path),
                   branch_name=COALESCE(?, branch_name)
               WHERE id=?""",
            (status, _now(), completed_at, worktree_path, branch_name, node_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # node_results
    # ------------------------------------------------------------------

    async def save_node_result(self, result: NodeResult) -> None:
        await self._db.execute(
            """INSERT OR REPLACE INTO node_results (node_id, dag_run_id, output, meta)
               VALUES (?,?,?,?)""",
            (result.node_id, result.dag_run_id,
             result.output.model_dump_json(),
             result.meta.model_dump_json()),
        )
        await self._db.commit()

    async def get_node_result(self, node_id: str) -> NodeResult | None:
        async with self._db.execute(
            "SELECT * FROM node_results WHERE node_id=?", (node_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        from pydantic import TypeAdapter
        from .models.dag import ExecutionMeta
        from .models.agent import AgentOutput
        _output_adapter: TypeAdapter = TypeAdapter(AgentOutput)  # type: ignore[type-arg]
        d = dict(row)
        return NodeResult(
            node_id=d["node_id"],
            dag_run_id=d["dag_run_id"],
            output=_output_adapter.validate_python(json.loads(d["output"])),
            meta=ExecutionMeta.model_validate(json.loads(d["meta"])),
        )

    # ------------------------------------------------------------------
    # shared_context  (append-only)
    # ------------------------------------------------------------------

    async def append_shared_context(
        self,
        entry_id: str,
        dag_run_id: str,
        source_node_id: str,
        category: str,
        data: dict,  # type: ignore[type-arg]
    ) -> None:
        await self._db.execute(
            """INSERT INTO shared_context
               (id, dag_run_id, source_node_id, category, data, timestamp)
               VALUES (?,?,?,?,?,?)""",
            (entry_id, dag_run_id, source_node_id, category,
             json.dumps(data), _now()),
        )
        await self._db.commit()

    async def list_shared_context(self, run_id: str) -> list[dict]:  # type: ignore[type-arg]
        async with self._db.execute(
            "SELECT * FROM shared_context WHERE dag_run_id=? ORDER BY timestamp",
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # budget_events  (append-only)
    # ------------------------------------------------------------------

    async def append_budget_event(self, event: BudgetEvent) -> None:
        await self._db.execute(
            """INSERT INTO budget_events
               (id, dag_run_id, node_id, event_type,
                usd_before, usd_after, reason, timestamp)
               VALUES (?,?,?,?,?,?,?,?)""",
            (event.id, event.dag_run_id, event.node_id, event.event_type.value,
             event.usd_before, event.usd_after, event.reason,
             event.timestamp.isoformat()),
        )
        await self._db.commit()

    async def list_budget_events(self, run_id: str) -> list[BudgetEvent]:
        async with self._db.execute(
            "SELECT * FROM budget_events WHERE dag_run_id=? ORDER BY timestamp",
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [BudgetEvent.model_validate(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # escalations
    # ------------------------------------------------------------------

    async def create_escalation(self, record: EscalationRecord) -> None:
        await self._db.execute(
            """INSERT INTO escalations
               (id, dag_run_id, node_id, severity, trigger,
                message, status, resolution, created_at, resolved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (record.id, record.dag_run_id, record.node_id,
             record.severity.value, record.trigger, record.message,
             record.status.value, record.resolution,
             record.created_at.isoformat(),
             record.resolved_at.isoformat() if record.resolved_at else None),
        )
        await self._db.commit()

    async def get_escalation(self, escalation_id: str) -> EscalationRecord | None:
        async with self._db.execute(
            "SELECT * FROM escalations WHERE id=?", (escalation_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return EscalationRecord.model_validate(dict(row))

    async def list_escalations(self, run_id: str) -> list[EscalationRecord]:
        async with self._db.execute(
            "SELECT * FROM escalations WHERE dag_run_id=? ORDER BY created_at",
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [EscalationRecord.model_validate(dict(r)) for r in rows]


@asynccontextmanager
async def open_state_store(db_url: str) -> AsyncIterator[StateStore]:
    store = StateStore(db_url)
    await store.init()
    try:
        yield store
    finally:
        await store.close()
