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
| [integration-test-fixtures.md](integration-test-fixtures.md) | Architecture for the Tier 3 pytest integration fixture system: ephemeral GitHub repos created per-test from candidate-prs metadata, issue posted, agent run, teardown. Covers _FixtureBotClient, fixture_repo fixture, idempotency, per-repo JSON layout, and CI integration. Keywords: integration tests, fixture_repo, ephemeral repo, agent-agent-fixtures, FixtureMeta, teardown, GITHUB_TOKEN. |
| [policy-review-testing.md](policy-review-testing.md) | Defines three test modes for the PolicyReviewer sub-agent: Mode 1 (arbitrary — single post-hoc policy, PolicyReviewer isolation, detect violation); Mode 2 (integrated — single policy committed before run, assert orchestrator steers toward compliant implementation); Mode 3 (corpus — N non-conflicting policies, assert correct relevance discrimination, no omissions, no false positives). Covers oracle design, three-part corpus oracle, fixture catalog layout, PolicyReviewOutput/PolicyCitation models, two-phase implementation sequence, and corpus scale degradation strategy. Keywords: policy review, PolicyReviewer, policy testing, corpus, multi-policy, omission, false positive, PolicyReviewOutput, policy_citations, steering, compliance. |
| [policy-fixture-candidates.md](policy-fixture-candidates.md) | Evaluated shortlist of real Python repos suitable as Mode 2 integrated policy test fixtures: five Tier 1 candidates (blinker, backoff, parse, cachetools, stamina) with design decisions an LLM would naturally violate, plus five Tier 2 candidates. Includes policy text, fixture issue, violation grep check, and recommended starting order. Keywords: policy fixtures, blinker, backoff, parse, cachetools, stamina, Mode 2, fixture candidates, LLM violation. |
| [policy-reviewer-decisions.md](policy-reviewer-decisions.md) | Records human-guidance decisions made during PolicyReviewer MVP implementation: optionality of policy_review field, P3.4/P8.5 argument validation gap, budget backstop sharing, CLAUDE.md priority, and policy_id format. Keywords: PolicyReviewer, decisions, policy_review, argument validation, budget backstop. |

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
