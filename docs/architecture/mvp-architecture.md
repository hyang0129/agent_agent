# MVP Architecture

*Implements: P01, P02, P03, P04, P05, P06, P07, P08, P09, P10, P11*

---

## Problem

This document defines the end-to-end architecture for the Mode 1 MVP: a deployable Python package that takes a GitHub issue and produces a reviewed PR with no mid-execution human involvement beyond the two planned checkpoints.

---

## Scope

Mode 1 only (senior developer / architect). Mode 2 (Architecture Agent) and Mode 3 (drift handling) are post-MVP and are not represented here. The bootstrap agent is in-scope as a prerequisite to the dev team core.

---

## System Overview

```
User
 │
 ├─ agent-agent bootstrap   ← one-time first-run setup
 │
 └─ agent-agent run         ← issue → PR
         │
         ▼
    ┌──────────────────────────────────────────────┐
    │  Orchestrator                                │
    │  ┌────────────┐  ┌──────────┐  ┌──────────┐ │
    │  │ DAG Engine │  │ Executor │  │  Budget  │ │
    │  └────────────┘  └──────────┘  └──────────┘ │
    │  ┌──────────────────────────────────────────┐│
    │  │ State Store (SQLite)                     ││
    │  └──────────────────────────────────────────┘│
    └──────────────────────────────────────────────┘
         │
         ├── Research agent
         ├── Plan composite
         ├── Coding composite (× N, isolated worktrees)
         │     └── Programmer → Test Designer → Test Executor → Debugger
         ├── Integration node (conditional)
         └── Review composite
                          │
                          ▼
                    GitHub PR  ← human checkpoint 2
```

---

## Components

### 1. CLI Layer

The user-facing surface. On `run`, the CLI starts an in-process FastAPI server (not a separate daemon) and hands control to the orchestrator. The server is an implementation detail; users interact with the CLI, not the HTTP layer directly.

| Command | Action |
|---------|--------|
| `agent-agent bootstrap` | *(Post-MVP)* Run the bootstrap agent against a target repo |
| `agent-agent run --issue <url>` | Start a DAG run for a GitHub issue |
| `agent-agent status [run-id]` | Show DAG run status (L1 observability) |

On `run`, the CLI:
1. Loads config (`AGENT_AGENT_ENV` → `.env.{env}` + `.env.local` + env vars)
2. **Rejects if `--repo` resolves to the orchestrator's own installation directory.** agent_agent must never operate on its own live working tree. To use it to improve itself, clone the repo to a separate directory first and pass that clone as `--repo`.
3. Validates that `CLAUDE.md` and initial policies exist in the target repo (fails with a clear error if missing — bootstrap redirect is post-MVP)
4. Creates a DAG run record in the state store
5. Hands control to the orchestrator

---

### 2. Bootstrap Agent *(Post-MVP)*

Runs once before the dev team core is usable on a new target repo. Not a DAG — single-pass agent flow.

**Inputs:** target repo path, user answers to scoping questions
**Outputs:** `CLAUDE.md` + initial policy set written into the target repo
**User approval:** required before any issue work begins

The bootstrap agent reads: repo structure, language/framework, CI config, any existing CLAUDE.md or ADRs. It does not ship this repo's internal policies verbatim.

For MVP, the `CLAUDE.md` and policy set for any target repo are authored manually. The bootstrap agent automates this authoring step but is not required for the orchestrator to function.

---

### 3. Config System

*Follows best practice: dev/prod separation via pydantic-settings.*

- `AGENT_AGENT_` prefix on all settings
- Load order: `.env.{env}` → `.env.local` (gitignored, has secrets) → actual env vars
- Per-environment databases: `data/dev.db`, `data/prod.db`, `:memory:` for tests
- Safety rails in dev: `GIT_PUSH_ENABLED=false`, `DRY_RUN_GITHUB=true`

| Setting | Dev | Prod |
|---------|-----|------|
| `LOG_LEVEL` | DEBUG | INFO |
| `LOG_FORMAT` | console | json |
| `MODEL` | haiku | sonnet |
| `GIT_PUSH_ENABLED` | false | true |
| `DRY_RUN_GITHUB` | true | false |
| `MAX_BUDGET_TOKENS` | 500,000 | 500,000 |

---

### 4. State Store

SQLite. One database per environment. All persistent state lives here — agents are stateless.

| Table | Contents |
|-------|----------|
| `dag_runs` | Run metadata: issue URL, status, budget allocation, timestamps |
| `dag_nodes` | Node records: type, parent/child IDs, status, level |
| `node_results` | Serialized `NodeResult` (agent output + `ExecutionMeta`) per node |
| `shared_context` | Append-only `SharedContext` entries: key, value, source node, timestamp |
| `budget_events` | `BudgetEvent` log: all allocations, freezes, increases |
| `escalations` | Escalation history: severity, structured message, resolution |

DAGs are persisted to `dag_nodes` before any node executes [P1.7].

---

### 5. DAG Engine

*Implements P01, P02.*

Manages immutable, recursively nested DAGs. Adaptation never mutates a live DAG — the terminal Plan composite spawns a child DAG instead.

**Structure rules:**
- L0: single Plan composite (the root)
- L1+: `Coding composites (×N)` → `[Integration node]` → `Review composite` → `Plan composite`
- Parallelism: intra-level only (concurrent Coding composites within a level)
- Nesting hard cap: 4 levels; beyond that, escalate
- Plan composite: 2–5 Coding composites per level; 6–7 requires justification; 8+ rejected

**Traversal:** topological sort, level by level. The executor dispatches all ready nodes at a level and awaits their completion before advancing.

**MVP concurrency:** `MAX_WORKERS` is config-driven. The primary testing target is `MAX_WORKERS=1` (serial) — this isolates correctness bugs from concurrency bugs. A secondary test pass at `MAX_WORKERS=2` validates the parallel path end-to-end. The dispatch mechanism is identical at any worker count — each composite gets an isolated worktree and an independent branch — so no architectural change is required to increase parallelism.

---

### 6. Executor

Dispatches agent invocations, collects `NodeResult`s, and updates the state store and shared context after each node completes.

**Per-node dispatch sequence:**
1. Assemble `NodeContext` via `ContextProvider`: issue + parent `AgentOutput`s + `SharedContextView` (capped at 25% of node budget) [P05, P07]
2. Set `max_tokens` on the API call from the node's budget allocation [P07]
3. Invoke agent; receive structured `AgentOutput`
4. Validate output against the canonical Pydantic model
5. Wrap in `NodeResult` with `ExecutionMeta`; persist to `node_results`
6. Extract discoveries from output; orchestrator writes to `shared_context`
7. Log all observability events via single `emit_event` call [P11]

**Failure classification** (see P10):

| Class | Action |
|-------|--------|
| Transient | Retry with exponential backoff |
| Agent Error | Re-invoke with concrete failure context |
| Resource Exhaustion | Stop node, preserve output, escalate |
| Deterministic | Escalate immediately |
| Safety Violation | Escalate immediately, no retry |

Re-invocations without concrete failure context are prohibited [P10].

---

### 7. Agent Types

*Implements P03, P08.*

Three composite types, each containing one or more sub-agents. Permissions are enforced at the tool layer (argument validation + deny logging), not only in system prompts. Sub-agents within a composite are not exposed to the DAG as separate nodes — they are internal to the composite.

| Composite | Sub-agent | Permissions | Output model |
|-----------|-----------|-------------|--------------|
| Plan composite | ResearchPlannerOrchestrator | Read files, read GitHub, read git history, no writes | `PlanOutput` |
| Coding composite | Programmer | Write files + git within worktree only | `CodeOutput` |
| Coding composite | Test Designer | Read files, design test plan | `TestOutput` (plan) |
| Coding composite | Test Executor | Run test suite commands | `TestOutput` (results) |
| Coding composite | Debugger | Write files + git within worktree | `CodeOutput` |
| Review composite | Reviewer | Read files, read GitHub, no writes | `ReviewOutput` |

No sub-agent creates or merges PRs. PR creation is an orchestrator operation.

---

### 8. Composite Node Internals

#### Plan Composite

A single `ResearchPlannerOrchestrator` agent handles everything in one invocation: reads the issue and repo context, produces an investigation summary, and generates the child DAG specification.

```
ResearchPlannerOrchestrator → PlanOutput (investigation summary + child DAG spec, or null)
      ↓
Human checkpoint 1 — user approves investigation summary and plan
      ↓
Persist child DAG → hand off to executor
```

If `PlanOutput` is `null`, work is complete; orchestrator creates the PR.

#### Coding Composite

Each Coding composite runs in an isolated git worktree (prevents mutations to primary checkout or sibling nodes) [P08]. Sub-agents receive context from NodeContext (issue + ResearchPlannerOrchestrator's PlanOutput + SharedContextView); dedicated research is not a separate step.

```
Internal cycle (max 3 cycles):
  Programmer → CodeOutput
       ↓
  Test Designer → TestOutput (plan)
       ↓
  Test Executor → TestOutput (results)
       ↓
  Debugger (if failures) → CodeOutput
      ↓
Push branch to remote on exit (success or failure) [P01]
```

Sub-agent outputs are persisted after each step, enabling resumption from the last completed sub-agent without restarting the composite [P10].

#### Review Composite

A single `Reviewer` sub-agent performs the full evaluation.

```
Reviewer:
  Read all Coding composite outputs + shared context
      ↓
  Evaluate: code quality, test coverage, acceptance criteria, policy compliance
      ↓
ReviewOutput: approved | rejected + structured findings
```

---

### 9. Context System

*Implements P05.*

Three simultaneous layers:

| Layer | Type | Writer | Readers |
|-------|------|--------|---------|
| Edge context | Typed Pydantic objects | Each agent | Direct downstream nodes |
| Shared context | `SharedContext` (append-only) | Orchestrator only (from agent proposals) | All nodes via `SharedContextView` |
| Node context | `NodeContext` assembled at dispatch | `ContextProvider` | Single node |

The original GitHub issue is always included verbatim in every node's context. The target repo's `CLAUDE.md` is also included verbatim in every node's context for MVP. Selective or priority-based CLAUDE.md injection is post-MVP.

**Context overflow (in order):** structured masking → consumer-driven LLM summarization → truncation. The `SharedContextView` cap (25% of node budget) is enforced by the budget system, not by the context layer alone.

---

### 10. GitHub Integration

- Issue read: GitHub API (read-only for ResearchPlannerOrchestrator)
- Branch naming: `agent/<issue-number>/<short-description>`
- PR creation: orchestrator only; no sub-agent ever creates or merges a PR
- Protected branches: push blocked for `main`, `master`, `production` (config-driven)
- Dev safety: `DRY_RUN_GITHUB=true` prevents any write to GitHub in dev

#### Worktree Dispatch Mechanism

Each Coding composite receives an isolated git worktree before dispatch. This provides filesystem isolation without cloning the full repo again, keeping all worktrees within the same dev container and on the same filesystem.

```
target repo (primary checkout)
  └── .git/worktrees/
        ├── agent-<run-id>-node-1/   ← Coding composite 1
        ├── agent-<run-id>-node-2/   ← Coding composite 2
        └── ...
```

**What is isolated per worktree:** working tree (files), HEAD (branch), index. **What is shared:** `.git` object store, venv, installed dependencies, any read-only repo tooling. Sub-agents within a Coding composite work exclusively in their worktree directory — they never reference the primary checkout path.

The orchestrator creates the worktree, sets the branch (`agent/<issue-number>/<short-description>`), and passes the worktree path to the composite at dispatch. On composite exit (success or failure), the composite pushes the branch; the orchestrator then removes the worktree directory.

With `MAX_WORKERS=1`, worktrees are created and torn down sequentially. The mechanism is identical when `MAX_WORKERS > 1` — worktrees exist concurrently, each on its own branch.

---

### 11. Merge Integration

*Implements P04.*

After all Coding composites at a level complete:

1. Determine merge order: topological (foundational files before leaf files as tiebreaker)
2. For each branch in order: rebase onto accumulated target → merge → run full test suite
3. Conflict resolution ladder:
   - Trivial auto-resolve
   - AST-aware merge (tree-sitter)
   - LLM resolution agent
   - Triage agent selects branch to rebuild; rebuild (max 1 per branch, configurable via `max_rebuilds_per_branch`)
   - Human escalation
4. Never force-push agent branches

Integration node is required when parallel branches touch overlapping files, imports, or API contracts [P02].

---

### 12. Budget System

*Implements P07.*

- Budget set at DAG run creation; never autonomously increased by the orchestrator
- Top-down allocation: run budget → composite budget → node budget
- Each API call has `max_tokens` set from node budget
- At 5% remaining: continue for review stages; stop and escalate for plan/implementation stages
- Active nodes are never killed mid-call; frozen after current API call completes (`frozen_at_budget`)
- All events logged as `BudgetEvent` records

Budget increases require human approval via the escalation flow [P06].

---

### 13. Escalation

*Implements P06.*

| Trigger | Severity | Retry? |
|---------|----------|--------|
| Safety violation | CRITICAL | No |
| Semantic anomaly, deterministic error, depth limit | HIGH | No |
| Retry exhaustion, budget exhaustion | MEDIUM | No |

Every escalation is a structured message containing: attempt history, DAG impact summary, budget state, and a recovery options menu (including budget increase). Completed node outputs are always preserved; partial work can be delivered as a labeled PR.

Every escalation is treated as a policy gap signal — if it recurs, the policy or CLAUDE.md must be updated.

---

### 14. Human Checkpoints

*Implements P09.*

Exactly two per issue. No configurable levels.

| Checkpoint | When | Medium |
|------------|------|--------|
| 1 — Issue approval | After investigation summary, before planning | CLI prompt |
| 2 — PR review | After execution completes | GitHub PR review |

Mid-execution pauses are allowed only for genuine policy conflicts or projected budget overruns. Every pause must produce a durable fix: a policy document update, a CLAUDE.md edit, or a written scope clarification committed to the state store. A one-off verbal answer is not sufficient.

**Rejected PR improvement loop:** orchestrator negotiates with the human to classify the failure (misunderstood issue / wrong approach / bad execution), then outputs a CLAUDE.md edit, prompt change, or policy update committed to the state store.

---

### 15. Observability

*Implements P11.*

All events flow through a single `emit_event(...)` call. Every log record is JSON via `structlog` and includes `dag_run_id` and `node_id`.

| Level | Content | Retention | Medium |
|-------|---------|-----------|--------|
| L1 — Status | DAG and per-node execution state | Indefinite | Structured log + SQLite |
| L2 — Metrics | Tokens, cost, retries, budget events | 90 days | Structured log |
| L3 — Traces | Full LLM I/O, tool calls, denied calls | 7 days | Log files (OTel-compatible field names) |

MVP: local structured log files only. No SaaS observability platform.

API endpoint: `GET /dags/{id}/status` (L1 real-time status).
CLI: progress display reading from L1 status.

---

### 16. Testing Infrastructure

Two tiers for MVP. A third tier (real-repo end-to-end) is deferred.

#### Tier 1 — Unit Tests

Pure Python: DAG construction, state persistence, context passing, budget math, config loading. No filesystem access, no git.

- Database: `:memory:` SQLite (config `AGENT_AGENT_ENV=test`)
- Fast; run on every commit

#### Tier 2 — Component Tests

Test subsystems that require a real filesystem and real git operations: worktree creation, coding composite flow (Research → Programmer → Test Designer → Test Executor → Debugger), executor retry, state resumption.

**Work directory:** `/workspaces/.agent_agent_tests/` — outside the `agent_agent` git tree so worktrees and clones never affect `git status`.

```
/workspaces/.agent_agent_tests/
  worktrees/      ← orchestrator creates worktrees here during component test runs
```

**Target repo fixture:** A minimal Python package (~30 files, real pytest suite) stored as plain source files in `tests/fixtures/template/` (checked in, not a git repo). Each component test gets a fresh `git init` built from these files via `pytest tmp_path` — isolated per test, auto-cleaned on completion.

The fixture has two layers:

| Layer | Contents | Purpose |
|-------|----------|---------|
| Code | App source files, existing tests, `pyproject.toml` | What Programmer / Test Executor agents read and modify |
| Documentation | `CLAUDE.md` + policy docs in the repo's expected layout | What ResearchPlannerOrchestrator and agent system prompts consume |

The documentation layer is **hand-authored** and checked in to `tests/fixtures/template/`. It represents what a well-formed target repo looks like after a human has written a `CLAUDE.md` and the expected policy structure — the same precondition that `agent-agent run` validates before starting. It is updated manually when the expected documentation structure changes.

```python
@pytest.fixture
def target_repo(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(TEMPLATE_DIR, repo)  # copies both code and documentation layers
    subprocess.run(["git", "init"], cwd=repo)
    subprocess.run(["git", "add", "."], cwd=repo)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo)
    return repo
```

**Target repo resolution:** The orchestrator always loads the target repo's `CLAUDE.md` and docs via the `--repo <absolute-path>` argument. It never resolves these paths relative to its own working directory. This is an invariant — violating it causes the server to silently load the wrong documentation when the orchestrator is run against itself (dogfooding) or in tests.

**Push:** `GIT_PUSH_ENABLED=false` for all test runs. The push step no-ops with a logged warning; the orchestrator treats this as success. Branch exists locally; the composite's output is still fully testable.

**Concurrency in component tests:** Component tests run at two worker counts:
- `MAX_WORKERS=1` — primary suite. All multi-composite tests run serially. Isolates correctness and state-management bugs from concurrency issues. Run on every commit.
- `MAX_WORKERS=2` — secondary suite. Validates concurrent worktree creation, shared-context ordering, and parallel executor dispatch. Marked `pytest.mark.parallel` and run as a separate CI step.

> **TODO (MVP):** The review composite and integration node require pushed branches to be visible on the remote. Push must be enabled (against a local bare repo or a real remote) before those components can be component-tested end-to-end. Mark all push-dependent tests with `pytest.mark.requires_push` and skip them until resolved.

#### Tier 3 — Real-Repo End-to-End

Deferred. See [Out of Scope for MVP](#out-of-scope-for-mvp).

---

## Execution Flow (Happy Path)

```
agent-agent run --issue <url> --repo <path>

1. CLI loads config, validates bootstrap state
2. Orchestrator creates dag_run, allocates budget, persists L0 DAG
3. Plan composite (L0):
     a. ResearchPlannerOrchestrator reads issue + repo context, produces investigation summary + L1 DAG spec → PlanOutput
     b. Human checkpoint 1: user approves investigation summary and plan
     c. Orchestrator persists L1 DAG
4. Executor dispatches L1 Coding composites (parallel):
     a. Each composite gets isolated git worktree
     b. Research → Programmer → Test Designer → Test Executor → [Debugger]
     c. On exit: composite pushes branch to remote
5. Integration node (if overlap): sequential rebase-merge-test
6. Review composite: reads all outputs → ReviewOutput (approved)
7. Terminal Plan composite: PlanOutput = null (done)
8. Orchestrator creates PR on target repo
9. Human checkpoint 2: GitHub PR review → human merges
```

---

## Out of Scope for MVP

| Item | Status |
|------|--------|
| Bootstrap agent (`agent-agent bootstrap`) | Post-MVP; target repo `CLAUDE.md` and policies are hand-authored for MVP |
| Architecture Agent (Mode 2) | Post-MVP |
| Requirements drift handling (Mode 3) | Post-MVP |
| SaaS observability integration | Post-MVP |
| Git permission boundaries for Code agents | Post-MVP (noted in P03) |
| Empirical calibration of iteration caps | Required before MVP ships (P10) |
| Parallel issue execution (multiple concurrent DAG runs) | Post-MVP |
| Real-repo end-to-end tests (Tier 3) | Post-MVP; requires push-enabled test remote and pinned test repo |
| Push-dependent component tests (review composite, integration node) | Deferred; tracked via `pytest.mark.requires_push` |
| Issue / Requirements Agent (pre-execution scope validation) | Post-MVP |
| Selective / priority-based CLAUDE.md injection | Post-MVP; MVP includes target repo CLAUDE.md verbatim in every node's context |
