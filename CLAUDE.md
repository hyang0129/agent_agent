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
│       ├── config.py          # Pydantic settings, env profile loading
│       ├── models/            # Pydantic models: agent outputs, context, DAG, budget, escalation
│       ├── state.py           # SQLite schema + async CRUD (aiosqlite); all 6 tables
│       ├── budget.py          # BudgetManager: top-down allocation, 25% cap, 5% threshold
│       ├── dag/               # DAG engine and executor
│       │   ├── engine.py      # DAGRun construction, topological traversal
│       │   └── executor.py    # Dispatch loop, NodeContext assembly, failure classification
│       ├── context/           # Context assembly and shared state write protocol
│       │   ├── provider.py    # ContextProvider: assembles NodeContext, enforces 25% cap
│       │   └── shared.py      # SharedContext write protocol: discovery validation + append
│       ├── worktree.py        # WorktreeManager: git worktree add/remove for composites
│       ├── agents/            # Agent composites (Phase 4+)
│       │   ├── base.py        # invoke_agent wrapper; enforces iteration cap
│       │   ├── plan.py        # ResearchPlannerOrchestrator
│       │   ├── coding.py      # CodingComposite: Programmer → Test Designer → Debugger
│       │   └── review.py      # ReviewComposite: read-only worktree review
│       ├── github/            # GitHub integration (issues, PRs, branches)
│       │   └── client.py      # async httpx; DRY_RUN_GITHUB guard; branch protection check
│       ├── cli.py             # typer CLI: run, status, bootstrap
│       ├── orchestrator.py    # Orchestrator: run lifecycle (DAGRun → executor → result)
│       └── server.py          # FastAPI app: GET /dags/{id}/status
├── tests/
│   ├── unit/                  # Pure Python, no I/O; AGENT_AGENT_ENV=test
│   ├── component/             # Real git ops and HTTP mocks; AGENT_AGENT_ENV=test
│   └── fixtures/              # template/ repo + conftest.py fixtures
├── pyproject.toml
├── CLAUDE.md
└── README.md
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
