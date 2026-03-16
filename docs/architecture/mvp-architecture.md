# MVP Architecture

*Implements: P01, P02, P03, P05, P06, P07, P08, P09, P10, P11*

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
         ├── Plan composite (L0 — analysis + DAG spec)
         │     (declares parallel pairs, sequential chains, or mixed)
         ├── Coding composite A → Review composite A  ← parallel pair
         ├── Coding composite B → Review composite B  ← parallel pair
         ├── Coding composite C → Review C → Coding composite D → Review D  ← sequential chain
         │     (each Coding composite: Programmer → Test Designer → Test Executor → Debugger)
         └── Plan composite (consolidation — decision or next level)
                          ▼
                    branch ready  ← human review (local MVP; GitHub PR post-MVP)
```

---

## Components

### 1. CLI Layer

The user-facing surface. On `run`, the CLI starts an in-process FastAPI server bound to a config-driven port (`AGENT_AGENT_PORT`, default `8100`) and hands control to the orchestrator. The server remains alive for the duration of the run. `agent-agent status` connects to this port to poll L1 status — it does not read SQLite directly. Users interact with the CLI, not the HTTP layer directly.

| Command | Action |
|---------|--------|
| `agent-agent bootstrap` | Not yet implemented — prints a stub message and exits |
| `agent-agent run --issue <url>` | Start a DAG run for a GitHub issue; binds server on `AGENT_AGENT_PORT` |
| `agent-agent status [run-id]` | Poll `GET /dags/{id}/status` on the running server; fails with a clear error if the server is not reachable |

On `run`, the CLI:
1. Loads config (`AGENT_AGENT_ENV` → `.env.{env}` + `.env.local` + env vars)
2. **Rejects if `--repo` resolves to the orchestrator's own installation directory.** agent_agent must never operate on its own live working tree. To use it to improve itself, clone the repo to a separate directory first and pass that clone as `--repo`.
3. Validates that `CLAUDE.md` and initial policies exist in the target repo (fails with a clear error if missing — bootstrap redirect is post-MVP)
4. Binds the FastAPI server on `AGENT_AGENT_PORT`; if the port is already in use, exit immediately with a clear error message naming the port
5. Creates a DAG run record in the state store
6. Hands control to the orchestrator

---

### 2. Bootstrap Agent *(Not yet implemented)*

The `agent-agent bootstrap` command is a stub. Calling it prints "bootstrap not yet implemented" and exits with a non-zero code.

For MVP, the `CLAUDE.md` and policy set for any target repo are authored manually. The orchestrator's `run` command validates their presence before starting a DAG run and fails with a clear error if missing.

> **Implementation note:** The error message must NOT suggest running `agent-agent bootstrap` — that command is a non-zero stub. The error should instruct the user to create the required files manually and point to the documentation for the expected layout.

---

### 3. Config System

*Follows best practice: dev/prod separation via pydantic-settings.*

- `AGENT_AGENT_` prefix on all settings
- Load order: `.env.{env}` → `.env.local` (gitignored, has secrets) → actual env vars
- Per-environment databases: `data/dev.db`, `data/prod.db`, `:memory:` for tests
- Safety rails in dev: `GIT_PUSH_ENABLED=false`, `DRY_RUN_GITHUB=true`

| Setting | Dev | Prod | Test |
|---------|-----|------|------|
| `LOG_LEVEL` | DEBUG | INFO | DEBUG |
| `LOG_FORMAT` | console | json | console |
| `MODEL` | haiku | sonnet | haiku |
| `GIT_PUSH_ENABLED` | false | true | true (pushes to local bare repo) |
| `DRY_RUN_GITHUB` | true | false | true |
| `MAX_BUDGET_USD` | 5.00 | 5.00 | 5.00 |
| `WORKTREE_BASE_DIR` | `<repo>/../.agent_agent_worktrees` | `<repo>/../.agent_agent_worktrees` | `/workspaces/.agent_agent_tests/worktrees` |
| `PORT` | 8100 | 8100 | 8100 |

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

> **MVP stub:** The DAG Engine for MVP is hardcoded to a single `(Coding, Review)` pair — no dynamic decomposition, no sequential chains, no parallel pairs, no multi-level nesting. The full design below describes the target architecture. See [Deferred Components](#deferred-components) for the complete list of what is stubbed.

Manages immutable, recursively nested DAGs. Adaptation never mutates a live DAG — the terminal Plan composite spawns a child DAG instead.

**Structure rules:**
- L0: single Plan composite (the root)
- L1+: one or more `(Coding, Review)` pairs — parallel, sequential, or mixed — converging into a `Plan composite (consolidation)`
- Each Review composite depends on exactly one Coding composite and starts as soon as that Coding composite exits
- A Coding composite may additionally depend on the Review composite of a preceding Coding composite (sequential chain) — see Dependent Branch Chains below
- The Plan composite depends on all Review composites at the level
- Nesting hard cap: 4 levels; beyond that, escalate
- Plan composite: 2–5 Coding composites per level; 6–7 requires justification; 8+ rejected

**Traversal:** topological sort across all nodes at the level. The executor dispatches every node whose upstream dependencies are satisfied, regardless of whether it is part of a parallel group or a sequential chain. No special scheduling modes.

#### Dependent Branch Chains

The Plan composite may declare a sequential dependency between two `(Coding, Review)` pairs when the second Coding composite's scope cannot be determined without inspecting the first Coding composite's actual output.

**The test:** can Composite B be written correctly if it only knows the *intent* of Composite A, but not the *artifact*? If yes, they should be parallel. If no — if B must examine A's actual diff, run a tool against it, or inspect its concrete output to know which files to touch or what to write — they should be sequential.

**True example:** adding a required parameter to a widely-called internal function.
- Composite A: changes the function signature (new required argument), updates the function body
- Composite B: fixes all call sites that now pass the wrong number of arguments

B cannot enumerate the call sites without running the type-checker or linter against A's actual changes. The set of files to modify and the exact argument to pass are determined by A's output. Running them in parallel would have B working against the pre-change codebase and producing incorrect or incomplete fixes.

**Anti-pattern to avoid:** database migration + backend handler. These look dependent but are not. The backend can be written to target the expected schema without the migration having run. Both touch independent files and merge cleanly as parallel branches. Sequential chaining here wastes wall-clock time with no correctness benefit.

**What the downstream Coding composite receives in a sequential chain:**
- The upstream `CodeOutput` (the actual diff/changes)
- The upstream `ReviewOutput` (structured findings, including any flagged downstream impacts)
- The original issue and `SharedContextView`

It does not receive the upstream Coding composite's worktree directly — it works in its own isolated worktree, seeded from the same base branch, with the upstream diff available as context for it to apply or build on.

**MVP concurrency:** `MAX_WORKERS` is config-driven. The primary testing target is `MAX_WORKERS=1` (serial) — this isolates correctness bugs from concurrency bugs. A secondary test pass at `MAX_WORKERS=2` validates the parallel path end-to-end. The dispatch mechanism is identical at any worker count — each composite gets an isolated worktree and an independent branch — so no architectural change is required to increase parallelism.

---

### 6. Executor

Dispatches agent invocations, collects `NodeResult`s, and updates the state store and shared context after each node completes.

**Per-node dispatch sequence:**
1. Assemble `NodeContext` via `ContextProvider`: issue + parent `AgentOutput`s + `SharedContextView` (capped at 25% of node budget) [P05, P07]
2. Set `max_tokens` to the model's maximum [P07]
3. Invoke agent; receive structured `AgentOutput`
4. Validate output against the canonical Pydantic model
5. Wrap in `NodeResult` with `ExecutionMeta`; persist to `node_results`
6. Extract discoveries from output; orchestrator writes to `shared_context`
7. Log all observability events via single `emit_event` call [P11]

**Failure classification** (see P10):

| Class | Action |
|-------|--------|
| Transient | Retry with exponential backoff (max 3; does not consume the rerun) |
| Agent Error | Re-invoke once with concrete failure context; escalate if it fails again |
| Unknown | Re-invoke once with concrete failure context; escalate if it fails again |
| Resource Exhaustion | Escalate immediately, no rerun |
| Deterministic | Escalate immediately, no rerun |
| Safety Violation | Escalate immediately, no rerun |

Max 1 rerun per node (for Agent Error / Unknown). Re-invocations without concrete failure context are prohibited [P10].

**Review composite dispatch precondition:** The executor must confirm the Coding composite's branch push has completed before dispatching the paired Review composite. The Reviewer reads the pushed branch diff from the remote; dispatching before the push completes will produce a review against a stale or missing branch.

---

### 7. Agent Invocation

Every sub-agent is invoked via the **Claude Code Agent SDK** (not the raw Anthropic messages API). This applies to all sub-agents in all composite types for MVP. Each sub-agent is a separate SDK agent invocation with:
- A role-specific system prompt
- The tool set for that sub-agent type (see §8 permissions table) — sub-agents only receive the tools they are permitted to use
- `NodeContext` serialized as the initial user turn
- The model configured for the environment (`MODEL` setting)

The Plan composite uses extended reasoning (`thinking` blocks enabled) [P10.11]. All other sub-agents use standard mode.

Tool permission enforcement is at two layers:
1. **SDK tool configuration** — sub-agents only receive the tools they are permitted to call; disallowed tools are simply not provided
2. **Orchestrator argument validation** — the executor validates every tool call's arguments against the sub-agent's permission profile before execution; a permitted tool with dangerous arguments is still rejected and logged [P3.4, P8.5]

All agent invocations are async. The executor dispatches composites whose DAG dependencies are satisfied and awaits their completion via async task management.

See [data-models.md](data-models.md) for the complete Pydantic model specifications for all agent inputs and outputs.

---

### 8. Agent Types

*Implements P03, P08.*

Three composite types, each containing one or more sub-agents. Permissions are enforced at the tool layer (argument validation + deny logging), not only in system prompts. Sub-agents within a composite are not exposed to the DAG as separate nodes — they are internal to the composite.

| Composite | Sub-agent | Permissions | Output model |
|-----------|-----------|-------------|--------------|
| Plan composite | ResearchPlannerOrchestrator | Read files, read GitHub, read git history, no writes | `PlanOutput` |
| Coding composite | Programmer | Write files + git within worktree only | `CodeOutput` |
| Coding composite | Test Designer | Read files, design test plan | `TestOutput` (plan) |
| Coding composite | Test Executor | Run test suite commands | `TestOutput` (results) |
| Coding composite | Debugger | Write files + git within worktree | `CodeOutput` |
| Review composite | Reviewer | Read files (one branch's worktree), read its CodeOutput/TestOutput, no writes | `ReviewOutput` |

No sub-agent creates or merges PRs. PR creation is an orchestrator operation.

Each Review composite is scoped to a single Coding composite's output. The Reviewer evaluates that branch in isolation — it does not see sibling branches. The Plan composite is the sole node that holds all N `ReviewOutput`s and decides the integration strategy.

---

### 8. Composite Node Internals

#### Plan Composite

The Plan composite has two distinct invocation roles depending on its position in the DAG:

**L0 (analysis):** A single `ResearchPlannerOrchestrator` agent reads the issue and repo context and generates the child DAG specification. No human checkpoint occurs here — execution proceeds immediately. The agent reads the target repo's `CLAUDE.md`, structure, and git history from the **primary checkout** (the path passed via `--repo`). No worktrees exist at this point — worktree creation begins only after the L0 Plan composite produces its child DAG spec.

```
ResearchPlannerOrchestrator → PlanOutput (child DAG spec)
      ↓
Persist child DAG → hand off to executor
```

The child DAG spec declares each `(Coding, Review)` pair and the dependency edges between them. Pairs with no inter-pair dependency are parallel by default. A sequential dependency edge (`Review_A → Coding_B`) is declared only when Composite B genuinely requires Composite A's artifact to determine its own scope — not merely when the tasks feel related. See [data-models.md](data-models.md) for the `ChildDAGSpec` and `CompositeSpec` Pydantic model definitions.

**Consolidation (after all reviews at the level complete):** The same `ResearchPlannerOrchestrator` agent receives all `(CodeOutput, ReviewOutput)` pairs as upstream inputs and decides next steps.

```
ResearchPlannerOrchestrator
  inputs: all (CodeOutput, ReviewOutput) pairs, SharedContextView, original issue
      ↓
  Decision:
    all approved → PlanOutput = null (work complete)
    any needs_rework/rejected → PlanOutput with child DAG spec (rework level)
```

If `PlanOutput` is `null`, work is complete; orchestrator surfaces the finished branch.
If `PlanOutput` contains a child DAG spec, execution continues at the next nesting level.

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

One Review composite is dispatched per Coding composite. Each runs as soon as its paired Coding composite exits — it does not wait for sibling Coding composites to finish. N Coding composites produce N independent Review composites running in parallel.

Each Review composite is scoped to its paired branch.

**Worktree:** The orchestrator creates a **read-only git worktree** for the Review composite, checked out to the paired Coding composite's pushed branch. This gives the Reviewer filesystem access to the branch's files without the risk of writes. The worktree is created after the Coding composite's push completes and torn down when the Review composite exits.

```
Reviewer (one instance per Coding composite):
  Worktree: read-only checkout of agent/<issue-number>/<branch_suffix>
  Inputs: CodeOutput, TestOutput (all cycles), diff vs base branch, SharedContextView
      ↓
  Evaluate: code quality, test coverage, acceptance criteria, policy compliance
      (evaluation is against the branch in isolation, not against merged state)
      ↓
ReviewOutput: approved | needs_rework | rejected + structured findings
```

The Review composite does not see sibling branches. Cross-branch concerns (integration conflicts, API contract mismatches) are detected and resolved by the Plan composite in the consolidation step, not by individual Reviewers.

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

> **MVP stub:** Context overflow handling is hard truncation only. The structured masking and LLM summarization steps are deferred. The 25% `SharedContextView` cap is enforced at dispatch time in both the stub and the full implementation.

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
        ├── agent-<run-id>-code-1/    ← Coding composite 1 (read/write, own branch)
        ├── agent-<run-id>-review-1/  ← Review composite 1 (read-only, same branch as code-1)
        ├── agent-<run-id>-code-2/    ← Coding composite 2 (read/write, own branch)
        ├── agent-<run-id>-review-2/  ← Review composite 2 (read-only, same branch as code-2)
        └── ...
```

**What is isolated per worktree:** working tree (files), HEAD (branch), index. **What is shared:** `.git` object store, venv, installed dependencies, any read-only repo tooling. Sub-agents within a composite work exclusively in their worktree directory — they never reference the primary checkout path.

**Coding composite worktrees:** read/write. Orchestrator creates the worktree on a new branch before dispatch. On composite exit, the composite pushes the branch; the orchestrator tears down the worktree.

**Review composite worktrees:** read-only (no write tools provided to the Reviewer). Orchestrator creates the worktree checked out to the paired Coding composite's pushed branch, after confirming the push completed. Torn down when the Review composite exits.

With `MAX_WORKERS=1`, worktrees are created and torn down sequentially. The mechanism is identical when `MAX_WORKERS > 1` — worktrees exist concurrently, each on its own branch.

---

### 11. Budget System

*Implements P07.*

- Budget set at DAG run creation in USD (`MAX_BUDGET_USD`); never autonomously increased by the orchestrator
- All budget tracking is USD-denominated; `BudgetEvent` records `usd_before`/`usd_after`; `dag_runs.usd_used` is the running total; token counts are recorded in `ExecutionMeta` for observability only
- Top-down allocation: run budget (USD) → composite budget → node budget
- `max_tokens` is always set to the model's maximum — separate from the USD budget; nodes always run to completion
- Active nodes are never interrupted; `frozen_at_budget` is applied at node boundaries only
- `SharedContextView` cap: 25% of node's USD budget allocation, enforced at dispatch time
- At 5% remaining: continue for Review composites; stop and escalate for Plan/Coding composites (stage-aware; flat halt in MVP, full stage-awareness in Phase 6)
- All events logged as `BudgetEvent` records

Budget increases require human approval via the escalation flow [P06].

---

### 12. Escalation

*Implements P06.*

| Trigger | Severity | Retry? |
|---------|----------|--------|
| Safety violation | CRITICAL | No |
| Semantic anomaly, deterministic error, depth limit | HIGH | No |
| Retry exhaustion, budget exhaustion | MEDIUM | No |

Every escalation is a structured message containing: attempt history, DAG impact summary, budget state, and a recovery options menu (including budget increase). Completed node outputs are always preserved; partial work can be delivered as a labeled PR.

Every escalation is treated as a policy gap signal — if it recurs, the policy or CLAUDE.md must be updated.

---

### 13. Human Checkpoints

*Implements P09.*

Exactly one per issue. Execution is fully autonomous from run start until the branch is ready for review.

| Checkpoint | When | Medium |
|------------|------|--------|
| Branch review | After execution completes | Local branch inspection (MVP); GitHub PR review (post-MVP) |

**MVP:** The orchestrator prints the finished branch name and a summary. The human reviews locally (`git checkout`, `git diff`). No GitHub PR is created.

Mid-execution pauses are allowed only for genuine policy conflicts or projected budget overruns. Every pause must produce a durable fix: a policy document update, a CLAUDE.md edit, or a written scope clarification committed to the state store. A one-off verbal answer is not sufficient.

**Rejected review improvement loop:** orchestrator negotiates with the human to classify the failure (misunderstood issue / wrong approach / bad execution), then outputs a CLAUDE.md edit, prompt change, or policy update committed to the state store.

---

### 14. Observability

*Implements P11.*

All events flow through a single `emit_event(...)` call. Every log record is JSON via `structlog` and includes `dag_run_id` and `node_id`.

| Level | Content | Retention | Medium |
|-------|---------|-----------|--------|
| L1 — Status | DAG and per-node execution state | Indefinite | Structured log + SQLite |
| L2 — Metrics | Tokens, cost, retries, budget events | 90 days | Structured log |
| L3 — Traces | Full LLM I/O, tool calls, denied calls | 7 days | Log files (OTel-compatible field names) |

MVP: local structured log files only. No SaaS observability platform.

API endpoint: `GET /dags/{id}/status` (L1 real-time status). The server is bound by `agent-agent run` on `AGENT_AGENT_PORT` (default `8100`) and is alive for the duration of the run. `agent-agent status` connects to this port; it does not read SQLite directly.
CLI: progress display polling the L1 status endpoint.

---

### 15. Testing Infrastructure

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

**Push:** Component tests use a local bare repo as the push target. A composed fixture creates both the target repo and the bare remote, wires them together, and returns both paths:

```python
@pytest.fixture
def repo_with_remote(tmp_path):
    # bare remote
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)

    # target repo, seeded from template
    repo = tmp_path / "repo"
    shutil.copytree(TEMPLATE_DIR, repo)
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo, check=True)

    return repo, remote
```

`GIT_PUSH_ENABLED=true` in component tests (`AGENT_AGENT_ENV=test`); the push succeeds against the local bare repo. The Review composite reads the pushed branch from the remote. This enables end-to-end testing of the Coding composite push-on-exit, the Review composite reading pushed branches, and the consolidation Plan composite.

**Concurrency in component tests:** Component tests run at two worker counts:
- `MAX_WORKERS=1` — primary suite. All multi-composite tests run serially. Isolates correctness and state-management bugs from concurrency issues. Run on every commit.
- `MAX_WORKERS=2` — secondary suite. Validates concurrent worktree creation, shared-context ordering, and parallel executor dispatch. Marked `pytest.mark.parallel` and run as a separate CI step.

#### Tier 3 — Real-Repo End-to-End

Deferred. See [Out of Scope for MVP](#out-of-scope-for-mvp).

---

## Execution Flow (Happy Path)

```
agent-agent run --issue <url> --repo <path>

1. CLI loads config, validates bootstrap state
2. Orchestrator creates dag_run, allocates budget, persists L0 DAG
3. Plan composite (L0):
     a. ResearchPlannerOrchestrator reads issue + repo context, produces L1 DAG spec → PlanOutput
     b. Orchestrator persists L1 DAG
4. Executor dispatches L1 Coding composites (parallel):
     a. Each composite gets isolated git worktree
     b. Programmer → Test Designer → Test Executor → [Debugger]
     c. On exit: composite pushes branch to remote
5. As each Coding composite exits, its paired Review composite is dispatched immediately:
     a. Reviewer reads this branch's CodeOutput, TestOutput, and worktree diff
     b. Produces ReviewOutput: approved | needs_rework | rejected + structured findings
     c. Review composites for different branches run concurrently
6. Consolidation Plan composite: receives all N (CodeOutput, ReviewOutput) pairs
     a. If any branch needs_rework or rejected: spawn child DAG for rework
     b. If all branches approved (single branch): emit null (done)
     c. If all branches approved (multiple branches): emit next-level child DAG or null
7. Terminal Plan composite: PlanOutput = null (done)
8. Orchestrator prints branch name and execution summary → human reviews branch locally (MVP)
   Post-MVP: orchestrator creates GitHub PR → human reviews and merges
```

---

## Out of Scope for MVP

| Item | Status |
|------|--------|
| GitHub PR creation | Post-MVP; MVP surfaces finished branch name + summary via CLI for local review |
| Bootstrap agent (`agent-agent bootstrap`) | Post-MVP; target repo `CLAUDE.md` and policies are hand-authored for MVP |
| Architecture Agent (Mode 2) | Post-MVP |
| Requirements drift handling (Mode 3) | Post-MVP |
| SaaS observability integration | Post-MVP |
| Git permission boundaries for Code agents | Post-MVP (noted in P03) |
| Empirical calibration of iteration caps | Required before MVP ships (P10) |
| Parallel issue execution (multiple concurrent DAG runs) | Post-MVP |
| Real-repo end-to-end tests (Tier 3) | Post-MVP; requires push-enabled test remote and pinned test repo |
| Issue / Requirements Agent (pre-execution scope validation) | Post-MVP |
| Selective / priority-based CLAUDE.md injection | Post-MVP; MVP includes target repo CLAUDE.md verbatim in every node's context |

---

## Deferred Components

These components are architecturally essential — the system is not complete without them — but are intentionally omitted from the first implementation pass to keep the MVP buildable and testable end-to-end. Each must be built before the system can be considered production-ready.

| Component | MVP Stub | What is Deferred |
|-----------|----------|-----------------|
| **Plan composite: sub-DAG structure determination** | Hardcoded single `(Coding, Review)` pair per run; no dynamic decomposition | The ResearchPlannerOrchestrator determining how many composites to spawn, which are parallel, which form sequential chains, and what dependency edges exist. This is the core planning intelligence of the system. |
| **Sequential dependency chain support** | Not implemented; all pairs treated as parallel | Declaring `Review_A → Coding_B` edges in the child DAG spec; passing upstream `CodeOutput`/`ReviewOutput` as context to downstream Coding composites; the test for artifact dependency vs intent dependency at plan time. |
| **Consolidation Plan composite logic** | Returns `null` after first approved review (single-branch stub) | Receiving N `(CodeOutput, ReviewOutput)` pairs, reasoning across them, deciding rework child DAG vs completion. |
| **Parallel branch execution** | `MAX_WORKERS=1` only; all composites run serially | Concurrent Coding and Review composite dispatch, shared-context write ordering under concurrency, worktree lifecycle with concurrent branches. |
| **Context overflow handling** | Hard truncation only | Structured masking → consumer-driven LLM summarization → truncation cascade (§9). The 25% `SharedContextView` cap is enforced at dispatch time; overflow recovery is truncation-only for MVP. |
| **Full escalation flow** | Log + halt | Structured escalation message with attempt history, DAG impact summary, budget state, and recovery options menu. Budget increase approval path. Policy gap signal recording. |
| **Budget top-down allocation** | Fixed equal split across composites | Plan composite requesting per-node budget allocations; reallocation when a node completes under budget. The 25% SharedContextView cap and the 5% stage-aware stop threshold are enforced even in the stub. |
| **Empirical calibration of iteration and retry caps** | Defaults shipped uncalibrated | Coding composite internal cycle cap (currently 3), iteration caps per agent type. Must be calibrated against real runs before production use. |
