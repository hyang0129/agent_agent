# Data Models

*Implements: P05, P10*

This document defines every Pydantic model used for inter-node data exchange. It is the canonical source for model shapes — the Policy Index names the models; this document specifies their fields.

---

## Discovery Types

Defined in P5.7. Every agent output model carries a `discoveries` list. The orchestrator validates, conflict-checks, and appends these to `SharedContext` after each node completes — agents never write to shared context directly.

```python
class FileMapping(BaseModel):
    path: str
    description: str                   # e.g. "contains token validation logic at line 45"
    confidence: float                  # 0.0–1.0

class RootCause(BaseModel):
    description: str
    evidence: str                      # concrete — file path, line number, error message
    confidence: float

class Constraint(BaseModel):
    description: str                   # e.g. "this module has no test coverage"
    evidence: str
    confidence: float

class DesignDecision(BaseModel):
    description: str                   # e.g. "fix via null check rather than refactoring caller"
    rationale: str
    confidence: float

class NegativeFinding(BaseModel):
    description: str                   # e.g. "checked utils.py — not relevant"
    confidence: float

Discovery = FileMapping | RootCause | Constraint | DesignDecision | NegativeFinding
```

All discovery instances carry provenance fields added by the orchestrator before writing (never by the agent):

```python
# Added by orchestrator at write time — not part of agent output
class DiscoveryRecord(BaseModel):
    discovery: Discovery
    source_node_id: str
    timestamp: datetime
    superseded_by: str | None = None
```

---

## Child DAG Specification

This is the payload inside `PlanOutput` when more work is needed. It is the sole mechanism by which new composites are spawned — the orchestrator builds the DAG from this spec.

```python
class CompositeSpec(BaseModel):
    id: str                            # short unique label: "A", "B", "C" — used in branch names and logs
    scope: str                         # what this composite is responsible for; shown in status output
    branch_suffix: str                 # used in agent/<issue-number>/<branch_suffix>

class SequentialEdge(BaseModel):
    from_composite_id: str             # the Review of this composite must complete first
    to_composite_id: str               # before this Coding composite may start

class ChildDAGSpec(BaseModel):
    composites: list[CompositeSpec]    # 2–5 per P02; 6–7 requires justification; 8+ rejected
    sequential_edges: list[SequentialEdge]   # empty list = all composites run in parallel
    justification: str | None = None   # required when len(composites) >= 6
```

**Validation rules (enforced by the orchestrator, not by Pydantic):**
- `len(composites) >= 8` → rejected; orchestrator escalates with P02 violation
- `len(composites) in (6, 7)` and `justification is None` → rejected
- Every `from_composite_id` and `to_composite_id` in `sequential_edges` must reference an `id` in `composites`
- `branch_suffix` values must be unique within a `ChildDAGSpec`

---

## Agent Output Models

### PlanOutput

Produced by the Plan composite (ResearchPlannerOrchestrator sub-agent).

```python
class PlanOutput(BaseModel):
    investigation_summary: str         # always present; shown to human in status output
    child_dag: ChildDAGSpec | None     # None = work complete (terminal Plan composite)
    discoveries: list[Discovery] = []
```

### CodeOutput

Produced by the Programmer and Debugger sub-agents inside the Coding composite.

```python
class CodeOutput(BaseModel):
    summary: str                       # one-paragraph description of changes made
    files_changed: list[str]           # relative paths within the worktree
    branch_name: str                   # agent/<issue-number>/<branch_suffix>
    commit_sha: str | None             # None if no commit was made (e.g. early failure)
    tests_passed: bool | None          # None if tests were not run in this sub-agent
    discoveries: list[Discovery] = []
```

### TestOutput

Produced by the Test Designer (plan) and Test Executor (results) sub-agents.

```python
class TestOutput(BaseModel):
    role: Literal["plan", "results"]
    summary: str
    # role == "plan"
    test_plan: str | None = None       # prose description of what to test and how
    # role == "results"
    passed: bool | None = None
    total_tests: int | None = None
    failed_tests: int | None = None
    failure_details: str | None = None # raw test output, truncated to 2000 chars
    discoveries: list[Discovery] = []
```

### ReviewOutput

Produced by the Reviewer sub-agent inside the Review composite.

```python
class ReviewVerdict(str, Enum):
    APPROVED = "approved"
    NEEDS_REWORK = "needs_rework"
    REJECTED = "rejected"

class ReviewFinding(BaseModel):
    severity: Literal["critical", "major", "minor"]
    location: str | None               # file:line if applicable
    description: str
    suggested_fix: str | None = None

class ReviewOutput(BaseModel):
    verdict: ReviewVerdict
    summary: str
    findings: list[ReviewFinding] = []
    downstream_impacts: list[str] = [] # cross-branch concerns for Plan composite consolidation
    discoveries: list[Discovery] = []
```

---

## System Models

These are defined in the Policy Index; field shapes are specified here.

### AgentOutput (union)

```python
AgentOutput = PlanOutput | CodeOutput | TestOutput | ReviewOutput
```

### NodeResult

```python
class ExecutionMeta(BaseModel):
    attempt_number: int
    started_at: datetime
    completed_at: datetime
    input_tokens: int
    output_tokens: int
    tool_calls: int
    failure_category: str | None = None   # set on failure; see P10.7

class NodeResult(BaseModel):
    output: AgentOutput
    meta: ExecutionMeta
```

`ExecutionMeta` is never passed downstream. It is stored in `node_results` and used by the orchestrator for budget accounting and observability.

### NodeContext

Assembled by `ContextProvider` at dispatch time. Never stored — reconstructed on each dispatch.

```python
class IssueContext(BaseModel):
    url: str
    title: str
    body: str                          # verbatim; never summarized [P5.3]

class AncestorContext(BaseModel):
    # Outputs from non-parent ancestor nodes, after summarization rules (P5.12/P5.13)
    entries: list[AncestorEntry]

class AncestorEntry(BaseModel):
    node_id: str
    depth: int                         # 1 = parent, 2 = grandparent, etc.
    output: AgentOutput | str          # str when summarized; AgentOutput when passed through

class NodeContext(BaseModel):
    issue: IssueContext                # always verbatim [P5.3]
    parent_outputs: dict[str, AgentOutput]   # key = node_id; all immediate DAG predecessors
    ancestor_context: AncestorContext  # grandparent+ after summarization rules
    shared_context_view: SharedContextView   # capped at 25% of node budget [P5, P7]
    context_budget_used: int           # tokens consumed by this context assembly
```

**`parent_outputs` population rules:**
- Coding composite: receives the `PlanOutput` from the L0 Plan composite
- Review composite: receives the `CodeOutput` and `TestOutput` (all cycles) from its paired Coding composite
- Consolidation Plan composite: receives all `(CodeOutput, ReviewOutput)` pairs from the completed level — one entry per node, keyed by node ID

### SharedContext / SharedContextView

```python
class SharedContext(BaseModel):
    issue: IssueContext                        # immutable
    repo_metadata: RepoMetadata                # immutable
    file_mappings: list[DiscoveryRecord] = []  # append-only
    root_causes: list[DiscoveryRecord] = []
    constraints: list[DiscoveryRecord] = []
    design_decisions: list[DiscoveryRecord] = []
    negative_findings: list[DiscoveryRecord] = []
    summary: str = ""                          # derived; recomputed by orchestrator [P5.15]
    active_plan: str = ""                      # derived

class SharedContextView(BaseModel):
    # Read-only snapshot at dispatch time; evidence fields masked per P5.8 pruning rules
    file_mappings: list[DiscoveryRecord]
    root_causes: list[DiscoveryRecord]
    constraints: list[DiscoveryRecord]
    design_decisions: list[DiscoveryRecord]
    negative_findings: list[DiscoveryRecord]
    summary: str
    active_plan: str
    token_budget_used: int
```

### RepoMetadata

```python
class RepoMetadata(BaseModel):
    path: str                          # absolute path to the target repo (--repo argument)
    default_branch: str
    language: str | None = None
    framework: str | None = None
    claude_md: str                     # verbatim content of target repo's CLAUDE.md
```
