# Policy 17: Merge Conflict Resolution

## Background / State of the Art

When multiple agents work on the same codebase in parallel, merge conflicts arise when branches are integrated. Standard `git merge` operates on lines of text with no language awareness. AST-aware merge tools (Mergiraf, tree-sitter) resolve structurally independent changes that line-based merge flags incorrectly. LLM-based resolution can handle semantic conflicts but is expensive and not always confident.

See [merge-conflict-resolution.md](../merge-conflict-resolution.md) for full state-of-the-art analysis including GitHub Copilot Workspace, GitLab AI resolution, and CodeFusion.

---

## Policy

### 1. No append-only restriction on agents

Agents modify existing code freely. We do not constrain agents to only adding new files/functions as a conflict avoidance strategy. Append-only patterns lead to codebase bloat: dead functions never get removed, logic gets duplicated, and the codebase grows in surface area with every issue. Real issues require editing existing code, and we accept the higher conflict surface in exchange for a codebase that stays clean.

### 2. AST merging as a preprocessing step

Before entering LLM-based resolution tiers, run an AST-aware merge pass (tree-sitter) to auto-resolve structural conflicts that line-based git merge flags incorrectly. This reduces load on more expensive LLM-based resolution.

### 3. Tiered conflict resolution

Conflicts are resolved through an escalating sequence:

1. **Trivial auto-resolve** — import reorder, whitespace, independent additions to same file.
2. **AST-aware merge** — tree-sitter parse and structural merge. If resolved, run tests.
3. **Generated/formulaic code** — regenerate the section with both contexts merged in the prompt.
4. **Conflict resolution agent** — sees both branches, original issue, and outputs from both agents. If confident, apply resolution and run tests.
5. **Triage agent** — if the resolution agent is not confident (or tests fail after resolution), a triage agent decides which branch to rebuild.
6. **Rebuild** — re-run the chosen branch's subtask from scratch on a new branch, using the conflicting branch as context. Branch named `agent/<issue>/<desc>-rebuild-<n>`.
7. **Escalate to human** — after max rebuilds (configurable, default: 1 per branch), escalate with full context.

### 4. Rebuild before human escalation

When the resolution agent is not confident, rebuild the conflicting branch from scratch rather than immediately escalating. This gives the system one more autonomous attempt before consuming human attention.

### 5. Configurable rebuild limit

A parameter `max_rebuilds_per_branch` (default: **1**) controls how many times a branch can be rebuilt before escalating to a human. Each rebuild increments a per-branch counter tracked in the state store.

### 6. Triage agent decides which branch to rebuild

The triage agent does not resolve the conflict. Its job is to decide **which branch should be rebuilt** — the branch that is harder to reconcile with the merged state. The other side merges as-is.

### 7. Semantic conflict detection via test-after-merge

After a textually clean merge, run the test suite. Semantic conflicts (clean merge but broken behavior) only surface at runtime. If tests fail after a clean merge, treat it as a conflict requiring investigation.

### 8. Rich context for resolution agents

When delegating to a conflict resolution agent, provide: the conflict diff with markers, the original subtask descriptions for both agents, the outputs/reasoning from both agents, the full file content (not just the conflicting region), and the original issue for grounding.

### 9. Never force-push agent branches

Agent branches are merge-only, never rebased or force-pushed. This preserves the full history of what each agent did, which is essential for debugging conflict resolution failures.

---

## Rationale

This policy makes three key departures from common approaches:

1. **No append-only restriction** — accepting higher conflict surface in exchange for clean code. Append-only strategies avoid conflicts at the cost of codebase quality.

2. **Rebuild before escalate** — giving the system one more autonomous attempt before consuming human attention. Rebuilding with full conflict context often succeeds because the agent can see what went wrong and write compatible code.

3. **AST preprocessing** — resolving a class of false-positive conflicts cheaply before invoking expensive LLM resolution. Tree-sitter grammars cover most mainstream languages and handle independent additions, reorderings, and formatting changes that line-based merge cannot.

The tiered approach ensures that cheap resolution methods are tried before expensive ones, and that human attention is only consumed when autonomous methods have been exhausted.
