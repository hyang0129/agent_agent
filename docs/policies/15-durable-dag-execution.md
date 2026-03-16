# Policy 15: Durable DAG Execution over SQLite

## Background / State of the Art

Durable execution engines like Temporal guarantee that a workflow runs to completion regardless of infrastructure failures by separating deterministic orchestration logic from retryable side effects, recording every state transition in an append-only event history, and recovering via replay. Agent DAGs have the same structure: deterministic orchestration (which agent runs next, how results flow) wrapping non-deterministic side effects (LLM calls, GitHub API calls, code execution).

See [error-handling-and-recovery.md](../error-handling-and-recovery.md) for full state-of-the-art analysis including Temporal, LangGraph, Prefect, and Airflow patterns.

---

## Policy

### 1. Single-process SQLite, no external workflow engine

Agent Agent adopts the architectural discipline of durable execution without taking on Temporal as a runtime dependency. The orchestrator is a single-process FastAPI server backed by SQLite — no external workflow engine, no message broker, no distributed state. If agent_agent scales to multi-user or multi-machine execution, Temporal becomes the natural migration path.

### 2. Every issue resolution is a DAG run with node-level state machine

Every GitHub issue resolution is a DAG run. Each sub-task is a DAG node mapping to exactly one agent invocation. Each node follows a state machine:

```
                  ┌──────────────────────────────────┐
                  │                                  │
                  ▼                                  │
pending ──► running ──► completed                    │
              │                                      │
              ├──► failed ──► running  (retry)       │
              │       │                              │
              │       └──► dead_letter (max retries) │
              │                                      │
              └──► crashed ──► pending  (auto-reset) ┘
```

- **pending** — waiting for upstream dependencies.
- **running** — agent invocation in progress, heartbeat timestamp updated periodically.
- **completed** — agent returned success with stored output.
- **failed** — agent returned structured error, eligible for retry with context enrichment.
- **crashed** — infrastructure failure detected (stale heartbeat on recovery). Reset to pending automatically.
- **dead_letter** — max retries exhausted, parked with full attempt history.

### 3. Append-only event log for every node execution attempt

Every node execution attempt is appended to a `node_events` table. Events are never updated or deleted. The current state of a node is derived by reading its latest event. On crash recovery, the orchestrator replays the event log to reconstruct DAG state.

### 4. Crash recovery via event log reconstruction

On startup, the orchestrator:

1. Finds in-flight DAG runs (`status = 'running'`).
2. Detects crashed nodes — any node in `running` state with a stale heartbeat (older than `heartbeat_timeout`, default: 120s) is transitioned to `crashed`, then reset to `pending`.
3. Rebuilds DAG state from `node_events` — completed nodes are preserved, their stored outputs feed downstream nodes.
4. Resumes traversal from wherever the DAG left off.

### 5. Heartbeats for liveness detection

Agents emit heartbeats during execution. The executor updates the node's `last_heartbeat` timestamp in SQLite. This serves crash detection (stale heartbeats on recovery) and liveness monitoring (API reports active agents).

### 6. Compensation on DAG failure

When a node reaches `dead_letter` and its downstream subtree cannot proceed, the orchestrator runs compensation for completed upstream side effects:

| Side Effect | Compensation |
|---|---|
| Branch created | Delete branch (if no commits worth preserving) |
| Commits pushed | Leave in place (branches are disposable) |
| Draft PR opened | Close PR with summary comment |
| PR comments posted | No action needed |

Compensation is best-effort with its own retry policy. Cleanup failures are logged but do not block the DAG from being marked as failed.

### 7. Classify errors before deciding to retry

Every error MUST be classified as transient, agent logic, deterministic, budget exceeded, or unknown before a retry decision is made. Transient and agent logic errors are retried. Deterministic and budget errors fail fast. Unknown gets one retry before escalation. (See Policy 07 for full retry rules.)

### 8. Budget enforcement integrated with node dispatch

Before dispatching a node, check remaining budget. If budget is exhausted mid-DAG: stop dispatching new nodes, let in-flight nodes complete, mark remaining nodes as `skipped`, preserve all partial results, and report to the user.

---

## Rationale

**Why not Temporal?** Temporal is a distributed system requiring a server cluster, persistence backend, and worker processes. Agent Agent is a single-developer local tool. SQLite gives durable state with zero operational overhead. The architectural patterns are aligned for future migration.

**Why node-level granularity?** Each agent invocation is a single LLM call (or small chain). Replaying an entire node costs one LLM call — expensive but bounded. Sub-node checkpointing adds complexity without meaningful recovery benefit since LLM calls are not resumable mid-stream.

**Why append-only events?** The event log provides full auditability (every attempt, every error, every timing) for free. Mutable status columns lose history. The marginal cost of extra SQLite rows per node is negligible compared to the debugging value when investigating multi-attempt failures.
