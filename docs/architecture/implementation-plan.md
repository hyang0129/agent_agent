# MVP Implementation Plan

*Implements the architecture defined in [mvp-architecture.md](mvp-architecture.md) and [data-models.md](data-models.md).*

Each phase delivers a runnable, testable slice. Do not proceed to the next phase until the gate tests pass.

---

## Phase 1 — Foundation (no agents, no git, no I/O)

Establish the package skeleton, config system, all Pydantic models, state store, and budget. Nothing talks to Claude or GitHub. Everything is pure Python.

**Deliverables**

```
src/agent_agent/
    __init__.py
    config.py              # pydantic-settings; env profiles; all AGENT_AGENT_* settings;
                           # includes MAX_BUDGET_USD (float), WORKTREE_BASE_DIR (str)
    models/
        __init__.py
        agent.py           # PlanOutput, CodeOutput, TestOutput, ReviewOutput, AgentOutput
        context.py         # NodeContext, SharedContext, SharedContextView, IssueContext,
                           # RepoMetadata, AncestorContext, DiscoveryRecord, all Discovery types
        dag.py             # DAGRun, DAGNode, NodeResult, ExecutionMeta
        budget.py          # BudgetEvent, BudgetEventType
        escalation.py      # EscalationConfig, EscalationRecord
    state.py               # SQLite schema + async CRUD (aiosqlite); all 6 tables;
                           # dag_runs includes usd_used REAL DEFAULT 0.0 column;
                           # async CRUD includes increment_usd_used(dag_run_id, amount: float)
    budget.py              # BudgetManager: top-down allocation; all budget tracking is
                           # USD-denominated; 25% SharedContextView cap (of node's USD budget),
                           # 5% pause threshold (of total USD budget); BudgetEvent records
                           # usd_before/usd_after; token counts tracked in ExecutionMeta only
tests/
    __init__.py
    unit/
        __init__.py
        test_config.py     # env profile loading, setting overrides
        test_models.py     # Pydantic validation, AgentOutput union, discovery field shapes
        test_state.py      # CRUD round-trips against :memory: SQLite
        test_budget.py     # allocation, 5% USD threshold, event log (usd_before/usd_after)
```

**Dependencies to add to pyproject.toml:** `pydantic-settings`, `aiosqlite`, `structlog`

**Gate:** `pytest tests/unit/` passes with `AGENT_AGENT_ENV=test`. No network, no filesystem except `:memory:`.

---

## Phase 2 — DAG Engine + Worktree Manager (no agents)

Build the orchestration spine. The DAG engine is the MVP stub (hardcoded single `Coding → Review → Plan` triple per P1.2). The worktree manager handles creation and teardown for both composite types. The executor wires dispatch order, NodeContext assembly, and failure classification — but every agent slot is a stub that returns a hardcoded `AgentOutput`.

**Deliverables**

```
src/agent_agent/
    dag/
        __init__.py
        engine.py          # DAGRun construction, topological traversal. MVP stub DAG shape
                           # (per P1.2 — every level ends with a terminal Plan composite):
                           #
                           #   L0 DAG:  NodeType.PLAN  (initial decomposition — spawns L1 child DAG)
                           #   L1 DAG:  NodeType.CODING  (one pair)
                           #            NodeType.REVIEW  (depends on CODING)
                           #            NodeType.PLAN    (terminal — stub always returns PlanOutput
                           #                             with child_dag=null, signalling completion)
                           #
                           # The terminal Plan at L1 is not a separate level; it is the last node
                           # in the L1 child DAG.
                           # Child DAG spawn (Phase 4 real behaviour): when terminal Plan returns a
                           # non-null child_dag, the executor constructs a new child DAGRun with
                           # parent_dag_run_id set to the current run's id, persists all nodes to
                           # dag_nodes before any execution begins [P1.8], then dispatches it
                           # recursively with the same Coding→Review→Plan shape (max 4 levels).
                           # Phase 2 stub: raise NotImplementedError if child_dag is non-null.
                           # All nodes persisted to dag_nodes before any execution begins [P1.7/P1.8]
        executor.py        # dispatch loop, NodeContext assembly via ContextProvider,
                           # failure classification (P10.7), retry/backoff stubs;
                           # WorktreeManager calls always via asyncio.to_thread() — never
                           #   called bare; executor never awaits blocking subprocess directly;
                           # Review dispatch gate: check coding_node.branch_name is not None
                           #   (stub CodeOutput sets a fake branch name; real world sets it on push —
                           #   executor always checks the same state store field);
                           # after each node: calls budget_manager.drain_events() → flush to
                           #   StateStore + increment_usd_used(); then checks should_pause()
                           #   → sets DAGRunStatus.PAUSED; all pending nodes remain PENDING
                           #   (not SKIPPED); no further nodes are dispatched for this run;
                           # child_dag recursion: if terminal Plan output has non-null child_dag
                           #   → raise NotImplementedError (Phase 4 wires real recursive dispatch)
    context/
        __init__.py
        provider.py        # ContextProvider: assembles NodeContext; fields:
                           #   issue: sourced from DAGRun.issue_context (IssueContext); always
                           #     included verbatim, never summarized or omitted [P5.3/P5.18];
                           #     Phase 2 stub: IssueContext fields are empty strings — populated
                           #     by the GitHub client in Phase 3;
                           #   repo_metadata: sourced from SharedContext.repo_metadata; always
                           #     included verbatim on every dispatch, never capped or pruned
                           #     [P5.3]; Phase 2 stub: path and default_branch populated from
                           #     DAGRun; claude_md is empty string until GitHub client (Phase 3)
                           #     populates the target repo's CLAUDE.md content;
                           #   parent_outputs: from all immediate DAG predecessors (keyed by
                           #     node_id);
                           #   ancestor_context: populated but empty for two-level MVP DAG
                           #     (logic present for Phase 4);
                           #   shared_context_view: cap reads budget_manager.shared_context_cap(
                           #     node_id) (USD) and settings.usd_per_byte; if usd_per_byte == 0.0,
                           #     cap is unenforced (placeholder until profiled); otherwise stub
                           #     applies truncation-only (Tiers 2+3 masking/summarization deferred
                           #     to Phase 6 per P5.8): sort DiscoveryRecords newest-first,
                           #     accumulate byte sizes, drop records that exceed limit;
                           #   context_bytes_used: byte sum of included DiscoveryRecords
        shared.py          # SharedContext write protocol: Pydantic type validation + append;
                           # conflict detection deferred to Phase 6 (MVP: last-write-wins + log warn)
    worktree.py            # WorktreeManager: git worktree add/remove for Coding and Review
                           # composites; naming: agent-<run-id>-code-<n> / review-<n>;
                           # path: all worktrees created under settings.WORKTREE_BASE_DIR;
                           #   dev/prod default: <repo_path>/../.agent_agent_worktrees/;
                           #   test: /workspaces/.agent_agent_tests/worktrees/ (set by env);
                           #   WorktreeManager raises if WORKTREE_BASE_DIR is not set;
                           # async: all subprocess git calls wrapped in asyncio.to_thread()
                           #   so the executor's async dispatch loop is never blocked;
                           # read-only for Review is enforced by tool selection in Phase 4,
                           # not filesystem permissions — WorktreeManager creates both identically;
                           # readonly=True flag stored on the worktree record for Phase 4 use
    observability.py       # emit_event(event_type, dag_run_id, *, node_id, **payload);
                           # EventType enum with dot-notation names (dag.started, node.completed,
                           # budget.usage, tool.called, etc.); level (1/2/3) resolved from type
                           # registry; trace_id/span_id as first-class keyword args for OTel
                           # compatibility (Phase 4); wraps structlog; Phase 3 server reads L1
                           # status from SQLite directly — emit_event is the log stream only
tests/
    unit/
        test_dag.py        # DAG construction, topological sort, node dependency resolution;
                           # L0 DAG has one Plan node; L1 child DAG has Coding→Review→Plan shape;
                           # terminal Plan node has NodeType.PLAN and no successors
        test_executor.py   # dispatch order, Review gate (branch_name None → not dispatched,
                           # branch_name set → dispatched); failure classification routing;
                           # pause-after-overrun: stub agent over-spends → DAG status = PAUSED,
                           #   pending nodes remain PENDING, no further nodes dispatched;
                           # rerun cap [P10.9]: node fails twice → third invocation not attempted,
                           #   node marked failed and escalation triggered;
                           # no-retry failures [P10.9]: Safety Violation → escalated on first
                           #   occurrence, no second invocation attempted;
                           #   Resource Exhaustion → escalated immediately, no rerun;
                           #   Deterministic → escalated immediately, no rerun;
                           # transient retry [P10.7]: node raises transient error twice, succeeds
                           #   on third attempt; assert retry count = 2, no rerun slot consumed,
                           #   no escalation triggered, final status = COMPLETED
        test_context.py    # NodeContext assembly: issue field always present (empty stub);
                           # repo_metadata field always present and never pruned — assert
                           #   present even when SharedContextView is fully truncated;
                           # parent_outputs keyed by node_id;
                           # AncestorContext empty for two-level DAG;
                           # cap unenforced when usd_per_byte=0; cap enforced (truncation stub)
                           # when usd_per_byte set; context_bytes_used equals byte sum of
                           # included records
        test_observability.py  # emit_event writes structlog record with correct level, dag_run_id,
                               # node_id; L3 events include trace_id/span_id fields
    component/
        __init__.py
        conftest.py        # tmp_git_repo(tmp_path): git init + initial commit fixture;
                           # sets AGENT_AGENT_WORKTREE_BASE_DIR = /workspaces/.agent_agent_tests/worktrees/
                           #   via monkeypatch/env so all WorktreeManager calls land there,
                           #   never inside the agent_agent git tree
        test_worktree.py   # real git ops: worktree create/teardown, branch isolation;
                           # assert worktree paths are under WORKTREE_BASE_DIR;
                           # readonly flag stored correctly; uses tmp_git_repo fixture
```

**Budget / StateStore wiring:** `BudgetManager` remains synchronous and in-memory. All amounts are USD (`float`). Add `drain_events() -> list[BudgetEvent]` to `BudgetManager` (returns and clears `self.events`). The executor is the sole flusher: after each node, calls `drain_events()`, awaits `state.append_budget_event()` for each (recording `usd_before`/`usd_after`), then `state.increment_usd_used(dag_run_id, amount_usd)`. Pause check follows immediately after.

**P11 Source of Truth Rule:** SQLite is the authoritative source of truth for current state — it is what the server queries and what `agent-agent status` displays. The structured log stream (`emit_event`) is the authoritative audit trail — append-only, ordered, never mutated. Both must fire on every state transition. Failure semantics: a SQLite write failure is fatal (surface immediately and halt the operation); an `emit_event` failure is non-fatal (log to stderr and continue — it must never block execution). In case of conflict between the two, SQLite wins for "what the current state is"; the log stream is used for forensic investigation of how the divergence occurred.

**Pause rule:** `should_pause()` returns `True` when remaining DAG budget ≤ 5% of total. When triggered, the executor sets `DAGRunStatus.PAUSED` and stops dispatching. Pending nodes are left in `NodeStatus.PENDING` — they are not skipped or cancelled. The DAG is frozen at a clean node boundary; the run can be resumed or inspected without loss of state.

**SDK budget backstop (Phase 4 wiring):** `BudgetManager` owns all pause logic. The SDK's `max_budget_usd` is set to `total_budget_usd * 2` per invocation as a runaway-prevention backstop only — it is never expected to trigger under normal operation. The executor passes this value to `invoke_agent()` in Phase 4.

**Gate:** Unit tests pass. Component worktree tests pass against a real `git init` repo in `tmp_path`. Executor dispatch loop runs to completion with stub agents returning hardcoded outputs.

---

## Phase 3 — CLI + Server + GitHub Client

The user-facing surface. `agent-agent run` starts the in-process FastAPI server, validates the target repo, creates a DAG run record, and hands off to the executor (which still uses stub agents). `agent-agent status` polls the status endpoint.

**Deliverables**

```
src/agent_agent/
    cli.py                 # typer CLI: `run`, `status`, `bootstrap` (stub: non-zero exit)
                           # run: port conflict → exit with clear error; self-repo rejection;
                           # CLAUDE.md + policy presence validation
    server.py              # FastAPI app: GET /dags/{id}/status (L1 status from SQLite)
                           # bound in-process by `run`; lifetime = run duration
    github/
        __init__.py
        client.py          # async httpx: issue read (GET), DRY_RUN_GITHUB guard on writes
                           # branch protection check (main/master/production blocklist)
tests/
    unit/
        test_cli.py        # self-repo rejection, missing CLAUDE.md error, bootstrap stub exit
        test_server.py     # status endpoint response shape against :memory: state
    component/
        test_github.py     # issue fetch with pytest-httpx mock; DRY_RUN guard
```

**Dependencies to add:** `typer`, `fastapi`, `uvicorn`, `httpx`, `pytest-httpx`

**Gate:** `agent-agent run --issue <url> --repo <path>` runs end-to-end (with stub agents) and prints a branch name + summary. `agent-agent status` returns L1 status. All tests pass.

---

## Phase 4 — Agent Composites (Claude Code SDK)

Replace stub agents with real Claude Code SDK invocations. Build each composite in isolation, tested against the component fixture repo.

**Deliverables — in this order:**

### 4a. SDK wrapper + base agent
```
src/agent_agent/agents/
    __init__.py
    base.py                # invoke_agent(system_prompt, node_context, tools, model, max_iterations,
                           #              sdk_budget_backstop_usd)
                           # → (AgentOutput, cost_usd); wraps Claude Code SDK; sets
                           # max_budget_usd = sdk_budget_backstop_usd (= total_budget_usd * 2)
                           # on ClaudeAgentOptions as a runaway-prevention backstop only;
                           # reads total_cost_usd from ResultMessage and returns it to caller;
                           # enforces iteration cap; argument validation before each tool call;
                           # logs all calls/denials
```

### 4b. Plan composite
```
src/agent_agent/agents/
    plan.py                # ResearchPlannerOrchestrator: extended reasoning, read-only tools,
                           # produces PlanOutput (MVP stub: hardcoded ChildDAGSpec with 1 composite)
```

### 4c. Coding composite
```
src/agent_agent/agents/
    coding.py              # CodingComposite: runs Programmer → Test Designer → Test Executor
                           # → Debugger cycle (max 3); sub-agents share worktree; persists
                           # sub-agent outputs after each step for resumption; push-on-exit
```

### 4d. Review composite
```
src/agent_agent/agents/
    review.py              # ReviewComposite: read-only worktree, reads CodeOutput + TestOutput,
                           # produces ReviewOutput; dispatched after paired Coding composite push
```

### 4e. SharedContext write protocol
```
src/agent_agent/context/
    shared.py              # (update) wire discoveries from real AgentOutputs into SharedContext;
                           # conflict detection stubs (MVP: last-write-wins with log warning)
```

**Component tests** (all use `repo_with_remote` fixture from §15 of the architecture):
```
tests/component/
    test_plan_composite.py    # produces PlanOutput with valid ChildDAGSpec
    test_coding_composite.py  # full Programmer→Test→Debug cycle; push verified on bare remote
    test_review_composite.py  # reads pushed branch; produces ReviewOutput
```

**Gate:** Component tests pass at `MAX_WORKERS=1` against the fixture repo. Each composite produces a valid, Pydantic-validated output. The coding composite's pushed branch is visible on the bare remote before the review composite starts.

---

## Phase 5 — End-to-End Happy Path

Wire all composites together through the executor. One full run: issue in → branch out.

**Deliverables**

```
tests/component/
    test_e2e.py            # `agent-agent run --issue <fixture-issue> --repo <fixture-repo>`
                           # asserts: branch pushed to bare remote; ReviewOutput is approved;
                           # final Plan composite returns null (work complete);
                           # CLI prints branch name + summary
                           # runs at MAX_WORKERS=1
```

**Gate:** Happy path test passes end-to-end. Branch exists on the bare remote. `agent-agent status` reflects completed state.

---

## Phase 6 — Hardening

Fill in the stubs that are safe to skip until the happy path works.

| Stub | Full implementation |
|------|---------------------|
| Escalation: log + halt | Structured escalation message with attempt history, DAG state, budget snapshot |
| Context overflow: truncation-only stub (Tiers 2+3 skipped) | Full P5.8 compaction: observation masking → Haiku summarization → truncation as last resort |
| Budget split: equal | Weighted allocation; reclaim completed-node surplus to reserve; try_top_up |
| Worktree cleanup: none | Startup scan: remove orphaned worktrees from prior crashed runs |
| Conflict resolution: last-write-wins | Auto-resolve by confidence/recency; escalate on genuine conflict (P5.9) |
| Pause: flat halt at 5% | Stage-aware: continue Review composites at 5% remaining, escalate only for Plan/Coding composites [P07] |

**Gate:** Each hardening item has its own unit or component test. `pytest tests/` passes in full.

---

## Test Fixture

Used by all component and e2e tests. Defined in `tests/fixtures/`:

```
tests/
    fixtures/
        template/          # ~30 files: real Python package with pytest suite, pyproject.toml,
                           # CLAUDE.md (hand-authored), policy docs in expected layout
        conftest.py        # target_repo(tmp_path) and repo_with_remote(tmp_path) fixtures
```

The `repo_with_remote` fixture:
1. Copies `template/` to `tmp_path/repo/`, runs `git init` + initial commit
2. Creates `tmp_path/remote.git` bare repo
3. Wires origin + pushes main
4. Returns `(repo_path, remote_path)`

`GIT_PUSH_ENABLED=true` in `AGENT_AGENT_ENV=test`. Pushes go to the local bare repo.

---

## Build Order Summary

```
Phase 1  →  models, config, state, budget          (pure Python, unit tests only)
Phase 2  →  DAG engine, executor, worktree          (stub agents, component git tests)
Phase 3  →  CLI, server, GitHub client              (runnable end-to-end with stubs)
Phase 4  →  real agent composites (4a→4b→4c→4d)   (Claude Code SDK, component tests)
Phase 5  →  happy path e2e test                    (full run, one issue)
Phase 6  →  harden stubs                           (escalation, context, budget, cleanup)
```

Each phase is independently committable and reviewable. Phases 1–3 require no Claude API key.
