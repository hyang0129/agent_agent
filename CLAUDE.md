# Agent Agent — CLAUDE.md

## Project Overview

Agent Agent is a Claude-powered orchestrator that resolves GitHub issues by decomposing them into sub-task DAGs and assigning specialized agents to execute each node.

**Distribution:** The MVP ships as a Python package (PyPI / GitHub release) with a CLI entry point (`agent-agent bootstrap`, `agent-agent run`). The FastAPI server is an implementation detail, not the user-facing surface. Users run the bootstrap agent once against their target repo to generate a `CLAUDE.md` and policy set scoped to that repo — the docs in *this* repository are internal development artifacts and are not shipped to users. See `docs/goals/goals.md` § MVP Distribution for the full rationale.

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

## Setup

See [docs/setup.md](docs/setup.md) for prerequisites, install steps, and environment configuration.

## Commands

```bash
source /workspaces/.venvs/agent_agent/bin/activate  # activate venv
AGENT_AGENT_ENV=dev uvicorn agent_agent.server:app --reload --port 8100  # run server
pytest tests/          # tests
mypy src/agent_agent/  # type check
ruff check src/ tests/ # lint
ruff format src/ tests/ # format
```

## IMPORTANT: Architecture, Design, and Review Work

Before making any architectural decision, adding a new agent type, changing DAG structure, modifying context flow, scoping agent permissions, or **reviewing any design or implementation** — adhere to policies, follow best practices, and verify alignment with goals. Read the relevant indexes first:

- **Goals:** `docs/goals/goals.md` — what the system is trying to achieve; every decision must align with these
- **Policies:** `docs/policies/POLICY_INDEX.md` — active design policies (P1–P11); every architectural and design decision must be checked against the relevant policies
- **Best Practices:** `docs/best-practices/INDEX.md` — implementation guidance for recurring concerns; follow these unless a policy overrides

For additional documentation (architecture decisions, ADRs, etc.) see `docs/claude.md`.

Policies are not optional commentary. They encode decisions that were made deliberately. If your implementation conflicts with a policy, resolve the conflict explicitly — either by changing the implementation or by proposing a policy update.

## Code Style

- Python 3.11, type hints on all public functions
- Pydantic v2 for all data models and config
- async/await throughout (FastAPI + async Claude SDK calls)
- Ruff for linting and formatting (pyproject.toml has config)

## Architectural Invariants

These are absolute. No policy or task overrides them:

- **Agents are stateless** — all state lives in the orchestrator's SQLite store
- **Context flows forward only** — upstream outputs feed downstream inputs, never backwards
- **Fail loudly** — agents return structured results with explicit success/failure; never swallow errors
- **Orchestrator is the sole writer to shared context** — agents propose discoveries; the orchestrator validates and commits them
- **Never operate on the live installation directory** — the orchestrator rejects `--repo` paths that resolve to its own working tree. To use agent_agent to improve itself, clone the repo to a separate directory and pass that clone as `--repo`.

## Git

- Agent-generated code branches: `agent/<issue-number>/<short-description>`
- The orchestrator creates the branch; agents commit to it; review agents comment
- PRs are created against the target repo's default branch
- **Agents never merge to main** — human merges only

## Key Dependencies

See [docs/architecture/dependencies.md](docs/architecture/dependencies.md).
