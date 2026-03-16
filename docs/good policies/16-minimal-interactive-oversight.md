# Policy 16: Minimal Interactive Oversight

## Background / State of the Art

Human-in-the-loop AI systems must balance autonomy with oversight. Too many checkpoints and the human becomes the bottleneck — the slowest node in the DAG. Too few and the system may solve the wrong problem or produce a bad solution without the human knowing until it's too late.

See [human-checkpoints.md](../human-checkpoints.md) for the full design rationale.

---

## Policy

### 1. Two planned checkpoints, structured mid-execution pauses when necessary

Human involvement is anchored to two planned checkpoints (issue approval and PR review). Between them, execution is autonomous by default — but the orchestrator MAY pause for budget overruns or approval requests when a genuine conflict arises. These pauses are not free-form; they follow the structured formats in sections 5 and 6.

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

### 5. Mid-execution approval requests

The orchestrator MAY pause for human approval when it encounters a genuine conflict that cannot be resolved from existing policies. Each approval request MUST present a structured justification:

1. **What situation was encountered?** — Concrete description of the decision point.
2. **Why is it unclear?** — What policy gap, contradiction, or ambiguity prevents autonomous resolution?
3. **What conflicts?** — Identify the specific tension. Examples:
   - A design objective (e.g. "must serve 500k users daily") vs. a policy constraint (e.g. "simple single-server deployment")
   - Two policies that give contradictory guidance for this situation
   - A requirement that falls outside the scope boundary agreed at checkpoint 1
4. **What are the options?** — Enumerate concrete alternatives the orchestrator has identified.
5. **Suggested policy change** — Propose a specific policy update, CLAUDE.md edit, or design decision that would resolve this class of ambiguity permanently.

The human's response MUST include a policy or instruction change — not just "do option A." If the same kind of pause could recur, the underlying ambiguity must be fixed so it doesn't.

### 6. Mid-execution budget pauses

The orchestrator MUST pause when projected token spend will exceed the budget set for the current run. The budget pause presents:

1. **Current spend** — Tokens and estimated cost consumed so far.
2. **Projected remaining cost** — What the remaining DAG nodes are estimated to require.
3. **Why the overrun?** — What caused spend to exceed the original estimate? (e.g. unexpected retries, larger codebase than scoped, additional research needed)
4. **Options** — At minimum:
   - Increase budget to projected amount
   - Increase budget to a capped amount (orchestrator suggests a reasonable cap)
   - Abort the run (preserve work done so far on the feature branch)
   - Reduce scope (orchestrator suggests which remaining nodes could be dropped)
5. **Suggested budget adjustment** — If this class of issue is likely to recur, propose a change to default budget estimates (e.g. "repos with >50k LOC should use the large budget tier").

The human's response adjusts the budget for the current run. If the overrun reflects a systemic estimation error, the human should also update budget defaults.

### 7. Pause discipline

Mid-execution pauses are pressure-release valves, not supervision checkpoints. The bar for pausing is high:

- **Do not pause for decisions the policies already cover.** If the answer is in the policies, follow it.
- **Do not pause for routine uncertainty.** Agents should make reasonable judgment calls and move forward.
- **Every pause must produce a durable fix** — a policy change, budget adjustment, or scope clarification that prevents the same pause from recurring.
- **If a run accumulates more than 2 approval pauses**, treat it as a signal that the investigation at checkpoint 1 was insufficient. After completing the run, trigger the improvement loop (section 4) focused on investigation quality.

---

## Rationale

The two planned checkpoints cover the two failure modes that matter most:

- **Checkpoint 1** catches "solving the wrong problem." Catching a misunderstood requirement before any code is written costs nothing.
- **Checkpoint 2** catches "solved it badly." Standard PR review is the natural validation point for implementation quality.

Mid-execution pauses exist for two cases that cannot be prevented by better upfront planning alone: **policy conflicts** discovered only during implementation, and **budget overruns** caused by estimation error or unexpected complexity. Both are legitimate reasons to pause — but only if the pause is structured to produce a systemic fix, not just a one-off answer. Unstructured "what should I do?" pauses are still prohibited; they mask the real problem (bad instructions) with human bottlenecks.

The improvement loop ensures that PR rejections and recurring pauses produce systemic improvements rather than one-off fixes. Each rejection or pattern of pauses should make the same class of issue less likely in future runs.
