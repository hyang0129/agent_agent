# Policy 09: Minimal Interactive Oversight

Human involvement is anchored to exactly two planned checkpoints: an issue approval before DAG planning begins (catching "solving the wrong problem") and a PR review after execution completes (catching "solved it badly"). Between those checkpoints, execution is autonomous. Mid-execution pauses are permitted only for genuine policy conflicts or budget overruns that cannot be resolved from existing instructions, and every pause must produce a durable policy or instruction fix — not just a one-off answer. A rejected PR triggers a structured improvement loop that produces a concrete change to CLAUDE.md or agent prompts to prevent recurrence.

---

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

The output is a concrete change (typically to CLAUDE.md or agent prompt templates) that prevents recurrence. The loop closes when the human confirms the adaptation is sufficient.

### 5. Mid-execution approval requests

The orchestrator MAY pause for human approval when it encounters a genuine conflict that cannot be resolved from existing policies. Each approval request MUST present a structured justification:

1. **What situation was encountered?** — Concrete description of the decision point.
2. **Why is it unclear?** — What policy gap, contradiction, or ambiguity prevents autonomous resolution?
3. **What conflicts?** — Identify the specific tension (e.g., design objective vs. policy constraint; two contradictory policies; requirement outside the scope boundary).
4. **What are the options?** — Enumerate concrete alternatives.
5. **Suggested policy change** — Propose a specific policy update, CLAUDE.md edit, or design decision that would resolve this class of ambiguity permanently.

The human's response MUST include a policy or instruction change — not just "do option A."

### 6. Mid-execution budget pauses

The orchestrator MUST pause when projected token spend will exceed the budget set for the current run. The budget pause presents:

1. **Current spend** — Tokens and estimated cost consumed so far.
2. **Projected remaining cost** — What the remaining DAG nodes are estimated to require.
3. **Why the overrun?** — What caused spend to exceed the original estimate.
4. **Options** — At minimum: increase budget, increase to a capped amount, abort the run, or reduce scope.
5. **Suggested budget adjustment** — If this class of issue is likely to recur, propose a change to default budget estimates.

### 7. Pause discipline

Mid-execution pauses are pressure-release valves, not supervision checkpoints. The bar for pausing is high:

- **Do not pause for decisions the policies already cover.** If the answer is in the policies, follow it.
- **Do not pause for routine uncertainty.** Agents should make reasonable judgment calls and move forward.
- **Every pause must produce a durable fix** — a policy change, budget adjustment, or scope clarification that prevents the same pause from recurring.
- **If a run accumulates more than 2 approval pauses**, treat it as a signal that the investigation at checkpoint 1 was insufficient. After completing the run, trigger the improvement loop (section 4) focused on investigation quality.
