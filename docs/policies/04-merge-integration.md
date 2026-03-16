# Policy 04: Merge Integration

When parallel Coding composite branches complete, they must be merged sequentially in DAG dependency order — not all at once and not in arbitrary order. Sequential dependency-ordered merging prevents avoidable conflicts by ensuring each branch integrates against a base state that matches its assumptions. When conflicts do occur, the system escalates through a tiered resolution sequence (trivial auto-resolve → AST-aware merge → LLM resolution agent → branch rebuild → human escalation) rather than immediately involving a human. Tests run after every individual merge to detect semantic conflicts that textual merging cannot catch.

---

## Part A: Merge Ordering (Prevention)

### P4.1 Merge branches sequentially in DAG dependency order

When a set of Coding composite branches is ready for integration, the orchestrator MUST merge them one at a time in topological order of the task DAG. If task B depends on task A (A → B edge in the DAG), then A's branch merges before B's branch. This is not optional even if all branches appear textually conflict-free.

### P4.2 Use a stable tiebreaker for independent branches

For branches with no dependency relationship, merge in order of decreasing scope: branches that touch more foundational files (models, schemas, config) merge before branches that touch leaf files (tests, docs, UI). If scope is equal, merge in task creation order. The tiebreaker MUST be deterministic — never random.

### P4.3 Rebase each branch onto the accumulated target before merging

Before merging branch B, rebase it onto the target branch (which already contains all previously merged branches). If the rebase produces conflicts, route them through the tiered conflict resolution process (Part B). Do not attempt a direct merge without rebase.

### P4.4 Run the test suite after each merge

After each successful merge (or conflict resolution), run the full test suite before proceeding to the next merge. If tests fail, the most recently merged branch is the cause. Halt the merge sequence, diagnose, and either fix (via a resolution agent) or escalate to a human [P6]. Do not merge subsequent branches on top of a failing state.

### P4.5 The Plan composite must encode file-level dependencies in the DAG

When the Plan composite generates a child DAG, it MUST identify which files each Coding composite is likely to modify. If two Coding composites modify the same file, the Plan composite MUST add a dependency edge between them (serializing access to that file) rather than leaving them as independent parallel nodes.

### P4.6 Never merge more than one branch in a single git operation

The orchestrator MUST NOT use octopus merge or any strategy that combines more than two branch tips. Every merge is a two-way operation: the current target state plus one Coding composite branch.

### P4.7 Independent branches may merge in parallel only with guard tests

As an optimization, branches that share no files AND have no DAG dependency MAY be merged in parallel into separate intermediate branches, then combined. However, the final integration must still run the test suite, and this optimization is only permitted when the Plan composite has verified file-set disjointness. When in doubt, merge sequentially.

### P4.8 Record merge order in the execution log

The orchestrator MUST log the merge sequence with timestamps, branch names, conflict status, and test results. Store it in the SQLite state store alongside the DAG execution record.

---

## Part B: Conflict Resolution (Response)

### P4.9 Agents modify existing code freely

Coding composite agents are not constrained to append-only changes. Real issues require editing existing code, and append-only patterns lead to codebase bloat. The system accepts the conflict surface that comes with unrestricted edits and handles it through ordering (Part A) and resolution (this section).

### P4.10 AST merging as a preprocessing step

Before entering LLM-based resolution tiers, run an AST-aware merge pass (tree-sitter) to auto-resolve structural conflicts that line-based git merge flags incorrectly.

### P4.11 Tiered conflict resolution

Conflicts are resolved through an escalating sequence:

1. **Trivial auto-resolve** — import reorder, whitespace, independent additions to same file.
2. **AST-aware merge** — tree-sitter parse and structural merge. If resolved, run tests.
3. **Generated/formulaic code** — regenerate the section with both contexts merged in the prompt.
4. **Conflict resolution agent** — sees both branches, original issue, and outputs from both agents. If confident, apply resolution and run tests.
5. **Triage agent** — if the resolution agent is not confident (or tests fail after resolution), a triage agent decides which branch to rebuild.
6. **Rebuild** — re-run the chosen branch's subtask from scratch on a new branch, using the conflicting branch as context. Branch named `agent/<issue>/<desc>-rebuild-<n>`.
7. **Escalate to human** — after max rebuilds (configurable, default: 1 per branch), escalate with full context [P6].

### P4.12 Rebuild before human escalation

When the resolution agent is not confident, rebuild the conflicting branch from scratch rather than immediately escalating [P6].

### P4.13 Configurable rebuild limit

A parameter `max_rebuilds_per_branch` (default: **1**) controls how many times a branch can be rebuilt before escalating to a human.

### P4.14 Triage agent decides which branch to rebuild

The triage agent does not resolve the conflict. Its job is to decide **which branch should be rebuilt** — the branch that is harder to reconcile with the merged state. The other side merges as-is.

### P4.15 Rich context for resolution agents

When delegating to a conflict resolution agent, provide: the conflict diff with markers, the original subtask descriptions for both agents, the outputs/reasoning from both agents, the full file content (not just the conflicting region), and the original issue for grounding.

### P4.16 Never force-push agent branches

Agent branches are never force-pushed. This preserves the full history of what each agent did. Rebasing onto the target branch [P4.3] is permitted because it integrates upstream changes, not rewrites agent history.

---

### Violations

- Merging branches in arbitrary or random order rather than topological DAG order.
- Skipping the test suite after a merge.
- Using octopus merge (combining more than two branch tips in one operation).
- Force-pushing agent branches.
- Going directly to human escalation without attempting the tiered resolution sequence first [P4.11].
- Omitting the merge log entry from the state store.

### Quick Reference

| Parameter | Default | Notes |
|-----------|---------|-------|
| Merge order | Topological DAG order | Deterministic tiebreaker for independent branches [P4.2] |
| Pre-merge operation | Rebase onto accumulated target | Required before every merge [P4.3] |
| Test run | After every merge | Required; halt sequence on failure [P4.4] |
| Max rebuilds per branch | 1 | Configurable via `max_rebuilds_per_branch` [P4.13] |
| Octopus merge | Prohibited | All merges are two-way [P4.6] |
| Force push | Never | Agent branch history is preserved [P4.16] |
| Conflict resolution | Tiered (7 steps) | Exhaust all tiers before escalating [P4.11] |
