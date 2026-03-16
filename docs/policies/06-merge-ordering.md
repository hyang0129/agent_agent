# Policy 06: Sequential Merge Ordering

## Background / State of the Art

### The Core Problem

When an orchestrator spawns N parallel agents on separate git branches, those branches must eventually merge into a single target branch. The merge order matters: merging branch B before branch A can produce conflicts that would not arise if A merged first, especially when B's work was designed atop assumptions that depend on A's changes.

### Conflict Rates in Parallel Development

Research on 267,000+ merge scenarios across 744 GitHub repositories found that the top predictors of merge conflicts are: the number of parallel lines changed, the number of commits on a branch, and the active duration of development (Predicting Merge Conflicts in Collaborative Software Development, 2019). About 60% of conflict resolutions involve changes to the AST that are entangled -- so-called semantic merge conflicts that survive textual merging but break behavior. An additional 24% are trivial (formatting, comments) but still interrupt workflow (An Analysis of Merge Conflicts and Resolutions in Git-Based Open Source Projects, Ghiotto et al., 2018).

The probability of a textual conflict between any two branches grows with the number of overlapping files and the duration of divergence. When N branches exist in parallel, an all-at-once merge strategy (merge all branches into target in arbitrary order) maximizes the chance that early merges shift file contents in ways that create spurious conflicts for later merges. Sequential merging in dependency order minimizes this because each merge lands on a codebase that matches what the next agent expected.

### How Multi-Agent Coding Systems Handle This

**Isolation via git worktrees.** Cursor (multi-agent, Oct 2025), OpenAI Codex (Feb 2026), and Devin all isolate parallel agents in separate git worktrees or sandboxed environments. This eliminates runtime interference but defers the merge problem to integration time.

**Devin and SWE-Agent** sidestep merge conflicts by working sequentially on a single branch. This is safe but sacrifices parallelism. The Devin team has noted that a core problem with naive multi-agent setups is that sub-agents have no context of each other's work, leading to conflicting decisions.

**OpenAI Codex** creates one branch per agent and opens PRs from each branch to a shared feature branch. Merge order is left to the developer or a merge queue.

**Composio Agent Orchestrator** (open-source, 2025) plans tasks, spawns agents, and autonomously handles merge conflicts and CI fixes, but relies on post-hoc resolution rather than ordered prevention.

None of these systems implement dependency-ordered sequential merging as a first-class orchestration primitive. This is a gap that Agent Agent fills.

### The Semantic Merge Problem

Textual merge success does not guarantee correctness. Martin Fowler's definition: a semantic conflict is when two changes merge cleanly at the textual level but cause the program to behave incorrectly. Research tools like SAM (Detecting Semantic Conflicts with Unit Tests, 2024) and CodeFusion (Microsoft, 2023) use automated test generation and AST analysis to detect these, but no production multi-agent system yet integrates semantic conflict detection into the merge pipeline.

The practical defense is test-after-merge: after each sequential merge, run the test suite. If tests fail on a textually clean merge, a semantic conflict exists and requires investigation. Sequential ordering makes this tractable because the failing merge is the most recent one -- you know exactly which branch introduced the semantic conflict.

### Sequential vs. All-at-Once Merge

| Property | Sequential (dependency-ordered) | All-at-once (arbitrary order) |
|---|---|---|
| Conflict surface | Each merge integrates against the expected base state | Later merges integrate against an unpredictable accumulation |
| Debugging | Conflict is attributable to a single branch | Conflict may involve interactions among multiple branches |
| Semantic conflict detection | Test after each merge pinpoints the source | Tests only run after all merges; ambiguous blame |
| Throughput | O(N) merge+test cycles | O(1) merge attempt, but O(N) conflict resolution in the worst case |
| Rollback granularity | Revert the last merge to restore a known-good state | Must untangle interleaved changes |

The throughput cost of sequential merging is acceptable when each merge+test cycle takes minutes (typical for agent-scoped changes), and the alternative is hours of manual conflict resolution.

### Topological Ordering for Merge Sequencing

When the planner produces a DAG of sub-tasks, the DAG's edges encode data and file dependencies. A topological sort of this DAG yields a merge order that respects dependencies: if task B depends on the output of task A, then A's branch merges first. For independent tasks (no DAG edge between them), merge order does not matter semantically, but a stable tiebreaker (e.g., by creation time or scope breadth) avoids nondeterminism.

This is the same principle used by build systems (Make, Maven, Gradle) to order compilation units, applied here to order branch integration.

## Policy

### 1. Merge branches sequentially in DAG dependency order

When a set of agent branches is ready for integration, the orchestrator MUST merge them one at a time in topological order of the task DAG. If task B depends on task A (A -> B edge in the DAG), then A's branch merges before B's branch. This is not optional even if all branches appear textually conflict-free.

### 2. Use a stable tiebreaker for independent branches

For branches with no dependency relationship (no DAG path between them), merge in order of decreasing scope: branches that touch more foundational files (models, schemas, config) merge before branches that touch leaf files (tests, docs, UI). If scope is equal, merge in task creation order. The tiebreaker MUST be deterministic -- never random.

### 3. Rebase each branch onto the accumulated target before merging

Before merging branch B, rebase it onto the target branch (which already contains all previously merged branches). If the rebase produces conflicts, route them through the tiered conflict resolution process (see `docs/merge-conflict-resolution.md`, section 3). Do not attempt a direct merge without rebase, as this obscures whether the branch is compatible with the current target state.

### 4. Run the test suite after each merge

After each successful merge (or conflict resolution), run the full test suite before proceeding to the next merge. If tests fail, the most recently merged branch is the cause. Halt the merge sequence, diagnose, and either fix (via a resolution agent) or escalate to a human. Do not merge subsequent branches on top of a failing state.

### 5. Record merge order in the execution log

The orchestrator MUST log the merge sequence with timestamps, branch names, conflict status, and test results. This log is the primary debugging artifact when integration fails. Store it in the SQLite state store alongside the DAG execution record.

### 6. The planner must encode file-level dependencies in the DAG

When the planner decomposes an issue into sub-tasks, it MUST identify which files each task is likely to modify. If two tasks modify the same file, the planner MUST add a dependency edge between them (serializing access to that file) rather than leaving them as independent parallel nodes. This is the single most effective conflict-avoidance measure.

### 7. Prefer additive changes and file isolation

Agents SHOULD be instructed to add new files and functions rather than modify existing ones when the task permits. When modification is unavoidable, the planner SHOULD scope each agent's changes to non-overlapping files. This reduces both textual and semantic conflict probability.

### 8. Never merge more than one branch in a single git operation

The orchestrator MUST NOT use octopus merge or any strategy that combines more than two branch tips. Every merge is a two-way operation: the current target state plus one agent branch.

### 9. Independent branches may merge in parallel only with guard tests

As an optimization, branches that share no files AND have no DAG dependency MAY be merged in parallel into separate intermediate branches, then combined. However, the final integration must still run the test suite, and this optimization is only permitted when the planner has verified file-set disjointness. When in doubt, merge sequentially.

## Rationale

Sequential merge ordering is a prevention strategy, not a resolution strategy. It is cheaper to avoid conflicts than to resolve them. The key insights:

1. **Agents write code against an assumed base state.** When agent B writes code, it assumes the codebase looks a certain way. If agent A's changes are merged first, the codebase matches B's assumptions (because B depended on A, or because A's changes are in a different area). If merged in the wrong order, B's assumptions are violated, producing avoidable conflicts.

2. **Sequential merge makes blame unambiguous.** When a conflict or test failure occurs after merging branch K in a sequence of N branches, the cause is the interaction between branch K and the accumulated state of branches 1..K-1. This is far easier to diagnose than an N-way merge failure.

3. **Test-after-merge catches semantic conflicts.** The most dangerous conflicts are the ones git does not detect -- where both changes merge cleanly but the program breaks. Running tests after each sequential merge turns the test suite into a semantic conflict detector with single-branch resolution.

4. **The throughput cost is low for agent-scoped changes.** Agent sub-tasks are small by design (per the Maximum Agent Separation policy). A typical merge+test cycle for a single agent's changes takes 1-5 minutes. Even with 10 sequential merges, total integration time is under an hour -- acceptable for a system that already spent longer generating the code.

5. **Topological ordering is already available.** The orchestrator already builds and traverses a task DAG using networkx. Extracting a topological sort for merge ordering is trivial additional work that yields significant reliability gains.
