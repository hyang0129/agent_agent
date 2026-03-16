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
| `BudgetEvent` | Orchestrator | Allocation/freeze/increase event: `dag_run_id`, `node_id`, `event_type`, `tokens_before`, `tokens_after`, `reason`, `timestamp` |
| `EscalationConfig` | Orchestrator | Channel config: `channel`, `webhook_url`, `github_mention`, `timeout_seconds`, `timeout_action`, `severity_filter` |

---

## [P01 — DAG Orchestration](01-dag-orchestration.md)

Every issue resolution runs as an immutable, recursively nested DAG. The Plan composite node is the orchestration primitive at every level. Level 0 is a single Plan composite; Level 1+ follows the structure: Coding composite(s) → [Integration] → Review composite → Plan composite. DAGs are never mutated — adaptation happens by spawning a new child DAG from the terminal Plan composite. Nesting is hard-capped at 4 levels. Parallelism is intra-level only (concurrent Coding composites within a level). Every DAG is persisted before execution begins. When a Coding composite exits (success or failure), it pushes all in-progress changes to its remote branch.

**Violations include:** mutating a DAG in place; a branch terminating at any node other than the terminal Plan composite; spawning multiple child DAGs from one level; executing before the DAG is persisted; a Coding composite exiting without pushing to remote; nesting beyond 4 levels without escalating.

*See also: [P08 — Granular Agent Decomposition](#p08--granular-agent-decomposition) — worktree isolation and git permission boundaries for Coding composite nodes.*

---

## [P02 — Sub-task Decomposition](02-subtask-decomposition.md)

The unit of work at each level is one reviewable commit: at most 3–5 files per Coding composite branch with a clear completion criterion. The Plan composite targets 2–5 parallel Coding composites per level; 6–7 requires justification; 8+ is rejected by the orchestrator. A branch is justified when it meets at least two of: different capability required, different scope/files, independent verifiability, failure isolation, or different blast radius. An Integration node is required when parallel branches touch overlapping concerns. The Review composite and terminal Plan composite are structural — not decomposition choices.

**Violations include:** producing a child DAG with 8+ parallel Coding composites; a child DAG with 7 Coding composites without documented justification; omitting an Integration node when branches share imports or API contracts; decomposing a change that fits in under 200 words and 3 files; omitting the Review composite or Plan composite from an L1+ DAG.

*Note: The 7-composite limit is a hard stop — implementation must determine the appropriate threshold empirically before MVP.*

---

## [P03 — Agent Type Taxonomy](03-agent-type-taxonomy.md)

Three composite types: Plan (ResearchPlannerOrchestrator sub-agent — research + planning in a single invocation), Coding (Programmer, Test Designer, Test Executor, Debugger sub-agents), and Review (Reviewer sub-agent). Sub-agents within a composite are not exposed to the DAG as separate nodes. There is no standalone Research agent type and no separate Commit or git composite — Programmer and Debugger handle git within their Coding composite's isolated worktree. The Plan composite is mandatory at every nesting level. Permissions are enforced at the tool layer. New composite types must pass a four-point checklist.

**Violations include:** giving Programmer or Debugger the ability to create or comment on PRs; giving ResearchPlannerOrchestrator file-write tools; creating a separate Commit or git composite; adding a new composite type without the four-point checklist; any sub-agent merging PRs; invoking a sub-agent from a composite it does not belong to; a ResearchPlannerOrchestrator writing source files directly.

*See also: [P08 — Granular Agent Decomposition](#p08--granular-agent-decomposition) — permission enforcement and decomposition checklist for agent types.*

---

## [P04 — Merge Integration](04-merge-integration.md)

When parallel Coding composite branches complete, merge them sequentially in topological DAG order (foundational files before leaf files as the tiebreaker for independent branches). Each branch is rebased onto the accumulated target before merging. The full test suite runs after every individual merge. Conflicts escalate through a tiered sequence: trivial auto-resolve → AST-aware merge (tree-sitter) → LLM resolution agent → triage agent selects which branch to rebuild → rebuild → human escalation. Rebuild is capped at **1 per branch by default** (configurable via `max_rebuilds_per_branch`). Agent branches are never force-pushed.

**Violations include:** merging branches in arbitrary order; skipping tests after a merge; using octopus merge; force-pushing agent branches; going directly to human escalation without attempting the tiered resolution sequence; exceeding `max_rebuilds_per_branch` without escalating.

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

Every DAG run has a token budget set at creation time. The budget may be increased via human-approved escalation [P6.4] or mid-execution budget pause [P9.6]; the orchestrator never increases it autonomously. Budget flows top-down: composite nodes receive a share and re-allocate to children. Active nodes are never terminated — frozen after the current API call (`frozen_at_budget`). At 5% remaining, stage-aware evaluation: continue for review stages, stop for planning/implementation stages, then escalate [P6.1b]. All budget events (including increases) are logged.

**Violations include:** the orchestrator increasing the budget autonomously; killing an active node mid-execution; not setting `max_tokens` per API call; starting an implementation or planning stage at 5% remaining budget; not logging budget events.

*See also: [P05 — Context Management](#p05--context-management) — the `SharedContextView` is capped at 25% of the node's budget allocation.*

---

## [P08 — Granular Agent Decomposition](08-granular-agent-decomposition.md)

Every sub-agent does exactly one kind of work. Programmer and Debugger sub-agents handle file writes and git within their isolated Coding composite worktree; PR creation is an orchestrator operation — no sub-agent creates or merges PRs. Each Coding composite runs in its own git worktree, preventing mutations from affecting the primary checkout or other concurrent nodes. Permissions are enforced at the tool layer including argument validation. All tool calls — allowed or denied — are logged.

**Violations include:** any sub-agent creating or merging PRs; Programmer or Debugger touching the primary checkout; enforcing permissions only in system prompts; omitting argument validation for dangerous tools; not logging denied tool calls.

*See also: [P01 — DAG Orchestration](#p01--dag-orchestration) — the push-on-exit requirement [P1.11] and worktree lifecycle are defined there. [P03 — Agent Type Taxonomy](#p03--agent-type-taxonomy) — canonical composite types and the four-point decomposition checklist.*

---

## [P09 — Minimal Interactive Oversight](09-minimal-interactive-oversight.md)

Human involvement is limited to two planned checkpoints: (1) issue approval, where an investigation agent presents its understanding before any planning begins, and (2) PR review after execution completes. Between those checkpoints, execution is fully autonomous. Mid-execution pauses are allowed only for genuine policy conflicts or projected budget overruns. Every pause must produce a **durable fix** — specifically: a policy document update, a CLAUDE.md edit, or a written scope clarification committed to the state store. A one-off verbal answer does not satisfy this requirement. A rejected PR triggers a structured improvement loop.

**Violations include:** pausing mid-execution for a decision that existing policies already cover; pausing for routine uncertainty without a clear policy gap; producing a one-off answer from a pause without updating a policy document or CLAUDE.md; allowing a run to accumulate more than 2 approval pauses without triggering the improvement loop.

---

## [P10 — Node Execution Model](10-node-execution-model.md)

Each DAG node maps to exactly one agent invocation bounded by a per-agent iteration cap. The caps listed in the full policy (Planner: 50, Programmer: 40, Test Designer/Debugger: 20, Test Executor: 15, Review: 20) are **conceptual placeholders** — the actual values must be determined empirically before MVP ships and encoded in config. Three composite node types: Plan composite, Coding composite, Review composite. The Coding composite's internal cyclic DAG (Programmer → Test Designer → Test Executor → Debugger, max 3 cycles) is treated as an unrolled DAG with persisted sub-agent outputs, enabling resumption from the last completed sub-agent. Every failure must be classified as: Transient (retry with backoff), Agent Error (re-invoke with context), Resource Exhaustion (stop, preserve output), Deterministic (escalate immediately), or Safety Violation (escalate immediately, no retry). Blind re-invocations without concrete failure context are prohibited.

**Violations include:** a node spawning multiple agents; re-invoking a composite node from scratch when a sub-agent fails; re-invoking with "try again" without concrete error evidence; treating cycle-cap exhaustion as an Agent Error; retrying after a Safety Violation; exceeding iteration caps without treating them as Resource Exhaustion; hardcoding iteration caps instead of reading from config.

---

## [P11 — Observability](11-observability.md)

Three observability levels are defined: Level 1 (status — DAG and per-node execution state), Level 2 (metrics — tokens, cost, retries, budget events), and Level 3 (traces — full LLM I/O and tool calls). MVP implements Levels 1 and 2 natively; Level 3 is captured in structured log files with OTel-compatible field names so a trace exporter can be added later without schema changes. All log records are JSON via `structlog` and must include `dag_run_id` and `node_id` on every line. A real-time `/dags/{id}/status` endpoint and CLI progress display are required for MVP. Failure log records must include input context, the triggering LLM response, upstream node IDs, and budget state at time of failure. **For MVP, all observability routes to local structured log files — no SaaS observability platform integration is permitted.** Retention: status indefinitely, metrics 90 days, traces 7 days.

**Violations include:** emitting plain-text log lines instead of structured JSON; omitting `dag_run_id` or `node_id` from any log record; maintaining separate logging paths for status, metrics, and traces instead of a single `emit_event` call; using OTel-incompatible field names for Level 3 records; omitting upstream node IDs or budget state from failure events; integrating any SaaS observability platform during MVP; purging Level 1 status records.
