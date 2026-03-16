# Merge Integration

## Background / State of the Art

### The Core Problem

When an orchestrator spawns N parallel agents on separate git branches, those branches must eventually merge into a single target branch. Two distinct challenges arise: (1) the order in which branches merge affects whether conflicts occur at all, and (2) when conflicts do occur, the system needs a resolution strategy that exhausts autonomous options before consuming human attention.

### Conflict Rates in Parallel Development

Research on 267,000+ merge scenarios across 744 GitHub repositories found that the top predictors of merge conflicts are: the number of parallel lines changed, the number of commits on a branch, and the active duration of development (Predicting Merge Conflicts in Collaborative Software Development, 2019). About 60% of conflict resolutions involve AST-entangled semantic merge conflicts that survive textual merging but break behavior. An additional 24% are trivial (formatting, comments) but still interrupt workflow (Ghiotto et al., 2018).

The probability of a textual conflict between any two branches grows with the number of overlapping files and the duration of divergence. When N branches exist in parallel, merging in arbitrary order maximizes the chance that early merges shift file contents in ways that create spurious conflicts for later merges. Sequential merging in dependency order minimizes this because each merge lands on a codebase that matches what the next agent expected.

### How Multi-Agent Coding Systems Handle This

**Isolation via git worktrees.** Cursor, OpenAI Codex, and Devin isolate parallel agents in separate git worktrees or sandboxed environments. This eliminates runtime interference but defers the merge problem to integration time.

**Sequential single-branch work.** Devin and SWE-Agent sidestep conflicts by working sequentially on a single branch. This is safe but sacrifices parallelism.

**PR-per-agent.** OpenAI Codex creates one branch per agent and opens PRs to a shared feature branch. Merge order is left to the developer or a merge queue.

**Post-hoc resolution.** Composio Agent Orchestrator plans tasks, spawns agents, and autonomously handles merge conflicts and CI fixes, but relies on resolution after the fact rather than ordered prevention.

None of these systems combine dependency-ordered sequential merging with tiered autonomous conflict resolution as a unified pipeline.

### The Semantic Merge Problem

Textual merge success does not guarantee correctness. A semantic conflict is when two changes merge cleanly at the textual level but cause the program to behave incorrectly. Research tools like SAM (2024) and CodeFusion (Microsoft, 2023) use automated test generation and AST analysis to detect these, but no production multi-agent system yet integrates semantic conflict detection into the merge pipeline.

The practical defense is test-after-merge: after each sequential merge, run the test suite. If tests fail on a textually clean merge, a semantic conflict exists and requires investigation. Sequential ordering makes this tractable because the failing merge is the most recent one — you know exactly which branch introduced the problem.

### AST-Aware Merging

Standard `git merge` operates on lines of text with no language awareness. AST-aware merge tools (Mergiraf, tree-sitter) resolve structurally independent changes that line-based merge flags incorrectly — such as independent additions to the same file, import reorderings, and formatting changes. Using AST merging as a preprocessing step before LLM-based resolution reduces cost and latency.

---

## Policy

### Part A: Merge Ordering (Prevention)

#### 1. Merge branches sequentially in DAG dependency order

When a set of agent branches is ready for integration, the orchestrator MUST merge them one at a time in topological order of the task DAG. If task B depends on task A (A → B edge in the DAG), then A's branch merges before B's branch. This is not optional even if all branches appear textually conflict-free.

#### 2. Use a stable tiebreaker for independent branches

For branches with no dependency relationship (no DAG path between them), merge in order of decreasing scope: branches that touch more foundational files (models, schemas, config) merge before branches that touch leaf files (tests, docs, UI). If scope is equal, merge in task creation order. The tiebreaker MUST be deterministic — never random.

#### 3. Rebase each branch onto the accumulated target before merging

Before merging branch B, rebase it onto the target branch (which already contains all previously merged branches). If the rebase produces conflicts, route them through the tiered conflict resolution process (Part B). Do not attempt a direct merge without rebase, as this obscures whether the branch is compatible with the current target state.

#### 4. Run the test suite after each merge

After each successful merge (or conflict resolution), run the full test suite before proceeding to the next merge. If tests fail, the most recently merged branch is the cause. Halt the merge sequence, diagnose, and either fix (via a resolution agent) or escalate to a human. Do not merge subsequent branches on top of a failing state.

#### 5. The planner must encode file-level dependencies in the DAG

When the planner decomposes an issue into sub-tasks, it MUST identify which files each task is likely to modify. If two tasks modify the same file, the planner MUST add a dependency edge between them (serializing access to that file) rather than leaving them as independent parallel nodes. This is the single most effective conflict-avoidance measure.

#### 6. Never merge more than one branch in a single git operation

The orchestrator MUST NOT use octopus merge or any strategy that combines more than two branch tips. Every merge is a two-way operation: the current target state plus one agent branch.

#### 7. Independent branches may merge in parallel only with guard tests

As an optimization, branches that share no files AND have no DAG dependency MAY be merged in parallel into separate intermediate branches, then combined. However, the final integration must still run the test suite, and this optimization is only permitted when the planner has verified file-set disjointness. When in doubt, merge sequentially.

#### 8. Record merge order in the execution log

The orchestrator MUST log the merge sequence with timestamps, branch names, conflict status, and test results. This log is the primary debugging artifact when integration fails. Store it in the SQLite state store alongside the DAG execution record.

### Part B: Conflict Resolution (Response)

#### 9. Agents modify existing code freely

Agents are not constrained to append-only changes. Real issues require editing existing code, and append-only patterns lead to codebase bloat. Equally, there is no prohibition on additive changes — agents choose whatever approach best fits the task. The system accepts the conflict surface that comes with unconstrained edits and handles it through ordering (Part A) and resolution (this section).

#### 10. AST merging as a preprocessing step

Before entering LLM-based resolution tiers, run an AST-aware merge pass (tree-sitter) to auto-resolve structural conflicts that line-based git merge flags incorrectly. This reduces load on more expensive resolution methods.

#### 11. Tiered conflict resolution

Conflicts are resolved through an escalating sequence:

1. **Trivial auto-resolve** — import reorder, whitespace, independent additions to same file.
2. **AST-aware merge** — tree-sitter parse and structural merge. If resolved, run tests.
3. **Generated/formulaic code** — regenerate the section with both contexts merged in the prompt.
4. **Conflict resolution agent** — sees both branches, original issue, and outputs from both agents. If confident, apply resolution and run tests.
5. **Triage agent** — if the resolution agent is not confident (or tests fail after resolution), a triage agent decides which branch to rebuild.
6. **Rebuild** — re-run the chosen branch's subtask from scratch on a new branch, using the conflicting branch as context. Branch named `agent/<issue>/<desc>-rebuild-<n>`.
7. **Escalate to human** — after max rebuilds (configurable, default: 1 per branch), escalate with full context.

#### 12. Rebuild before human escalation

When the resolution agent is not confident, rebuild the conflicting branch from scratch rather than immediately escalating. This gives the system one more autonomous attempt before consuming human attention.

#### 13. Configurable rebuild limit

A parameter `max_rebuilds_per_branch` (default: **1**) controls how many times a branch can be rebuilt before escalating to a human. Each rebuild increments a per-branch counter tracked in the state store.

#### 14. Triage agent decides which branch to rebuild

The triage agent does not resolve the conflict. Its job is to decide **which branch should be rebuilt** — the branch that is harder to reconcile with the merged state. The other side merges as-is.

#### 15. Rich context for resolution agents

When delegating to a conflict resolution agent, provide: the conflict diff with markers, the original subtask descriptions for both agents, the outputs/reasoning from both agents, the full file content (not just the conflicting region), and the original issue for grounding.

#### 16. Never force-push agent branches

Agent branches are never force-pushed. This preserves the full history of what each agent did, which is essential for debugging conflict resolution failures. Note: rebasing onto the target branch (§3) is permitted because it integrates upstream changes, not rewrites agent history.

---

## Rationale

This policy unifies merge ordering (prevention) and conflict resolution (response) into a single pipeline. The key design decisions:

1. **Sequential dependency-ordered merging prevents avoidable conflicts.** Agents write code against an assumed base state. Merging in DAG order ensures the codebase matches each agent's assumptions. When a conflict or test failure occurs after merging branch K in a sequence of N branches, the cause is the interaction between branch K and branches 1..K-1 — unambiguous blame.

2. **No position on additive vs. editing changes.** Agents choose the approach that best fits the task. Additive changes reduce conflict surface but append-only restrictions cause codebase bloat. The system handles conflicts through ordering and resolution rather than constraining how agents write code.

3. **Tiered resolution exhausts cheap methods first.** Trivial auto-resolve and AST merge handle the majority of conflicts at near-zero cost. LLM-based resolution handles the rest. Rebuilding a branch gives the system one final autonomous attempt. Human attention is only consumed when all autonomous methods are exhausted.

4. **Test-after-merge catches semantic conflicts.** The most dangerous conflicts are the ones git does not detect — where both changes merge cleanly but the program breaks. Running tests after each sequential merge turns the test suite into a semantic conflict detector with single-branch resolution.

5. **Rebase from target is allowed, force-push is not.** Rebasing onto the target integrates upstream changes so the branch is compatible with the current merged state. Force-pushing rewrites agent history, which destroys debugging artifacts. These are different operations with different risk profiles.

6. **Topological ordering is already available.** The orchestrator already builds and traverses a task DAG using networkx. Extracting a topological sort for merge ordering is trivial additional work that yields significant reliability gains.

7. **The throughput cost is acceptable.** Agent sub-tasks are small by design (per the Agent Type Taxonomy policy). A typical merge+test cycle takes 1–5 minutes. Even with 10 sequential merges, total integration time is under an hour.
