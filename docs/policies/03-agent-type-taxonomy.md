# Policy 03: Agent Type Taxonomy

Agent Agent uses five agent types — Research, Code, Test, Review, and Commit — corresponding to the four phases of issue resolution (understand, change, verify, evaluate) plus a dedicated persistence boundary. Each type has a single, well-defined responsibility enforced at the tool layer, not just the prompt layer: agents literally cannot call tools they were not given. The taxonomy defines a ceiling (all available types) not a floor — the planner selects only the types needed for a given issue. New agent types require passing a four-point decomposition checklist before they may be added.

---

### 1. The system uses four primary agent types: Research, Code, Test, and Review.

These four types map to the four essential phases of issue resolution: understand the problem, produce a solution, verify the solution, and evaluate the solution for quality.

### 2. A fifth type, Commit, handles persistence as a separate concern.

The Commit agent is a distinct type that bridges the Code/Test phase and the Review phase. It receives file changes, validates them, and persists them to git. No agent should both produce changes and make them permanent.

### 3. Each agent type has a single, well-defined responsibility.

| Type | Responsibility | Can | Cannot |
|------|---------------|-----|--------|
| **Research** | Understand the problem. Read code, issues, and documentation. Identify affected files, root causes, and constraints. | Read files, search code, read git history, read GitHub issues/PRs | Write files, run tests, execute code, touch git, comment on PRs |
| **Code** | Produce file changes that resolve the assigned sub-task. | Read files, write files, run Python/pytest for local validation | Any git operation, branch/commit/push, create or comment on PRs |
| **Test** | Execute test suites and validate that changes meet acceptance criteria. | Read files, run pytest and other test commands | Write source files, touch git, comment on PRs |
| **Commit** | Persist validated changes to git. | Read files (to verify diffs), git add/commit/push on assigned branch only | Write/modify source files, run arbitrary commands, create PRs |
| **Review** | Evaluate code quality, correctness, and adherence to standards. | Read files, read diffs, read git history, comment on PRs | Write files, touch git, run tests, merge PRs |

### 4. Agent types are defined by their tool sets, not just their prompts.

System prompts describe the agent's role, but enforcement happens at the tool layer. Each agent type receives only the tools it needs via the Anthropic API's tool parameter. The orchestrator's executor validates every tool call against the agent's permission profile before execution. A research agent that somehow attempts a file write is rejected by the executor, not just discouraged by its prompt.

### 5. No agent type covers architecture, debug, refactor, or deploy as a standalone role.

These are intentionally excluded:
- **Architecture** is the planner's job, not an agent's.
- **Debug** is handled by the Code-Test retry loop.
- **Refactor** is a sub-case of Code.
- **Deploy** is out of scope. This system produces PRs; humans (or CI/CD pipelines) handle deployment.

### 6. New agent types require justification against the decomposition checklist.

Before adding a new agent type, it must pass all four checks:

1. Does the proposed type have a responsibility that no existing type covers?
2. Does it require a materially different tool set than any existing type?
3. Does combining it with an existing type violate least privilege (i.e., the combined type would have tools it does not need for one of its responsibilities)?
4. Does the coordination cost of adding another DAG node outweigh the safety and clarity benefits of separation?

If a proposed type fails checks 1 or 2, it should be a variant prompt on an existing type, not a new type. If it fails check 3, it must be a new type.

### 7. The taxonomy is a ceiling, not a floor.

Not every DAG requires all five types. A documentation-only issue might need only Research and Code. A test-only issue might need only Research and Test. The planner selects which agent types to include based on the issue's requirements. The taxonomy defines the maximum set of available roles, not a mandatory pipeline.
