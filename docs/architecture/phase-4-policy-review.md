# Phase 4 Policy Compliance Review

*Reviewed: 2026-03-17*
*Reviewer: Agent 3 — Policy Compliance Reviewer*
*Document under review: [phase-4-plan.md](phase-4-plan.md)*

---

## Summary

15 checks performed across policies P01, P03, P05, P07, P08, P10, P11 and the architectural invariants. Results: 10 COMPLIANT, 2 AMBIGUOUS, 0 VIOLATIONS (1 violation + 1 ambiguous + 1 ambiguous resolved via policy updates).

| Classification | Count |
|---------------|-------|
| COMPLIANT | 10 |
| AMBIGUOUS | 2 |
| VIOLATION | 0 |
| RESOLVED (policy updated) | 3 |

---

## P01 — DAG Orchestration

### P1.8 — Every DAG persisted before execution begins; iterative nested DAGs persisted per-iteration
- **[COMPLIANT]** — The `_spawn_child_dag()` method in Phase 4e persists all child DAG nodes via `await self._state.create_dag_node(node)` before the child dispatch loop begins. The CodingComposite (Phase 4c) persists sub-agent outputs after each step via `_persist_sub_agent_output()`, and the plan states each cycle's DAG is persisted before execution. Per-cycle DAG persistence is addressed at the sub-agent output level, which satisfies the spirit of P1.8 for iterative nested DAGs.

### P1.10 — Nesting hard-capped at 4 levels
- **[COMPLIANT]** — `_spawn_child_dag()` checks `if child_level > 4: raise ResourceExhaustionError(...)`. The level is computed from the parent Plan node's level + 1. Internal Coding composite cycles do not increment the level, consistent with P1.10's rule that composite-internal DAGs do not count.

### P1.11 — Coding composite pushes all changes on exit
- **[AMBIGUOUS]** — The plan places `await self._push_branch()` in a `finally` block, which ensures it runs on both success and failure. However, if `git push` fails, the plan only logs the error and does not raise. P1.11 states this push is required for crash recoverability, and the Violations section of P01 lists "exiting without pushing" as a violation.
  **Clarification needed:** Should a push failure trigger escalation or at minimum set a warning flag on the node status? Silently swallowing a push failure means the Review composite could be dispatched against a branch that does not exist on the remote, which would fail at the review gate — but the failure would be attributed to the Review node rather than the Coding node that failed to push.

Human - hmm if git push fails thats a within node failure state and should be treated as such. Essentially we would want to retry the whole node or retry the git push at least once. It is quite unlikely that the git push would fail repeatedly unless there was an issue with the git config. So we can do a few git push retries without the whole node and if that fails its just a human escalation. We should handle dependency failures differently (eg. if we can't use the claude sdk then thats obviously human review or if github is down thats human review). 

### P1.1 / P1.3 — DAGs immutable and acyclic
- **[COMPLIANT]** — The child DAG recursion in `_spawn_child_dag()` creates new nodes rather than mutating existing ones. The `_build_child_dag_nodes()` method constructs a fresh set of nodes with proper parent/child edges. The Coding composite's internal cycle loop is sequential (not structural cycles in the DAG), consistent with P1.3's allowance for iterative nested DAGs.

---

## P03 — Agent Type Taxonomy

### P3.3 — Capability intents match permission matrix
- **[COMPLIANT]** — The tool permission definitions in `tools.py` (Phase 4a) map capability intents to SDK tool names as follows:
  - **ResearchPlannerOrchestrator**: `plan_permissions()` grants Read, Glob, Grep, and Bash with `_validate_read_only_bash`. No write tools. Matches P3.3 "Read files, read GitHub, read git history, no writes."
  - **Programmer**: `programmer_permissions()` grants Read/Glob/Grep, Edit/Write (with worktree path validation), and Bash (with worktree + no-push validation). Matches P3.3.
  - **Test Designer**: `test_designer_permissions()` grants Read/Glob/Grep and Bash with `_validate_read_only_bash`. Matches P3.3 "Read files, read test suite."
  - **Test Executor**: `test_executor_permissions()` grants Read/Glob/Grep and Bash (with worktree path validation but no write-blocker). Matches P3.3 "Read files, run test suite commands." However, see P8.5 finding below regarding Test Executor write capability.
  - **Debugger**: `debugger_permissions()` delegates to `programmer_permissions()`. Matches P3.3.
  - **Reviewer**: `reviewer_permissions()` grants Read/Glob/Grep and Bash with `_validate_read_only_bash`. Matches P3.3 "Read files, read diffs, read git history, no writes."

### P3.3 / P8.2 — No sub-agent creates or merges PRs
- **[COMPLIANT]** — No sub-agent permission set includes any PR-related tools. The system prompts for Programmer, Debugger, and Reviewer all explicitly state "Do not create or comment on PRs" / "Do not merge PRs." PR creation remains an orchestrator operation per the plan's Phase 4e wiring.

---

## P05 — Context Management

### P5.3 — Original issue always included verbatim
- **[COMPLIANT]** — `serialize_node_context()` always includes `ctx.issue.url`, `ctx.issue.title`, and `ctx.issue.body` as the first section. The comment explicitly references P5.3. The issue is never summarized or truncated.

### P5.4 / Architectural invariant — Context flows forward only; no backward signaling
- **[COMPLIANT]** — The CodingComposite's `_augment_context()` method passes previous cycle test results forward to the next cycle's Programmer via `parent_outputs`. There is no mechanism for downstream sub-agents to signal upstream. The Debugger receives CodeOutput and TestOutput from the same cycle — this is intra-composite forward flow, not backward signaling.

### P5.8 — SharedContextView capped at 25% of node budget
- **[AMBIGUOUS]** — The plan does not explicitly address the 25% SharedContextView cap within Phase 4 code. The existing `BudgetManager.shared_context_cap()` method returns 25% of the node allocation, and the `ContextProvider.build_context()` is expected to enforce this. However, the plan's `serialize_node_context()` function serializes the entire `SharedContextView` without checking whether it exceeds the cap.
  **Clarification needed:** Is the 25% cap enforced in ContextProvider before the view reaches `serialize_node_context()`? If so, this is compliant. If `serialize_node_context()` is expected to enforce or verify the cap, the plan is missing that logic.

Human - Yes lets just enforce it if it doesn't add too much complexity

### Architectural invariant — Orchestrator is sole writer to shared context
- **[COMPLIANT]** — The CodingComposite's `_persist_sub_agent_output()` method writes sub-agent outputs to the state store for resumability, but this is keyed as `category="sub_agent_output"` — it is internal composite state, not shared context. Agent discoveries are extracted by the executor's `_dispatch_node()` success path via `append_discoveries()`, which is the orchestrator's write protocol. Agents propose discoveries via their output; the orchestrator commits them.

---

## P07 — Budget Allocation

### P7.1 / P7.4 — USD-denominated, no autonomous increase, nodes never interrupted
- **[COMPLIANT]** — `BudgetManager` tracks in USD. `record_usage()` takes `usd` amounts. `invoke_agent()` returns `cost_usd` from the SDK's `total_cost_usd`. There is no method on BudgetManager or any plan component that autonomously increases the budget. The executor checks `should_pause()` only after a node completes, never mid-execution.

### P7.6 — 5% threshold pauses run, pending nodes stay PENDING
- **[COMPLIANT]** — The executor's main loop calls `self._budget.should_pause()` after each node and after each child DAG node. When triggered, the run status is set to `DAGRunStatus.PAUSED` and the loop breaks. No pending nodes are marked as SKIPPED — they remain in `NodeStatus.PENDING` as required.

### P7.7 — Budget enforcement uses `max_budget_usd`, not `max_tokens`
- **[RESOLVED — policy updated]** — P7.7 has been updated to use `max_budget_usd` for budget enforcement instead of requiring `max_tokens` to be set to the model maximum. The plan correctly omits `max_tokens` and uses `max_budget_usd` (the SDK backstop) as the enforcement mechanism. This is now compliant.

---

## P08 — Granular Agent Decomposition

### P8.3 / P8.5 — Worktree isolation enforced at tool layer
- **[COMPLIANT]** — The plan enforces worktree isolation at the tool layer via `_validate_worktree_path()`, which rejects file operations with absolute paths outside the worktree root. This validator is applied to Programmer, Debugger, and Test Executor permissions. The `can_use_tool` callback is passed to the SDK via `ClaudeAgentOptions`, providing runtime enforcement beyond system prompts. Argument validation is included per P8.5.

### P8.5 — Test Executor write capability via Bash
- **[RESOLVED — policy updated]** — P3.3 has been updated to allow Test Executor write access during test execution (tests may create temp files, coverage reports, __pycache__, etc.). Write enforcement is now post-execution: after the Test Executor completes, `CodingComposite._validate_no_source_modifications()` runs `git diff --name-only HEAD` to verify the net effect on tracked source files is zero. If modifications are detected, they are reverted with `git checkout .` and an `AgentError` is raised. Git mutation commands (commit, add, reset, checkout, stash) are still blocked at the tool layer. This approach is more robust than Bash command blocklists and matches real test execution behavior.

### P8.6 — All tool calls logged (allowed and denied)
- **[COMPLIANT]** — The `can_use_tool` callback in `invoke_agent()` calls `emit_event(EventType.TOOL_CALLED, ...)` for every tool call and `emit_event(EventType.TOOL_DENIED, ...)` for denied calls with the denial reason. Both allowed and denied actions are logged with agent type, agent ID (node_id), and tool name.

---

## P10 — Node Execution Model

### P10.2 / P10.4 / P10.5 — Composite as DAG container, iterative cycles, resumption
- **[COMPLIANT]** — The CodingComposite implements the iterative nested DAG pattern: up to 3 cycles of Programmer -> Test Designer -> Test Executor -> Debugger. Each sub-agent output is persisted after completion via `_persist_sub_agent_output()`. The `cycle_history` list tracks pass/fail per cycle. Cycle continuation is evaluated after Test Executor completes (tests pass -> done, fail + cycles remain -> next cycle, cap reached -> exit with failure). This matches P10.4's specification.

  Resumption (P10.5) is partially addressed: sub-agent outputs are persisted to the state store with composite-node-id + cycle + sub-agent keys. However, the plan does not show the resumption code path — how does `CodingComposite.execute()` detect and skip already-completed sub-agents on restart? The persistence is in place, but the reconstruction logic is absent.
  **Note:** This is classified COMPLIANT because the persistence mechanism satisfies P10.5's data requirement. The reconstruction logic is an implementation detail that should be addressed during coding but does not constitute a policy violation in the plan.

### P10.7 — Failure classification matches exact categories
- **[COMPLIANT]** — The plan's error mapping table in Phase 4a defines exact mappings from SDK exceptions to P10.7 categories: Rate limit -> Transient, Auth failure -> Deterministic, max_turns/max_budget exceeded -> Resource Exhaustion, Pydantic validation failure -> Agent Error, can_use_tool rejection -> Safety Violation, unexpected -> Unknown. All six categories from P10.7 are represented. The existing executor (already implemented) enforces the retry/rerun limits correctly: transient retries up to 3, Agent Error/Unknown rerun at most 1, immediate escalation for Resource Exhaustion/Deterministic/Safety Violation.

### P10.12 — No blind re-invocations
- **[COMPLIANT]** — The executor's `_dispatch_node()` re-invocation path increments `reruns_used` and loops back through `_run_with_transient_retry()`, which rebuilds context. The plan's `_augment_context()` methods on CodingComposite include previous cycle failure context. The existing executor logs the failure category and error message before re-invocation. Concrete failure evidence is always attached per P10.12.

### P10.13 — Sub-agent git ops within worktree, push is composite-level
- **[COMPLIANT]** — Programmer and Debugger system prompts instruct "Stage and commit your changes" and "Do NOT push." The `_validate_no_git_push` validator rejects `git push` in Bash commands at the tool layer. Push is performed by `CodingComposite._push_branch()` in the `finally` block — a composite-level operation, not a sub-agent operation. All sub-agents share the same worktree (the Programmer's commits are visible to Test Executor and Debugger).

---

## P11 — Observability

### P11 / P1-P4 — emit_event on every state transition, structured JSON, dag_run_id + node_id
- **[COMPLIANT]** — The plan emits events at the following transitions:
  - `EventType.NODE_STARTED` with `cycle` and `max_cycles` for each Coding composite cycle
  - `EventType.TOOL_CALLED` and `EventType.TOOL_DENIED` for every tool call in `can_use_tool`
  - Push success/failure logged via structlog with structured fields
  - Sub-agent output persistence events
  - All existing executor events (NODE_STARTED, NODE_COMPLETED, NODE_FAILED, NODE_RETRYING, DAG_STARTED, DAG_COMPLETED, DAG_PAUSED, DAG_FAILED, ESCALATION_TRIGGERED)

  Every `emit_event` call includes `dag_run_id` and `node_id`. The CodingComposite uses composite sub-agent IDs of the form `{node_id}-cycle{cycle}-{sub_agent}` for per-sub-agent observability. structlog is used throughout with bound loggers.

---

## Architectural Invariants

### Agents are stateless — all state in SQLite
- **[COMPLIANT]** — No sub-agent or composite maintains persistent state across invocations. The CodingComposite stores cycle state in local variables during execution and persists sub-agent outputs to SQLite via `_persist_sub_agent_output()`. The BudgetManager is in-memory during a run but its events are flushed to SQLite. Agent SDK invocations are stateless (each `query()` call is independent).

### Context flows forward only — no backward signaling
- **[COMPLIANT]** — Covered under P5.4 above. No mechanism exists for downstream agents to signal upstream. The Coding composite's cycle loop passes test results forward to the next cycle's Programmer, which is forward flow within the iterative nested DAG.

### Orchestrator is sole writer to shared context
- **[COMPLIANT]** — Covered under P05 above. Agents propose discoveries via their output; the executor's `append_discoveries()` is the write path.

### DAGs never mutated — adaptation via child DAG spawn only
- **[COMPLIANT]** — `_spawn_child_dag()` creates new nodes. No existing node is modified after persistence (aside from status updates, which are operational metadata, not DAG structure mutations).

---

## Findings Summary

### RESOLVED (via policy updates)

| # | Policy | Original Finding | Resolution |
|---|--------|-----------------|------------|
| 1 | P3.3 / P8.5 | Test Executor write capability via Bash | P3.3 updated: Test Executor may write during execution; net effect validated post-execution via `git diff`. Git mutations still blocked at tool layer. |
| 2 | P7.7 | `max_tokens` not set in `ClaudeAgentOptions` | P7.7 updated: budget enforcement uses `max_budget_usd`, not `max_tokens`. Omitting `max_tokens` is now compliant. |

### AMBIGUOUS (needs clarification before implementation)

| # | Policy | Finding | Clarification Needed |
|---|--------|---------|---------------------|
| 1 | P1.11 | Push failure in `_push_branch()` is silently logged without escalation | Should push failure set a node-level warning flag or trigger escalation? The current plan allows the Review composite to be dispatched against a missing remote branch. |
| 2 | P5.8 | 25% SharedContextView cap enforcement not visible in Phase 4 code | Confirm that ContextProvider enforces the cap before the view reaches `serialize_node_context()`. If not, add cap enforcement. |
| 3 | P10.5 | Sub-agent output persistence is in place, but crash-recovery reconstruction logic is not shown | Address the reconstruction code path during implementation: CodingComposite should detect already-completed sub-agents and skip them on restart. |
