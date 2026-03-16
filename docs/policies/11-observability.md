# Policy 11: Observability

An agent orchestrator is a black box by default — multiple agents run concurrently, make LLM calls, manipulate files, and produce outputs that are invisible without explicit instrumentation. When something goes wrong (wrong code generated, excessive cost, agent stuck in a loop), the developer needs to understand what happened, why, and where. This policy defines three levels of observability, what each level must capture, and how those signals are produced and stored. MVP implements Levels 1 and 2 natively; Level 3 is available via log files.

---

### 1. Three Observability Levels

Every observable event belongs to one of three levels. Higher levels are more expensive to produce and store, so each level has different retention and collection requirements.

**Level 1 — Status (is it working?)**
- DAG execution status: `pending` / `running` / `completed` / `failed`
- Per-node status: `pending` / `running` / `completed` / `failed` / `skipped` / `frozen_at_budget` / `dead_letter`
- Surfaced via the `/api/v1/dags/{dag_id}/status` endpoint and CLI display
- Required for MVP

**Level 2 — Metrics (how well is it working?)**
- Total tokens used and cost, per-agent token breakdown, run duration
- Retry count per node; `frozen_at_budget` and `dead_letter` counts
- Budget event log (see Policy 07 — Budget Allocation)
- Stored in SQLite; queryable across runs
- Required for MVP

**Level 3 — Traces (why did it do that?)**
- Full LLM inputs and outputs per agent invocation
- Every tool call and its result
- Context object passed to each node at dispatch time
- Available via structured log files for MVP; OTel-compatible format required so a trace viewer can be added without re-instrumentation

**P1. All three levels must be produced by the same instrumentation path.** A single `emit_event` call on the orchestrator produces the Level 1 status update, the Level 2 metric record, and the Level 3 log entry for that event. Do not maintain three separate logging paths.

---

### 2. Structured Log Format

**P2. Every log record must be a JSON object emitted via `structlog`.** Plain-text log lines are prohibited.

**P3. Every log record must include the following fields:**

```json
{
  "timestamp": "2026-03-15T10:23:45.123Z",
  "level": "info",
  "dag_run_id": "dag_abc123",
  "node_id": "node_research_01",
  "agent_type": "research",
  "event": "llm_call_complete",
  "input_tokens": 4523,
  "output_tokens": 891,
  "duration_ms": 3200,
  "model": "claude-sonnet-4-6",
  "cache_read_tokens": 3100,
  "cache_write_tokens": 0
}
```

Fields `dag_run_id` and `node_id` are mandatory on every record; they are the primary keys for filtering and grouping. Records that predate a node assignment (e.g., DAG initialization) use `node_id: null`.

**P4. Log context is injected via `structlog` bound loggers, not passed manually.** At DAG dispatch, bind `dag_run_id`. At node dispatch, bind `node_id` and `agent_type`. These fields propagate automatically to all log calls within that scope.

---

### 3. Status Endpoint

**P5. The orchestrator MUST expose a real-time status endpoint:**

```
GET /api/v1/dags/{dag_run_id}/status
```

Response schema:

```python
class NodeStatus(BaseModel):
    id: str
    agent_type: str
    status: Literal["pending", "running", "completed", "failed",
                    "skipped", "frozen_at_budget", "dead_letter"]
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    retry_count: int

class DAGStatus(BaseModel):
    dag_run_id: str
    issue_ref: str
    status: Literal["pending", "running", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None
    nodes: list[NodeStatus]
    total_tokens_used: int
    token_budget: int
    estimated_cost_usd: float | None
```

The endpoint reads from the SQLite state store; it does not require polling agents directly. Node status transitions are written to SQLite as they occur (see Policy 10 — Node Execution Model).

---

### 4. CLI Progress Display

**P6. For MVP, the CLI display is the primary human interface for run progress.** No web dashboard is required.

The display is updated in-place (using `rich` or equivalent) as node status transitions arrive:

```
Issue #42: Add rate limiting
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[✓] research_01      3.2s    5,414 tok
[▸] implement_01     1.8s    2,100 tok  (streaming...)
[ ] test_01
[ ] review_01
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total: 7,514 / 50,000 tokens  ~$0.12
```

**P7. The CLI display reads from the same status endpoint as external callers.** It does not have special internal access to orchestrator state. This ensures the endpoint is always exercised during development.

---

### 5. Error Correlation

**P8. When a node fails, the log record for the failure event must include:**

- The exact input context the agent received (or a reference to it by hash if it is large)
- The LLM response that caused the failure, or the exception message if the failure was pre-LLM
- The IDs of upstream nodes whose outputs fed into this agent
- The budget state at the time of failure (tokens used, tokens remaining)

This is the minimum needed to turn "agent failed" into a diagnosable root cause. Records that omit these fields are not actionable.

**P9. Failure events are classified using the same taxonomy as Policy 10 (Transient / Agent Error / Resource Exhaustion / Deterministic).** The classification is included in the log record as `failure_class`.

---

### 6. Retention Policy

| Level | Store | Retention |
|-------|-------|-----------|
| 1 — Status | SQLite `node_events` table | Indefinite |
| 2 — Metrics | SQLite `budget_events` + `node_events` | 90 days |
| 3 — Traces | Structured log files (JSON lines) | 7 days |

**P10. Level 3 log files are rotated daily and purged after 7 days.** Full LLM I/O is large and may contain repository source code; long retention creates both storage and privacy risk.

**P11. Level 1 status records are never purged.** They are small and constitute the audit trail for which issues were resolved and how.

---

### 7. OTel Compatibility (Forward Compatibility Requirement)

**P12. Level 3 log records must be structured so they can be exported as OpenTelemetry spans without schema changes.** Required fields that map to OTel span attributes:

| Log field | OTel attribute |
|-----------|---------------|
| `dag_run_id` | `trace_id` (or parent span attribute) |
| `node_id` | `span_id` |
| `agent_type` | `agent.type` |
| `event` | span name |
| `duration_ms` | span duration |
| `input_tokens` | `llm.usage.prompt_tokens` |
| `output_tokens` | `llm.usage.completion_tokens` |
| `model` | `llm.model` |

MVP emits these to log files. Adding an OTel exporter later requires only a new sink, not schema changes.

**P13. Do not adopt an LLM observability SaaS platform (LangSmith, Helicone, etc.) as the primary logging target for MVP.** External platforms introduce vendor lock-in and require outbound network access. Use structured local logs that are compatible with any downstream target.

---

### 8. Future Direction: Agent-Interpreted Observability

**Out of scope for MVP.** The long-term goal is that humans never read raw logs. A log interpretation agent consumes OTel traces and metrics and produces human-readable summaries on demand. The OTel-compatible schema required by P12 provides the machine-readable foundation this requires.

When extending beyond MVP, priority order is:
1. Log interpretation agents that consume traces and explain what happened
2. Agent-first telemetry design — instrument for agent consumption, not dashboard viewing
3. No human-facing log dashboards as the primary interface

**P14. Do not design the telemetry schema for dashboard readability at the cost of machine parseability.** Every field should be typed and enumerable. Free-text descriptions belong in a separate `message` field, never as the primary discriminator.

---

## Quick Reference

| Requirement | MVP | Beyond MVP |
|-------------|-----|-----------|
| Level 1 status (DAG + node) | Required | — |
| Level 2 metrics (tokens, cost, retries) | Required | — |
| Level 3 traces (full LLM I/O) | Log files only | OTel export |
| Structured JSON logs via `structlog` | Required | — |
| `dag_run_id` + `node_id` on every record | Required | — |
| `/dags/{id}/status` endpoint | Required | — |
| CLI progress display | Required | — |
| Error correlation in failure events | Required | — |
| OTel-compatible field names | Required | — |
| SaaS observability platform | Prohibited | Optional |
| Log interpretation agent | Out of scope | Priority 1 |
| Retention: status indefinite, metrics 90d, traces 7d | Required | — |
