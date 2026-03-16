# Error Handling & Recovery

## Problem Statement

An orchestrator dispatching multiple agents across a DAG will encounter failures at every level: transient API errors, agent hallucinations, tool call failures, network timeouts, and full process crashes. Without structured error handling, a single failure can cascade through the DAG, waste budget on doomed subtrees, or leave the system in an unrecoverable state.

## State of the Art

### Temporal.io / Durable Execution Engines

The gold standard for workflow recovery. Temporal's core abstraction is **durable execution** — the guarantee that a function will run to completion regardless of infrastructure failures, process crashes, or deployment changes.

#### How It Works

Temporal splits code into two categories:

- **Workflow functions** — pure orchestration logic. These are deterministic: given the same inputs and event history, they always produce the same sequence of commands. Workflow functions must never call external services, generate random numbers, read the clock, or do anything non-deterministic directly. Instead, they delegate all side effects to activities.

- **Activity functions** — the impure, side-effecting work: API calls, file I/O, database writes, LLM invocations. Each activity execution is recorded as an event. Activities can fail, timeout, or be retried independently of the workflow.

When a workflow runs for the first time, Temporal records every completed activity result, timer firing, and signal received as an **event** in an append-only **event history** stored in the Temporal server's persistence layer (Cassandra, MySQL, or Postgres). If the worker process crashes mid-workflow, a new worker picks up the workflow and **replays** the event history: the workflow function re-executes from the top, but instead of actually calling activities again, it reads their previously-recorded results from the history. Replay is fast — it's just function calls returning cached values. Once replay catches up to the point of failure, execution continues forward normally.

This gives you crash recovery without checkpoints, resume files, or manual intervention. The workflow simply re-derives its state from the event log.

#### Retry Policies

Activities have first-class retry semantics configured declaratively:

```python
# Temporal Python SDK example
@activity.defn
async def call_claude_api(prompt: str) -> str:
    # This is the side effect — it talks to an external service
    response = await anthropic_client.messages.create(...)
    return response.content[0].text

# Retry policy attached at invocation, not in the activity itself
result = await workflow.execute_activity(
    call_claude_api,
    prompt,
    start_to_close_timeout=timedelta(seconds=120),
    retry_policy=RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=60),
        maximum_attempts=5,
        non_retryable_error_types=["InvalidRequestError"],  # Don't retry 4xx
    ),
)
```

Key properties of the retry model:

- **Backoff is built in.** Exponential backoff with jitter, configurable coefficient, and maximum interval. No hand-rolled `time.sleep()` loops.
- **Non-retryable errors are declarative.** You specify which exception types should fail immediately (e.g., `InvalidRequestError`, `AuthenticationError`) vs. which should be retried (everything else by default). This maps directly to the error classification table in Best Practices below.
- **Timeouts are multi-layered.** `start_to_close_timeout` bounds a single attempt. `schedule_to_close_timeout` bounds all attempts combined. `heartbeat_timeout` detects stuck activities — if an activity stops heartbeating, Temporal cancels it and retries.

#### Heartbeats

Long-running activities (e.g., an agent executing a multi-step code change) can emit **heartbeats** — periodic signals that say "I'm still alive and making progress." Heartbeats can carry progress data:

```python
@activity.defn
async def run_agent_pipeline(tasks: list[Task]) -> list[Result]:
    results = []
    for i, task in enumerate(tasks):
        result = await execute_task(task)
        results.append(result)
        # Report progress — if we crash, retry resumes from here
        activity.heartbeat({"completed": i + 1, "total": len(tasks)})
    return results
```

If the worker dies, the next retry receives the last heartbeat detail, allowing it to skip already-completed work within a single activity. This is how Temporal achieves sub-activity-level recovery without requiring the workflow to model every micro-step as a separate activity.

#### Saga Pattern for Compensating Actions

Temporal natively supports the **saga pattern** for distributed transactions. If step 3 of a 5-step workflow fails and can't be retried, the workflow can execute compensating actions for steps 1 and 2 (e.g., deleting a created branch, reverting a commit, closing a draft PR). The compensation logic is just more workflow code — deterministic and replayable:

```python
@workflow.defn
class ResolveIssueWorkflow:
    @workflow.run
    async def run(self, issue: Issue) -> Result:
        compensations = []

        branch = await workflow.execute_activity(create_branch, issue)
        compensations.append(("delete_branch", branch))

        try:
            code = await workflow.execute_activity(generate_code, branch)
            compensations.append(("revert_commit", code.commit_sha))

            pr = await workflow.execute_activity(create_pr, branch)
            compensations.append(("close_pr", pr.number))

            await workflow.execute_activity(run_tests, pr)
        except Exception:
            # Unwind in reverse order
            for action, detail in reversed(compensations):
                await workflow.execute_activity(compensate, action, detail)
            raise
```

#### Why This Matters for Agent Orchestrators

An agent DAG has the same structure as a Temporal workflow: deterministic orchestration logic (which agent runs next, how results flow between nodes) wrapping non-deterministic side effects (LLM calls, GitHub API calls, code execution). The mapping is direct:

| Temporal Concept | Agent Orchestrator Equivalent |
|---|---|
| Workflow | DAG execution run |
| Activity | Single agent invocation |
| Event history | Execution log / state store |
| Replay | Resume from last completed node |
| Heartbeat | Agent progress signals |
| Retry policy | Per-node retry config |
| Saga compensation | Cleanup on DAG failure (delete branch, close PR) |

**Key insight:** Separate the orchestration logic (deterministic, replayable) from the side effects (retryable, idempotent). This is the pattern agent orchestrators should aspire to. Even without adopting Temporal itself, the architectural discipline it enforces — deterministic control flow, externalized state, declarative retry, and compensation — produces systems that are fundamentally more resilient than ad-hoc retry loops and checkpoint files.

### LangGraph (LangChain)
LangGraph checkpoints graph state after each node execution. On failure, it can resume from the last checkpoint. Retry logic is configurable per node. The state is serializable and can be persisted to SQLite, Postgres, or Redis. LangGraph also supports "time travel" — rolling back to any previous checkpoint and re-executing from there.

**Limitation:** Checkpointing is coarse-grained (per node). If a node involves multiple expensive LLM calls, a failure partway through replays the entire node.

### CrewAI / AutoGen
These frameworks handle errors primarily through agent-level retries. If an agent fails, it retries the entire agent invocation. There is no DAG-level recovery — if the process crashes, you start over. This works for simple pipelines but breaks down for complex DAGs with expensive upstream computations.

### Prefect / Airflow
Data pipeline orchestrators with mature retry and recovery patterns. Prefect 2+ uses a "task run" model where each task invocation is independently retryable and has its own state machine (Pending → Running → Completed/Failed/Crashed). Failed tasks can be retried without re-running upstream tasks. Airflow uses a similar model with configurable retries, retry delays, and exponential backoff per task.

**Key insight from Prefect:** Distinguish between `Failed` (task raised an exception) and `Crashed` (infrastructure failure, process killed). They require different recovery strategies.

## Best Practices

### 1. Classify Errors by Recoverability

Not all errors deserve retries:

| Error Type | Example | Strategy |
|---|---|---|
| **Transient** | API rate limit, network timeout | Retry with exponential backoff |
| **Agent logic** | Hallucinated tool call, invalid output format | Retry with modified prompt or context |
| **Deterministic** | File not found, permission denied, invalid config | Fail fast, escalate to human |
| **Budget exceeded** | Token limit hit | Stop, preserve partial results |
| **Process crash** | OOM, SIGKILL, power loss | Resume from last checkpoint |

### 2. Structured Result Objects

Every agent invocation returns a structured result, never raw text:

```python
class AgentResult(BaseModel):
    status: Literal["success", "failed", "skipped"]
    output: dict | None          # Typed per agent role
    error: str | None            # Human-readable error description
    error_type: str | None       # Machine-classifiable error category
    token_usage: TokenUsage
    duration_ms: int
    retries_used: int
```

This prevents the orchestrator from having to parse free-text output to determine success/failure.

### 3. Retry with Context Enrichment

Blind retries are wasteful. On retry, append the failure context to the agent's prompt:

```
Your previous attempt failed with: {error_description}
The specific issue was: {parsed_error_detail}
Please adjust your approach accordingly.
```

This gives the agent a chance to self-correct rather than repeating the same mistake.

### 4. Dead Letter / Escalation Path

After max retries, don't silently drop the task:

1. Mark the node as `failed` in state store
2. Record all attempt details (inputs, outputs, errors)
3. Evaluate DAG impact — can downstream nodes proceed without this result?
4. If not, escalate to human with full context
5. Human can: fix and retry, skip the node, or abort the DAG

### 5. Idempotent Side Effects

Agents that create branches, commits, or PR comments must be idempotent. On retry, they should check whether the side effect already exists before creating it. This prevents duplicate branches, duplicate commits, or repeated PR comments on retry.

### 6. Checkpoint Granularity

Checkpoint after every completed DAG node, not just at DAG boundaries. The cost of writing a SQLite row is negligible compared to the cost of re-running an agent.

## Our Policy: Durable DAG Execution over SQLite

Agent Agent adopts the architectural discipline of durable execution engines without taking on Temporal as a runtime dependency. The orchestrator is a single-process FastAPI server backed by SQLite — no external workflow engine, no message broker, no distributed state. The key ideas from the SOTA are preserved: deterministic orchestration separated from retryable side effects, append-only event recording, and crash recovery via replay.

### Core Model

Every GitHub issue resolution is a **DAG run**. The planner decomposes an issue into sub-tasks, each sub-task becomes a **DAG node**, and each node maps to exactly one **agent invocation** (the activity). The orchestrator walks the DAG topologically, dispatching nodes whose dependencies are satisfied, and records every state transition to SQLite.

```
Issue
  → Planner (produces DAG)
    → Node A: research agent    ← activity
    → Node B: implement agent   ← activity (depends on A)
    → Node C: implement agent   ← activity (depends on A)
    → Node D: test agent        ← activity (depends on B, C)
    → Node E: review agent      ← activity (depends on D)
  → PR created
```

The orchestrator's traversal logic is the workflow. It is deterministic: given a DAG definition and a set of completed node results, it always produces the same next set of nodes to execute. The agents are the activities: non-deterministic, side-effecting, and independently retryable.

### Node State Machine

Each DAG node follows a state machine modeled after Prefect's task-run states, with the `failed` vs. `crashed` distinction:

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
- **running** — agent invocation in progress. Heartbeat timestamp is updated periodically.
- **completed** — agent returned `AgentResult` with `status: "success"`. Output is stored.
- **failed** — agent returned a structured error or raised a classified exception. Eligible for retry with context enrichment.
- **crashed** — infrastructure failure detected (process restart found node in `running` with stale heartbeat). Reset to `pending` automatically.
- **dead_letter** — max retries exhausted. Node is parked with full attempt history. Orchestrator evaluates downstream impact.

State transitions are written to SQLite in a single transaction with the node's result payload. This is the event record — the equivalent of Temporal's event history, scoped to what we need.

### Event Log

Every node execution attempt is appended to an `node_events` table:

```sql
CREATE TABLE node_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dag_run_id    TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    attempt       INTEGER NOT NULL,
    event_type    TEXT NOT NULL,  -- 'started', 'completed', 'failed', 'crashed', 'heartbeat'
    timestamp     TEXT NOT NULL,  -- ISO 8601
    payload       TEXT,           -- JSON: AgentResult on completion, error detail on failure
    FOREIGN KEY (dag_run_id) REFERENCES dag_runs(id)
);
```

This is append-only. We never update or delete event rows. The current state of a node is derived by reading its latest event — the same principle as event sourcing. On crash recovery, the orchestrator replays the event log to reconstruct DAG state without checkpoint files.

### Crash Recovery

On startup, the orchestrator queries for incomplete DAG runs:

1. **Find in-flight runs** — `SELECT * FROM dag_runs WHERE status = 'running'`.
2. **Detect crashed nodes** — any node in `running` state whose last heartbeat is older than `heartbeat_timeout` (default: 120s) is transitioned to `crashed`, then reset to `pending`.
3. **Rebuild DAG state** — read all `node_events` for the run. Mark nodes as `completed`, `failed`, `dead_letter`, or `pending` based on their latest event. Reconstruct the in-memory DAG with outputs from completed nodes already populated.
4. **Resume traversal** — the orchestrator continues from wherever the DAG left off. Completed nodes are not re-executed. Their stored outputs feed downstream nodes exactly as they did in the original run.

This is functionally equivalent to Temporal's replay: the orchestrator re-derives its state from the event log, then continues forward. The difference is granularity — we replay at the node level, not the line-of-code level. For an agent orchestrator where each node is an expensive LLM invocation, node-level granularity is the right trade-off.

### Retry Policy

Retry configuration is declared per agent type, not hardcoded in the agent itself:

```python
class RetryPolicy(BaseModel):
    max_attempts: int = 3
    initial_backoff_s: float = 2.0
    backoff_multiplier: float = 2.0
    max_backoff_s: float = 60.0
    non_retryable_errors: list[str] = [
        "AuthenticationError",
        "InvalidRequestError",
        "BudgetExceededError",
    ]

AGENT_RETRY_POLICIES: dict[AgentType, RetryPolicy] = {
    AgentType.RESEARCH:  RetryPolicy(max_attempts=3),
    AgentType.IMPLEMENT: RetryPolicy(max_attempts=2, initial_backoff_s=5.0),
    AgentType.TEST:      RetryPolicy(max_attempts=3),
    AgentType.REVIEW:    RetryPolicy(max_attempts=2),
}
```

On retry, the orchestrator applies **context enrichment** — the previous attempt's error is appended to the agent's prompt so it can self-correct rather than repeat the same failure. The full attempt history (inputs, outputs, errors, token usage) is preserved in `node_events` for debugging and dead-letter review.

### Error Classification

The executor classifies errors before deciding whether to retry:

```python
def classify_error(error: Exception) -> ErrorType:
    if isinstance(error, anthropic.RateLimitError):
        return ErrorType.TRANSIENT
    if isinstance(error, anthropic.AuthenticationError):
        return ErrorType.DETERMINISTIC
    if isinstance(error, BudgetExceededError):
        return ErrorType.BUDGET
    if isinstance(error, AgentOutputValidationError):
        return ErrorType.AGENT_LOGIC
    if isinstance(error, (ConnectionError, TimeoutError)):
        return ErrorType.TRANSIENT
    return ErrorType.UNKNOWN  # Default: retry once, then escalate
```

`TRANSIENT` and `AGENT_LOGIC` errors are retried (with backoff and context enrichment respectively). `DETERMINISTIC` and `BUDGET` errors fail fast. `UNKNOWN` gets one retry before escalation.

### Heartbeats

Agents emit heartbeats during execution by calling back to the orchestrator. The executor updates the node's `last_heartbeat` timestamp in SQLite. This serves two purposes:

1. **Crash detection** — on recovery, nodes with stale heartbeats are identified as crashed.
2. **Liveness monitoring** — the API can report which agents are actively working and surface progress to the user.

Heartbeats are lightweight — a timestamp and optional progress payload (e.g., `{"step": "running tests", "files_changed": 4}`). They do not carry result data; that only comes on completion.

### Compensation on DAG Failure

When a node reaches `dead_letter` and its downstream subtree cannot proceed, the orchestrator runs compensation for any side effects created by completed upstream nodes:

| Completed Side Effect | Compensation |
|---|---|
| Branch created | Delete branch (if no commits worth preserving) |
| Commits pushed | Leave in place (branches are disposable) |
| Draft PR opened | Close PR with summary comment |
| PR comments posted | No action needed (informational) |

Compensation is best-effort — it runs with its own retry policy and failures are logged but do not block the DAG from being marked as failed. The principle: leave the external world as clean as possible, but don't let cleanup failures mask the original problem.

### Budget Enforcement

Token usage is tracked per node and accumulated per DAG run. Before dispatching a node, the orchestrator checks remaining budget:

```python
remaining = dag_run.budget.max_tokens - dag_run.budget.tokens_used
if remaining < node.estimated_token_cost:
    node.transition(NodeState.DEAD_LETTER, error="BudgetExceededError")
```

If budget is exhausted mid-DAG, the orchestrator:
1. Stops dispatching new nodes.
2. Lets in-flight nodes complete (their work is already paid for).
3. Marks remaining nodes as `skipped`.
4. Preserves all partial results — completed nodes' outputs are still valid and stored.
5. Reports to the user with a summary of what was completed and what was skipped.

### Design Decisions and Trade-offs

**Why not Temporal?** Temporal is a distributed system — it requires a server cluster, a persistence backend (Cassandra/MySQL/Postgres), and worker processes. Agent Agent is a single-developer local tool. SQLite gives us durable state with zero operational overhead. If agent_agent scales to multi-user or multi-machine execution, Temporal becomes the natural migration path — the architectural patterns are already aligned.

**Why node-level granularity, not line-level replay?** Each agent invocation is a single LLM call (or a small chain of them). The cost of replaying an entire node on crash is one LLM call — expensive but bounded. Modeling sub-node steps as separate events would add complexity without meaningful recovery benefit, since LLM calls are not resumable mid-stream.

**Why append-only events instead of mutable status columns?** The event log gives us full auditability (every attempt, every error, every timing) for free. Mutable status columns lose history. The cost is marginal — a few extra SQLite rows per node — and the debugging value is substantial when investigating why an agent failed on attempt 2 but succeeded on attempt 3.

## Previous Stable Approach (Pre-Durable-Execution)

Before durable execution engines, the standard pattern was:

1. **Flat retry loops** — wrap each external call in a `for i in range(max_retries)` with `time.sleep(backoff)`. No structured error classification. Retries are blind.

2. **PID files + cron** — write a PID file on startup. A cron job checks if the process is alive; if not, it restarts it. The restarted process reads a "last completed step" marker from a file and skips ahead. Fragile — the marker file can be inconsistent if the crash happened mid-write.

3. **Try/except everything** — broad exception handlers at every level, often swallowing errors or logging them without actionable structure. Leads to silent failures and corrupted state.

4. **Database status columns** — tasks have a `status` column (pending/running/done/failed). On startup, the system queries for `running` tasks (assumed crashed) and resets them to `pending`. This works but requires careful handling of the "running but actually still alive" edge case (heartbeats solve this).

The fundamental limitation of all pre-durable approaches: they conflate "what step are we on" with "what happened during that step." Durable execution engines solve this by recording the full event history, not just the current position.
