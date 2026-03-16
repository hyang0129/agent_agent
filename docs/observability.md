# Observability

## Problem Statement

An agent orchestrator is a black box by default. Multiple agents run concurrently, make LLM calls, read files, create branches, and produce outputs — all invisible to the developer unless explicitly instrumented. When something goes wrong (wrong code generated, excessive cost, agent stuck in a loop), the developer needs to understand what happened, why, and where. Without observability, debugging agent behavior devolves into reading raw API logs.

## State of the Art

### LangSmith (LangChain)
The most mature observability platform for LLM applications. LangSmith provides:

- **Tracing**: Every LLM call, tool call, and chain step is recorded as a span in a trace tree. Parent-child relationships show how a top-level request decomposes into sub-calls.
- **Token tracking**: Per-call and cumulative token usage with cost calculation.
- **Latency analysis**: Time spent in LLM calls vs. tool execution vs. application logic.
- **Feedback/annotation**: Humans can annotate traces with quality scores for evaluation.
- **Datasets/testing**: Traces can be saved as test cases for regression testing.

**Key insight:** The trace tree is the right abstraction for agent observability. It naturally maps to DAG execution — each DAG node is a span, each agent call within a node is a child span.

### Anthropic Console / Dashboard
Anthropic's console provides per-request logging, token usage, and rate limit monitoring. It operates at the API key level, not at the application level. There's no built-in way to group requests by task or agent.

### OpenTelemetry (OTel)
The vendor-neutral observability standard. OTel provides three signals:

- **Traces**: Distributed trace context propagated across service boundaries.
- **Metrics**: Counters, gauges, histograms for quantitative monitoring.
- **Logs**: Structured log records correlated with traces.

Several LLM observability libraries wrap OTel for AI-specific instrumentation: `opentelemetry-instrumentation-anthropic`, `traceloop`, etc. These automatically capture LLM calls as spans with token metadata.

**Key insight:** Using OTel means your agent traces are compatible with the entire observability ecosystem (Jaeger, Grafana, Datadog, Honeycomb) without vendor lock-in.

### Braintrust / Weights & Biases Weave
Evaluation-focused platforms that track LLM outputs, scores, and experiments. Less focused on real-time debugging and more on batch evaluation ("did this prompt change improve quality?"). Useful for tuning agent prompts over time.

### Helicone
An LLM proxy that captures all API traffic and provides dashboards for cost, latency, and usage patterns. Zero-code integration (just change the base URL). Good for aggregate analysis but doesn't capture application-level context (which agent made the call, what subtask it belongs to).

## Best Practices

### 1. Structured Logging with Trace Context

Every log line should include:

```json
{
  "timestamp": "2026-03-15T10:23:45Z",
  "level": "info",
  "dag_id": "dag_abc123",
  "node_id": "node_research_01",
  "agent_type": "research",
  "event": "llm_call_complete",
  "input_tokens": 4523,
  "output_tokens": 891,
  "duration_ms": 3200,
  "model": "claude-sonnet-4-6",
  "cache_hit": true
}
```

Use Python's `structlog` for structured JSON logging. The `dag_id` and `node_id` fields enable filtering and grouping.

### 2. Three Levels of Observability

```
Level 1: Status (is it working?)
  - DAG execution status: running / completed / failed
  - Per-node status: pending / running / completed / failed / skipped
  - Visible via CLI command or API endpoint

Level 2: Metrics (how well is it working?)
  - Total tokens used, cost, duration
  - Per-agent token usage breakdown
  - Retry count per node
  - Visible via logged metrics, queryable from SQLite

Level 3: Traces (why did it do that?)
  - Full LLM inputs/outputs per agent
  - Tool calls and results
  - Context passed between nodes
  - Visible via trace viewer (LangSmith, Jaeger, or log files)
```

MVP should implement Levels 1 and 2 natively, with Level 3 available via log files.

### 3. Real-Time Status Endpoint

A FastAPI endpoint that returns current DAG execution state:

```
GET /api/v1/dags/{dag_id}/status

{
  "dag_id": "dag_abc123",
  "issue": "#42",
  "status": "running",
  "started_at": "2026-03-15T10:20:00Z",
  "nodes": [
    {"id": "research_01", "status": "completed", "duration_ms": 8500, "tokens": 5414},
    {"id": "implement_01", "status": "running", "elapsed_ms": 3200, "tokens": 2100},
    {"id": "test_01", "status": "pending"},
    {"id": "review_01", "status": "pending"}
  ],
  "total_tokens": 7514,
  "estimated_remaining_tokens": 6000
}
```

### 4. CLI Progress Display

For the MVP (local developer), a CLI-friendly display matters more than a web dashboard:

```
Issue #42: Add rate limiting
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[✓] research_01    3.2s   5,414 tokens
[▸] implement_01   1.8s   2,100 tokens (streaming...)
[ ] test_01
[ ] review_01
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total: 7,514 tokens  ~$0.12
```

### 5. Error Correlation

When an agent fails, correlate the error with:

- The exact input context it received
- The LLM response that caused the failure
- The upstream nodes whose outputs fed into this agent
- The budget state at the time of failure

This turns "agent failed" into "agent failed because upstream research gave it the wrong file list, which happened because the issue description was ambiguous about which module."

### 6. Retention Policy

For MVP, keep everything in SQLite and log files. For production:

- Level 1 (status): Keep indefinitely (small data)
- Level 2 (metrics): Keep 90 days
- Level 3 (traces with full LLM I/O): Keep 7 days (large data, PII risk)

## Future Direction: Agent-Interpreted Observability

**Out of scope for MVP**, but the long-term vision is that humans should never have to read logs themselves. Instead, a log interpretation agent should investigate telemetry data and explain what happened, why, and what to do about it.

The current OTel-based approach provides the structured, machine-readable foundation this requires. When we build beyond MVP, the priority is:

1. **Log interpretation agents** that consume OTel traces/metrics and produce human-readable summaries on demand.
2. **Agent-first telemetry design** — instrument for agent consumption, not dashboard viewing.
3. **No human-facing log dashboards as the primary interface** — the agent is the interface.

## Previous Stable Approach

### Print Statements
The universal starting point. `print(f"Agent {name} starting...")` scattered through the code. No structure, no correlation, no filtering. Works for single-agent debugging but falls apart with concurrent agents producing interleaved output.

### Unstructured Log Files
A step up: write logs to files with timestamps. Each agent gets its own log file. Better than print statements but requires manually correlating events across files. No queryability.

### Database Status Tables
Store task status in a database table with columns like `task_id`, `status`, `updated_at`. Poll the table to see what's happening. Provides a queryable status layer but captures only coarse-grained state transitions, not the rich context needed for debugging.

### Request-Level Logging at the API Proxy
Tools like `mitmproxy` or custom reverse proxies that log all HTTP traffic to/from the LLM API. Captures raw requests and responses but lacks application context — you see the API calls but don't know which agent or subtask generated them.
