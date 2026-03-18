# Policy 03: Agent Type Taxonomy

Agent_agent uses three composite types — Plan, Coding, and Review. Each composite contains one or more named sub-agents. Sub-agents within a composite are not exposed to the **outer** DAG as separate nodes; the outer DAG sees only the composite's typed input/output. Internally, sub-agents execute as nodes within a composite-scoped DAG (an iterative nested DAG for Coding composites [P1.8, P10.4], or a single-iteration internal DAG for Plan and Review composites). Permissions are enforced at the tool layer, not only in system prompts. New composite types require passing a four-point decomposition checklist before they may be added.

---

### P3.1 The system uses three composite types: Plan, Coding, and Review

These three composites cover the full lifecycle of issue resolution: understand the problem and produce a plan (Plan composite), implement and verify the solution (Coding composite), and evaluate the result for quality (Review composite).

### P3.2 Each composite contains named sub-agents

| Composite | Sub-agent | Responsibility |
|-----------|-----------|----------------|
| **Plan composite** | ResearchPlannerOrchestrator | Reads the issue and repo context, produces an investigation summary, and generates the child DAG specification or `null` (work complete). Single invocation; no separate research and planning steps. |
| **Coding composite** | Programmer | Writes file changes that resolve the assigned sub-task. Handles git within the Coding composite's worktree. |
| **Coding composite** | Test Designer | Designs the test plan for the Programmer's changes. |
| **Coding composite** | Test Executor | Runs the test suite and reports results. |
| **Coding composite** | Debugger | Diagnoses test failures and writes corrective changes. |
| **Review composite** | Reviewer | Evaluates code quality, correctness, test coverage, and policy compliance across all Coding composite outputs. |

Sub-agents are not interchangeable across composites. The Programmer cannot be invoked from the Review composite; the Reviewer cannot be invoked from the Coding composite. Sub-agents are nodes within their composite's internal DAG [P10.2, P10.4] but are opaque to the outer DAG — the Plan composite reasons about "Coding composite A," not about individual sub-agent invocations within it.

### P3.3 Each sub-agent type has a single, well-defined responsibility

| Sub-agent | Can | Cannot |
|-----------|-----|--------|
| **ResearchPlannerOrchestrator** | Read files, read GitHub, read git history, DAG plan construction, child DAG spawning | Write files, touch git, create or comment on PRs |
| **Programmer** | Read files, write files, git operations within worktree | Create or comment on PRs, touch files outside worktree |
| **Test Designer** | Read files, read test suite | Write source files, run test commands |
| **Test Executor** | Read files, run test suite commands, write temporary/generated files during test execution | Net-modify source files committed by Programmer (validated post-execution via git diff), git operations |
| **Debugger** | Read files, write files, git operations within worktree | Create or comment on PRs, touch files outside worktree |
| **Reviewer** | Read files, read diffs, read git history, read GitHub PRs | Write files, touch git, merge PRs |

### P3.4 Sub-agent permissions are enforced at the tool layer

System prompts describe each sub-agent's role, but enforcement happens at the tool layer. Each sub-agent receives only the tools it needs via the SDK's tool configuration. The orchestrator's executor validates every tool call against the sub-agent's permission profile before execution. A sub-agent that attempts a disallowed tool call is rejected by the executor, not just discouraged by its prompt. Arguments are also validated — a permitted tool with dangerous arguments is still rejected [P8.5].

**Tool names are intent-level.** The permission matrix in P3.3 defines capabilities (read files, write files, run tests, git operations) rather than literal tool name strings. The implementation maps these capability intents to the specific tool names provided by the SDK (e.g., "Read", "Edit", "Bash"). Compliance is evaluated against the permission intent — read-only agents cannot write, worktree-scoped agents cannot escape — not against exact tool name matches.

### P3.5 No agent handles architecture, debug, refactor, or deploy as a standalone composite

- **Architecture** is handled by the Plan composite's ResearchPlannerOrchestrator.
- **Debug** is handled by the Coding composite's Debugger sub-agent.
- **Refactor** is a sub-case of what the Programmer handles.
- **Deploy** is out of scope. The system produces PRs; humans or CI/CD pipelines handle deployment.

### P3.6 The Code agent handles git operations within Coding composite nodes

There is no separate Commit or git composite. Programmer and Debugger sub-agents handle file writes and git operations within their isolated worktree. When the Coding composite node exits, it pushes all changes to its remote branch [P1.11]. Specific per-operation git permission boundaries will be defined post-MVP.

### P3.7 New composite types require justification against the decomposition checklist

Before adding a new composite type, it must pass all four checks:

1. Does the proposed composite have a responsibility that no existing composite covers?
2. Does it require a materially different tool set than any existing composite?
3. Does combining it with an existing composite violate least privilege (the combined composite would have tools it does not need for one of its responsibilities)?
4. Does the coordination cost of adding another DAG node outweigh the safety and clarity benefits of separation?

If a proposed composite fails checks 1 or 2, it should be a variant prompt on an existing sub-agent, not a new composite. If it fails check 3, it must be a new composite.

### P3.8 The taxonomy is a ceiling, not a floor

Not every DAG requires all three composites at every level. The Plan composite is the one mandatory composite — it appears at every nesting level. The Review composite and Coding composites are selected based on issue requirements.

---

### Violations

- Giving a Programmer or Debugger the ability to create or comment on PRs.
- Giving the ResearchPlannerOrchestrator file-write tools.
- Creating a separate composite or sub-agent type for git operations — Programmer and Debugger handle git within their Coding composite.
- Adding a new composite type without passing the four-point decomposition checklist [P3.7].
- A Reviewer merging PRs.
- The ResearchPlannerOrchestrator writing source files directly.
- Invoking a sub-agent from a composite it does not belong to.

### Quick Reference

| Composite | Sub-agents | Core Capability | Hard Restriction |
|-----------|-----------|----------------|-----------------|
| Plan | ResearchPlannerOrchestrator | Read-only analysis + DAG construction | No file writes, no PR creation |
| Coding | Programmer, Test Designer, Test Executor, Debugger | Read/write files; run tests; git within worktree | No PR creation or comments outside worktree |
| Review | Reviewer | Read files, diffs, git history; evaluate quality | No file writes, no git mutations, no merges |
