# Agent Agent — Policy Index

This index summarizes each active policy. Each summary provides enough context to determine whether a proposed code change is consistent with the policy. Detailed policy rules and rationale are in the linked files; the full documents (with background research) are in `../`.

---

## [01 — DAG Orchestration](01-dag-orchestration.md)

Every issue resolution runs as an immutable, recursively nested DAG. DAGs are never mutated during execution — the executing DAG is frozen; adaptation happens by spawning a new child DAG inside the `orchestrate` node. The standard spine at every level is `research → plan → orchestrate`. All branches within a level must converge to a single `orchestrate` terminal node. Parallelism is intra-level only (independent branches run concurrently within a level, never across levels). Every DAG is persisted before execution begins, enabling crash recovery at any nesting depth. Git state is checkpointed immediately after each code node completes, before any test or review node runs.

**Violations include:** mutating a DAG in place rather than spawning a child; allowing a branch to terminate at a non-orchestrate node; spawning multiple child DAGs from a single level; running code without persisting the DAG first; not checkpointing git state after code completes.

---

## [02 — Sub-task Decomposition](02-subtask-decomposition.md)

The unit of work at each nesting level is one reviewable commit: changes to at most 3–5 files per parallel branch with a clear completion criterion. The planner targets 2–5 parallel code branches per level; more than 5 branches is rejected by the orchestrator. A branch is justified when it meets at least two of: different capability required, different scope/files, independent verifiability, failure isolation, or different blast radius. An integration node is required when parallel branches touch overlapping concerns. Research, test, and review are structural (always present via the standard spine) — they are not decomposition choices.

**Violations include:** creating more than 5 parallel code branches without splitting the issue; omitting an integration node when branches share imports or API contracts; decomposing a change that fits in under 200 words and 3 files; omitting test/review from a DAG level.

---

## [03 — Agent Type Taxonomy](03-agent-type-taxonomy.md)

Five agent types are defined: Research (read-only codebase analysis), Code (file writes, no git), Test (run test suites, no writes), Commit (git operations only, no file writes), and Review (read-only evaluation). Permissions are enforced at the tool layer — an agent literally cannot call a tool it was not given. The taxonomy is a ceiling, not a floor: the planner uses only the types needed for a given issue. New agent types must pass a four-point checklist (unique responsibility, different tool set, least-privilege violation if merged, coordination cost justified) before being added.

**Violations include:** giving a Code agent git push access; giving a Research agent file-write tools; creating an agent that both writes code and commits it; adding a new agent type without passing the decomposition checklist; any agent merging PRs.

---

## [04 — Merge Integration](04-merge-integration.md)

When parallel branches complete, they are merged sequentially in topological DAG order (foundational files before leaf files as a tiebreaker for independent branches). Each branch is rebased onto the accumulated target before merging. The full test suite runs after every individual merge — a semantic conflict that textual merging misses will surface here. Conflicts escalate through a tiered resolution sequence: trivial auto-resolve → AST-aware merge (tree-sitter) → LLM resolution agent → triage agent selects which branch to rebuild → rebuild (max 1 per branch by default) → human escalation. Agent branches are never force-pushed.

**Violations include:** merging branches in arbitrary or random order; skipping tests after a merge; using octopus merge; force-pushing agent branches; going directly to human escalation without attempting the tiered resolution sequence first.

---

## [05 — Context Management](05-context-management.md)

Context operates at three simultaneous layers: typed Pydantic objects on DAG edges (edge context), an append-only structured knowledge base accumulating discoveries across the whole run (shared context), and a `ContextProvider` that assembles the right context for each node at dispatch time (context assembly). Information flows forward only — downstream agents never send signals upstream. The original GitHub issue text is always included verbatim in every agent's context, without summarization or truncation. When context exceeds budget, a tiered strategy applies: structured masking → consumer-driven LLM summarization (using the downstream agent's schema as the negotiation protocol) → truncation. The orchestrator is the sole writer to shared context; agents propose discoveries via their output.

**Violations include:** passing free-text between agents instead of typed Pydantic models; a downstream agent modifying or sending signals to an upstream node; omitting the original issue from any agent's context; an agent writing directly to shared context; passing the full output of every ancestor node without summarization.

---

## [06 — Escalation Policy](06-escalation-policy.md)

The system escalates to a human on: retry exhaustion (node reaches `dead_letter`), budget exhaustion, deterministic errors on the critical path, safety violations (agent exceeded permission profile), semantic anomalies (empty diff, zero test assertions, review approves failing tests), or DAG planning failures. Escalation is optimized for recall over precision — uncertain outputs are escalated, not passed downstream. Every escalation must be delivered as a structured message with attempt history, DAG impact, budget status, and a menu of recovery options. Completed work is never discarded; partial results can be delivered as a labeled PR. Every escalation is treated as a policy gap signal to be fixed, not suppressed.

**Violations include:** silently continuing after a semantic anomaly; failing to escalate after retry exhaustion on a critical-path node; escalating for transient errors within retry budget; delivering an unstructured escalation message; discarding completed node outputs when a run fails.

---

## [07 — Budget Allocation](07-budget-allocation.md)

Every DAG run has a single immutable top-level token budget set at creation time and never increased. Budget flows top-down: composite nodes receive a share and re-allocate to children using the same mechanism at every depth. Active nodes are never terminated mid-execution — when a node's budget is exhausted, it completes its current API call and is frozen, emitting its output normally (marked `frozen_at_budget`). When the top-level budget reaches 5% remaining, the orchestrator evaluates stage-aware continuation: continue through cheap review stages, stop before expensive planning or implementation stages. Every API call sets `max_tokens` to the lesser of the model maximum and the node's remaining allocation. All allocation and freeze events are logged as structured `BudgetEvent` records for empirical tuning.

**Violations include:** increasing the top-level budget during execution; killing an active node mid-execution for budget reasons; not setting `max_tokens` per API call; starting a new implementation or planning stage when the budget is within 5% of exhaustion; not logging allocation events.

---

## [08 — Granular Agent Decomposition](08-granular-agent-decomposition.md)

Every agent does exactly one kind of work. Mutation (producing file changes) and persistence (git commit/push/PR) must be separated into different agents — an agent that can both write files and push to git has unacceptable blast radius. Each coding composite node runs in its own isolated git worktree, preventing its file mutations from affecting the primary checkout or other concurrent nodes. Permissions are enforced at the tool layer: the orchestrator validates every tool call against the agent's permission profile, including argument validation (a permitted tool with dangerous arguments is still dangerous). All tool calls — allowed or denied — are logged with agent type, tool name, status, and denial reason.

**Violations include:** any agent that can both write source files and run git operations; coding agents that touch the primary git checkout instead of a worktree; enforcing permissions only in system prompts without tool-layer validation; omitting argument validation for dangerous tools; not logging denied tool calls.

---

## [09 — Minimal Interactive Oversight](09-minimal-interactive-oversight.md)

Human involvement is limited to two planned checkpoints: (1) issue approval, where an investigation agent presents its understanding of the problem before any planning or coding begins, and (2) PR review, the standard GitHub review after execution completes. Between those checkpoints, execution is fully autonomous. Mid-execution pauses are allowed only for genuine policy conflicts (two policies contradict each other, or a situation falls outside existing policy coverage) or budget overruns. Every pause must produce a durable fix — a policy update, CLAUDE.md edit, or scope clarification — not just a one-off answer. A rejected PR triggers a structured improvement loop that produces a concrete change to prevent recurrence.

**Violations include:** pausing mid-execution for a decision that existing policies already cover; pausing for "routine uncertainty" without a clear policy gap; producing a one-off answer from a mid-execution pause without updating policy; allowing a run to accumulate more than 2 approval pauses without triggering the improvement loop.

---

## [10 — Node Execution Model](10-node-execution-model.md)

Each DAG node maps to exactly one agent invocation bounded by a per-agent iteration cap (Planner: 50, Programmer: 40, Test Designer/Debugger: 20, Test Executor: 15, Review: 20). Three node types are composite (Planning, Coding, Review) — their internal execution is itself a DAG of sub-agents opaque to the outer DAG. The Coding Node's internal cyclic DAG (Programmer → Test Designer → Test Executor → Debugger, max 3 cycles) is treated as an unrolled DAG with persisted sub-agent outputs, enabling resumption from the last completed sub-agent. Every failure must be classified as Transient (retry with backoff, no cycle consumed), Agent Error (re-invoke with failure context, consumes an attempt), Resource Exhaustion (stop, preserve output, let Planning Node replan), or Deterministic (escalate immediately). Blind re-invocations without concrete failure context are prohibited.

**Violations include:** a node spawning multiple agents; re-invoking a composite node from scratch when a sub-agent fails (instead of resuming from the last completed sub-agent); re-invoking with "try again" without attaching concrete error evidence; treating cycle-cap exhaustion as an Agent Error (it is Resource Exhaustion, not retryable); exceeding the per-agent iteration caps without treating it as resource exhaustion.

---

## [11 — Observability](11-observability.md)

Three observability levels are defined: Level 1 (status — DAG and per-node execution state), Level 2 (metrics — tokens, cost, retries, budget events), and Level 3 (traces — full LLM I/O and tool calls). MVP implements Levels 1 and 2 natively; Level 3 is captured in structured log files with OTel-compatible field names so a trace exporter can be added later without schema changes. All log records are JSON via `structlog` and must include `dag_run_id` and `node_id` on every line. A real-time `/dags/{id}/status` endpoint and CLI progress display are required for MVP. Failure log records must include input context, the triggering LLM response, upstream node IDs, and budget state at time of failure. SaaS observability platforms are prohibited as the primary target for MVP. Retention: status indefinitely, metrics 90 days, traces 7 days.

**Violations include:** emitting plain-text log lines instead of structured JSON; omitting `dag_run_id` or `node_id` from any log record; maintaining separate logging paths for status, metrics, and traces instead of a single `emit_event` call; using OTel-incompatible field names for Level 3 records; omitting upstream node IDs or budget state from failure events; routing MVP logs to a SaaS platform as the primary target; purging Level 1 status records.
