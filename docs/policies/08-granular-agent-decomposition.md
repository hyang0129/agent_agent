# Policy 08: Granular Agent Decomposition

Every agent must do exactly one kind of work. The most critical decomposition is between agents that produce changes (write files, generate code) and agents that persist changes (git commit, push, create PRs) — these must never be the same agent. Permissions are enforced at the tool layer, not the prompt layer: each agent type receives only the tools it needs via the API, and the executor validates every tool call against the agent's permission profile before execution. Each coding composite node receives its own isolated git worktree, so file mutations from one node cannot interfere with other concurrent nodes.

---

### 1. Every agent should do exactly one kind of work

If an agent can both produce changes and persist them, it has too much power. Decompose broad agent roles into narrow, single-responsibility agents. This improves:

- **Safety** — destructive operations are isolated to dedicated agents with minimal scope.
- **Logging** — every log entry maps to a clear, auditable action.
- **Retry and rollback** — if a narrow agent fails, its impact is contained.
- **Review surface** — a human reviewing agent actions can reason about a single-purpose agent far more easily than a multi-purpose one.

### 2. Separate mutation from persistence

The most critical decomposition is between agents that **produce changes** (write files, generate code) and agents that **persist changes** (git commit, git push, create PRs). These MUST never be the same agent.

An agent that can both write arbitrary files and run `git push` has an unacceptably large blast radius. A hallucination in a coding step can be immediately and irreversibly published. Separating these concerns introduces a mandatory validation boundary.

### 3. The coding composite node receives its own working tree

The orchestrator creates a temporary git worktree for each coding composite node. The coding agents operate exclusively within this isolated worktree, so their file mutations never touch the primary checkout or interfere with other concurrent coding nodes. When the node completes, the orchestrator merges or discards the worktree as appropriate.

### 4. New agent type decomposition checklist

When defining a new agent type, apply these checks:

1. **Does this agent both produce output and persist it?** Split it.
2. **Does this agent have access to both read and write tools for the same resource?** Consider whether the read and write sides can be separate agents.
3. **Does this agent have access to any destructive operation?** That operation should be the agent's *only* job, so it can be audited and gated independently.
4. **Can this agent's failure cause state that is hard to roll back?** Isolate the irreversible action into its own agent with the narrowest possible scope.

If a proposed agent type fails any of these checks, decompose it further before implementation.

### 5. Enforce permissions at the tool layer, not the prompt layer

- Only pass allowed tools to the Claude API call.
- Wrap tool execution in a permission checker that validates against the agent's permission profile.
- Validate tool **arguments**, not just tool names — a permitted tool with dangerous arguments is still dangerous.
- If an agent returns a tool call it shouldn't have, the executor rejects it.

### 6. Audit all tool calls

Every tool call, regardless of whether it is allowed, MUST be logged with: agent type, agent ID, tool name, allowed/denied status, denial reason (if denied), and timestamp. Denied actions are especially important to log — they indicate either a misconfigured agent or a prompt injection attempt.
