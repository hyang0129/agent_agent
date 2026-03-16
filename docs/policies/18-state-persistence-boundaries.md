# Policy 18: State Persistence Boundaries

## Background / State of the Art

Durable execution engines like Temporal achieve crash recovery by recording every side effect in an append-only event history and replaying deterministically. LangGraph checkpoints full graph state after each node. The question for any agent orchestrator is: at what granularity should state be persisted?

See [state-persistence.md](../state-persistence.md) for full analysis of Temporal, LangGraph, Prefect, Airflow, and SQLite patterns.

---

## Policy

### 1. Persist node boundaries, not node internals

The orchestrator checkpoints the DAG structure and each node's inputs and outputs. Everything that happens *within* a node — agent tool calls, intermediate reasoning, partial results — is treated as ephemeral. If a node is interrupted, it is restarted from scratch, not resumed.

### 2. The persistence boundary is node completion

The checkpoint boundary is the moment an agent returns a structured result. At that point, the orchestrator atomically writes the node's output and status to SQLite. On crash recovery, completed nodes are preserved and any `running` node is reset to `pending` and re-executed.

### 3. No intra-node checkpointing

Agent tool calls, chain-of-thought steps, and partial file edits within a node are not persisted. A crashed node is a lost node — its work is redone. The cost of re-running one agent node is low compared to the engineering cost of making every agent resumable mid-execution.

### 4. Restart, don't resume

Agents are designed to be restartable: research is read-only, implementation overwrites files on a feature branch, tests are idempotent. There is no mechanism for resuming an agent mid-turn.

### 5. Parallel nodes are independent

When multiple nodes execute concurrently, each commits its own completion independently. A crash mid-layer preserves whichever nodes finished and restarts the rest.

### 6. DAGs are immutable within a generation

If execution reveals the plan is wrong, the orchestrator spawns a new DAG generation rather than mutating the running one. Each generation is a complete, immutable graph with simple resume semantics.

### 7. Event sourcing is rejected at this granularity

At node-handover granularity, an event log and a checkpoint table store identical information (node completed with output X). Event sourcing's value — deterministic replay — requires deterministic execution, which agent nodes cannot provide. The added complexity buys nothing.

### 8. Serialization with Pydantic, not pickle

Store agent outputs as JSON via Pydantic's `.model_dump_json()`:

- JSON is inspectable with standard tools.
- JSON is safe (no arbitrary code execution on deserialization).
- JSON is version-tolerant (adding a field doesn't break old records).
- Pydantic handles validation on both serialization and deserialization.

### 9. WAL mode for crash safety

SQLite MUST use Write-Ahead Logging (WAL) mode. WAL ensures crash during write doesn't corrupt the database, and readers are never blocked by writers.

---

## Rationale

This policy follows from a core property of the system: agent execution within a node is non-deterministic. A Claude API call cannot be replayed to produce the same result. There is no meaningful "replay" of intra-node state — only re-execution. Persisting intermediate agent state would add complexity without enabling recovery, because the saved state could not be used to skip ahead.

Node-level granularity is the right trade-off for an agent orchestrator where each node is an expensive but bounded LLM invocation. The cost of re-running one node on crash is one LLM call. The engineering cost of making agents resumable mid-execution — serializing tool call state, handling partial file writes, managing conversation continuations — far exceeds this.

The rejection of event sourcing is specific to this granularity. At the node level, events and checkpoints are equivalent. If the system later needs sub-node recovery (e.g., for agents that make many sequential tool calls), event sourcing could be reconsidered at that lower level.
