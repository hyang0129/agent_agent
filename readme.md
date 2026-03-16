# Agent Agent

An orchestrator that automatically creates and manages Claude agents to resolve GitHub issues through PR merging.

## Vision

A project-level orchestrator that uses a DAG to manage simultaneous issue resolution across repositories. Tasks are defined as GitHub issues; resolution is executed through automated PR creation, review, and merge.

## MVP Scope

The MVP is a **task-level orchestrator**, manually initiated by a single developer on a local system.

### MVP Flow

1. Human points the orchestrator at a GitHub issue
2. Orchestrator reads the issue and related codebase context
3. Orchestrator decomposes the task into a sub-task DAG
4. Sub-tasks are assigned to specialized agents (research, implement, test, review)
5. Agents execute in dependency order, with parallel execution where the DAG allows
6. Results are assembled into a PR with review comments
7. Human approves/merges

### What MVP Includes

- Local FastAPI server for orchestration
- GitHub issue ingestion and PR creation via `gh` CLI / GitHub API
- Sub-task DAG construction and execution
- Agent assignment based on task type (research, code, test, review)
- Context passing between dependent agents
- Basic observability (structured logging, task status)
- Dev/prod configuration separation via environment profiles

### What MVP Excludes

- Remote hosting / multi-user support
- Web UI for configuration (edit CLAUDE.md and configs locally)
- Isolated dev containers per agent (uses host dev container)
- Automatic issue discovery (human initiates each task)
- Multi-repo orchestration in a single DAG

## Architecture

```
┌─────────────────────────────────────────────┐
│                 FastAPI Server               │
│                                              │
│  ┌──────────┐   ┌───────────┐   ┌────────┐  │
│  │  Issue    │──▶│  Planner  │──▶│  DAG   │  │
│  │  Intake   │   │           │   │ Engine │  │
│  └──────────┘   └───────────┘   └───┬────┘  │
│                                     │        │
│                    ┌────────────────┼──────┐ │
│                    ▼        ▼       ▼      │ │
│                 ┌──────┐ ┌──────┐ ┌──────┐ │ │
│                 │Agent │ │Agent │ │Agent │ │ │
│                 │  A   │ │  B   │ │  C   │ │ │
│                 └──┬───┘ └──┬───┘ └──┬───┘ │ │
│                    └────────┼───────┘      │ │
│                             ▼              │ │
│                       ┌───────────┐        │ │
│                       │  Review   │        │ │
│                       │  Agent(s) │        │ │
│                       └─────┬─────┘        │ │
│                             ▼              │ │
│                       ┌───────────┐        │ │
│                       │  PR       │        │ │
│                       │  Assembly │        │ │
│                       └───────────┘        │ │
│                                            │ │
│  ┌───────────────────────────────────────┐ │ │
│  │          State Store (SQLite)         │ │ │
│  └───────────────────────────────────────┘ │ │
└─────────────────────────────────────────────┘
```

## Design Policies

A **design policy** is a conceptual decision about how the system should be designed — a guiding principle that shapes architecture, agent boundaries, and implementation choices. Policies are not code; they are the reasoning behind the code. They exist so that future decisions stay consistent with past intent, even as the system grows.

Each policy has a name, a statement of the principle, and the rationale behind it.

### Maximum Agent Separation

**Policy:** Decompose agent roles into the smallest independently-scoped units rather than combining capabilities into fewer, broader agents.

**Rationale:** When an agent has multiple capabilities (e.g., writing code *and* committing to git), its failure modes multiply and its permissions become harder to reason about. By splitting an "implement" agent into a **coding agent** (file edits only) and a **git agent** (branch/commit operations only), each agent has a minimal permission surface, failures are isolated to one concern, and the DAG can retry or replace a single capability without re-running the other.

**Applies to:** Agent type definitions, DAG decomposition, permission scoping.

---

## Key Design Decisions

### Error Handling & Recovery
- Agents report structured success/failure results
- Failed sub-tasks can be retried (configurable max retries)
- Persistent failures escalate to human via CLI prompt
- DAG state is checkpointed to SQLite so orchestration can resume after crashes

### Context Passing
- Each agent receives: the original issue, its sub-task description, and outputs from upstream DAG nodes
- A shared context object accumulates discoveries (file mappings, root causes, design decisions)
- Downstream agents inherit relevant upstream context automatically

### Conflict Resolution
- Parallel agents work on separate git branches
- Orchestrator merges branches sequentially in dependency order
- Merge conflicts trigger a resolution agent or escalate to human

### Cost Controls
- Configurable token/cost budget per task and per agent
- Hard stop when budget is exceeded; partial results are preserved
- Usage logged per agent for post-hoc analysis

### Human Checkpoints
- MVP: human initiates task and reviews final PR
- Optional: configurable approval gates at DAG stage transitions
- All agent actions are logged for async human review

### Agent Permissions
- Agents are typed (research, implement, test, review) with scoped capabilities
- Research agents: read-only codebase access, no git writes
- Implement agents: branch creation, file edits, commits
- Review agents: read-only + comment on PR
- Test agents: can execute test suites, read-only to source

### Observability
- Structured JSON logging for all agent actions
- DAG execution status viewable via CLI or API endpoint
- Per-agent token usage and timing metrics

### Dev vs Prod Separation
- Environment profiles loaded from `.env.dev` / `.env.prod`
- Dev: verbose logging, relaxed budgets, local git operations
- Prod: structured logging, strict budgets, remote git operations
- Single codebase, behavior controlled by `AGENT_AGENT_ENV` env var

## Tech Stack

- **Python 3.11** — orchestrator runtime
- **FastAPI** — local API server
- **Claude API** (Anthropic SDK) — agent execution
- **SQLite** — state persistence and checkpointing
- **GitHub CLI (`gh`)** — issue/PR interaction
- **Pydantic** — config and data validation
- **NetworkX** — DAG construction and traversal

## Integration Testing

Agent Agent is validated by pointing it at real GitHub issues on purpose-built test repositories. Each test repo is a realistic codebase with pre-written issues of known difficulty and known-correct solutions. The orchestrator's output (PRs) is evaluated against these known solutions.

### Test Repositories

| Repo | Domain | Why This Domain |
|---|---|---|
| **test-webstore** | Shopify-style e-commerce (Python) | CRUD-heavy, multi-model, payment/cart logic — tests breadth of code understanding |
| **test-mailservice** | Gmail-style email service (Python) | Async processing, search indexing, auth — tests complex system interactions |
| **test-agent-agent** | Previous version of agent_agent (Python) | Self-referential — tests ability to reason about orchestration and meta-architecture |

See [docs/integration-testing.md](docs/integration-testing.md) for full testing principles and architecture.

## Future Scope

- Remote hosting with auth and multi-user support
- Isolated dev containers per agent (sandboxed execution)
- Project-level orchestrator managing multiple issues concurrently
- Web dashboard for monitoring and configuration
- Webhook-driven automatic issue intake
- Multi-repo support with cross-repo dependency awareness
- Team-based access controls and approval workflows
