# Architecture — Index

This directory documents architectural decisions, subsystem designs, and testing infrastructure that are too detailed for policy files but too structural for best-practices guides.

## Documents

| Document | Summary |
|----------|---------|
| [implementation-plan.md](implementation-plan.md) | Phased build plan for the MVP: 6 phases from foundation to hardened end-to-end, with deliverables, test gates, and fixture spec. Keywords: implementation, phases, build order, deliverables, test gates, fixtures. |
| [mvp-architecture.md](mvp-architecture.md) | Defines the end-to-end Mode 1 MVP architecture: CLI, bootstrap agent, orchestrator, DAG engine, agent types, composite internals, context system, GitHub integration, merge integration, budget, escalation, checkpoints, and observability. Keywords: MVP, architecture, orchestrator, DAG, composite, executor, context, budget, observability. |
| [data-models.md](data-models.md) | Canonical Pydantic model definitions for all agent output types, discovery types, ChildDAGSpec, NodeContext, SharedContext, and NodeResult. Reference this before implementing any model. Keywords: Pydantic, models, PlanOutput, CodeOutput, TestOutput, ReviewOutput, ChildDAGSpec, NodeContext, discoveries. |
| [integration-testing.md](integration-testing.md) | Defines the end-to-end test strategy using three purpose-built repos, tiered issue types, a 0–3 scoring rubric, and regression tracking to validate autonomous issue resolution. Keywords: integration testing, test repos, evaluation, scoring, regression, architecture. |
| [fixture-strategy.md](fixture-strategy.md) | Selects five real-repo snapshots (schema, schedule, environs, jmespath, cattrs) as vendored pytest fixtures at pinned SHAs, extending repo_with_remote via indirect parametrization while keeping the synthetic template for fast component tests. Keywords: fixtures, test fixtures, real repos, vendored snapshot, git archive, conftest, repo_with_remote. |

## Relationship to Other Docs

- **Policies** (`docs/policies/`) — *what* the system must do and *why*. Policies are binding.
- **Architecture** (`docs/architecture/`) — *how* specific subsystems are designed. Architecture docs describe concrete mechanisms that implement policies.
- **Best Practices** (`docs/best practices/`) — recurring implementation patterns (config, context strategy, CLAUDE.md authoring).
- **Goals** (`docs/goals/`) — the north star; all architecture decisions should serve the goals.

## Adding a Document

Create a new `.md` file in this directory and add a row to the table above. Architecture docs should:

1. State the problem being solved
2. Describe the chosen approach and its structure
3. Note any out-of-scope items and when they become in-scope
4. Reference the policies they implement (by number, e.g. P07)
