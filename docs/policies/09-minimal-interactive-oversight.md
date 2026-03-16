# Policy 09: Minimal Interactive Oversight

Human involvement is anchored to one planned checkpoint: a review after execution completes (catching "solved it badly"). Execution is fully autonomous from the moment a run starts until the branch is ready for review. Mid-execution pauses are permitted only for genuine policy conflicts or budget overruns that cannot be resolved from existing instructions, and every pause must produce a durable policy or instruction fix — not just a one-off answer. A rejected review triggers a structured improvement loop that produces a concrete change to CLAUDE.md or agent prompts to prevent recurrence.

---

### P9.1 One planned checkpoint, structured mid-execution pauses when necessary

Human involvement is anchored to one planned checkpoint (branch/PR review after execution completes). Execution is fully autonomous from run start until that checkpoint. The orchestrator MAY pause mid-execution for budget overruns or genuine policy conflicts. These pauses are not free-form; they follow the structured formats in [P9.4] and [P9.5].

### P9.2 Checkpoint: Branch ready for review

After execution completes, the orchestrator surfaces the finished branch for human review. The human reads the diff and either approves (merge) or rejects (triggers improvement loop).

**MVP:** No GitHub PR is created. The orchestrator prints the branch name and a summary of what was done. The human reviews the branch locally (`git checkout`, `git diff`).
**Post-MVP:** Standard GitHub PR with review workflow.

### P9.3 Workflow improvement loop on rejection

A rejected review triggers a structured root-cause conversation:

1. **Was the issue misunderstood?** → Improve investigation prompts or add domain-specific guidance to CLAUDE.md.
2. **Was the implementation approach wrong?** → Update workflow instructions, agent prompts, or design policies.
3. **Was the code quality insufficient?** → Tighten agent constraints or add repo-specific rules.

The output is a concrete change (typically to CLAUDE.md or agent prompt templates) that prevents recurrence. The loop closes when the human confirms the adaptation is sufficient.

### P9.4 Mid-execution approval requests

The orchestrator MAY pause for human approval when it encounters a genuine conflict that cannot be resolved from existing policies. Each approval request MUST present a structured justification:

1. **What situation was encountered?** — Concrete description of the decision point.
2. **Why is it unclear?** — What policy gap, contradiction, or ambiguity prevents autonomous resolution?
3. **What conflicts?** — Identify the specific tension (e.g., design objective vs. policy constraint; two contradictory policies; requirement outside the scope boundary).
4. **What are the options?** — Enumerate concrete alternatives.
5. **Suggested policy change** — Propose a specific policy update, CLAUDE.md edit, or design decision that would resolve this class of ambiguity permanently.

The human's response MUST include a policy or instruction change — not just "do option A."

### P9.5 Mid-execution budget pauses

The orchestrator MUST pause when projected token spend will exceed the budget set for the current run. The budget pause presents:

1. **Current spend** — Tokens and estimated cost consumed so far.
2. **Projected remaining cost** — What the remaining DAG nodes are estimated to require.
3. **Why the overrun?** — What caused spend to exceed the original estimate.
4. **Options** — At minimum: increase budget [P7.1], increase to a capped amount, abort the run, or reduce scope.
5. **Suggested budget adjustment** — If this class of issue is likely to recur, propose a change to default budget estimates.

Note: this pause fires on **projected** overrun, before the budget is actually exhausted. The stage-aware evaluation [P7.6] and escalation [P6.1b] apply if execution continues and the budget reaches the 5% threshold.

### P9.6 Pause discipline

Mid-execution pauses are pressure-release valves, not supervision checkpoints. The bar for pausing is high:

- **Do not pause for decisions the policies already cover.** If the answer is in the policies, follow it.
- **Do not pause for routine uncertainty.** Agents should make reasonable judgment calls and move forward.
- **Every pause must produce a durable fix** — a policy change, budget adjustment, or scope clarification that prevents the same pause from recurring.
- **If a run accumulates more than 2 approval pauses**, treat it as a signal that the policies are under-specified for this class of issue. After completing the run, trigger the improvement loop [P9.3] focused on policy coverage.

---

### Violations

- Pausing mid-execution for a decision that existing policies already cover.
- Pausing for "routine uncertainty" without a clear policy gap.
- Producing a one-off answer from a mid-execution pause without updating policy.
- Allowing a run to accumulate more than 2 approval pauses without triggering the improvement loop [P9.3].

### Quick Reference

| Checkpoint | When | Blocks execution? |
|-----------|------|------------------|
| Branch/PR review [P9.2] | After execution completes | N/A — run is finished |
| Mid-execution approval [P9.4] | Genuine policy conflict only | Yes — DAG paused |
| Budget pause [P9.5] | Projected overrun | Yes — DAG paused until human responds |
| Rejection loop [P9.3] | After review rejected | N/A — produces policy change |
