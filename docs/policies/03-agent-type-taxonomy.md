# Policy 03: Agent Type Taxonomy

Agent_agent uses five agent types — Research, Code, Test, Review, and Plan composite — each with a single, well-defined responsibility enforced at the tool layer. There is no separate Commit or git agent: the Code agent handles git operations within its Coding composite's isolated worktree (specific per-operation git permission boundaries will be defined post-MVP). The Plan composite is the mandatory orchestration node present at every nesting level; all other types are selected by the planner based on issue requirements. New agent types require passing a four-point decomposition checklist before they may be added.

---

### P3.1 The system uses five agent types: Research, Code, Test, Review, and Plan composite

These five types cover the full lifecycle of issue resolution: understand the problem (Research), produce a solution (Code), verify the solution (Test), evaluate it for quality (Review), and orchestrate the overall strategy (Plan composite).

### P3.2 The Code agent handles git operations within Coding composite nodes

There is no separate Commit or git agent. The Code agent (and its sub-agents within the Coding composite) handles file writes and git operations within its isolated worktree. When the Coding composite node exits, it pushes all changes to its remote branch [P1.11]. Specific per-operation git permission boundaries will be defined post-MVP.

### P3.3 Each agent type has a single, well-defined responsibility

| Type | Responsibility | Can | Cannot |
|------|---------------|-----|--------|
| **Research** | Understand the problem. Read code, issues, and documentation. Identify affected files, root causes, and constraints. | Read files, search code, read git history, read GitHub issues/PRs | Write files, run tests, execute code, touch git, comment on PRs |
| **Code** | Produce file changes that resolve the assigned sub-task. Handle git within the Coding composite's worktree. | Read files, write files, run tests for local validation, git operations within worktree (post-MVP: scoped) | Create or comment on PRs |
| **Test** | Execute test suites and validate that changes meet acceptance criteria. Used as sub-agents within Coding composites. | Read files, run pytest and other test commands | Write source files, comment on PRs |
| **Review** | Evaluate code quality, correctness, and adherence to standards. | Read files, read diffs, read git history, comment on PRs | Write files, touch git, run tests, merge PRs |
| **Plan composite** | Orchestrate. Research the current state, produce a plan, and decide to spawn a child DAG or return `null`. Internal sub-DAG (Research → Plan → Orchestrate) is opaque to the outer DAG. | All Research capabilities; DAG plan construction; child DAG spawning | Write source files, touch git outside internal Research sub-agents, merge PRs |

### P3.4 Agent types are defined by their tool sets, not just their prompts

System prompts describe the agent's role, but enforcement happens at the tool layer. Each agent type receives only the tools it needs via the Anthropic API's `tools` parameter. The orchestrator's executor validates every tool call against the agent's permission profile before execution. An agent that attempts a disallowed tool call is rejected by the executor, not just discouraged by its prompt. Arguments are also validated — a permitted tool with dangerous arguments is still rejected [P8.5].

### P3.5 No agent type covers architecture, debug, refactor, or deploy as a standalone role

These are intentionally excluded:
- **Architecture** is handled by the Plan composite's internal planning sub-agent.
- **Debug** is handled by the Coding composite's internal Debugger sub-agent.
- **Refactor** is a sub-case of Code.
- **Deploy** is out of scope. The system produces PRs; humans or CI/CD pipelines handle deployment.

### P3.6 New agent types require justification against the decomposition checklist

Before adding a new agent type, it must pass all four checks:

1. Does the proposed type have a responsibility that no existing type covers?
2. Does it require a materially different tool set than any existing type?
3. Does combining it with an existing type violate least privilege (the combined type would have tools it does not need for one of its responsibilities)?
4. Does the coordination cost of adding another DAG node outweigh the safety and clarity benefits of separation?

If a proposed type fails checks 1 or 2, it should be a variant prompt on an existing type, not a new type. If it fails check 3, it must be a new type.

### P3.7 The taxonomy is a ceiling, not a floor

Not every DAG requires all five types. A documentation-only issue might need only Research and Code. A test-only issue might use only Research and Test. The Plan composite is the one mandatory type — it appears at every nesting level. The taxonomy defines the maximum available set of roles, not a mandatory pipeline.

---

### Violations

- Giving a Code agent the ability to create or comment on PRs.
- Giving a Research agent file-write tools.
- Creating a separate agent type for git operations (Commit, git, deploy) — the Code agent handles git within its Coding composite.
- Adding a new agent type without passing the four-point decomposition checklist [P3.6].
- A Review agent merging PRs.
- A Plan composite writing source files directly.

### Quick Reference

| Type | Core Capability | Hard Restriction |
|------|----------------|-----------------|
| Research | Read-only: files, git history, GitHub | No writes, no git mutations, no test execution |
| Code | Read/write files; run tests; git within worktree | No PR creation or comments |
| Test | Run test commands, read files | No file writes, no git |
| Review | Read files, diffs, git history; PR comments | No file writes, no git mutations, no merges |
| Plan composite | Research capabilities + DAG construction + child DAG spawning | No source file writes, no PR merges |
