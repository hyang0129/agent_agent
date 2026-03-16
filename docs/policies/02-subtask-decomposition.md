# Policy 02: Sub-task Decomposition Strategy

Sub-task decomposition operates per nesting level. At each level, the Plan composite generates the child DAG structure: how many parallel Coding composites to fan out, and whether an Integration node is needed. The target grain size is one reviewable commit per level — changes that touch at most 3–5 files per branch with a clear completion criterion. The hard limit is 7 parallel Coding composites per level; plans exceeding this are rejected by the orchestrator. The Review composite and terminal Plan composite are structural (always present in L1+ DAGs) and are not decomposition choices. Over-decomposition wastes tokens and causes context fragmentation; under-decomposition overwhelms agents and eliminates partial recovery.

---

## Decomposition Granularity

### P2.1 Target the "single reviewable commit" grain size per nesting level

Each nesting level's Coding phase should produce a change that:
- Could be a standalone, meaningful commit (not "part 1 of function X").
- Touches at most 3–5 files per parallel branch in the common case.
- Has a clear, statable completion criterion ("tests pass," "endpoint returns 200," "type errors resolved").

The git checkpoint occurs when the Coding composite node exits, pushing all changes to remote [P1.11]. Each level therefore maps naturally to one reviewable commit.

### P2.2 Aim for 2–5 parallel Coding composites per level. Treat 8+ as a rejection trigger.

When the Plan composite at a given level produces a child DAG with parallel Coding composites:
- **1 branch**: The level's work is sequential and does not benefit from parallelism. This is the common case for simple changes.
- **2–5 branches**: The normal range. Each branch targets an independent scope.
- **6–7 branches**: Acceptable for complex, multi-component changes. Each branch must meet the decomposition criteria in [P2.4].
- **8+ branches**: Rejected by the orchestrator. The Plan composite must consolidate branches or recommend splitting the issue.

### P2.3 Never decompose what can be done atomically

If the Plan composite can describe the full change in under 200 words and it touches fewer than 3 files, emit a single Coding composite with no parallel branches. Do not decompose for the sake of decomposition.

For trivially simple issues (typo fix, config change, single-function edit), the child DAG at L1 may have a single Coding composite → Review composite → Plan composite sequence with no fan-out.

---

## Decomposition Criteria

### P2.4 A parallel branch is justified when it meets at least TWO of these criteria

1. **Different capability required.** E.g., schema migration vs. API handler vs. frontend component.
2. **Different scope/files.** Changes to independent modules, layers, or packages with no shared files.
3. **Independent verifiability.** The branch produces changes that can be checked in isolation before integration.
4. **Failure isolation.** If this branch fails, other branches' work is preserved.
5. **Different blast radius.** One branch's failure mode is fundamentally different from another's.

### P2.5 The Review composite and terminal Plan composite are structural, not decomposition choices

In every L1+ DAG, the Review composite and terminal Plan composite are always present. Decomposition decisions are therefore limited to: **how many parallel Coding composites within a level, and whether an Integration node is needed.**

---

## Intra-Level Structure Constraints

### P2.6 Maximum parallel Coding composites per level: 7 (hard limit)

No single nesting level may have more than 7 concurrent Coding composites. This is enforced by the orchestrator when validating the Plan composite's output.

If the plan calls for more than 7 branches, the Plan composite must either:
- Consolidate branches that share scope or capability, or
- Recommend splitting the issue into multiple issues.

### P2.7 An Integration node is REQUIRED when parallel branches modify overlapping concerns

If two or more parallel Coding composites could produce changes that interact (shared imports, related API contracts, co-dependent config), the child DAG must include an explicit Integration node between the parallel Coding composites and the Review composite. The Integration node's sole job is to reconcile the parallel changes into a coherent state.

If parallel branches are truly independent (no shared files, no shared interfaces), the Integration node may be omitted.

---

## Context Propagation

### P2.8 Intra-level: each node receives (a) the level's plan output, (b) direct upstream node outputs, and (c) the current git state reference

Do not pass the full output of every node in the level. Parallel Coding composites are invisible to each other — only the Integration node and subsequent nodes see all branches' outputs.

### P2.9 Inter-level: the terminal Plan composite mediates all context between parent and child DAGs

The child DAG receives context from the parent exclusively through the terminal Plan composite that spawned it. This context includes:
- The original issue (propagated at every level).
- The parent level's plan output (what was intended).
- The parent level's Review composite output (what failed, if this is a corrective recursion).
- The current nesting depth and the maximum depth (4).
- The remaining budget.

### P2.10 Node outputs must be structured, not free-text

Every node must return a typed Pydantic result. See the Model Reference in [POLICY_INDEX.md](POLICY_INDEX.md) for canonical types. Free-text narratives are not propagated between nodes.

---

### Violations

- Producing a child DAG with 8 or more parallel Coding composites.
- Omitting an Integration node when two or more Coding composites touch overlapping files or interfaces.
- Decomposing a change that fits in under 200 words and fewer than 3 files into multiple branches.
- Omitting the Review composite or terminal Plan composite from an L1+ DAG.
- Passing free text (instead of typed Pydantic models) between nodes.

### Quick Reference

| Constraint | Limit | Enforcement |
|-----------|-------|-------------|
| Parallel Coding composites per level | 2–5 typical, 7 max | Orchestrator rejects ≥8 |
| Integration node | Required when branches overlap | Planner validation |
| Files per branch | 1–5 typical | Advisory (planner guidance) |
| Node output summary | 500-word max | Enforced truncation |
| Max nesting depth | 4 levels | Hard cap [P1.10] |
| Shared context cap per node | 25% of node's context budget | [P5.8] |
