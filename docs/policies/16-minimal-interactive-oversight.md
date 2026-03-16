# Policy 16: Minimal Interactive Oversight

## Background / State of the Art

Human-in-the-loop AI systems must balance autonomy with oversight. Too many checkpoints and the human becomes the bottleneck — the slowest node in the DAG. Too few and the system may solve the wrong problem or produce a bad solution without the human knowing until it's too late.

See [human-checkpoints.md](../human-checkpoints.md) for the full design rationale.

---

## Policy

### 1. Exactly two human checkpoints

Human involvement is limited to exactly two checkpoints. Everything between them is fully autonomous. No other checkpoints exist. No configurable checkpoint levels. No mid-execution pauses.

### 2. Checkpoint 1: Issue approved for DAG

Before DAG planning begins, a dedicated investigation agent analyzes the issue and presents its understanding to the human. The investigation agent does NOT plan implementation. It answers:

1. **What is the actual problem?** — Distinguish symptoms from root causes. If the issue is vague, ask for clarification rather than guessing.
2. **What is the desired behavior?** — Concrete, testable description of the end state.
3. **What are the side effects?** — What else might change? What could break?
4. **What is the scope boundary?** — What is explicitly out of scope?

The human either approves (triggering DAG planning and execution) or provides further clarification.

### 3. Checkpoint 2: PR ready for review

Standard GitHub PR review. The human reads the diff and either approves (merge) or rejects (triggers improvement loop).

### 4. Workflow improvement loop on PR rejection

A rejected PR triggers a structured root-cause conversation:

1. **Was the issue misunderstood?** → Improve investigation prompts or add domain-specific guidance to CLAUDE.md.
2. **Was the implementation approach wrong?** → Update workflow instructions, agent prompts, or design policies.
3. **Was the code quality insufficient?** → Tighten agent constraints or add repo-specific rules.

The output is a concrete change (typically to CLAUDE.md or agent prompt templates) that prevents recurrence. The loop closes when the human confirms the adaptation is sufficient. The rejected PR can then be re-attempted with updated rules or abandoned.

### 5. No mid-execution approvals

The orchestrator MUST NOT pause for human approval between checkpoint 1 and checkpoint 2. If agents need constant supervision, the fix is better agents and better instructions — not more gates.

---

## Rationale

The two checkpoints cover the two failure modes that matter:

- **Checkpoint 1** catches "solving the wrong problem." This is the highest-leverage intervention — catching a misunderstood requirement before any code is written costs nothing.
- **Checkpoint 2** catches "solved it badly." Standard PR review is the natural validation point for implementation quality.

Mid-execution approvals create bottlenecks where the human becomes the slowest node in the DAG. Checkpoint proliferation masks the real problem — if agents need constant supervision, the system's instructions and constraints need improvement, not more human gates.

The improvement loop ensures that PR rejections produce systemic improvements rather than one-off fixes. Each rejection should make the same class of mistake less likely in future runs.
