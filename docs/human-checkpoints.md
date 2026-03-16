# Human Checkpoints

## Policy: Minimal Interactive Oversight

Human involvement is limited to exactly two checkpoints. Everything between them is fully autonomous.

```
GitHub issue arrives
       │
       ▼
  Issue Investigation Agent
  (clarifies requirements, identifies side effects,
   proposes acceptance criteria)
       │
       ▼
  ═══ CHECKPOINT 1: Issue Approved for DAG ═══
  Human confirms: "Yes, this is the right problem
  and the right scope. Proceed."
       │
       ▼
  DAG planning + agent execution
  (fully autonomous — no intermediate approvals)
       │
       ▼
  ═══ CHECKPOINT 2: PR Ready for Review ═══
  Human reviews the PR via normal GitHub flow.
  Approve → merge. Reject → triggers improvement loop.
```

No other checkpoints exist. No configurable checkpoint levels. No mid-execution pauses.

## Checkpoint 1: Issue Approved for DAG

### Purpose

Ensure the system understands the actual problem before writing any code. This is the highest-leverage checkpoint — catching a misunderstood requirement here costs nothing; catching it after implementation wastes tokens and time.

### Issue Investigation Agent

Before checkpoint 1, a dedicated investigation agent analyzes the issue. This agent does NOT plan implementation. It answers:

1. **What is the actual problem?** — Distinguish symptoms from root causes. If the issue is vague ("cannot reach website please fix"), the agent must ask the human for clarification rather than guessing.
2. **What is the desired behavior?** — Concrete, testable description of the end state.
3. **What are the side effects?** — What else might change? What could break? What assumptions does this touch?
4. **What is the scope boundary?** — What is explicitly out of scope for this change?

### Interaction Model

The investigation agent presents its understanding back to the human:

```
Issue: #42 — "Cannot reach website please fix"

Before I proceed, I need clarification:

1. Which website? (the docs site at docs.example.com, or the main app at app.example.com?)
2. What does "cannot reach" mean? (DNS failure, HTTP error, slow load, blank page?)
3. When did this start? (always broken, or regression?)

Once I understand the problem, I'll confirm the proposed behavior
and flag any side effects before we begin implementation.
```

After clarification, the agent presents a confirmation:

```
Understood. Here's what I'll implement:

Problem: docs.example.com returns 502 after the nginx config
change in commit abc123.

Proposed fix: Revert the upstream directive in nginx.conf to
point to the correct backend port (3001 → 3000).

Side effects:
- The staging docs site uses the same nginx config — this will
  affect staging too.
- If port 3000 is intentional for another service, this will
  conflict.

Scope boundary: I will NOT touch the app site config or modify
any application code.

Approve this understanding? [yes / clarify further]
```

The human either approves (triggering DAG planning and execution) or provides further clarification.

## Checkpoint 2: PR Ready for Review

Standard GitHub PR review. The human reads the diff, checks the approach, and either approves or rejects.

**Approve** → human merges. Done.

**Reject** → triggers the workflow improvement loop (see below).

## Workflow Improvement Loop (On Reject)

A rejected PR means the autonomous pipeline produced the wrong output. This is a system-level failure, not just a code failure. The goal is to adapt the system so the same class of mistake doesn't recur.

### Root Cause Negotiation

When a PR is rejected, the system enters a structured conversation with the human to determine what went wrong:

1. **Was the issue misunderstood?** — The investigation agent failed to surface the real problem or missed a key constraint. Fix: improve investigation prompts or add domain-specific guidance to CLAUDE.md.

2. **Was the implementation approach wrong?** — The issue was understood correctly, but the DAG plan or agent execution produced a bad solution. Fix: update workflow instructions, agent prompts, or design policies in CLAUDE.md.

3. **Was the code quality insufficient?** — Right problem, right approach, but sloppy execution (missed edge cases, poor style, broke existing tests). Fix: tighten agent constraints or add repo-specific rules to CLAUDE.md.

### Adaptation Mechanism

The output of the improvement loop is a concrete change — typically to CLAUDE.md or agent prompt templates — that prevents recurrence. Examples:

- Human rejects because the agent used an ORM pattern the team avoids → add to CLAUDE.md: "Never use raw SQL; always use the repository pattern via `db/repos/`."
- Human rejects because the agent didn't run the linter → add to workflow: "Run `ruff check` before creating PR."
- Human rejects because the investigation agent didn't ask about backwards compatibility → update investigation prompts to always surface breaking changes.

The improvement loop closes when the human confirms the adaptation is sufficient. The rejected PR can then be re-attempted with the updated rules, or abandoned.

### Improvement Loop Flow

```
PR rejected by human
       │
       ▼
  "What went wrong?"
  (system asks structured questions)
       │
       ▼
  Human identifies the failure category
  (misunderstood issue / wrong approach / bad execution)
       │
       ▼
  System proposes a CLAUDE.md or prompt change
       │
       ▼
  Human approves the adaptation
       │
       ▼
  Optionally re-run the issue with updated rules
```

## Why Not More Checkpoints?

- **Mid-execution approvals create bottlenecks.** The human becomes the slowest node in the DAG. If you need to approve every subtask, you might as well do the work yourself.
- **Checkpoint proliferation masks the real problem.** If agents need constant supervision, the fix is better agents and better instructions — not more gates.
- **The two checkpoints cover the two failure modes that matter.** Checkpoint 1 catches "solving the wrong problem." Checkpoint 2 catches "solved it badly." Everything else is an optimization of agent quality, not human oversight.
