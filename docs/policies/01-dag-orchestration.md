# Policy 01: DAG as Orchestration Model

Every GitHub issue resolution is modeled as an immutable, recursively nested Directed Acyclic Graph (DAG). DAGs are never mutated during execution — adaptation happens by nesting a new child DAG inside an orchestrate node, not by modifying a running DAG. The standard spine at every level is `research → plan → orchestrate`, where the orchestrate node either signals completion (returns `null`) or spawns a child DAG for the next level of work. This model provides termination guarantees, deadlock freedom, bounded cost, and crash recovery at every nesting level while supporting dynamic adaptation through recursion.

---

### 1. Every issue resolution MUST be modeled as an immutable, recursively nested DAG

When the orchestrator receives a GitHub issue, it begins execution with a top-level DAG. DAGs are immutable once persisted — no nodes are added, removed, or re-executed, and no edges are modified. Adaptation to runtime discoveries is achieved through **nesting**: an orchestrate node may spawn a child DAG as its internal execution, following the Temporal Child Workflow model. The parent DAG sees only the orchestrate node's typed inputs and outputs; the child DAG's structure is opaque to the parent.

Each level of the hierarchy is an independent, immutable, acyclic graph. Iteration and replanning are expressed as recursion through nesting, not as structural cycles or mutations to an executing DAG.

### 2. The standard node sequence is: research → plan → orchestrate

Every DAG level follows the same spine:

```
research → plan → orchestrate
```

The **orchestrate** node is the decision point. It examines the plan and either:
- Returns `null` (the work is complete, no child DAG needed), or
- Spawns a child DAG to execute the plan.

A typical issue resolution produces a recursion like:

```
L0: research → plan → orchestrate
                          └─ L1: code → test → review → research → plan → orchestrate
                                                                              └─ L2: code → test → review → research → plan → orchestrate
                                                                                                                                └─ L3: git → PRReview → research → plan → orchestrate
                                                                                                                                                                            └─ null (done)
```

At each level after L0, the sequence begins with the work phase (code/test/review or git/PRReview) followed by the research → plan → orchestrate spine. The research and plan nodes at each level are **preloaded**: they are always present in the DAG structure but execute as stubs (pass-through with minimal output) if the preceding review/test passes. If the review/test fails, they activate fully.

### 3. DAGs MUST be acyclic at every nesting level

No DAG at any level of the hierarchy may contain cycles. The acyclic constraint provides termination guarantees, deadlock freedom, and predictable cost bounds within each level. What would traditionally be modeled as a cycle (test fails → fix → re-test) is instead expressed as recursion: the orchestrate node spawns a new child DAG that includes a new code → test → review sequence. Each child DAG is independently acyclic.

### 4. Parallelism is horizontal within a level, never vertical across levels

The orchestrator dispatches all nodes whose upstream dependencies are satisfied concurrently, up to the configured concurrency limit. This parallelism is strictly **intra-level**: independent branches within a single DAG run concurrently. The concurrency limit is configurable per environment.

**Cross-level parallelism does not exist.** Because all branches within a DAG must converge into a single orchestrate node, and only that orchestrate node can spawn the next nesting level, recursion is strictly sequential. Level N+1 cannot begin until level N's orchestrate node executes.

### 5. The planner MUST declare explicit edges for every data dependency

No implicit dependencies. If node B needs output from node A, there must be a directed edge from A to B in the DAG definition. The orchestrator validates the DAG at construction time: every node's declared input types must be satisfiable by the output types of its upstream nodes. This applies at each nesting level independently.

### 6. Every branch MUST terminate in an orchestrate node

When a plan produces parallel branches, every branch must converge into a single orchestrate node as its terminal node. No branch may terminate in a non-orchestrate node. The orchestrate node is the only node authorized to make the completion decision (return `null` or spawn a child DAG).

```
plan → orchestrate
           ├─ code(module_a) → test(module_a) → review(module_a) ─┐
           └─ code(module_b) → test(module_b) → review(module_b) ─┤
                                                                   └─ research → plan → orchestrate
```

### 7. Every node MUST produce a typed, structured output

Agent outputs are Pydantic models, not free text. The output schema is determined by the agent type (ResearchOutput, CodeOutput, TestOutput, ReviewOutput, PlanOutput, OrchestrateOutput). Downstream nodes receive typed inputs derived from upstream outputs. The orchestrate node's output is either `null` (signaling completion) or a reference to the child DAG it spawned.

### 8. Every DAG at every nesting level MUST be persisted before execution begins

The full DAG definition (nodes, edges, agent types, parent DAG reference, nesting depth) is written to the state store before the first node is dispatched. Each DAG record includes a reference to its parent orchestrate node (null for the top-level DAG). On restart, the orchestrator reconstructs the DAG tree from the state store and resumes from the last completed node. No in-memory-only DAG state.

### 9. Topological order MUST be the sole determinant of execution order within a DAG level

The orchestrator traverses each DAG level in topological order. No priority hints, no manual ordering overrides, no agent-type-based scheduling preferences. If two nodes are both ready (all upstream dependencies met), both are dispatched concurrently.

### 10. Recursion depth is estimated, not hard-capped

The orchestrator maintains a **recursion estimate** (default: 10) representing the expected maximum nesting depth for a given issue. This estimate is passed as context to research and plan nodes at every level, along with the current depth. The estimate is advisory, not a hard ceiling. Budget constraints (which are hard limits) naturally bound recursion depth.

### 11. Git state MUST be preserved after code changes, before testing

When a code node completes, the working tree state is preserved in a recoverable form before the test node executes. The preservation mechanism may be a git commit, stash, or patch file. The code node records the git state reference as part of its output, and downstream nodes receive it as context.

Test and review nodes never modify the working tree — they only read and evaluate it. If a child DAG at level N+1 fails catastrophically, the orchestrator can restore the working tree to the state locked in by level N's code node.
