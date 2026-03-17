# Phase 4 Planning — Agent Team Specification

*This document defines the agent team used to produce a concrete, reviewable Phase 4 implementation plan. It is a process document, not a deliverable — remove before committing the final plan.*

---

## Goal

Produce a Phase 4 implementation plan concrete enough that a coding agent can execute it with ≤5% ambiguity — meaning 95% of decisions are made upfront, and the remaining 5% are explicitly flagged as deferred-to-implementation decisions with clear fallback behavior.

---

## Agent Team

### Agent 1 — Plan Author

**Role:** Take the existing Phase 4 section of `implementation-plan.md` and expand it into a step-by-step implementation plan with:
- Exact file paths, class names, method signatures
- SDK integration details (import paths, API surface, configuration)
- System prompt skeletons for each sub-agent type
- Tool selection per sub-agent (exact tool names + argument validation rules)
- NodeContext serialization format for SDK user turns
- Error mapping (SDK exceptions → failure taxonomy)
- The Coding composite's internal cycle state machine
- Child DAG spawn wiring in the executor
- Component test design (what each test asserts, fixture requirements)

**Inputs:** All architecture docs, policy index, data models, existing executor/worktree/context code, Claude Code Agent SDK documentation.

**Constraints:**
- Must not contradict any active policy (P01–P11)
- Must not introduce new abstractions beyond what the architecture defines
- Must specify what is deferred vs. what is implemented now
- Must be ordered: 4a → 4b → 4c → 4d → 4e (each builds on the previous)

### Agent 2 — Technical Reviewer

**Role:** Review the Plan Author's output for:
- **Internal consistency** — do the pieces fit together? Does the SDK wrapper match what the composites need? Do the test assertions match the implementation?
- **Deviation from best practices** — Claude Code SDK usage patterns, async patterns, Pydantic v2 patterns, pytest patterns
- **Reasonable deferrals** — are things being deferred that should be done now? Are things being done now that should wait for Phase 6?
- **Missing error paths** — what happens when the SDK returns unexpected output? When a sub-agent produces invalid Pydantic output? When git push fails?
- **Test coverage gaps** — are there behaviors specified in the plan that have no corresponding test?

**Output:** Annotated plan with issues classified as:
- `[FIX]` — incorrect or inconsistent; must be fixed before the plan is final
- `[RISK]` — technically correct but likely to cause problems in implementation
- `[DEFER-OK]` — reasonable deferral, no action needed
- `[DEFER-BAD]` — deferral that will cause rework; should be addressed now

### Agent 3 — Policy Compliance Reviewer

**Role:** Check every decision in the plan against the policy index (P01–P11) and architectural invariants. Specifically:
- Tool permissions match P03/P08 tables exactly
- Budget handling matches P07 (USD-denominated, never autonomous increase, 5% threshold)
- Context flow matches P05 (forward-only, issue always verbatim, 25% cap)
- Failure classification matches P10 (exact categories, retry/rerun limits)
- Observability matches P11 (emit_event on every state transition, structured JSON)
- No sub-agent creates/merges PRs (P03/P08)
- Worktree isolation enforced (P08)
- Push-on-exit for Coding composites (P01.11)

**Output:** Policy compliance report with:
- `[VIOLATION]` — plan contradicts a policy; must be fixed
- `[AMBIGUOUS]` — plan doesn't clearly satisfy a policy; needs clarification
- `[COMPLIANT]` — explicitly verified

---

## Workflow

```
Agent 1 (Plan Author)
    │
    ▼
┌──────────────────────────┐
│  Draft Phase 4 Plan      │
└──────────────────────────┘
    │
    ├──────────────────────────┐
    ▼                          ▼
Agent 2 (Technical Review)  Agent 3 (Policy Review)
    │                          │
    ▼                          ▼
┌──────────────┐       ┌──────────────┐
│ Issue Report  │       │ Compliance   │
│ [FIX/RISK/   │       │ Report       │
│  DEFER]      │       │ [VIOLATION/  │
└──────────────┘       │  AMBIGUOUS]  │
    │                  └──────────────┘
    │                          │
    └──────────┬───────────────┘
               ▼
        Agent 1 (Revision Pass)
               │
               ▼
    ┌─────────────────────┐
    │  Fork Decision       │
    │                      │
    │  For each issue:     │
    │  - Can fix without   │
    │    human? → fix it   │
    │  - Needs human       │
    │    judgment?          │
    │    Fork 1: PAUSE     │
    │    Fork 2: BEST GUESS│
    └─────────────────────┘
               │
        ┌──────┴──────┐
        ▼             ▼
   Fork 1          Fork 2
   (conservative)  (autonomous)
```

---

## Fork Definitions

### Fork 1 — Conservative (Human-in-the-Loop)

- All `[FIX]` and `[VIOLATION]` issues are resolved in-plan
- All `[RISK]` issues that can be resolved without human judgment are resolved
- `[RISK]` issues requiring human judgment → marked `⏸️ HUMAN GUIDANCE NEEDED` inline, with the specific question and options listed
- `[AMBIGUOUS]` policy interpretations → marked `⏸️ HUMAN GUIDANCE NEEDED`
- All questions are also collected into `phase-4-human-questions.md` for convenience

**Intended use:** Human checks `phase-4-human-questions.md` periodically, answers questions, and the plan is updated.

### Fork 2 — Autonomous (Best-Guess)

- All `[FIX]` and `[VIOLATION]` issues are resolved identically to Fork 1
- `[RISK]` issues → resolved with a best-guess decision, marked `🤖 AUTO-DECIDED: <rationale>`
- `[AMBIGUOUS]` policy interpretations → resolved by choosing the interpretation most consistent with the policy's stated intent, marked `🤖 AUTO-DECIDED: <rationale>`

**Intended use:** Ready for immediate coding execution. Human reviews auto-decisions after the fact.

---

## Output Files

| File | Purpose | Remove before commit? |
|------|---------|----------------------|
| `phase-4-plan-fork1.md` | Conservative implementation plan | No (becomes the final plan after questions answered) |
| `phase-4-plan-fork2.md` | Autonomous implementation plan | Yes (reference only; merge decisions into Fork 1) |
| `phase-4-human-questions.md` | Collected questions from Fork 1 | Yes |
| `phase-4-planning-spec.md` | This file (process doc) | Yes |
