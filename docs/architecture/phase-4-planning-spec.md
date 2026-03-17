# Phase 4 Planning — Agent Team Specification

*Process document — remove before committing the final plan.*

---

## Goal

Produce a Phase 4 implementation plan concrete enough that a coding agent can execute it with ≤5% ambiguity. The remaining 5% are explicitly flagged with clear fallback behavior.

---

## Resolved Decisions

These were resolved during the planning session and are inputs to the plan, not open questions.

| Decision | Resolution |
|----------|-----------|
| SDK package | `claude-agent-sdk`: `query(prompt, options)` → `AsyncIterator[Message]`, collect `ResultMessage` at end |
| Config class | `ClaudeAgentOptions`: `system_prompt`, `allowed_tools`/`disallowed_tools`, `model`, `max_budget_usd`, `max_turns`, `cwd`, `permission_mode`, `can_use_tool` callback, `thinking` config, `output_format` |
| Cost tracking | `ResultMessage.total_cost_usd` returned by SDK |
| Tool validation | `can_use_tool` async callback for per-call argument validation [P8.5] |
| Extended thinking | `thinking` config + `effort` field for Plan composite [P10.11] |
| Structured output | Use SDK's `output_format` parameter with JSON schema matching each `AgentOutput` model |
| Tool names | Intent-level per P3.4/P8.5 — map SDK tool names to capability intents (read/write/execute), not literal strings |
| Composite internal model | Composites are DAG containers; sub-agents are real nodes in a composite-scoped internal DAG using the same infrastructure (tables, executor) as the outer DAG |
| Coding composite cycles | Sequential cycles of linear DAGs (iterative nested DAG per P1.8/P10.4); each cycle is a 4-node acyclic DAG persisted on demand |
| Sub-agent git ops | Programmer/Debugger handle own staging/committing for resumability [P10.13]; push-on-exit is composite-level |
| Child DAG recursion | Full 4-level support (not stubbed) |
| Component test strategy | Real SDK calls, marked `@pytest.mark.sdk`; hard budget cap $1/test; after measuring actual usage, set cap at max(2× actual, $0.10) |
| System prompts | Do not include all policies; add a Phase 6 step to estimate what policy context to provide and to define the policy reviewer agent |

---

## Agent Team

### Agent 1 — Plan Author

Expands the existing Phase 4 section of `implementation-plan.md` into a step-by-step implementation plan covering:

- Exact file paths, class names, method signatures
- SDK integration (imports, `ClaudeAgentOptions` configuration per sub-agent type)
- System prompt skeletons for each sub-agent type
- Tool selection per sub-agent (SDK tool names mapped from P3.3 capability intents)
- `can_use_tool` argument validation rules per sub-agent type
- NodeContext serialization format for SDK user turns
- Error mapping (SDK exceptions → P10.7 failure taxonomy)
- Internal DAG model: `DAGRun.composite_node_id`, per-cycle persistence, executor recursion
- Child DAG spawn wiring (replacing the `NotImplementedError` in executor)
- Component test design (assertions, fixtures, $1 budget caps)

**Constraints:**
- Must not contradict policies P01–P11 (as updated this session)
- Must not introduce abstractions beyond what the architecture defines
- Must specify what is deferred to Phase 6 vs. implemented now
- Ordered: 4a → 4b → 4c → 4d → 4e (each builds on the previous)

### Agent 2 — Technical Reviewer

Reviews the Plan Author's output for:

- **Internal consistency** — SDK wrapper ↔ composite needs, test assertions ↔ implementation
- **Best practice deviation** — SDK usage patterns, async patterns, Pydantic v2, pytest
- **Deferral assessment** — things deferred that should be done now, or done now that belong in Phase 6
- **Missing error paths** — unexpected SDK output, invalid Pydantic output, git push failure
- **Test coverage gaps** — behaviors specified without corresponding tests

**Issue classifications:**
- `[FIX]` — incorrect or inconsistent; must fix
- `[RISK]` — correct but likely to cause problems
- `[DEFER-OK]` — reasonable deferral
- `[DEFER-BAD]` — deferral that will cause rework

### Agent 3 — Policy Compliance Reviewer

Checks every decision against policies P01–P11 and architectural invariants:

- Tool permissions match P3.3 capability intents
- Budget handling matches P07 (USD-denominated, no autonomous increase, 5% threshold)
- Context flow matches P05 (forward-only, issue always verbatim, 25% cap)
- Failure classification matches P10.7 (exact categories, retry/rerun limits)
- Internal DAG model satisfies P10.2/P10.4/P10.5 (composite as DAG container, resumption)
- Iterative nested DAG persistence satisfies P1.8 (per-cycle)
- Sub-agent git ops match P10.13 (own commits, composite-level push)
- No sub-agent creates/merges PRs (P3.3/P8.2)
- Worktree isolation enforced at tool layer (P8.3/P8.5)
- Observability: `emit_event` on every state transition, structured JSON (P11)

**Issue classifications:**
- `[VIOLATION]` — contradicts a policy; must fix
- `[AMBIGUOUS]` — doesn't clearly satisfy a policy; needs clarification
- `[COMPLIANT]` — verified

---

## Workflow

```
Agent 1 (Plan Author)
    │
    ▼
Draft Phase 4 Plan
    │
    ├───────────────────────┐
    ▼                       ▼
Agent 2 (Technical)    Agent 3 (Policy)
    │                       │
    ▼                       ▼
Issue Report            Compliance Report
[FIX/RISK/DEFER]        [VIOLATION/AMBIGUOUS]
    │                       │
    └───────┬───────────────┘
            ▼
     Agent 1 (Revision)
            │
            ▼
     Single Plan with inline markers:
       - [FIX] and [VIOLATION] → resolved
       - [RISK] fixable without human → resolved
       - [RISK] needing judgment → ⏸️ marker + 🤖 auto-decision
       - [AMBIGUOUS] → ⏸️ marker + 🤖 auto-decision
```

---

## Output Structure

One plan, not two forks. Each unresolved point has both markers inline:

```markdown
### Some decision point

⏸️ **HUMAN GUIDANCE NEEDED:** Should X be Y or Z?
- Option A: ...
- Option B: ...

🤖 **AUTO-DECISION:** Option A — rationale here. Proceeding with this unless overridden.
```

The human can review `phase-4-human-questions.md` (a flat list of all ⏸️ points) and override any auto-decision. If no override, the 🤖 decision stands.

---

## Output Files

| File | Purpose | Remove before commit? |
|------|---------|----------------------|
| `phase-4-plan.md` | The implementation plan (single document) | No — becomes the final plan |
| `phase-4-human-questions.md` | Flat list of all ⏸️ points for async human review | Yes |
| `phase-4-planning-spec.md` | This file | Yes |
