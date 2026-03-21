# Default Issue Resolution Team Structure

## Overview

This document defines the default team structure used when agent_agent resolves a GitHub issue. Every issue resolution run instantiates this seven-role team. The structure is designed for issue #19 (semi-autonomous self-improvement) and first exercised on issue #14 (Write/Edit path validation).

This is the authoritative default. Deviations require explicit justification committed to the state store before the run begins.

---

## Relation to Active Issues

| Issue | Relevance |
|-------|-----------|
| [#19 — Semi-autonomous self-improvement epic](https://github.com/hyang0129/agent_agent/issues/19) | Defines the worker/dispatch architecture this team structure executes within |
| [#14 — Write/Edit path validation](https://github.com/hyang0129/agent_agent/issues/14) | First issue resolved using this team structure |

---

## Team Roles

### 0. Issue Reviewer *(Pre-execution gate)*

**Purpose:** Determines whether the issue is sufficiently well-defined to execute as a single PR, or whether it should be decomposed, refined, or returned to the human before any code is written.

**This role runs first and is a hard gate.** No other role starts until the Issue Reviewer produces a verdict. If the verdict is anything other than `proceed`, the run stops and the Architect posts a `github_comment` with the findings.

**Responsibilities:**
- Read the issue text, acceptance criteria, and any linked context
- Evaluate on four dimensions:

| Dimension | Question |
|-----------|---------|
| **Clarity** | Is the problem statement and expected outcome unambiguous? |
| **Scope** | Can this be implemented in one PR touching ≤ 5 files with a clear completion criterion? [P02] |
| **Prerequisites** | Are all prerequisite issues resolved, or is this blocked? |
| **Verifiability** | Is there a clear, testable acceptance criterion? |

- Produce a verdict with one of three values:

| Verdict | Meaning | Next step |
|---------|---------|-----------|
| `proceed` | Issue is well-defined; execute as-is | Hand off to Architect for full team dispatch |
| `decompose` | Issue is too broad for one PR; should be split | Post decomposition proposal as `github_comment`; stop |
| `refine` | Issue is under-defined or ambiguous in a way that cannot be resolved from existing policies or context | Post specific clarifying questions as `github_comment`; stop |

- When verdict is `decompose`: propose a concrete split — name each sub-issue, its scope, and its dependency order
- When verdict is `refine`: list each blocking ambiguity as a specific question; do not guess
- When verdict is `proceed`: summarise any minor ambiguities that were resolved by policy or context so the Architect has them in shared context from the start

**The Issue Reviewer does not write code, create branches, or modify any files.**

---

### 1. Architect *(Orchestration lead)*

**Purpose:** Ensures the overall execution stays aligned with the project's goals and policies. The Architect owns orchestration — it dispatches work to other roles and integrates their outputs into a coherent result.

**Responsibilities:**
- Receives the Issue Reviewer's `proceed` verdict and any pre-resolved ambiguities
- Read the issue, goals, and relevant policies before dispatching any work
- Dispatch Planner, Coder, Reviewer, Tester, and Policy Reviewer in the correct sequence
- Resolve ambiguities with **strict preference for policy compliance**
- When a decision is genuinely ambiguous: document the ambiguity, record the chosen interpretation in shared context, and proceed with best guess — do not pause for human input unless the issue is a safety violation or the ambiguity cannot be resolved within existing policies
- Raise blockers via `github_comment` escalation [P6, P12.8]; never block on stdin

**Ambiguity protocol:**
1. Check whether an existing policy already forecloses the decision. If yes, apply it.
2. If no policy covers it: document the ambiguity in a `## Ambiguities` comment block in shared context with fields: `question`, `chosen_interpretation`, `policy_basis` (cite the nearest policy, or `"none"` if none applies), `human_review_needed` (bool).
3. Proceed with the chosen interpretation.
4. Surface all `human_review_needed: true` ambiguities in the PR description.

---

### 2. Policy Reviewer

**Purpose:** Checks the planned and implemented work for policy violations before and after implementation. Runs in parallel with Reviewer [P03].

**Responsibilities:**
- Review the Planner's scope document against all active policies (P01–P12)
- Review the Coder's implementation against all active policies
- Produce a `PolicyReviewOutput` with a verdict (`approved` / `needs_changes`) and a list of specific violations with policy citations
- Never evaluate code quality, style, or correctness — those belong to Reviewer
- Never receive the Planner's scope without the policy corpus; never receive style or quality feedback as input

---

### 3. Planner

**Purpose:** Defines the exact scope of work to be done for the issue before any code is written.

**Responsibilities:**
- Read the issue, the relevant source files, and the policy index
- Produce a scope document containing: files to change, functions to add/modify, acceptance criteria, and a test plan outline
- Keep scope minimal — change only what the issue requires [P02]
- Flag any prerequisite issues that must land first (e.g., issue #9 before #14)
- Output is the primary input to Coder and Tester

---

### 4. Coder

**Purpose:** Implements the changes specified by the Planner's scope document.

**Responsibilities:**
- Work exclusively within the assigned git worktree [P08, P10]
- Follow the Planner's scope document precisely; do not expand scope
- Produce `CodeOutput` with file changes and a git state reference
- Run the existing test suite; surface failures in output — do not swallow them
- Path-validate all Write/Edit tool calls to the worktree boundary [P8.5]

---

### 5. Reviewer

**Purpose:** Reviews the implementation for code quality, correctness, and alignment with the Planner's scope.

**Responsibilities:**
- Operate in a read-only worktree [P08]
- Evaluate: does the implementation match the scope? Is it correct? Is it maintainable?
- Produce `ReviewOutput` with approval/rejection and structured findings
- Never evaluate policy compliance — that belongs to Policy Reviewer
- Never receive the policy corpus as context

---

### 6. Tester

**Purpose:** Builds tests appropriate to the change, runs them, and determines the correct level of testing for this issue.

**Responsibilities:**
- Read the Planner's scope and the Coder's `CodeOutput`
- Determine the appropriate test level: unit, component, or integration
- Write tests that cover the acceptance criteria in the scope document
- Run all relevant tests (existing + new); report pass/fail counts and any failures
- Produce `TestOutput` with test suite results
- Do not write tests for scenarios that cannot occur; do not over-test [P02]

---

## Execution Order

```
Issue Reviewer
    │
    ├─► verdict: refine / decompose → github_comment → STOP
    │
    └─► verdict: proceed
            │
          Architect
            │
            ├─► Planner ──────────────────────────────────┐
            │       │                                     │
            │       ▼                                     │
            │   Policy Reviewer (pre-implementation)      │
            │       │                                     │
            │       ▼                                     │
            │     Coder ──────────────────────────────────┤
            │       │                                     │
            │       ├─► Reviewer ◄──────────────────────►─┤
            │       └─► Policy Reviewer (post-impl) ◄──►──┤
            │                   │                         │
            │                   ▼                         │
            │               Tester ──────────────────────►┘
            │                   │
            └─► Architect (integrate results → PR)
```

Reviewer and Policy Reviewer run in parallel after Coder completes [P03].
Tester runs after Reviewer and Policy Reviewer both approve.

---

## Ambiguity and Escalation

The Architect is the single point of contact for ambiguity resolution. The priority order for resolving ambiguities is:

1. **Existing policy** — if a policy forecloses the decision, apply it with no deliberation
2. **Issue text** — if the issue explicitly states a requirement, follow it
3. **Goals alignment** — prefer the interpretation that best aligns with `docs/goals/goals.md`
4. **Conservative** — when all else is equal, prefer the smaller, safer change

All resolved ambiguities are documented in shared context and surfaced in the PR description. `human_review_needed: true` ambiguities are listed in a dedicated `## Requires Human Review` section of the PR body.

The Architect never pauses for human input mid-run except for safety violations [P09]. All other escalations go through `github_comment` [P6, P12.8].
