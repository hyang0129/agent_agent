# Agent Permissions

## Problem Statement

Not all agents should have the same capabilities. A research agent that only needs to read code should not be able to push commits. A coding agent that writes files should not be able to commit or push them. Without scoped permissions, a misbehaving or hallucinating agent can take destructive actions — deleting branches, overwriting files, posting incorrect PR comments — that are difficult or impossible to undo.

Beyond tool-level scoping, agents with broad roles are inherently harder to audit and constrain. An agent that both writes code and commits it can hallucinate a change and immediately persist it to git in a single turn. Granular decomposition — splitting broad roles into narrow, single-responsibility agents — is the primary mechanism for reducing blast radius.

## State of the Art

### Claude Code Permission Modes
Claude Code itself implements a tiered permission model:

- **Ask mode**: Every tool call requires explicit human approval.
- **Auto-accept mode**: Pre-approved tool categories execute without prompting.
- **Allowlists**: Specific tools or commands can be pre-approved via settings.

This is the closest existing model to what an agent orchestrator needs — scoped tool access per agent role.

**Key insight:** Permissions should be defined at the agent type level, not per invocation. A research agent always has read-only access; this shouldn't need re-confirmation each time.

### GitHub Fine-Grained Personal Access Tokens
GitHub's fine-grained PATs allow scoping permissions per repository and per operation (read contents, write contents, read issues, write issues, etc.). This maps directly to agent permissions — each agent type could use a different PAT scoped to its role.

**Limitation:** Managing multiple PATs adds complexity. For MVP on a single developer's machine, a single token with broad permissions is simpler, with enforcement at the orchestrator level.

### AWS IAM Principle of Least Privilege
AWS IAM is the canonical model for scoped permissions in cloud infrastructure. Each role has a policy document specifying exactly which actions it can take on which resources. The principle of least privilege states: grant only the permissions required to perform the task.

**Key insight:** Deny by default, explicitly allow. An agent with no declared permissions can do nothing. Each capability must be explicitly granted.

### Anthropic Tool Use / Tool Filtering
The Claude API allows specifying which tools are available to the model in each request. By passing different tool lists to different agents, the orchestrator can enforce capability boundaries at the API level — the model literally cannot call a tool it wasn't given.

**Key insight:** The most reliable permission enforcement is at the tool definition level. If the model doesn't know a tool exists, it can't hallucinate a call to it.

### OpenAI Assistants Function Calling
Similar to Anthropic's approach — each Assistant is configured with a specific set of functions. The model can only call functions in its definition. This is a hard boundary enforced by the API.

### LangChain Tool Restrictions
LangChain agents receive a list of tools at initialization. The agent can only use tools in its list. However, enforcement is at the framework level, not the API level — a determined prompt injection could potentially convince the agent to attempt unauthorized actions through other means.

## Policy: Granular Agent Decomposition

### Principle

**Every agent should do exactly one kind of work.** If an agent can both produce changes and persist them, it has too much power. Decompose broad agent roles into narrow, single-responsibility agents. This improves:

- **Safety** — destructive operations are isolated to dedicated agents with minimal scope, reducing blast radius when an agent misbehaves or hallucinates.
- **Logging** — when each agent does one thing, every log entry maps to a clear, auditable action. Mixed-responsibility agents produce ambiguous audit trails.
- **Retry and rollback** — if a narrow agent fails, its impact is contained. There is no tangled state to unwind across multiple concern areas.
- **Review surface** — a human reviewing agent actions can reason about a single-purpose agent far more easily than a multi-purpose one.

### Rule: Separate Mutation From Persistence

The most critical decomposition is between agents that **produce changes** (write files, generate code) and agents that **persist changes** (git commit, git push, create PRs). These must never be the same agent.

An agent that can both write arbitrary files and run `git push` has an unacceptably large blast radius. A hallucination in a coding step can be immediately and irreversibly published. Separating these concerns introduces a mandatory validation boundary.

### Case Study: Implement → Code + Commit

The original `IMPLEMENT` agent had permissions to read files, write files, create branches, commit, and run git/python/pytest. This is replaced by two agents:

| Agent | Responsibility | Can do | Cannot do |
|-------|---------------|--------|-----------|
| **Code** | Produce file changes | Read files, write files, run `python`/`pytest` for validation | Any git operation, branch creation, push, PR creation |
| **Commit** | Persist file changes to git | Read files (to verify diff), `git add`, `git commit`, `git push` on assigned branch only | Write/modify files, run arbitrary commands, create PRs |

The **code** agent outputs a set of file changes. The **commit** agent receives those changes, validates them (correct files modified, no sensitive files included, diff is non-empty), and persists them. If validation fails, the commit agent rejects the changes and the orchestrator can retry the code agent without any git state to clean up.

### DAG Impact

The standard issue-resolution DAG changes from:

```
research → implement → review
```

to:

```
research → code → commit → review
```

The `commit` node is a validation and persistence boundary. No changes exist in git until they pass through this gate.

For more complex flows (e.g., code + test iterations), the pattern extends naturally:

```
research → code → test → commit → review
         ↖____↙
         (retry loop)
```

The code and test agents can iterate without touching git. Only once tests pass does the commit agent persist the result.

### Applying This Policy to New Agent Types

When defining a new agent type, apply these checks:

1. **Does this agent both produce output and persist it?** Split it.
2. **Does this agent have access to both read and write tools for the same resource?** Consider whether the read and write sides can be separate agents.
3. **Does this agent have access to any destructive operation?** That operation should be the agent's *only* job, so it can be audited and gated independently.
4. **Can this agent's failure cause state that is hard to roll back?** Isolate the irreversible action into its own agent with the narrowest possible scope.

If a proposed agent type fails any of these checks, decompose it further before implementation.

## Best Practices

### 1. Define Permission Profiles Per Agent Type

Permission profiles reflect the granular decomposition policy. Each agent type has the minimum permissions required for its single responsibility.

```python
class AgentPermissions(BaseModel):
    can_read_files: bool = True
    can_write_files: bool = False
    can_create_branches: bool = False
    can_commit: bool = False
    can_push: bool = False
    can_create_pr: bool = False
    can_comment_pr: bool = False
    can_run_commands: list[str] = []   # Allowlisted commands
    can_access_network: bool = False
    max_files_readable: int | None = None
    allowed_file_paths: list[str] = []  # Glob patterns for write scope
    blocked_git_flags: list[str] = []   # Denied flags (e.g., --force, -D)

AGENT_PERMISSIONS = {
    AgentType.RESEARCH: AgentPermissions(
        can_read_files=True,
        can_run_commands=["grep", "git log", "git blame"],
    ),
    AgentType.CODE: AgentPermissions(
        can_read_files=True,
        can_write_files=True,
        can_run_commands=["python", "pytest"],
        allowed_file_paths=["src/**", "tests/**"],
    ),
    AgentType.COMMIT: AgentPermissions(
        can_read_files=True,
        can_commit=True,
        can_push=True,
        can_run_commands=["git add", "git commit", "git push", "git diff", "git status"],
        blocked_git_flags=["--force", "-f", "--hard", "-D", "--delete"],
    ),
    AgentType.TEST: AgentPermissions(
        can_read_files=True,
        can_run_commands=["pytest", "python -m pytest"],
    ),
    AgentType.REVIEW: AgentPermissions(
        can_read_files=True,
        can_comment_pr=True,
        can_run_commands=["git diff", "git log"],
    ),
}
```

### 2. Enforce at the Tool Layer, Not the Prompt Layer

Don't rely on system prompts telling agents "you are read-only." Agents can ignore instructions. Instead:

- Only pass allowed tools to the Claude API call
- Wrap tool execution in a permission checker that validates against the agent's profile
- Validate tool **arguments**, not just tool names — a permitted tool with dangerous arguments is still dangerous
- If an agent somehow returns a tool call it shouldn't have, the executor rejects it

```python
async def execute_tool(agent_type: AgentType, tool_call: ToolCall) -> ToolResult:
    permissions = AGENT_PERMISSIONS[agent_type]

    # Check tool-level permission
    if not is_allowed(tool_call, permissions):
        return ToolResult(
            error=f"Permission denied: {agent_type} cannot use {tool_call.name}"
        )

    # Check argument-level constraints
    if tool_call.name == "write_file":
        if not matches_any(tool_call.args["path"], permissions.allowed_file_paths):
            return ToolResult(
                error=f"Permission denied: {agent_type} cannot write to {tool_call.args['path']}"
            )

    if tool_call.name == "run_command" and tool_call.args["command"][0] == "git":
        for flag in permissions.blocked_git_flags:
            if flag in tool_call.args["command"]:
                return ToolResult(
                    error=f"Permission denied: blocked flag '{flag}'"
                )

    return await run_tool(tool_call)
```

### 3. Audit All Actions

Every tool call, regardless of whether it's allowed, should be logged:

```json
{
  "agent_type": "research",
  "agent_id": "agent_research_01",
  "tool": "write_file",
  "allowed": false,
  "reason": "research agents cannot write files",
  "timestamp": "2026-03-15T10:25:00Z"
}
```

Denied actions are especially important to log — they indicate either a misconfigured agent or a prompt injection attempt.

### 4. File System Sandboxing

For the MVP (shared dev container), agents operate on the host filesystem. Mitigate risk by:

- Scoping file access to the target repository directory only
- Using `allowed_file_paths` on write-capable agents (e.g., code agents scoped to `src/**` and `tests/**`) so that even a hallucinating agent cannot overwrite config, CI, or infrastructure files
- Blocking access to sensitive paths (`.env`, credentials, SSH keys, other repos) at the executor level — these are denied regardless of agent permissions
- Making research agents use a read-only mount or `git worktree` snapshot

For future versions, each agent runs in its own isolated container with a mounted workspace.

### 5. Git Operation Scoping

Only commit agents have git write access, and they are scoped to their assigned branch. The executor validates both the branch and the command arguments before execution.

```python
async def execute_git_tool(
    agent_id: str,
    agent_type: AgentType,
    command: list[str],
) -> str:
    # Only commit agents can run git write operations
    if agent_type != AgentType.COMMIT:
        raise PermissionError(
            f"{agent_type} agents cannot run git write commands"
        )

    # Enforce branch scoping
    current_branch = await get_current_branch()
    expected_branch = get_assigned_branch(agent_id)
    if current_branch != expected_branch:
        raise PermissionError(
            f"Agent {agent_id} can only operate on {expected_branch}, "
            f"currently on {current_branch}"
        )

    # Block destructive flags
    permissions = AGENT_PERMISSIONS[agent_type]
    for flag in permissions.blocked_git_flags:
        if flag in command:
            raise PermissionError(
                f"Blocked flag '{flag}' in git command: {command}"
            )

    return await run_command(command)
```

This enforces three boundaries: only commit agents touch git, they can only touch their own branch, and destructive flags (`--force`, `--hard`, `-D`) are rejected regardless of context.

### 6. Network Access Control

Most agents don't need network access beyond the Claude API and GitHub API. Block all other outbound traffic for safety. If a future agent type needs to access external documentation or APIs, add it as an explicit allowlisted domain.

## Previous Stable Approach

### No Permissions (Trust the Prompt)
Early agent frameworks gave every agent full tool access and relied on the system prompt to constrain behavior ("You are a research agent. Only read files, do not modify them."). This works most of the time but fails on adversarial inputs or ambiguous instructions. A prompt is a suggestion, not a boundary.

### Unix User/Group Permissions
The traditional OS-level approach: run each agent process as a different Unix user with appropriate file permissions. Effective but heavy — requires creating users, managing groups, and running processes with `su`/`sudo`. Doesn't apply to API-level operations (GitHub, Claude).

### API Key Scoping
Create separate API keys with different permission levels for different agent types. GitHub supports this well with fine-grained PATs. The Claude API doesn't support per-key tool restrictions. This works for some operations but not all.

### Container-Level Isolation
Run each agent in its own container with a restricted filesystem mount. The container image determines what tools and binaries are available. This is the strongest isolation model but adds significant complexity and latency for the MVP.
