# Policy 02: Sub-task Decomposition Strategy

Sub-task decomposition in Agent Agent operates per nesting level, not per issue globally. At each level, the plan node decides how many parallel code branches to fan out within that level's child DAG. The target grain size is one reviewable commit per level: changes that touch at most 3–5 files per branch with a clear completion criterion. Decomposition decisions are limited to how many parallel branches to create and whether an integration node is needed — research, test, and review are structural (always present) and not decomposition choices. Over-decomposition wastes tokens and causes context loss; under-decomposition overwhelms agents and eliminates partial recovery.

---

## 2.1 Decomposition Granularity

**P1. Target the "single reviewable commit" grain size per nesting level.**

Each nesting level's code phase should produce a change that:
- Could be a standalone, meaningful commit (not "part 1 of function X").
- Touches at most 3-5 files per parallel branch in the common case.
- Has a clear, statable completion criterion ("tests pass," "endpoint returns 200," "type errors resolved").

The commit checkpoint occurs after the code phase completes at each level (per Policy 01, P11). This means each level naturally maps to one reviewable commit.

**P2. Aim for 2-5 parallel branches within a nesting level. Treat exceeding 5 as a smell.**

When the plan node at a given level produces a child DAG with parallel branches:
- **1 branch**: The level's work is sequential and does not benefit from parallelism. This is the common case for simple changes.
- **2-5 branches**: The normal range. Each branch targets an independent scope.
- **6-7 branches**: Acceptable for complex, multi-component changes. Each branch must justify its existence by meeting the decomposition criteria in P4.
- **8+ branches**: The issue is too broad. Recommend splitting into multiple issues or consolidating branches that share scope.

Example — a level with two independent concerns plus integration:

```
orchestrate (parent level)
  └─ child DAG:
       ├─ code(backend API) ──────────┐
       ├─ code(database migration) ───┤
       └──────────────────────────────→ code(integration) → test → review → research → plan → orchestrate
```

**P3. Never decompose what can be done atomically.**

If the planner can describe the full change in under 200 words and it touches fewer than 3 files, emit a single code node with no parallel branches. Do not decompose for the sake of decomposition.

For trivially simple issues (typo fix, config change, single-function edit), the orchestrate node at L0 may dispatch a child DAG with a single code → test → review sequence and no fan-out.

---

## 2.2 Decomposition Criteria

**P4. A parallel branch is justified when it meets at least TWO of these criteria:**

1. **Different capability required.** E.g., schema migration vs. API handler vs. frontend component.
2. **Different scope/files.** Changes to independent modules, layers, or packages with no shared files.
3. **Independent verifiability.** The branch produces changes that can be checked in isolation before integration.
4. **Failure isolation.** If this branch fails, other branches' work is preserved.
5. **Different blast radius.** One branch's failure mode is fundamentally different from another's.

**P5. Research and validation are structural, not decomposition decisions.**

In the nested DAG model, research/localization and validation are part of the standard spine rather than decomposition choices:
- **Research and plan** are always present at every level (preloaded stubs that activate on failure).
- **Test and review** follow the code phase at every level as part of the standard sequence.

Decomposition decisions are therefore limited to: **how many parallel branches within the code phase, and whether an integration node is needed.**

---

## 2.3 Intra-Level Structure Constraints

**P6. Maximum parallel branches per level: 5.**

No single nesting level should have more than 5 concurrent code branches. This is enforced by the orchestrator when validating the plan node's output.

If the plan calls for more than 5 branches, the planner must either:
- Consolidate branches that share scope or capability, or
- Recommend splitting the issue into multiple issues.

**P7. An integration node is REQUIRED when parallel branches modify overlapping concerns.**

If two or more parallel branches could produce changes that interact (shared imports, related API contracts, co-dependent config), the plan must include an explicit integration code node between the parallel branches and the test node. This node's sole job is to reconcile the parallel changes into a coherent state.

If parallel branches are truly independent (no shared files, no shared interfaces), the integration node may be omitted and the branches feed directly into the test node.

---

## 2.4 Context Propagation

**P8. Intra-level: each node receives (a) the level's plan output, (b) direct upstream node output(s), and (c) the current git state reference.**

Do not pass the full output of every node in the level. Parallel branches are invisible to each other — only the integration node and subsequent nodes see all branches' outputs.

**P9. Inter-level: the orchestrate node mediates all context between parent and child DAGs.**

The child DAG receives context from the parent exclusively through the orchestrate node that spawned it. This context includes:
- The original issue (propagated at every level).
- The parent level's plan output (what was intended).
- The parent level's review/test output (what failed, if this is a corrective recursion).
- The current recursion depth and recursion estimate.
- The remaining budget.

**P10. Node outputs must be structured, not free-text.**

Every node must return a typed Pydantic result containing: status (success/failure), files changed (list of paths), summary (max 500 words), and artifacts (diffs, test results). Free-text narratives are not propagated between nodes.

---

## Quick Reference

| Constraint | Limit | Enforcement |
|-----------|-------|-------------|
| Parallel branches per level | 2-5 typical, 7 max | Planner validation |
| Max parallel branches (hard) | 5 | Orchestrator rejects wider plans |
| Integration node | Required when branches overlap | Planner validation |
| Files per branch | 1-5 typical | Advisory (planner guidance) |
| Node output size | 500-word summary max | Enforced truncation |
| Total work across levels | No node cap | Bounded by budget (Policy 07) |
| Recursion depth | Advisory estimate (default 10) | Budget exhaustion (Policy 01, P10) |
