# Integration Test Plan — Phase 4

## Overview

Ten tests across two tiers. SDK tests (1–5) validate the boundary with the
Claude API — the only place where mock-vs-reality mismatches live. Mid-level
tests (6–10) validate internal wiring with real executor, real git, and real
SQLite but no API calls.

**Cost estimate:** ~$0.50 total for tests 1–5 (Haiku); $0 for tests 6–10.

---

## Tier 1 — SDK Contract (real API calls, ~$0.10 each with Haiku)

### Test 1 — `invoke_agent` executes at least one tool before returning

**File:** `tests/component/test_sdk_wrapper.py`
**Marker:** `@pytest.mark.sdk`

**What it validates:**
The agent uses `Glob`/`Read` during execution rather than immediately emitting
JSON. Confirms the tool execution path is wired end-to-end (not just output
parsing). The existing `test_invoke_agent_returns_valid_output` checks output
shape; this checks that real tool calls happen.

**Setup:**
Give the agent a system prompt requiring it to read the repo before producing
output. Capture tool call logs from `invoke_agent`'s internal log or verify
`cost > 0` with a prompt that can only be answered via tools.

**Assertions:**
- `output` is a valid `PlanOutput`
- `cost_usd > 0`
- At least one tool call logged (can be verified via structlog capture or by
  checking token counts are above the no-tool baseline)

---

### Test 2 — `can_use_tool` callback fires and controls access

**File:** `tests/component/test_sdk_wrapper.py`
**Marker:** `@pytest.mark.sdk`

**What it validates:**
The callback the SDK provides is actually invoked at runtime (not just our
mocks). Allow `Read`/`Glob`, deny `Edit`. Verify the denial message reaches
the agent and the session completes without the denied tool executing.

**Setup:**
Custom `can_use_tool` that allows read-only tools and denies `Edit`. Prompt
the agent to both read a file and attempt to edit it.

**Assertions:**
- Session completes without raising
- `Edit` denial was logged / denial count ≥ 1 in the returned metadata

---

### Test 3 — Discovery write-through from `PlanComposite`

**File:** `tests/component/test_plan_composite.py`
**Marker:** `@pytest.mark.sdk`

**What it validates:**
After `PlanComposite.execute()` returns, the discoveries in `PlanOutput` are
written to SQLite via `SharedContext.append_discoveries()` and are readable
back out. Tests the discovery write-through path end-to-end, which is not
covered anywhere.

**Setup:**
Real `PlanComposite` against `tmp_git_repo`. In-memory `StateStore`. After
`execute()`, call `state_store.list_shared_context(dag_run_id)` and filter for
discovery categories.

**Assertions:**
- At least one `DiscoveryRecord` is present in the state store after execution
- Each record has a valid `category` from the allowed set
- `source_node_id` matches the plan node id

---

### Test 4 — `ReviewComposite` cannot use write tools

**File:** `tests/component/test_review_composite.py`
**Marker:** `@pytest.mark.sdk`

**What it validates:**
The `readonly=True` flag on the `WorktreeRecord` produces a read-only
permission set. The reviewer never invokes `Edit`, `Write`, or `Bash` during
execution. Tests tool restriction enforcement — the existing review tests check
output shape but not tool restrictions.

**Setup:**
Real `ReviewComposite` with `worktree.readonly=True` and a prompt that could
tempt the agent to edit. Capture tool call log.

**Assertions:**
- Session completes without raising
- No `Edit`, `Write`, or `Bash` tool calls appear in the log
- `output.verdict` is a valid `ReviewVerdict`

---

### Test 5 — Denial threshold stops session (`SafetyViolationError`)

**File:** `tests/component/test_sdk_wrapper.py`
**Marker:** `@pytest.mark.sdk`

**What it validates:**
After 5 consecutive `can_use_tool` denials, `invoke_agent` fires `interrupt=True`
and raises `SafetyViolationError`. Tests the safety backstop in `base.py`.

**Setup:**
`can_use_tool` callback that denies every tool. System prompt that repeatedly
attempts tool calls.

**Assertions:**
- `SafetyViolationError` is raised (not `AgentError` or timeout)

---

## Tier 2 — Mid-Level Integration (real executor + real git + mocked `invoke_agent`, $0)

All tests use an in-memory `StateStore`, a `tmp_git_repo` with a bare remote,
and a `WorktreeManager` pointed at the test worktree base directory. The
`agent_fn` parameter is replaced with a function that returns canned
`PlanOutput`/`CodeOutput`/`ReviewOutput` Pydantic objects.

---

### Test 6 — Executor dispatches Plan → Coding → Review end-to-end

**File:** `tests/component/test_executor_integration.py`

**What it validates:**
Real `DAGExecutor` with mocked `invoke_agent`. Verifies:
- Worktree create/remove lifecycle runs for Coding and Review nodes
- Node status transitions: PENDING → RUNNING → COMPLETED for all three nodes
- `NodeResult` records are persisted in the state store for each node

**Setup:**
Build a three-node DAG (PLAN → CODING → REVIEW). Mock `agent_fn` returns
valid Pydantic objects. Run `executor.execute()`.

**Assertions:**
- All three nodes end in `NodeStatus.COMPLETED`
- `DAGRun.status == COMPLETED`
- `NodeResult` exists for each node in state store
- Worktree directories are removed after execution

---

### Test 7 — Child DAG spawned from `PlanOutput` and executed

**File:** `tests/component/test_executor_integration.py`

**What it validates:**
L0 Plan node returns a `PlanOutput` with `child_dag` set. Executor calls
`_spawn_child_dag`, builds child nodes, persists them before execution [P1.8],
allocates budget to child nodes, and executes them. Verifies the
`NotImplementedError` replacement actually works with a real state store.

**Setup:**
Single L0 PLAN node. Mock `agent_fn` returns `PlanOutput` with a
`ChildDAGSpec` containing one CODING + one REVIEW. Mock `agent_fn` returns
canned outputs for the child nodes too.

**Assertions:**
- L0 PLAN node: `COMPLETED`
- L1 child nodes exist in state store before execution begins [P1.8]
- Both child nodes end in `COMPLETED`
- Budget events include `INITIAL_ALLOCATION` entries for the child node ids

---

### Test 8 — Push failure → `branch_name` stays `None` → review gate blocks

**File:** `tests/component/test_executor_integration.py`

**What it validates:**
Coding composite succeeds (agent writes code, commits) but `git push` fails
because no remote is configured. The `_push_branch()` path sets
`branch_name=None` via `update_dag_node_worktree`. The Review node's
`_can_dispatch` reads `None` from DB and blocks, causing the DAG to fail
gracefully.


**Setup:**
Real git repo with **no remote** configured. Real `CodingComposite` (mocked
sub-agents). `git_push_enabled=True`.

**Assertions (after bug fix):**
- `DAGRun.status == FAILED`
- Coding node: `COMPLETED` with `branch_name=None` in state store
- Review node: `FAILED` (blocked by gate, not escalated)
- Error message contains "review gate blocked"

---

### Test 9 — Budget flows through multi-level DAG; `should_pause` fires

**File:** `tests/component/test_executor_integration.py`

**What it validates:**
Real `BudgetManager` tracks costs across L0 Plan → L1 Coding/Review → terminal
Plan. `allocate_child` splits remaining budget. When cumulative cost from mocked
`invoke_agent` (each returning `cost_usd=X`) pushes remaining budget ≤ 5%
threshold, `should_pause()` returns `True`, the DAG status becomes `PAUSED`,
and no further nodes are dispatched. Pending nodes remain `PENDING` (not
`SKIPPED`).

**Setup:**
Total budget = $1.00. Mock `agent_fn` returns `cost_usd=0.32` per node (three
nodes × 0.32 = 0.96 → 4% remaining → pause after third node).

**Assertions:**
- `DAGRun.status == PAUSED`
- The node that triggered pause: `COMPLETED`
- Any remaining nodes: `PENDING` (not `SKIPPED` or `FAILED`)
- `BudgetManager.should_pause()` returns `True` at end of run
- `PAUSE` event exists in `budget_events` table

---

### Test 10 — Orchestrator `use_composites=True` wires everything

**File:** `tests/component/test_orchestrator_integration.py`

**What it validates:**
Real `Orchestrator` with `use_composites=True`, real state store, mocked
`invoke_agent`. Verifies:
- `WorktreeManager` is created and passed to the executor
- `git worktree prune` runs before execution starts
- DAG is built and nodes are persisted before execution [P1.8]
- Full lifecycle completes: PENDING → RUNNING → COMPLETED for all nodes
- `DAGRun.status == COMPLETED` at end

**Setup:**
`tmp_git_repo` with bare remote. Patch `invoke_agent` to return canned outputs.
`Settings(use_composites=True, git_push_enabled=False)`.

**Assertions:**
- `DAGRun.status == COMPLETED`
- All expected nodes exist in state store with `COMPLETED` status
- Worktree base directory is cleaned up after run

---

## Test File Layout

```
tests/component/
├── conftest.py                    # existing: tmp_git_repo, github_test_repo
├── test_sdk_wrapper.py            # Tests 1, 2, 5 (SDK tier)
├── test_plan_composite.py         # Test 3 (SDK tier)
├── test_review_composite.py       # Test 4 (SDK tier)
├── test_executor_integration.py   # Tests 6, 7, 8, 9 (new file)
└── test_orchestrator_integration.py  # Test 10 (new file)
```

---

## Markers and CI Gating

| Marker | Requires | Tests |
|--------|----------|-------|
| `@pytest.mark.sdk` | `ANTHROPIC_API_KEY` | 1–5 |
| _(none)_ | nothing extra | 6–10 |
| `@pytest.mark.github` | `GITHUB_TOKEN` | _(none in this plan)_ |

Tests 6–10 run in CI on every PR. Tests 1–5 are gated behind the API key.
