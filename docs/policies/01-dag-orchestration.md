# Policy 01: DAG as Orchestration Model

The Plan composite node is the orchestration primitive at every level of the hierarchy. Every issue resolution runs as a recursively nested DAG: a Plan composite node at level 0 analyzes the issue and spawns a child DAG; each inner level runs one or more Coding composites followed by a Review composite and a terminal Plan composite that either signals completion (`null`) or spawns the next level. DAGs are immutable once persisted — adaptation happens by nesting a new child DAG inside the terminal Plan composite, never by modifying an executing DAG. Parallelism is horizontal (independent Coding composites within a level run concurrently); cross-level nesting is strictly sequential. Nesting is hard-capped at 4 levels. Every node output is a typed Pydantic model, every DAG is persisted before execution begins, and every Coding composite node pushes all changes to its remote branch on exit for crash recoverability.

---

### P1.1 Every issue resolution MUST be modeled as an immutable, recursively nested DAG

When the orchestrator receives a GitHub issue, it begins execution with a top-level Plan composite node. DAGs are immutable once persisted — no nodes are added, removed, or re-executed, and no edges are modified after persistence. Adaptation to runtime discoveries is achieved through **nesting**: the terminal Plan composite at each level either returns `null` (completion) or spawns a child DAG as its output. The parent DAG sees only the terminal Plan composite's typed inputs and outputs; the child DAG's internal structure is opaque to the parent.

Each level of the hierarchy is an independent, immutable, acyclic graph. Iteration and replanning are expressed as recursion through nesting, not as structural cycles or mutations to an executing DAG.

### P1.2 The standard structure is: Plan composite at L0; Coding → Review → Plan composite at L1+

**Level 0** consists of a single Plan composite node. It analyzes the issue and either resolves it directly or spawns the first child DAG.

**Level 1 and beyond** follow the structure:

```
Coding composite(s) → Review composite(s) → Plan composite
```

The terminal **Plan composite** is the sole decision point at each level. It either:
- Returns `null` — work is complete, no child DAG needed.
- Spawns a child DAG — work continues at the next nesting level.

A typical issue resolution:

```
L0: [Plan composite]
      └─ L1: Coding composite → Review composite → [Plan composite]
                                                          └─ L2: Coding composite → Review composite → [Plan composite]
                                                                                                              └─ null (done)
```

When a level's work requires parallel coding branches:

```
[Plan composite — parent level]
  └─ child DAG:
       ├─ Coding composite (module_a) → Review composite (module_a) ─┐
       ├─ Coding composite (module_b) → Review composite (module_b) ─┤
       └──────────────────────────────────────────────────────────────→ [Plan composite]
```

### P1.3 DAGs MUST be acyclic at every nesting level

No DAG at any level may contain cycles. The acyclic constraint provides termination guarantees, deadlock freedom, and predictable cost bounds. What would traditionally be a cycle (test fails → fix → re-test) is expressed as recursion: the terminal Plan composite spawns a child DAG containing new Coding and Review composites. Each child DAG is independently acyclic.

The Coding composite's internal cyclic sub-agent loop (Programmer → Test Designer → Test Executor → Debugger) is an implementation detail internal to that composite and is modeled as an unrolled acyclic DAG with persisted sub-agent outputs [P10.4].

### P1.4 Parallelism is horizontal within a level, never vertical across levels

The orchestrator dispatches all nodes whose upstream dependencies are satisfied concurrently, up to the configured concurrency limit. This parallelism is strictly **intra-level**: independent Coding composites within a single child DAG run concurrently.

**Cross-level parallelism does not exist.** All branches within a level must converge into the terminal Plan composite before the next level can begin. Level N+1 cannot start until level N's Plan composite has executed.

### P1.5 The planner MUST declare explicit edges for every data dependency

No implicit dependencies. If node B needs output from node A, there must be a directed edge from A to B in the DAG definition. The orchestrator validates the DAG at construction time: every node's declared input types must be satisfiable by the output types of its upstream nodes. This validation applies at each nesting level independently.

### P1.6 Every parallel branch MUST converge into the terminal Plan composite

When a child DAG contains parallel Coding composites, every branch must converge (through its paired Review composite) into the single terminal Plan composite. No branch may terminate at any other node.

### P1.7 Every node MUST produce a typed, structured output

Agent outputs are Pydantic models, not free text. See the Model Reference in [POLICY_INDEX.md](POLICY_INDEX.md) for canonical model names. The terminal Plan composite's output is either `null` (signaling completion) or a reference to the child DAG it spawned.

### P1.8 Every DAG at every nesting level MUST be persisted before execution begins

The full DAG definition — nodes, edges, agent types, parent DAG reference, nesting depth — is written to the state store before the first node is dispatched. Each DAG record includes a reference to its parent Plan composite node (`null` for the top-level DAG). On restart, the orchestrator reconstructs the DAG tree from the state store and resumes from the last completed node. No in-memory-only DAG state.

### P1.9 Topological order MUST be the sole determinant of execution order within a level

The orchestrator traverses each DAG level in topological order. No priority hints, no manual ordering overrides, no agent-type-based scheduling preferences. If two nodes are both ready (all upstream dependencies met), both are dispatched concurrently.

### P1.10 Maximum nesting depth is 4 levels (hard cap)

The orchestrator enforces a maximum nesting depth of **4 levels** (L0 through L3). The current depth is passed as context to every Plan composite node. If a Plan composite at depth 4 determines that more work is needed, it MUST escalate to a human [P6.1] rather than spawning a fifth level.

Issues requiring more than 4 levels of decomposition should be split into separate issues before execution begins.

### P1.11 Coding composite nodes MUST push all changes to remote on exit

When a Coding composite node exits — whether on success, failure, or resource exhaustion — it pushes all in-progress file changes to its designated remote branch before returning control to the orchestrator. This ensures all work-in-progress is recoverable from the remote in the event of an orchestrator crash or restart. Git operations within Coding composite nodes are otherwise unrestricted; specific permission boundaries will be defined post-MVP.

---

### Violations

- Mutating an executing DAG (adding, removing, or re-executing nodes) instead of spawning a child DAG.
- Allowing a parallel branch to terminate at any node other than the terminal Plan composite.
- A terminal Plan composite spawning more than one child DAG.
- Executing any node before its containing DAG definition is persisted to the state store.
- A Coding composite node exiting without pushing all in-progress changes to its remote branch.
- A Plan composite at depth 4 spawning a child DAG instead of escalating to a human.
- Any cross-level parallelism — a child DAG beginning before its parent level's Plan composite has executed.

### Quick Reference

| Constraint | Value | Enforcement |
|-----------|-------|-------------|
| L0 structure | Single Plan composite node | Orchestrator validates |
| L1+ structure | Coding composite(s) → Review composite(s) → Plan composite | Planner output validation |
| Max nesting depth | 4 levels (L0–L3) | Hard cap, orchestrator enforced |
| Intra-level parallelism | Allowed (concurrent Coding composites) | Configurable concurrency limit |
| Cross-level parallelism | Prohibited | Structural — convergence required before next level |
| DAG mutation during execution | Prohibited | State store is append-only after plan persisted |
| Git push on Coding composite exit | Required (all in-progress changes) | Orchestrator post-node hook |
| Node output format | Typed Pydantic model (see Model Reference) | Schema validation at dispatch |
