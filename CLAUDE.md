# Agent Agent — CLAUDE.md

## Project Overview

Agent Agent is a Claude-powered orchestrator that resolves GitHub issues by decomposing them into sub-task DAGs and assigning specialized agents to execute each node. The MVP is a locally-run FastAPI server for single-developer use.

## Repository Structure

```
agent_agent/
├── src/
│   └── agent_agent/
│       ├── __init__.py
│       ├── server.py          # FastAPI app entry point
│       ├── config.py          # Pydantic settings, env profile loading
│       ├── models/            # Pydantic models (tasks, agents, DAG nodes)
│       ├── orchestrator/      # Core orchestration logic
│       │   ├── planner.py     # Issue analysis → sub-task DAG
│       │   ├── dag.py         # DAG construction, traversal, state
│       │   └── executor.py    # Agent dispatch and result collection
│       ├── agents/            # Agent type definitions and prompts
│       │   ├── base.py        # Base agent interface
│       │   ├── research.py    # Read-only codebase analysis
│       │   ├── implement.py   # Code changes
│       │   ├── test.py        # Test execution and validation
│       │   └── review.py      # Code review
│       ├── github/            # GitHub integration (issues, PRs, branches)
│       ├── context.py         # Shared context accumulator
│       └── state.py           # SQLite state persistence
├── tests/
├── .env.dev                   # Dev environment config
├── .env.prod                  # Prod environment config
├── pyproject.toml
├── claude.md
└── readme.md
```

## Commands

```bash
# Activate venv
source /workspaces/.venvs/agent_agent/bin/activate

# Install dependencies (editable)
pip install -e ".[dev]"

# Run the server (dev)
AGENT_AGENT_ENV=dev uvicorn agent_agent.server:app --reload --port 8100

# Run tests
pytest tests/

# Type checking
mypy src/agent_agent/

# Linting
ruff check src/ tests/
ruff format src/ tests/
```

## Development Rules

### Code Style
- Python 3.11, type hints on all public functions
- Pydantic v2 for all data models and config
- async/await throughout (FastAPI + async Claude SDK calls)
- Ruff for linting and formatting (pyproject.toml has config)

### Design Policies

A **design policy** is a conceptual decision about how the system should be designed — a guiding principle that shapes architecture, agent boundaries, and implementation choices. Policies are the reasoning behind the code. When making implementation decisions, check that they align with active policies.

- **Maximum Agent Separation** — decompose agent roles into the smallest independently-scoped units rather than combining capabilities into fewer, broader agents. For example, an "implement" agent is split into a coding agent (file edits) and a git agent (branch/commit), so each has minimal permissions and isolated failure modes. Apply this when defining agent types, scoping permissions, or decomposing DAG nodes.

### Architecture Principles
- **Agents are stateless** — all state lives in the orchestrator's SQLite store
- **DAG nodes are the unit of work** — each node maps to exactly one agent invocation
- **Context flows forward only** — upstream outputs feed downstream inputs, never backwards
- **Fail loudly** — agents return structured results with explicit success/failure; never swallow errors
- **Budget-aware** — every agent call tracks token usage; orchestrator enforces limits

### Git Workflow
- All agent-generated code goes on feature branches: `agent/<issue-number>/<short-description>`
- The orchestrator creates the branch, agents commit to it, review agents comment
- PRs are created against the target repo's default branch
- Human merges — agents never merge to main

### Testing
- Unit tests for DAG logic, context passing, and state persistence
- Integration tests mock the Claude API (use `pytest-httpx` or similar)
- E2E tests run against a test repo with real GitHub API calls (requires `GITHUB_TOKEN`)

### Config & Environment
- `AGENT_AGENT_ENV` controls which `.env.*` file loads (default: `dev`)
- Dev config: verbose logging, higher token budgets, `--reload` friendly
- Sensitive values (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`) come from env vars, never committed

### Key Dependencies
- `anthropic` — Claude API client
- `fastapi` + `uvicorn` — API server
- `networkx` — DAG operations
- `pydantic` + `pydantic-settings` — models and config
- `aiosqlite` — async SQLite for state persistence
- `httpx` — async HTTP client (GitHub API)

## Important Patterns

### Agent Execution
Agents are invoked via the Anthropic SDK, not via Claude Code CLI. Each agent type has a system prompt template that scopes its capabilities and provides task context.

### Error Handling
- Agent failures are caught and recorded in state store
- Configurable retry count per node (default: 2)
- After max retries, node is marked `failed` and orchestrator decides: skip dependents, or escalate
- Orchestrator crash recovery: on startup, check for incomplete DAGs in SQLite and offer to resume

### GitHub Integration
- Use `httpx` with GitHub REST API for programmatic access (issues, PRs, comments)
- Use `gh` CLI as fallback for operations that are simpler via CLI
- All GitHub operations are idempotent where possible (check before create)
