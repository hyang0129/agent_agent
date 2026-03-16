# State Persistence

## Design Policy

**Persist node boundaries, not node internals.** The orchestrator checkpoints the DAG structure and each node's inputs and outputs. Everything that happens *within* a node — agent tool calls, intermediate reasoning, partial results — is treated as ephemeral. If a node is interrupted, it is restarted from scratch, not resumed.

This follows from a core property of the system: agent execution within a node is non-deterministic. A Claude API call cannot be replayed to produce the same result. There is no meaningful "replay" of intra-node state — only re-execution. Persisting intermediate agent state would add complexity without enabling recovery, because the saved state could not be used to skip ahead.

The persistence boundary is therefore the **node completion**: the moment an agent returns a structured result. At that point the orchestrator atomically writes the node's output and status to SQLite. On crash recovery, completed nodes are preserved, and any node that was `running` is reset to `pending` and re-executed.

### Consequences

- **No intra-node checkpointing.** Agent tool calls, chain-of-thought steps, and partial file edits within a node are not persisted. A crashed node is a lost node — its work is redone.
- **Restart, don't resume.** The cost of re-running one agent node is low compared to the engineering cost of making every agent resumable mid-execution. Agents are designed to be restartable: research is read-only, implementation overwrites files on a feature branch, tests are idempotent.
- **Parallel nodes are independent.** When multiple nodes execute concurrently, each commits its own completion independently. A crash mid-layer preserves whichever nodes finished and restarts the rest.
- **DAGs are immutable within a generation.** If execution reveals the plan is wrong, the orchestrator spawns a new DAG generation rather than mutating the running one. Each generation is a complete, immutable graph with simple resume semantics.
- **Event sourcing is rejected.** At the node-handover granularity, an event log and a checkpoint table store identical information (node completed with output X). Event sourcing's value — deterministic replay — requires deterministic execution, which agent nodes cannot provide. The added complexity buys nothing.

## Prior Art

### Temporal Event Sourcing
Temporal stores an append-only event history and reconstructs state by replaying events deterministically. Workflows must be pure functions; all non-determinism is pushed to "activities" whose results are recorded in the event log. On replay, activities aren't re-executed — their stored results are substituted.

**Why we rejected it:** Deterministic replay requires caching activity results, which for agent nodes means storing the full agent output — the same data a checkpoint stores. The result is checkpointing with extra replay machinery. The complexity is not justified when the unit of work (a full agent invocation) is already the natural persistence boundary.

### LangGraph Checkpointing
LangGraph serializes the full graph state (all channel values) after each node execution and stores it in a configurable backend (SQLite, Postgres). On resume, it loads the latest checkpoint and continues from there.

- **SQLiteSaver**: File-based, zero-config, good for local use.
- **PostgresSaver**: For production, multi-process access.
- **MemorySaver**: In-memory only, for testing.

Checkpoints are keyed by `(thread_id, checkpoint_id)`, enabling multiple concurrent DAGs and point-in-time recovery.

**Limitation:** Checkpoint serialization uses Python `pickle` by default, which has security implications for untrusted data and versioning issues across code changes.

### Prefect 2+ State Management
Prefect stores task run states in a SQLite or Postgres database. Each task run has a state machine:

```
Pending → Running → Completed
                  → Failed → (retry) → Running
                  → Crashed → (resume) → Running
```

The state includes the task's result (serialized), timing metadata, and retry count. On restart, Prefect queries for incomplete runs and offers to resume them.

### Airflow XCom + Metadata DB
Airflow stores DAG execution state in a metadata database (SQLite for dev, Postgres for prod). Task results are passed between tasks via XCom (cross-communication), which serializes values to the database. State is durable but XCom was not designed for large payloads — it becomes a bottleneck with large agent outputs.

### SQLite as Application Database
SQLite has become the standard for local-first application state. Key properties:

- **ACID transactions**: State changes are atomic — no partial writes on crash.
- **WAL mode**: Concurrent readers don't block writers. Multiple processes can read while one writes.
- **Zero configuration**: No server, no setup, just a file.
- **Portable**: The database file can be copied, backed up, or inspected with standard tools.

Litestream and LiteFS extend SQLite with real-time replication for production use.

## Best Practices

### 1. Schema Design for DAG State

```sql
CREATE TABLE dags (
    id TEXT PRIMARY KEY,
    issue_url TEXT NOT NULL,
    issue_body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, running, completed, failed, aborted
    config JSON NOT NULL,                     -- budget limits, checkpoint level, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    dag_id TEXT NOT NULL REFERENCES dags(id),
    agent_type TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending, running, completed, failed, skipped, budget_exceeded
    input_context JSON,                       -- serialized context from upstream nodes
    output JSON,                              -- serialized agent output
    error TEXT,
    retries_used INTEGER DEFAULT 0,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE edges (
    from_node TEXT NOT NULL REFERENCES nodes(id),
    to_node TEXT NOT NULL REFERENCES nodes(id),
    PRIMARY KEY (from_node, to_node)
);

CREATE TABLE checkpoints (
    id TEXT PRIMARY KEY,
    dag_id TEXT NOT NULL REFERENCES dags(id),
    node_id TEXT REFERENCES nodes(id),
    checkpoint_type TEXT NOT NULL,            -- plan_review, pre_pr, custom
    status TEXT NOT NULL DEFAULT 'pending',   -- pending, approved, rejected, modified
    presented_data JSON,
    human_response JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);
```

### 2. Write-Ahead Logging (WAL) Mode

Always enable WAL mode for concurrent access:

```python
async def init_db(path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db
```

WAL mode ensures that a crash during a write doesn't corrupt the database, and readers are never blocked by writers.

### 3. Checkpoint After Every Node Completion

```python
async def complete_node(db: aiosqlite.Connection, node_id: str, result: AgentResult):
    async with db.execute("BEGIN"):
        await db.execute(
            """UPDATE nodes SET
                status = ?, output = ?, tokens_input = ?, tokens_output = ?,
                completed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (result.status, json.dumps(result.output),
             result.token_usage.input, result.token_usage.output, node_id)
        )
        await db.execute(
            "UPDATE dags SET updated_at = CURRENT_TIMESTAMP WHERE id = (SELECT dag_id FROM nodes WHERE id = ?)",
            (node_id,)
        )
        await db.commit()
```

The transaction ensures that node completion and DAG update are atomic.

### 4. Crash Recovery: Reset and Restart

```python
async def recover_incomplete_dags(db: aiosqlite.Connection) -> list[DAGState]:
    # Any node marked 'running' at startup was interrupted — reset it.
    # Its partial work is abandoned; the agent will redo it from scratch.
    await db.execute(
        "UPDATE nodes SET status = 'pending', started_at = NULL "
        "WHERE status = 'running'"
    )
    await db.commit()

    rows = await db.execute_fetchall(
        "SELECT * FROM dags WHERE status = 'running'"
    )
    incomplete = []
    for row in rows:
        nodes = await db.execute_fetchall(
            "SELECT * FROM nodes WHERE dag_id = ? AND status = 'pending'",
            (row['id'],)
        )
        if nodes:
            incomplete.append(DAGState.from_db(row, nodes))
    return incomplete
```

On startup, the orchestrator resets any `running` nodes to `pending` — their partial work is discarded. Completed nodes are untouched. The executor then schedules pending nodes whose dependencies are all completed, resuming the DAG from where it left off at node granularity.

### 5. Serialization with Pydantic, Not Pickle

Store agent outputs as JSON via Pydantic's `.model_dump_json()`, not Python pickle:

- JSON is inspectable with standard tools (`jq`, SQLite CLI)
- JSON is safe (no arbitrary code execution on deserialization)
- JSON is version-tolerant (adding a field doesn't break old records)
- Pydantic handles validation on both serialization and deserialization

### 6. Database File Location

```
repos/agent_agent/
├── data/
│   ├── agent_agent.db        # Main state database
│   └── agent_agent.db-wal    # WAL file (auto-managed)
```

Add `data/*.db*` to `.gitignore`. The database is runtime state, not source code.

### 7. Migrations

Use a simple migration system for schema evolution:

```python
MIGRATIONS = [
    ("001", "CREATE TABLE dags (...)"),
    ("002", "CREATE TABLE nodes (...)"),
    ("003", "ALTER TABLE nodes ADD COLUMN cache_key TEXT"),
]

async def migrate(db: aiosqlite.Connection):
    await db.execute("CREATE TABLE IF NOT EXISTS migrations (id TEXT PRIMARY KEY)")
    applied = {r[0] for r in await db.execute_fetchall("SELECT id FROM migrations")}
    for mid, sql in MIGRATIONS:
        if mid not in applied:
            await db.execute(sql)
            await db.execute("INSERT INTO migrations (id) VALUES (?)", (mid,))
    await db.commit()
```

## Rejected Approaches

### In-Memory Only
All state lives in Python dictionaries. Fast, easy to implement, loses everything on crash. Acceptable for prototyping but not for any workflow that costs money to re-run.

### Flat File Checkpoints
Write JSON or YAML files to disk after each step: `checkpoint_001.json`, `checkpoint_002.json`. On resume, find the latest checkpoint file and load it. Problems: no atomicity (partial writes on crash), no concurrent access, manual file management, and no query capability.

### Redis
In-memory key-value store with optional persistence (RDB snapshots, AOF append-only log). Fast and supports complex data structures. But it's a separate server process, requires configuration, and its persistence guarantees are weaker than SQLite's ACID transactions (RDB can lose the last few seconds of writes).

### Full Postgres
Production-grade relational database. Overkill for a single-developer local orchestrator — requires running a database server, managing connections, and handling migrations. The right choice for the future multi-user remote version, not for the MVP.

### Pickle Files
Serialize Python objects to disk with `pickle.dump()`. Preserves arbitrary Python objects (including class instances, closures, etc.). Problems: not human-readable, not safe to deserialize untrusted data, breaks when class definitions change, not queryable.
