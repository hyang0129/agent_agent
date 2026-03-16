# Merge Conflict Resolution

## Problem Statement

When multiple agents work on the same codebase in parallel, they operate on separate branches that will eventually need to merge. If two agents modify the same file — or even different files that interact — merge conflicts arise. An orchestrator must decide: resolve automatically, delegate to a specialized agent, or escalate to a human.

## State of the Art

### GitHub Copilot Workspace
Copilot Workspace generates code changes in a "spec → plan → implementation" pipeline. When conflicts arise between generated changes, it re-runs the implementation step with awareness of the conflicting changes. It does not attempt three-way merge resolution — it regenerates from a merged context.

**Key insight:** It can be cheaper and more reliable to regenerate conflicting code with full context than to resolve merge markers mechanically.

### Devin / SWE-Agent
These autonomous coding agents work on a single branch sequentially. They sidestep merge conflicts entirely by never parallelizing code changes. When they encounter existing merge conflicts in a repo, they use `git` tools to inspect the conflict markers and attempt resolution, but this is a secondary capability.

### GitLab AI Conflict Resolution (Experimental)
GitLab has experimented with using LLMs to resolve merge conflicts by presenting both sides of the conflict plus surrounding context to a model and asking it to produce the merged result. Results are mixed — it works well for trivial conflicts (import reordering, independent additions) but struggles with semantic conflicts where both sides modify the same logic.

### Microsoft CodeFusion (Research, 2023)
A research system that uses program analysis (AST diffing, dependency graphs) to detect semantic conflicts that `git merge` misses — cases where both sides merge cleanly textually but produce incorrect behavior. This is the harder problem: conflicts that don't show up as merge markers.

### Mergiraf / Tree-Sitter Merge
Tools that perform AST-aware merging instead of line-based merging. They parse both sides into syntax trees and merge at the tree level. This resolves many conflicts that line-based merge cannot.

**How AST merging works:**

Standard `git merge` operates on lines of text. It has no understanding of language structure — it just sees sequences of characters. When two branches modify nearby lines, git sees overlapping regions and declares a conflict even if the changes are structurally independent.

AST-aware merge tools (Mergiraf, tree-sitter-based mergers) add a parsing step:

1. **Parse all three versions** (base, ours, theirs) into abstract syntax trees using a language grammar (tree-sitter grammars cover most popular languages).
2. **Diff at the tree level** — instead of "line 14 changed," the diff says "the function `handle_auth` gained a new parameter" or "a new import was added to the import block." These are structural operations on named nodes, not anonymous text spans.
3. **Merge structurally** — two branches both adding imports? The AST merge inserts both into the import node's children. Two branches modifying different functions in the same file? No conflict — the changes target different subtrees. Two branches modifying the *same* function? That's a real semantic conflict and gets flagged.
4. **Render back to text** — the merged AST is pretty-printed back to source code, preserving formatting conventions.

**What AST merging resolves that line-based merge cannot:**

- **Independent additions to the same block** — two branches adding different items to a list, different imports, different test cases in the same file. Line-based merge sees overlapping regions; AST merge sees independent insertions into the same parent node.
- **Reordering vs. modification** — one branch reorders functions, the other modifies one. Line-based merge panics; AST merge tracks nodes by identity, not position.
- **Whitespace/formatting changes** — one branch reformats, the other adds logic. The AST is whitespace-agnostic, so formatting changes don't conflict with structural changes.

**What AST merging still cannot resolve:**

- **Same-node semantic conflicts** — two branches modify the same function body with different intent. The tool correctly flags this as a real conflict.
- **Cross-node semantic dependencies** — Branch A changes a function signature, Branch B calls that function with the old signature. The AST merge succeeds (different nodes) but the result doesn't compile. This is the domain of semantic conflict detection (see CodeFusion above).
- **Language coverage gaps** — AST merging requires a grammar for each language. Tree-sitter covers most mainstream languages, but configuration files, DSLs, and niche languages may fall back to line-based merge.

**Relevance to agent_agent:** AST merging is most valuable as a pre-processing step before our tiered resolution. Running an AST-aware merge first can auto-resolve a class of conflicts that would otherwise hit the resolution agent, reducing the load on more expensive LLM-based resolution. It could slot in as a step between "trivial auto-resolve" and "assign conflict resolution agent" in the tiered flow.

## Our Policy

Our merge conflict resolution policy follows best practices from the research above with three key departures:

1. **No append-only restriction** — agents modify existing code freely. We do not constrain agents to only adding new files/functions as a conflict avoidance strategy. Append-only patterns lead to codebase bloat: dead functions never get removed, logic gets duplicated across "new" files instead of being consolidated, and the codebase grows in surface area with every issue. Real issues require editing existing code, and we accept the higher conflict surface in exchange for a codebase that stays clean.
2. **Rebuild before human review** — when the resolution agent is not confident, we rebuild the conflicting branch from scratch rather than immediately escalating to a human. This gives the system one more autonomous attempt before consuming human attention.
3. **AST merging as a preprocessing step** — before entering the LLM-based resolution tiers, we run an AST-aware merge pass to auto-resolve structural conflicts that line-based git merge flags incorrectly.

### 1. Minimize Conflict Surface by Design

The best conflict resolution is conflict avoidance:

- **File-level task partitioning**: When the planner decomposes an issue, prefer subtasks that touch non-overlapping files. If two subtasks must modify the same file, make them sequential in the DAG rather than parallel.
- **Lock files in the DAG**: If the planner knows two subtasks touch the same file, it can model that file as a shared resource and serialize access.

### 2. Sequential Merge Order

Merge branches in DAG dependency order, not randomly:

```
Agent A (auth changes) ──→ merge first
Agent B (tests for auth) ──→ merge second (onto A's result)
Agent C (unrelated docs) ──→ merge any time (independent)
```

This ensures that when Agent B's branch is merged, it merges against a codebase that already includes A's changes — which is the state B expected when it was working.

### 3. Tiered Conflict Resolution

A configurable parameter `max_rebuilds_per_branch` (default: **1**) controls how many times a branch can be rebuilt from scratch before escalating to a human. Each rebuild increments a per-branch counter tracked in the state store.

```
Conflict detected
       │
       ▼
  Is it trivial?  ──yes──→  Auto-resolve (import reorder, whitespace,
       │                     independent additions to same file)
       no
       │
       ▼
  AST-aware merge pass   ──resolved──→  Run tests
  (tree-sitter parse,                        │
   structural merge)                    Tests pass?  ──yes──→  Continue
       │                                     │
   still conflicting                         no
       │                                     │
       ▼                                     ▼
  Is it in generated    ──yes──→  Regenerate the section with both
  or formulaic code?              contexts merged in the prompt
       │
       no
       │
       ▼
  Assign conflict resolution agent
  (sees both branches, original issue,
   and outputs from both agents)
       │
       ▼
  Agent confident?  ──yes──→  Apply resolution, run tests
       │                              │
       no                             ▼
       │                        Tests pass?  ──no──→  (treat as not confident,
       ▼                              │                 enter rebuild path below)
  Assign triage agent                yes
  (decides which side to rebuild)     │
       │                              ▼
       ▼                        Continue merge sequence
  rebuilds < max_rebuilds_per_branch?
       │
      yes
       │
       ▼
  Rebuild the chosen branch from scratch:
    1. Create new branch from merge target
       (named: agent/<issue>/<desc>-rebuild-<n>)
    2. Re-run the original agent's subtask,
       using the conflicting branch as context
       (the agent sees what was attempted and why
       it conflicted, but writes fresh code)
    3. Increment rebuild counter for this branch
    4. Re-attempt merge
    5. Go back to "Conflict detected" if new
       conflicts arise
       │
       no (max rebuilds reached)
       │
       ▼
  Escalate to human with:
    - Original branch diff
    - Rebuild branch diff(s)
    - Triage agent's reasoning
    - Conflict explanation
```

#### Rebuild Branch Naming

Rebuilt branches follow the pattern `agent/<issue>/<desc>-rebuild-<n>` where `<n>` is the rebuild count (starting at 1). This makes it immediately visible in git history that a branch was rebuilt due to a merge conflict and how many attempts were made.

#### Triage Agent Role

The triage agent does not attempt to resolve the conflict itself. Its job is narrower: given both sides of the conflict and the original subtask descriptions, it decides **which branch should be rebuilt**. The branch that is harder to reconcile with the merged state — typically the one making broader changes or the one whose assumptions are more invalidated by the other side — is the rebuild candidate. The other side merges as-is.

### 4. Semantic Conflict Detection

After a clean merge (no textual conflicts), run the test suite. Semantic conflicts — where both changes merge cleanly but break behavior — only show up at runtime. If tests fail after a clean merge, treat it as a conflict requiring investigation.

### 5. Conflict Context for Resolution Agents

When delegating to a conflict resolution agent, provide:

- The conflict diff with markers
- The original subtask descriptions for both agents
- The outputs/reasoning from both agents about why they made their changes
- The full file content (not just the conflicting region)
- The original issue for grounding

### 6. Never Force-Push Agent Branches

Agent branches should be merge-only, never rebased or force-pushed. This preserves the full history of what each agent did, which is essential for debugging conflict resolution failures.

## Future: Cross-PR Merge Graph (Post-MVP)

Beyond single-issue merge conflicts, the system should eventually have **project-level awareness** of all PRs undergoing work simultaneously. This is outside the scope of the MVP but is the intended direction.

### Concept

A **cross-PR merging agent** operates above individual issue DAGs. It maintains a live view of all in-flight PRs, estimates their complexity (files touched, overlap surface, expected merge order), and constructs a **merge graph** — a dependency graph of PRs that models how they should be merged relative to each other.

### How It Works

1. **PR inventory and complexity estimation** — the agent tracks all open agent-generated PRs with metadata: files modified, lines changed, overlap with other PRs, estimated merge difficulty (trivial/structural/semantic).
2. **Merge graph construction** — PRs that touch overlapping files are connected in the graph with edges weighted by conflict likelihood. The agent computes a merge order that minimizes cascading conflicts (merge the most-depended-on PRs first, independent PRs in parallel).
3. **Proactive conflict resolution** — when the graph predicts that two PRs will conflict, the agent can trigger early rebuilds or reorder merge sequencing before a human ever sees a conflict.
4. **Unified merge commit** — the goal is that when a human sits down to review, all PRs can be merged into a single combined branch with conflicts already resolved. The human reviews the aggregate result, not individual merge conflict diffs. If unresolvable conflicts remain, they are surfaced with full context from the graph.

### Why Post-MVP

This requires state that spans multiple issues and persists across orchestrator runs — a significant step up from the per-issue DAG model. It also requires heuristics for complexity estimation that need to be tuned against real merge data. The MVP focuses on getting single-issue conflict resolution right; the merge graph builds on that foundation.

## Previous Stable Approach

### Manual Human Resolution (Universal Default)
The longstanding standard: when merge conflicts arise, a human developer opens the file, reads both sides, and makes a judgment call. Tools like VS Code, IntelliJ, and `vimdiff` provide three-way merge UIs. This is reliable but doesn't scale when an orchestrator is generating dozens of parallel branches.

### git rerere (Reuse Recorded Resolution)
Git's built-in mechanism for remembering how you resolved a conflict and automatically applying the same resolution if the same conflict recurs. Useful for repeated merge/rebase cycles but doesn't help with novel conflicts.

### Merge Queues (GitHub, GitLab, Bors)
Merge queues serialize PR merges and test each merge commit before advancing. If a merge produces conflicts or test failures, it's ejected from the queue. This doesn't resolve conflicts — it prevents conflicting merges from landing. The resolution is still manual.

### Feature Flags / Branch by Abstraction
Avoid merge conflicts entirely by having all changes go to `main` behind feature flags. Changes are integrated continuously, and conflicts surface immediately (at commit time, not merge time). This is a workflow pattern, not a technical resolution mechanism — it trades merge conflicts for integration discipline.
