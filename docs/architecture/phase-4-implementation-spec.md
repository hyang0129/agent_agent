# Phase 4 Implementation — Agent Team Specification

*Process document — remove before merging.*

---

## Goal

Execute the Phase 4 implementation plan (`phase-4-plan.md`) to produce working, tested code across sub-phases 4a–4e. The plan contains exact file paths, class signatures, and code blocks — the team's job is to translate them into compiling, passing code that integrates with the existing codebase.

---

## Inputs

These documents are inputs to the implementation, not open questions:

| Document | Purpose |
|----------|---------|
| `phase-4-plan.md` | Implementation plan with code blocks, test lists, and gates |
| `phase-4-plan.md` § SDK Verification Addendum | Verified SDK API — corrections to original plan assumptions |
| `CLAUDE.md` (repo root) | Repo-specific instructions, structure, commands |
| `docs/policies/POLICY_INDEX.md` | Policy compliance reference |
| Existing source files | Patterns, signatures, and conventions to match |

---

## Constraints

1. **Plan is authoritative.** When the plan specifies a class name, method signature, or behavior, implement it as written. Deviate only when the plan contradicts the SDK verification addendum or the existing codebase — and document the deviation inline as a `# PLAN DEVIATION:` comment.

2. **SDK verification addendum overrides plan code blocks.** The plan's code blocks were written before SDK verification. Where they conflict (e.g., `can_use_tool` signature, `thinking` config, `output_format` key), the addendum is correct.

3. **Match existing patterns.** The codebase uses: Pydantic v2 `BaseModel` (not dataclass), `structlog` for logging, `async/await` throughout, type hints on all public functions. New code must follow these conventions.

4. **No changes outside `repos/agent_agent/`.** Hub workspace rules apply.

5. **Venv:** `source /workspaces/.venvs/agent_agent/bin/activate` before any pip/pytest/mypy/ruff commands.

6. **Each sub-phase must pass its gate before the next begins.** Gates are defined in the plan.

---

## Agent Team

### Agent 1 — Implementer

Writes source code and test code for each sub-phase, strictly following the plan.

**Per sub-phase workflow:**

1. **Read the plan section** for the current sub-phase (4a, 4b, 4c, 4d, or 4e).
2. **Read existing files** that the new code imports from or modifies. Understand current signatures, patterns, and conventions.
3. **Write source files.** Translate plan code blocks into real files. Adapt where the SDK verification addendum overrides the plan. Handle any mismatches with existing code (e.g., if a method the plan calls has a slightly different signature in the real codebase, match the real signature).
4. **Write test files.** Implement every test in the plan's test list. Use existing test patterns from `tests/unit/test_executor.py` and `tests/component/conftest.py` as templates for fixtures, mocking style, and assertions.
5. **Run tests:** `pytest tests/unit/<new_test_file>.py -v` for unit tests. Do not run component tests yet (they require SDK keys).
6. **Run type checker:** `mypy src/agent_agent/agents/` (Phase 4a–4d) or `mypy src/agent_agent/` (Phase 4e).
7. **Run linter:** `ruff check src/agent_agent/agents/ tests/unit/<new>` and `ruff format`.
8. **Fix** until all unit tests pass, mypy clean, ruff clean.
9. **Verify backward compatibility** (Phase 4e only): `pytest tests/unit/ -v` — all pre-existing tests must still pass.

**SDK integration rules:**

- Import from `claude_agent_sdk`, not `claude_code_sdk`
- `can_use_tool` callback: `async def(tool_name: str, input_data: dict, context: Any) -> PermissionResultAllow | PermissionResultDeny`
- `output_format`: `{"type": "json_schema", "schema": ...}`
- `thinking` and `effort` are separate `ClaudeAgentOptions` fields
- Structured output: read from `ResultMessage.structured_output`, fall back to `ResultMessage.result`
- Budget exceeded: `ResultMessage(subtype="error_max_budget_usd", is_error=True)` — not an exception
- Connection/process errors: `ProcessError`, `CLIConnectionError` — these ARE exceptions
- Streaming mode: wrap prompt in `async def _prompt_iter(msg): yield {...}`
- Denial threshold: 5 (not 3), `interrupt=True` on 5th denial

**File creation checklist per sub-phase:**

| Sub-phase | New Source Files | New Test Files | Modified Files |
|-----------|-----------------|----------------|----------------|
| 4a | `agents/__init__.py`, `agents/base.py`, `agents/tools.py`, `agents/prompts.py` | `tests/unit/test_agents_base.py`, `tests/component/test_sdk_wrapper.py` | `pyproject.toml` |
| 4b | `agents/plan.py` | `tests/unit/test_plan_composite.py`, `tests/component/test_plan_composite.py` | — |
| 4c | `agents/coding.py` | `tests/unit/test_coding_composite.py`, `tests/component/test_coding_composite.py` | — |
| 4d | `agents/review.py` | `tests/unit/test_review_composite.py`, `tests/component/test_review_composite.py` | — |
| 4e | — | `tests/unit/test_executor_phase4.py`, `tests/component/test_e2e_phase4.py` | `dag/executor.py`, `budget.py`, `state.py`, `orchestrator.py` |

### Agent 2 — Reviewer

Reviews each sub-phase's implementation after the Implementer declares the gate passed. Does NOT write code — produces a review report only.

**Review checklist:**

1. **Plan compliance** — Every file, class, method, and behavior specified in the plan exists in the implementation. Missing items are listed.
2. **SDK addendum compliance** — All 9 divergences from the addendum are correctly applied. Specifically check:
   - `permission_mode="default"` (not `"never_ask"`)
   - `can_use_tool` has 3-param async signature returning `PermissionResult*`
   - `thinking`/`effort` are separate fields
   - `output_format` uses `"json_schema"`
   - `structured_output` is read before `.result`
   - Error handling uses `ProcessError`/`ResultMessage` pattern (not typed exceptions)
   - Denial threshold is 5 with `interrupt=True`
   - Streaming prompt wrapper exists
3. **Existing code consistency** — New code matches the patterns in the existing codebase:
   - Pydantic v2 model style (not dataclass, unless plan explicitly says dataclass)
   - structlog usage (bound loggers, structured fields)
   - async/await (no sync blocking in async functions, `asyncio.to_thread` for subprocess)
   - Import style (relative within package)
4. **Test coverage** — Every test in the plan's test list has a corresponding test function. Test names are descriptive. Assertions match the plan's intent.
5. **Policy compliance** — Spot-check against POLICY_INDEX.md:
   - P3.3: tool permissions match the permission matrix
   - P5.3: issue always verbatim in serialize_node_context
   - P7: budget backstop uses `compute_sdk_backstop`, not raw total
   - P8.6: every tool call logged (allowed AND denied)
   - P10.7: error mapping matches the corrected table
   - P10.13: sub-agents commit, composite pushes
   - P11: emit_event on state transitions

**Issue classifications:**
- `[BLOCKER]` — will fail at runtime or violate a policy; must fix before next sub-phase
- `[ISSUE]` — incorrect but non-blocking; fix before Phase 4 is declared complete
- `[NIT]` — style or minor concern; fix if convenient

**Output:** A structured review per sub-phase. The Implementer fixes `[BLOCKER]` items before proceeding.

### Agent 3 — Integration Verifier

Runs after Phase 4e is complete and reviewed. Performs end-to-end verification.

**Verification steps:**

1. **Full unit test suite:** `pytest tests/unit/ -v` — all tests pass (Phase 1–4)
2. **Type check:** `mypy src/agent_agent/` — clean
3. **Lint:** `ruff check src/ tests/` — clean
4. **Component test suite (SDK):** `pytest tests/component/ -v -m sdk --timeout=120` — all pass with real SDK calls (requires `ANTHROPIC_API_KEY`). If no API key available, document this as a deferred verification.
5. **Backward compatibility audit:**
   - `DAGExecutor` with `agent_fn` kwarg still works (Phase 2/3 pattern)
   - Existing `test_executor.py` tests pass unchanged (or with only additive kwarg changes)
   - `Orchestrator.run()` with `_stub_agent_fn()` still works
6. **Structural verification:**
   - `agents/` package exists with `__init__.py`, `base.py`, `tools.py`, `prompts.py`, `plan.py`, `coding.py`, `review.py`
   - `pyproject.toml` includes `claude-agent-sdk>=0.1.49`
   - No circular imports (run `python -c "import agent_agent.agents.base"` etc.)
7. **SDK smoke test** (if API key available):
   - `invoke_agent()` with a trivial prompt → valid Pydantic output + `cost_usd > 0`
   - `can_use_tool` callback fires and returns `PermissionResultAllow`
   - `can_use_tool` denial → `PermissionResultDeny` message visible in agent behavior

**Output:** Pass/fail report with failure details. Failures go back to Implementer for fixing.

---

## Workflow

```
                    ┌─────────────────────────────┐
                    │        Phase 4a              │
                    │  Implementer writes code     │
                    │  Unit tests pass             │
                    │  mypy + ruff clean           │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │  Reviewer checks Phase 4a   │
                    │  [BLOCKER] items → fix loop  │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │        Phase 4b              │
                    │  (same cycle)                │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │        Phase 4c              │
                    │  (same cycle)                │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │        Phase 4d              │
                    │  (same cycle)                │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │        Phase 4e              │
                    │  Modifies existing files     │
                    │  ALL unit tests pass         │
                    │  Backward compat verified    │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │  Reviewer checks Phase 4e   │
                    │  [BLOCKER] items → fix loop  │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │  Integration Verifier        │
                    │  Full suite + SDK smoke      │
                    │  Failures → fix loop         │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                              ✅ Phase 4 Complete
```

**Optimization:** Phases 4b, 4c, 4d are independent (no imports between them). The Reviewer MAY batch-review 4b+4c+4d together after all three are implemented, rather than reviewing each individually. The Implementer still writes them sequentially (to avoid divergent patterns), but the review can be deferred to reduce round-trips.

---

## Sub-Phase Execution Context

Each sub-phase section below lists what the Implementer must read before writing.

### Phase 4a — SDK Wrapper + Base Agent

**Read before writing:**
- `phase-4-plan.md` § Phase 4a (full section)
- `phase-4-plan.md` § SDK Verification Addendum
- `src/agent_agent/models/agent.py` — `AgentOutput` union, individual output models
- `src/agent_agent/models/context.py` — `NodeContext`, `SharedContextView`, `AncestorEntry`
- `src/agent_agent/observability.py` — `EventType`, `emit_event` signature
- `src/agent_agent/dag/executor.py` — exception classes (`TransientError`, `AgentError`, `ResourceExhaustionError`, `DeterministicError`, `SafetyViolationError`)
- `tests/unit/test_executor.py` — test patterns, fixture style

**Critical SDK integration points:**
- Install `claude-agent-sdk>=0.1.49` into the venv first
- Verify exact import paths: `from claude_agent_sdk import ClaudeAgentOptions, query, ResultMessage, PermissionResultAllow, PermissionResultDeny, ProcessError, CLIConnectionError, CLINotFoundError, CLIJSONDecodeError`
- If any import fails, check the actual SDK package structure and adapt

**Gate:** Unit tests pass, mypy clean, ruff clean.

### Phase 4b — Plan Composite

**Read before writing:**
- `phase-4-plan.md` § Phase 4b
- `src/agent_agent/agents/base.py` (just written in 4a)
- `src/agent_agent/agents/tools.py` — `plan_permissions()`
- `src/agent_agent/agents/prompts.py` — `RESEARCH_PLANNER_ORCHESTRATOR`, `CONSOLIDATION_PLANNER`
- `src/agent_agent/budget.py` — `BudgetManager.remaining_node()`
- `src/agent_agent/config.py` — `Settings` fields

**Gate:** Unit tests pass, mypy clean.

### Phase 4c — Coding Composite

**Read before writing:**
- `phase-4-plan.md` § Phase 4c (longest section — read carefully)
- `src/agent_agent/agents/base.py`, `tools.py`, `prompts.py`
- `src/agent_agent/state.py` — `append_shared_context()` signature
- `src/agent_agent/worktree.py` — `WorktreeRecord` dataclass
- `src/agent_agent/config.py` — `Settings.git_push_enabled`
- `tests/component/conftest.py` — `tmp_git_repo` fixture pattern

**Gate:** Unit tests pass (all mocked), mypy clean.

### Phase 4d — Review Composite

**Read before writing:**
- `phase-4-plan.md` § Phase 4d
- `src/agent_agent/agents/base.py`, `tools.py`, `prompts.py`
- `src/agent_agent/worktree.py` — `WorktreeRecord`

**Gate:** Unit tests pass, mypy clean.

### Phase 4e — Executor Wiring + Child DAG Recursion

**Read before writing (most critical — modifies existing files):**
- `phase-4-plan.md` § Phase 4e (full section, including orchestrator changes)
- `src/agent_agent/dag/executor.py` — **entire file** (will be modified extensively)
- `src/agent_agent/dag/engine.py` — `topological_sort()` signature
- `src/agent_agent/budget.py` — **entire file** (adding `allocate_child()`)
- `src/agent_agent/state.py` — **entire file** (adding `update_dag_node_worktree()`)
- `src/agent_agent/orchestrator.py` — **entire file** (wiring changes)
- `src/agent_agent/worktree.py` — `WorktreeManager` class, `create_coding_worktree()`, `create_review_worktree()`, `remove_worktree()` signatures
- `tests/unit/test_executor.py` — **entire file** (must not break these tests)
- `tests/unit/test_orchestrator.py` — verify fixture patterns

**Highest risk areas:**
1. `_dispatch_node()` modification — must preserve the existing transient retry + rerun logic while adding the composite dispatch path
2. `_run_with_transient_retry()` signature change — adding `all_nodes` parameter; update all call sites
3. `DAGExecutor.__init__()` signature change — adding optional kwargs; all existing tests must still construct it
4. `allocate()` guard — `allocate_child()` must not trigger the "already allocated" guard

**Gate:** ALL unit tests pass (`pytest tests/unit/ -v`), including pre-existing Phase 2/3 tests. mypy clean. ruff clean.

---

## Output Files

| File | Purpose | Remove before merge? |
|------|---------|---------------------|
| `phase-4-implementation-spec.md` | This file | Yes |
| Per-sub-phase review reports (inline or separate) | Reviewer output | Yes |
| Integration verification report (inline or separate) | Verifier output | Yes |
| All source + test files listed in File Creation Checklist | Implementation | No |

---

## Existing Code Reference

Key signatures the Implementer will need (verified from codebase exploration):

```python
# dag/executor.py — Exception hierarchy (import these in base.py)
class TransientError(Exception): ...
class AgentError(Exception): ...
class ResourceExhaustionError(Exception): ...
class DeterministicError(Exception): ...
class SafetyViolationError(Exception): ...

# dag/executor.py — AgentFn type
AgentFn = Callable[[DAGNode, NodeContext], Awaitable[tuple[AgentOutput, float]]]

# dag/executor.py — DAGExecutor.__init__ (current, before Phase 4e changes)
class DAGExecutor:
    def __init__(
        self,
        state: StateStore,
        budget: BudgetManager,
        context_provider: ContextProvider,
        agent_fn: AgentFn,
        settings: Settings,
    ) -> None: ...

# dag/executor.py — _dispatch_node (current signature)
async def _dispatch_node(
    self, dag_run: DAGRun, node: DAGNode, all_nodes: list[DAGNode]
) -> bool: ...

# dag/engine.py
def topological_sort(nodes: list[DAGNode]) -> list[DAGNode]: ...

# budget.py — BudgetManager key methods
def allocate(self, node_ids: list[str]) -> None: ...
def record_usage(self, node_id: str, usd: float) -> None: ...
def remaining_node(self, node_id: str) -> float: ...
def remaining_dag(self) -> float: ...
def should_pause(self) -> bool: ...

# state.py — methods Phase 4 calls
async def create_dag_node(self, node: DAGNode) -> None: ...
async def get_dag_node(self, node_id: str) -> DAGNode | None: ...
async def update_dag_node_status(self, node_id: str, status: str, ...) -> None: ...
async def append_shared_context(self, entry_id: str, dag_run_id: str, source_node_id: str, category: str, data: dict) -> None: ...
async def get_dag_run(self, run_id: str) -> DAGRun | None: ...
async def update_dag_run_status(self, run_id: str, status: str, error: str | None = None) -> None: ...

# worktree.py
@dataclass
class WorktreeRecord:
    path: str
    branch: str

class WorktreeManager:
    async def create_coding_worktree(self, repo_path: str, dag_run_id: str, node_id: str, n: int) -> WorktreeRecord: ...
    async def create_review_worktree(self, repo_path: str, dag_run_id: str, node_id: str, n: int, existing_branch: str) -> WorktreeRecord: ...
    async def remove_worktree(self, repo_path: str, worktree_path: str) -> None: ...

# observability.py
class EventType(str, Enum):
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    TOOL_CALLED = "tool_called"
    TOOL_DENIED = "tool_denied"
    # ... etc

def emit_event(event_type: EventType, dag_run_id: str, *, node_id: str | None = None, **kwargs) -> None: ...

# models/context.py — NodeContext
class NodeContext(BaseModel):
    issue: IssueContext
    repo_metadata: RepoMetadata
    parent_outputs: dict[str, AgentOutput] = {}
    ancestor_context: AncestorContext = AncestorContext()
    shared_context_view: SharedContextView = SharedContextView()
    context_bytes_used: int = 0

# models/dag.py — Key types
class NodeType(str, Enum):
    PLAN = "plan"
    CODING = "coding"
    REVIEW = "review"

class DAGRunStatus(str, Enum):
    PAUSED = "paused"
    FAILED = "failed"
    ESCALATED = "escalated"
    # ... etc
```
