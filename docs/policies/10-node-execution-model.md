# Policy 10: Node Execution Model

Each DAG node maps to exactly one agent invocation: the agent receives tools appropriate to its type, runs a tool-use loop bounded by an iteration cap, and returns a typed structured result. Three node types are composite — their internal execution is itself a DAG of sub-agents (Plan composite, Coding composite, Review composite) — but the outer DAG sees only their typed inputs and outputs. The Coding composite's internal structure is a cyclic DAG (Programmer → Tester → Debugger, up to 3 cycles), treated as an unrolled DAG with persisted sub-agent outputs so the node resumes from the last completed sub-agent on failure rather than restarting from scratch. Every failure must be classified before deciding a response: Transient (retry with backoff), Agent Error (re-invoke with failure context), Resource Exhaustion (stop and let the next layer handle it), Deterministic (escalate immediately), or Safety Violation (escalate immediately, no retry).

---

### P10.1 One node = one agent

Each DAG node maps to exactly one agent invocation. The agent receives tools appropriate to its type, calls them as needed within a tool-use loop, and returns a typed, structured result. The node boundary is the agent boundary — no node spawns multiple agents, and no agent spans multiple nodes.

### P10.2 One composite node = multiple nodes

A composite node is a DAG node whose internal execution is itself a DAG. The outer DAG sees only typed inputs and outputs — the internal structure is opaque.

Agent_agent has three composite nodes:

| Composite Node | MVP Implementation | Target Architecture |
|---|---|---|
| **Plan composite** | Single Planner agent with extended reasoning | Research → Plan → Orchestrate (acyclic internal DAG) |
| **Coding composite** | 3 agents in a cyclic DAG (Programmer → Tester → Debugger) | Same |
| **Review composite** | Single Review agent | Code Reviewer ∥ Policy Reviewer → Merge (acyclic internal DAG with fan-out) |

The DAG nests across levels:
- **Level 0 (root):** A single Plan composite node.
- **Level 1+ (inner):** Coding composite(s) → Review composite → Plan composite (repeated up to depth 4 [P1.10]).

### P10.3 Iteration caps

Every agent invocation is bounded by an iteration cap on its tool-use loop:

| Agent | Max Tool-Use Iterations |
|---|---|
| Planner (MVP) | 50 |
| Programmer | 40 |
| Tester | 25 |
| Debugger | 20 |
| Review | 20 |

Hitting an iteration cap is Resource Exhaustion — the node fails and its output so far is preserved.

### P10.4 Composite nodes define collaboration as DAGs

The internal structure of a composite node is a DAG. Collaboration between sub-agents is expressed through edges, not through free-form conversation. Each sub-agent invocation is a node with persisted inputs and outputs.

**Coding composite — sequential cycles of linear DAGs:**

The Coding composite runs up to `max_cycles` (default 3) cycles. Each cycle is a 3-node acyclic DAG:

```
Cycle N:  Programmer → Tester → Debugger
```

Cycle continuation is evaluated by the composite after each cycle completes:
- Tests pass → done (composite returns success)
- Tests fail and cycles remain → persist and execute the next cycle's DAG
- Cycle cap reached or budget exhausted → exit with failure and full history

Each cycle's DAG is persisted before that cycle's first node executes [P1.8]. Cycles are created on demand — only the current cycle's nodes exist in the state store until the next cycle is needed.

> **Relationship to nested DAGs:** This framing is an acceptable implementation of nested DAG semantics — the composite's cycle loop produces the same outcomes as a model where the Debugger node spawns child DAGs (analogous to the Plan composite). The sequential-cycle framing is preferred because the cycle decision is a mechanical check (did tests pass?), not a planning decision, and does not warrant node-level spawning machinery. Implementations that use either framing are policy-compliant provided they satisfy P10.5 (resumption) and P1.3 (acyclic at every nesting level).

### P10.5 Composite node resumption

Because a composite node's internal structure is a DAG with persisted node outputs:

- **On sub-agent failure:** The failed sub-agent is re-invoked with its original inputs plus failure context. Other completed sub-agents are not re-executed.
- **On crash recovery:** The composite node's internal DAG is reconstructed from the state store. Completed sub-agents are loaded from persistence. Execution resumes from the first incomplete sub-agent within the current cycle.
- **On cycle-cap exhaustion (Coding composite):** The composite node exits with failure. Its output includes the full history of completed sub-agent outputs across all cycles.

### P10.6 Re-invocation is for nodes, not composite nodes

When a **simple node** fails, re-invocation means: invoke the agent fresh with its original inputs plus a failure summary. The agent starts clean.

When a **composite node** fails:
- **Sub-agent failure within a composite:** Re-invoke the specific sub-agent that failed.
- **Cycle-cap exhaustion (Coding composite):** Resource consumed. Exit with failure and history. The Plan composite replans with a different approach.
- **Budget exhaustion:** Same as cycle-cap — exit with partial results.
- **Transient failure during a sub-agent:** Re-invoke that specific sub-agent with exponential backoff. No cycle is consumed.

### P10.7 Failure classification

Every failure MUST be classified before deciding the response:

| Category | Definition | Examples | Action |
|---|---|---|---|
| **Transient** | External, time-dependent failure likely to resolve | API rate limit (429), network timeout | Re-invoke with exponential backoff. Does not consume a cycle or attempt. |
| **Agent Error** | The agent produced incorrect or malformed output | Failed Pydantic validation, hallucinated file path, invalid diff | Re-invoke the node with failure context. Consumes an attempt. |
| **Resource Exhaustion** | A bounded resource was consumed | Iteration cap hit, cycle cap hit, token budget exceeded | Fail the node/composite. Preserve outputs. No re-invocation. |
| **Deterministic** | The failure will recur on every attempt | Auth error (401/403), file not found, permission denied | Fail immediately. Escalate per [P6.1c]. |
| **Safety Violation** | An agent attempted a tool call outside its permission profile, rejected by executor | Disallowed tool call, argument validation failure [P8.5] | Fail immediately. Escalate per [P6.1d]. Do not retry under any circumstances. |
| **Unknown** | Unclassified exception | Unexpected exception types | Re-invoke once with context. If it fails again, escalate. |

### P10.8 Backoff for transient failures

```
wait = min(initial_backoff * (multiplier ^ attempt), max_backoff) + random_jitter
```

Defaults:
- `initial_backoff`: 2 seconds
- `multiplier`: 2.0
- `max_backoff`: 60 seconds
- `jitter`: uniform random 0–1 second

Max transient retries per sub-agent invocation: 3. After 3 transient retries, reclassify as Deterministic and escalate.

### P10.9 Re-invocation limits

Every node gets at most **1 re-invocation** after failure. The decision to re-invoke vs. escalate immediately is based on failure classification [P10.7]:

| Failure category | Action |
|---|---|
| Transient | Re-invoke with backoff (up to 3 transient retries per sub-agent — not counted against the 1-rerun limit) |
| Agent Error | Re-invoke once with full failure context. If it fails again, escalate. |
| Resource Exhaustion | Escalate immediately. No rerun. |
| Deterministic | Escalate immediately. No rerun. |
| Safety Violation | Escalate immediately. No rerun. |
| Unknown | Re-invoke once with context. If it fails again, escalate. |

The 1-rerun limit applies uniformly across all agent types. Transient retries [P10.8] are separate and do not consume the rerun.

### P10.10 The DAG is the collaboration mechanism

| Desired Interaction | DAG Modeling |
|---|---|
| Agent A refines Agent B's work | B → A (A receives B's output as input) |
| Two agents review independently | Fan-out: same input → [A, B] → merge node |
| Iterative code-test-debug loop | Coding composite's internal cyclic DAG |
| Agent needs information from a non-adjacent agent | Shared context store [P5], not a direct edge |

### P10.11 The Plan composite uses extended reasoning in MVP

The MVP Plan composite runs a single Planner agent that performs research, planning, and orchestration within one tool-use loop using extended reasoning (thinking/reflection). The Plan composite is still designated composite because the target architecture decomposes it into Research → Plan → Orchestrate when the Planner consistently hits its iteration cap or produces low-quality plans.

### P10.12 Context on re-invocation

Every re-invocation MUST include failure context. Blind re-invocations are prohibited.

The re-invocation prompt MUST contain:
1. **What failed:** The error category and a one-line summary.
2. **Concrete evidence:** The actual error message, failing test output, or validation error — not a paraphrase.
3. **Attempt number:** "This is attempt 2 of 2."
4. **Prior output (when relevant):** For Agent Error failures, include the relevant portion of prior output.

### P10.13 Git operations within Coding composites

*This sub-policy applies only to Coding composites — the only composite type whose sub-agents perform git operations.*

Each Coding composite executes in its own git worktree [P8.3], providing filesystem-level isolation. All sub-agent nodes within the composite's internal DAG share this worktree — the Programmer's changes in cycle 1 are visible to the Test Executor and Debugger in the same cycle, and to all sub-agents in subsequent cycles.

**Sub-agent git operations:** Programmer and Debugger sub-agent nodes handle their own git operations (staging, committing) within the shared worktree. This is required for resumability [P10.5] — if the composite crashes between sub-agents, the worktree's git history reflects all completed sub-agents' work, and execution resumes from the first incomplete sub-agent without loss.

**Push-on-exit is a composite-level concern:** The push to remote [P1.11] is performed by the Coding composite after its internal DAG completes (success or failure), not by individual sub-agent nodes. No sub-agent pushes directly.

**Worktree scope:** Sub-agents within a Coding composite are restricted to the composite's worktree. They must not reference the primary checkout, other composites' worktrees, or paths outside the worktree root. This is enforced at the tool layer [P8.5], not by filesystem permissions.

### P10.14 Metrics

| Metric | Alert Threshold |
|---|---|
| Iterations per agent invocation (p90, by agent type) | p90 > 80% of cap |
| Cycle count per Coding composite (p90) | p90 = max_cycles |
| Re-invocation rate by failure category | High Agent Error re-invocations that fail again → probably Resource Exhaustion misclassified |
| Transient retry rate | Sustained > 10% of invocations |
| Resource exhaustion by type | If one type dominates, rebalance allocations |
| Success rate by cycle number | If cycle 3 success rate ≈ 0%, reduce default max_cycles to 2 |
| Escalation rate | Track per issue complexity tier |
| Safety violation rate | Any non-zero rate is a signal: misconfigured agent or active injection attempt |

---

### Violations

- A node spawning multiple agents.
- Re-invoking a composite node from scratch when a sub-agent fails (instead of resuming from the last completed sub-agent) [P10.5].
- Re-invoking with "try again" without attaching concrete error evidence [P10.12].
- Treating cycle-cap exhaustion as an Agent Error (it is Resource Exhaustion, not retryable) [P10.7].
- Exceeding per-agent iteration caps without treating it as Resource Exhaustion [P10.3].
- Retrying after a Safety Violation [P10.7].

### Quick Reference

| Parameter | Value | Notes |
|-----------|-------|-------|
| Composite node types | Plan composite, Coding composite, Review composite | Internal structure opaque to outer DAG |
| Max Coding composite cycles | 3 | Cycle-cap exhaustion = Resource Exhaustion |
| Iteration caps | Planner 50, Programmer 40, Tester 25, Debugger/Review 20 | Hitting cap = Resource Exhaustion |
| Agent Error re-invocation limit | 1 (all agents) | Per node; see [P10.9] |
| Transient retry limit | 3 per sub-agent invocation | Then reclassify as Deterministic |
| Safety violation response | Escalate immediately, no retry | [P6.1d] |
| Blind re-invocation | Prohibited | Must include concrete failure context [P10.12] |
