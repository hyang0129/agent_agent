# Agent Agent — Policy Index

This index summarizes each active policy and serves as the canonical reference for Pydantic model names. Each summary provides enough context to determine whether a proposed code change is consistent with the policy. Cross-references use the format `[P{policy}.{rule}]` (e.g., `[P1.11]` = Policy 01, rule P1.11).

---

## Model Reference

All inter-node data MUST use these canonical Pydantic model names. Do not introduce synonyms or aliases.

### Agent Output Models

| Model | Produced By | Description |
|-------|------------|-------------|
| `PlanOutput` | Plan composite (ResearchPlannerOrchestrator) | Investigation summary + either `null` (work complete) or a child DAG specification |
| `CodeOutput` | Coding composite — Programmer and Debugger sub-agents | File changes, git state reference, local test results |
| `TestOutput` | Coding composite — Test Designer (plan) and Test Executor (results) | Test suite results, pass/fail status, assertion counts |
| `ReviewOutput` | Review composite (Reviewer) | Code quality evaluation, approval/rejection, structured findings |

### System Models

| Model | Used By | Description |
|-------|---------|-------------|
| `NodeResult` | Orchestrator | Wrapper: `output: AgentOutput`, `meta: ExecutionMeta` |
| `AgentOutput` | All edges | Union: `PlanOutput \| CodeOutput \| TestOutput \| ReviewOutput` |
| `ExecutionMeta` | Orchestrator only | Timing, token usage, attempt number — never passed downstream |
| `SharedContext` | Orchestrator (write) | Cross-cutting knowledge base, append-only |
| `SharedContextView` | Agents (read-only) | Snapshot of `SharedContext` at dispatch time, capped at 25% of node budget |
| `NodeContext` | Executor | Assembled context passed to each node: issue + parent outputs + shared view |
| `UpstreamIssue` | Failed agents | Structured signal about incorrect upstream output: `source_node_id`, `field`, `description`, `evidence` |

### Infrastructure Models

| Model | Used By | Description |
|-------|---------|-------------|
| `BudgetEvent` | Orchestrator | Allocation/freeze/increase event: `dag_run_id`, `node_id`, `event_type`, `usd_before`, `usd_after`, `reason`, `timestamp` |
| `EscalationConfig` | Orchestrator | Channel config: `channel`, `webhook_url`, `github_mention`, `timeout_seconds`, `timeout_action`, `severity_filter` |

---

## [P01 — DAG Orchestration](01-dag-orchestration.md)

Every issue resolution runs as an immutable, recursively nested DAG. The Plan composite node is the orchestration primitive at every level. Level 0 is a single Plan composite; Level 1+ follows the structure: Coding composite(s) → Review composite(s) → Plan composite. DAGs are never mutated — adaptation happens by spawning a new child DAG from the terminal Plan composite. Nesting is hard-capped at 4 levels (outer DAG nesting only — composite-internal DAGs do not increment the level). Two nesting patterns: **recursive nested DAGs** (Plan composite spawns structurally different child DAGs) and **iterative nested DAGs** (a composite repeats the same DAG structure with a binary continue/stop predicate, e.g., Coding composite cycles). Parallelism is intra-level only (concurrent Coding composites within a level). Every DAG is persisted before execution begins; iterative nested DAGs are persisted per-iteration. When a Coding composite exits (success or failure), it pushes all in-progress changes to its remote branch.

**Violations include:** mutating a DAG in place; a branch terminating at any node other than the terminal Plan composite; spawning multiple child DAGs from one level; executing before the DAG is persisted; a Coding composite exiting without pushing to remote; nesting beyond 4 outer levels without escalating.

*See also: [P08 — Granular Agent Decomposition](#p08--granular-agent-decomposition) — worktree isolation and git permission boundaries for Coding composite nodes.*

---

## [P02 — Sub-task Decomposition](02-subtask-decomposition.md)

The unit of work at each level is one reviewable commit: at most 3–5 files per Coding composite branch with a clear completion criterion. The Plan composite targets 2–5 parallel Coding composites per level; 6–7 requires justification; 8+ is rejected by the orchestrator. A branch is justified when it meets at least two of: different capability required, different scope/files, independent verifiability, failure isolation, or different blast radius. The Review composite and terminal Plan composite are structural — not decomposition choices.

**Violations include:** producing a child DAG with 8+ parallel Coding composites; a child DAG with 7 Coding composites without documented justification; decomposing a change that fits in under 200 words and 3 files; omitting the Review composite or Plan composite from an L1+ DAG.

*Note: The 7-composite limit is a hard stop — implementation must determine the appropriate threshold empirically before MVP.*

---

## [P03 — Agent Type Taxonomy](03-agent-type-taxonomy.md)

Three composite types: Plan (ResearchPlannerOrchestrator sub-agent — research + planning in a single invocation), Coding (Programmer, Test Designer, Test Executor, Debugger sub-agents), and Review (Reviewer sub-agent). Sub-agents within a composite are not exposed to the **outer** DAG as separate nodes; internally, they execute as nodes within a composite-scoped DAG [P10.2, P10.4]. There is no standalone Research agent type and no separate Commit or git composite — Programmer and Debugger handle git within their Coding composite's isolated worktree. The Plan composite is mandatory at every nesting level. Permissions are enforced at the tool layer; tool names in the permission matrix are intent-level — implementations map to SDK-specific tool names and validate against capability intent, not literal strings. New composite types must pass a four-point checklist.

**Violations include:** giving Programmer or Debugger the ability to create or comment on PRs; giving ResearchPlannerOrchestrator file-write tools; creating a separate Commit or git composite; adding a new composite type without the four-point checklist; any sub-agent merging PRs; invoking a sub-agent from a composite it does not belong to; a ResearchPlannerOrchestrator writing source files directly.

*See also: [P08 — Granular Agent Decomposition](#p08--granular-agent-decomposition) — permission enforcement and decomposition checklist for agent types.*

---

## [P05 — Context Management](05-context-management.md)

Context operates at three simultaneous layers: typed Pydantic objects on DAG edges (edge context), an append-only structured knowledge base accumulating discoveries across the whole run (shared context), and a `ContextProvider` that assembles the right context for each node at dispatch time. Information flows forward only — downstream agents never send signals upstream. The original GitHub issue is always included verbatim. When context exceeds budget: structured masking → consumer-driven LLM summarization → truncation. The orchestrator is the sole writer to shared context; agents propose discoveries via their output. The `SharedContextView` passed to each agent is capped at 25% of that node's token budget.

**Violations include:** passing free text between agents; a downstream agent attempting to signal upstream nodes; omitting the original issue from any agent's context; an agent writing directly to shared context; passing all ancestor outputs without applying summarization rules.

*See also: [P07 — Budget Allocation](#p07--budget-allocation) — the 25% `SharedContextView` cap is enforced by the budget system.*

---

## [P06 — Escalation Policy](06-escalation-policy.md)

All nodes are on the critical path — every level converges into the terminal Plan composite, so there is no non-critical-path node. Escalation triggers: safety violation (CRITICAL, immediate, no retry), semantic anomaly or deterministic error or depth limit reached (HIGH), retry exhaustion or budget exhaustion (MEDIUM). Every escalation is a structured message with attempt history, DAG impact, budget status, and a recovery options menu (including budget increase). Completed work is never discarded; partial results can be delivered as a labeled PR. Every escalation is a policy gap signal.

**Violations include:** silently continuing after a semantic anomaly; escalating for transient errors within retry budget; delivering an unstructured escalation message; discarding completed node outputs; retrying after a safety violation.

---

## [P07 — Budget Allocation](07-budget-allocation.md)

Every DAG run has a token budget set at creation time. The budget may be increased via human-approved escalation [P6.4] or mid-execution budget pause [P9.5]; the orchestrator never increases it autonomously. Budget flows top-down: composite nodes receive a share and re-allocate to children. Active nodes are never interrupted — once a node starts, it runs to completion regardless of budget state; freezing happens at node boundaries only (`frozen_at_budget`). `max_tokens` is always set to the model's maximum. At 5% remaining, the run is paused: pending nodes stay `PENDING` (not skipped), and the human is escalated for disposition. Stage-aware continuation (skipping the pause for cheap review stages) is preferred but not required. All budget events (including increases) are logged.

**Violations include:** the orchestrator increasing the budget autonomously; interrupting an active node for budget reasons; setting `max_tokens` below the model maximum; marking pending nodes as `skipped` instead of `PENDING` at the 5% threshold; not logging budget events.

*See also: [P05 — Context Management](#p05--context-management) — the `SharedContextView` is capped at 25% of the node's budget allocation.*

---

## [P08 — Granular Agent Decomposition](08-granular-agent-decomposition.md)

Every sub-agent does exactly one kind of work. Programmer and Debugger sub-agents handle file writes and git within their isolated Coding composite worktree; PR creation is an orchestrator operation — no sub-agent creates or merges PRs. Each Coding composite runs in its own git worktree, preventing mutations from affecting the primary checkout or other concurrent nodes. Permissions are enforced at the tool layer including argument validation. All tool calls — allowed or denied — are logged.

**Violations include:** any sub-agent creating or merging PRs; Programmer or Debugger touching the primary checkout; enforcing permissions only in system prompts; omitting argument validation for dangerous tools; not logging denied tool calls.

*See also: [P01 — DAG Orchestration](#p01--dag-orchestration) — the push-on-exit requirement [P1.11] and worktree lifecycle are defined there. [P03 — Agent Type Taxonomy](#p03--agent-type-taxonomy) — canonical composite types and the four-point decomposition checklist.*

---

## [P09 — Minimal Interactive Oversight](09-minimal-interactive-oversight.md)

Human involvement is limited to one planned checkpoint: a branch/PR review after execution completes. Execution is fully autonomous from run start until that checkpoint. Mid-execution pauses are allowed only for genuine policy conflicts or projected budget overruns. Every pause must produce a **durable fix** — specifically: a policy document update, a CLAUDE.md edit, or a written scope clarification committed to the state store. A one-off verbal answer does not satisfy this requirement. A rejected review triggers a structured improvement loop. MVP: no GitHub PR; orchestrator surfaces the finished branch name and summary for local review.

**Violations include:** pausing mid-execution for a decision that existing policies already cover; pausing for routine uncertainty without a clear policy gap; producing a one-off answer from a pause without updating a policy document or CLAUDE.md; allowing a run to accumulate more than 2 approval pauses without triggering the improvement loop.

---

## [P10 — Node Execution Model](10-node-execution-model.md)

Each DAG node maps to exactly one agent invocation bounded by a per-agent iteration cap. Three composite node types: Plan composite, Coding composite, Review composite. Composite nodes are DAG containers [P10.2]: their internal execution is itself a DAG where sub-agents run as nodes. The Coding composite uses an iterative nested DAG (Programmer → Test Designer → Test Executor → Debugger, up to 3 iterations) with persisted sub-agent outputs, enabling resumption from the last completed sub-agent. Programmer and Debugger sub-agents handle their own git operations (staging, committing) within the shared worktree for resumability; push-on-exit is a composite-level concern [P10.13]. Every failure must be classified before responding: Transient (retry with backoff, up to 3 per sub-agent), Agent Error (re-invoke once with full failure context), Resource Exhaustion (escalate immediately), Deterministic (escalate immediately), or Safety Violation (escalate immediately, no retry). Max 1 rerun per node for Agent Error or Unknown failures. Blind re-invocations without concrete failure context are prohibited.

**Violations include:** a node spawning multiple agents; re-invoking a composite node from scratch when a sub-agent fails; re-invoking with "try again" without concrete error evidence; treating cycle-cap exhaustion as an Agent Error; retrying after a Safety Violation; exceeding the 1-rerun limit; exceeding iteration caps without treating it as Resource Exhaustion.

---

## [P11 — Observability](11-observability.md)

Three observability levels are defined: Level 1 (status — DAG and per-node execution state), Level 2 (metrics — tokens, cost, retries, budget events), and Level 3 (traces — full LLM I/O and tool calls). MVP implements Levels 1 and 2 natively; Level 3 is captured in structured log files with OTel-compatible field names so a trace exporter can be added later without schema changes. All log records are JSON via `structlog` and must include `dag_run_id` and `node_id` on every line (`node_id: null` is correct for DAG-level events that predate node assignment). A real-time `/dags/{id}/status` endpoint is required for MVP; no live CLI progress display is required (DAG depth is dynamic and unpredictable). On completion, `agent-agent run` prints branch name and review summary. Failure log records must include input context, the triggering LLM response, upstream node IDs, and budget state at time of failure. **For MVP, all observability routes to local structured log files — no SaaS observability platform integration is permitted.** Retention: status indefinitely, metrics 90 days, traces 7 days.

**Violations include:** emitting plain-text log lines instead of structured JSON; omitting `dag_run_id` or `node_id` from any log record; maintaining separate logging paths for status, metrics, and traces instead of a single `emit_event` call; using OTel-incompatible field names for Level 3 records; omitting upstream node IDs or budget state from failure events; integrating any SaaS observability platform during MVP; purging Level 1 status records.
