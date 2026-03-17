# Policy 08: Granular Agent Decomposition

Every sub-agent must do exactly one kind of work. The Programmer and Debugger sub-agents handle file writes and git operations within their Coding composite's isolated worktree; PR creation is an orchestrator operation, not a sub-agent operation — no sub-agent is authorized to create or merge PRs. Permissions are enforced at the tool layer, not the prompt layer: each sub-agent receives only the tools it needs via the API, and the executor validates every tool call — including arguments — against the sub-agent's permission profile before execution. Each Coding composite node receives its own isolated git worktree, so file mutations from one node cannot interfere with other concurrent nodes.

---

### P8.1 Every agent should do exactly one kind of work

If an agent has responsibilities that span fundamentally different capability areas, decompose it. This improves:

- **Safety** — destructive operations are isolated to dedicated agents with minimal scope.
- **Logging** — every log entry maps to a clear, auditable action.
- **Retry and rollback** — if a narrow agent fails, its impact is contained.
- **Review surface** — a human reviewing agent actions can reason about a single-purpose agent far more easily than a multi-purpose one.

### P8.2 Separate code production from PR creation

The Programmer and Debugger sub-agents handle file writes and git operations (commits, pushes) within their Coding composite's isolated worktree. PR creation is an orchestrator operation triggered after all coding and review completes — it is not delegated to any sub-agent. No sub-agent is authorized to create or merge PRs.

This separation ensures that no hallucination or error in a coding step can produce an irreversible, publicly-visible artifact without passing through the Review composite and the orchestrator's PR creation gate.

Specific per-operation git permission boundaries within Coding composite nodes will be defined post-MVP [P3.6].

### P8.3 The Coding composite node receives its own isolated working tree

The orchestrator creates a temporary git worktree for each Coding composite node. Agents operating within that node work exclusively in this isolated worktree — their file mutations never touch the primary checkout or interfere with other concurrent Coding composite nodes. When the node exits, it pushes all changes to its remote branch [P1.11], and the orchestrator merges or discards the worktree as appropriate.

### P8.4 New agent type decomposition checklist

When defining a new agent type, apply these checks:

1. **Does this agent both produce code output and create PRs?** Separate them.
2. **Does this agent have access to both read and write tools for the same resource?** Consider whether read and write sides can be separate agents.
3. **Does this agent have access to any destructive operation?** That operation should be the agent's *only* job so it can be audited and gated independently.
4. **Can this agent's failure cause state that is hard to roll back?** Isolate the irreversible action into its own agent with the narrowest possible scope.

If a proposed agent type fails any of these checks, decompose it further before implementation.

### P8.5 Enforce permissions at the tool layer, not the prompt layer

- Only pass allowed tools to the SDK agent invocation.
- Wrap tool execution in a permission checker that validates against the agent's permission profile.
- Validate tool **arguments**, not just tool names — a permitted tool with dangerous arguments is still dangerous.
- If an agent returns a tool call it shouldn't have, the executor rejects it and classifies it as a Safety Violation [P10.7].

Tool names in the permission matrices (P3.3) describe capability intents, not literal SDK tool names. The implementation maps intents to SDK-specific tool names and validates compliance against the intent (e.g., "no file writes" means no tool that can modify files, regardless of what that tool is named in the SDK).

### P8.6 Audit all tool calls

Every tool call, regardless of whether it is allowed, MUST be logged with: agent type, agent ID, tool name, allowed/denied status, denial reason (if denied), and timestamp. Denied actions are especially important to log — they indicate either a misconfigured agent or a prompt injection attempt.

---

### Violations

- Any sub-agent that can both write source files and create or merge PRs.
- Programmer or Debugger sub-agents that write to the primary git checkout instead of their isolated worktree.
- Enforcing permissions only in system prompts without tool-layer validation.
- Omitting argument validation for dangerous tools [P8.5].
- Not logging denied tool calls [P8.6].
- A sub-agent creating a PR directly (PR creation is an orchestrator operation only).

### Quick Reference

| Concern | Owner | Notes |
|---------|-------|-------|
| File writes | Programmer / Debugger (in worktree) | Isolated per Coding composite |
| Git commit/push | Programmer / Debugger (in worktree) | Pushes to remote on node exit [P1.11] |
| PR creation | Orchestrator only | Not delegated to any sub-agent |
| PR merge | Human only | Sub-agents never merge |
| Permission enforcement | Tool layer + argument validation | Prompt-layer guidance alone is insufficient |
| Tool call audit | All calls, allowed and denied | Denied calls flagged as potential misconfig or injection |
