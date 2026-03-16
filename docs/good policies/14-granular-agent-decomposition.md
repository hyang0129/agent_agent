# Policy 14: Granular Agent Decomposition

## Background / State of the Art

Agent orchestrators must scope each agent's capabilities to minimize blast radius when agents misbehave or hallucinate. The principle of least privilege (AWS IAM, GitHub fine-grained PATs) applies directly: grant only the minimum access required to complete a task. The Anthropic and OpenAI APIs enforce this at the tool level — an agent that is not given a tool literally cannot call it. However, tool-level scoping alone is insufficient if agent roles are too broad. An agent that both writes code and commits it can hallucinate a change and immediately persist it to git in a single turn.

See [agent-permissions.md](../agent-permissions.md) for full state-of-the-art analysis.

---

## Policy

### 1. Every agent should do exactly one kind of work

If an agent can both produce changes and persist them, it has too much power. Decompose broad agent roles into narrow, single-responsibility agents. This improves:

- **Safety** — destructive operations are isolated to dedicated agents with minimal scope, reducing blast radius when an agent misbehaves or hallucinates.
- **Logging** — when each agent does one thing, every log entry maps to a clear, auditable action. Mixed-responsibility agents produce ambiguous audit trails.
- **Retry and rollback** — if a narrow agent fails, its impact is contained. There is no tangled state to unwind across multiple concern areas.
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

---

## Rationale

This policy is the enforcement mechanism for the Maximum Agent Separation design principle stated in CLAUDE.md. It operationalizes the principle into concrete rules: what to split, how to split it, and how to enforce the split at runtime.

Giving each coding composite node its own worktree is the highest-value isolation mechanism because it guarantees file mutations are contained — a misbehaving coding agent cannot corrupt the primary checkout or collide with other concurrent nodes.

The decomposition checklist (Rule 4) ensures this policy scales to future agent types without requiring per-type policy amendments. Any new agent type that passes all four checks is safe by construction; any that fails a check must be decomposed before implementation.

Tool-layer enforcement (Rule 5) is non-negotiable because system prompts are suggestions, not boundaries. Research and production experience consistently show that agents can ignore prompt-level restrictions under adversarial inputs or ambiguous instructions.
