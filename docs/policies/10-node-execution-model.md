# Policy 10: Node Execution Model

Each DAG node maps to exactly one agent invocation: the agent receives tools appropriate to its type, runs a tool-use loop bounded by an iteration cap, and returns a typed structured result. Three node types are "composite" — their internal execution is itself a DAG of sub-agents (Planning Node, Coding Node, Review Node) — but the outer DAG sees only their typed inputs and outputs. The Coding Node's internal structure is a cyclic DAG (Programmer → Test Designer → Test Executor → Debugger, up to 3 cycles), treated as an unrolled DAG with persisted sub-agent outputs so the node resumes from the last completed sub-agent on failure rather than restarting from scratch. Every failure must be classified before deciding a response: Transient (retry with backoff), Agent Error (re-invoke with failure context), Resource Exhaustion (stop and let the next layer handle it), or Deterministic (escalate immediately).

---

### 1. One Node = One Agent

Each DAG node maps to exactly one agent invocation. The agent receives tools appropriate to its type, calls them as needed within a tool-use loop, and returns a typed, structured result. The node boundary is the agent boundary — no node spawns multiple agents, and no agent spans multiple nodes.

### 2. One Composite Node = Multiple Nodes

A composite node is a DAG node whose internal execution is itself a DAG. The outer DAG sees only typed inputs and outputs — the internal structure is opaque.

Agent_agent has three composite nodes:

| Composite Node | MVP Implementation | Target Architecture |
|---|---|---|
| **Planning Node** | Single Planner agent with extended reasoning | Research → Plan → Orchestrate (acyclic internal DAG) |
| **Coding Node** | 4 agents in a cyclic DAG (Programmer → Test Designer → Test Executor → Debugger) | Same |
| **Review Node** | Single Review agent | Code Reviewer ∥ Policy Reviewer → Merge (acyclic internal DAG with fan-out) |

The DAG is nested across two levels:
- **Level 0 (root):** A single composite Planning Node.
- **Level 1 (inner):** Coding and Review nodes consolidate into a terminal Planning Node.

### 3. Iteration Caps

Every agent invocation is bounded by an iteration cap on its tool-use loop:

| Agent | Max Tool-Use Iterations |
|---|---|
| Planner (MVP) | 50 |
| Programmer | 40 |
| Test Designer | 20 |
| Test Executor | 15 |
| Debugger | 20 |
| Review | 20 |

Hitting an iteration cap is Resource Exhaustion — the node fails and its output so far is preserved.

### 4. Composite Nodes Define Collaboration as DAGs

The internal structure of a composite node is a DAG — acyclic or cyclic. Collaboration between sub-agents is expressed through edges, not through free-form conversation.

**Coding Node (cyclic):**
```
Cycle 1..N:  Programmer → Test Designer → Test Executor → Debugger
Exit: tests pass OR cycle cap reached OR budget exhausted
```

**A cyclic composite node is just an unrolled DAG.** The Coding Node with `max_cycles: 3` is structurally equivalent to a 12-node DAG (4 agents × 3 cycles). Each sub-agent invocation is a node with persisted inputs and outputs. The exit condition (tests pass) is evaluated after Test Executor in each unrolled cycle.

### 5. Composite Node Resumption

Because a composite node's internal structure is a DAG with persisted node outputs:

- **On sub-agent failure:** The failed sub-agent is re-invoked with its original inputs plus failure context. Other completed sub-agents are not re-executed.
- **On crash recovery:** The composite node's internal DAG is reconstructed from the state store. Completed sub-agents are loaded from persistence. Execution resumes from the first incomplete sub-agent.
- **On cycle-cap exhaustion (Coding Node):** The composite node exits with failure. Its output includes the full history of completed sub-agent outputs across all cycles.

### 6. Re-Invocation Is for Nodes, Not Composite Nodes

When a **simple node** fails, re-invocation means: invoke the agent fresh with its original inputs plus a failure summary. The agent starts clean.

When a **composite node** fails:
- **Sub-agent failure within a composite:** Re-invoke the specific sub-agent that failed.
- **Cycle-cap exhaustion (Coding Node):** Resource consumed. Exit with failure and history. The Planning Node replans with a different approach.
- **Budget exhaustion:** Same as cycle-cap — exit with partial results.
- **Transient failure during a sub-agent:** Re-invoke that specific sub-agent with exponential backoff. No cycle is consumed.

### 7. Failure Classification

Every failure MUST be classified before deciding the response:

| Category | Definition | Examples | Action |
|---|---|---|---|
| **Transient** | External, time-dependent failure likely to resolve | API rate limit (429), network timeout | Re-invoke with exponential backoff. Does not consume a cycle or attempt. |
| **Agent Error** | The agent produced incorrect or malformed output | Failed Pydantic validation, hallucinated file path, invalid diff | Re-invoke the node with failure context. Consumes an attempt. |
| **Resource Exhaustion** | A bounded resource was consumed | Iteration cap hit, cycle cap hit, token budget exceeded | Fail the node/composite. Preserve outputs. No re-invocation. |
| **Deterministic** | The failure will recur on every attempt | Auth error (401/403), file not found, permission denied | Fail immediately. Escalate per Policy 06. |
| **Unknown** | Unclassified exception | Unexpected exception types | Re-invoke once with context. If it fails again, escalate. |

### 8. Backoff for Transient Failures

```
wait = min(initial_backoff * (multiplier ^ attempt), max_backoff) + random_jitter
```

Defaults:
- `initial_backoff`: 2 seconds
- `multiplier`: 2.0
- `max_backoff`: 60 seconds
- `jitter`: uniform random 0–1 second

Max transient retries per sub-agent invocation: 3. After 3 transient retries, reclassify as Deterministic and escalate.

### 9. Re-Invocation Limits

| Node / Agent | Max Attempts (for Agent Error) |
|---|---|
| Planner (MVP) | 2 |
| Programmer | 2 |
| Test Designer | 2 |
| Test Executor | 1 |
| Debugger | 2 |
| Review | 2 |

These limits apply to **Agent Error** re-invocations only.

### 10. The DAG Is the Collaboration Mechanism

| Desired Interaction | DAG Modeling |
|---|---|
| Agent A refines Agent B's work | B → A (A receives B's output as input) |
| Two agents review independently | Fan-out: same input → [A, B] → merge node |
| Iterative code-test-debug loop | Coding Node's internal cyclic DAG |
| Agent needs information from a non-adjacent agent | Shared context store (Policy 05), not a direct edge |

### 11. The Planning Node Uses Extended Reasoning in MVP

The MVP Planning Node runs a single Planner agent that performs research, planning, and orchestration within one tool-use loop using extended reasoning (thinking/reflection). The Planning Node is still designated composite because the target architecture decomposes it into Research → Plan → Orchestrate when the Planner consistently hits its iteration cap or produces low-quality plans.

### 12. Context on Re-Invocation

Every re-invocation MUST include failure context. Blind re-invocations are prohibited.

The re-invocation prompt MUST contain:
1. **What failed:** The error category and a one-line summary.
2. **Concrete evidence:** The actual error message, failing test output, or validation error — not a paraphrase.
3. **Attempt number:** "This is attempt 2 of 2."
4. **Prior output (when relevant):** For Agent Error failures, include the relevant portion of prior output.

### 13. Idempotency on Re-Invocation

Each Coding Node executes in its own git worktree (Policy 08), providing filesystem-level isolation. Sub-agents inside composite nodes share the same worktree across cycles — the Programmer's changes in cycle 1 must be visible to the Test Executor and Debugger. Git operations are handled by the outer DAG after composite nodes complete — sub-agents inside composite nodes never touch git.

### 14. Metrics

| Metric | Alert Threshold |
|---|---|
| Iterations per agent invocation (p90, by agent type) | p90 > 80% of cap |
| Cycle count per Coding Node (p90) | p90 = max_cycles |
| Re-invocation rate by failure category | High Agent Error re-invocations that fail again → probably Resource Exhaustion misclassified |
| Transient retry rate | Sustained > 10% of invocations |
| Resource exhaustion by type | If one type dominates, rebalance allocations |
| Success rate by cycle number | If cycle 3 success rate ≈ 0%, reduce default max_cycles to 2 |
| Escalation rate | Track per issue complexity tier |
