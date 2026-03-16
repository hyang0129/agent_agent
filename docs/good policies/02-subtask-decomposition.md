# Sub-task Decomposition Strategy

## 1. Background and State of the Art

### 1.1 The Core Trade-off

Task decomposition in agentic coding systems presents a fundamental tension:
decomposing too finely introduces overhead, context loss, and compounding errors;
decomposing too coarsely overwhelms individual agents and reduces recoverability.

Research by Dziri et al. (2023) quantified the compounding-error problem: if each
step in an agent workflow has 90% accuracy, a 10-step pipeline achieves only 35%
end-to-end reliability (0.9^10). Even at 99% per-step accuracy, a 100-step chain
degrades to 36.6%. Every additional node in a DAG multiplies failure surface area.

In Agent Agent's nested DAG model (Policy 01), this math applies *within* each
nesting level rather than across the entire execution. A level with 5 nodes at 95%
per-node accuracy achieves 77% intra-level reliability — substantially better than
a flat 12-node graph at 54%. Failures at one level trigger a new child DAG at the
next level rather than compounding through a long chain. This reframes the
decomposition question: instead of "how many total nodes?" it becomes "how many
parallel branches within a level?"

### 1.2 Approaches in the Literature

| System | Decomposition Strategy | Key Insight |
|--------|----------------------|-------------|
| **Agentless** (UIUC, 2024) | No decomposition. Three fixed phases: localize, repair, validate. | A flat pipeline with no sub-task DAG solved 32% of SWE-bench Lite at $0.70/issue, outperforming many agentic systems. Simplicity is a competitive advantage. |
| **SWE-Agent** (Princeton, NeurIPS 2024) | Single agent with tool loop. No explicit sub-tasks; the LLM decides its own action sequence. | Autonomy over decomposition avoids premature commitment, but struggles with multi-file, long-horizon changes (SWE-EVO shows 65% -> 21% drop). |
| **AutoCodeRover** (NUS, 2024) | Two-phase: program-structure-aware localization, then context-informed patching. | Leveraging code structure (AST, call graphs) produces better localization than free-form exploration. |
| **MapCoder** (ACL 2024) | Four specialized agents: retrieval, planning, coding, debugging. | Separating retrieval from generation prevents context pollution; plan-derived debugging catches systematic errors. |
| **TDAG** (Wang et al., 2024) | Dynamic decomposition with per-subtask agent generation. Subtasks are revised when upstream results change. | Static upfront decomposition is fragile. Dynamic replanning after each node outperforms fixed DAGs on complex tasks. |
| **Stripe Minions** (2025-2026) | One-shot agents with hybrid workflow: creative LLM steps interleaved with deterministic gates (lint, type-check, test). | Tasks that succeed have "clear inputs, clear success criteria, and limited blast radius." Scope is the primary constraint. |
| **Six Sigma Agent** (2026) | Consensus-driven decomposed execution with voting across micro-agents. | At extreme scale (millions of steps), decomposition works only when each step is independently verifiable. |

### 1.3 Failure Modes

**Too Fine (Over-decomposition):**
- Context loss between nodes: downstream agents lack the reasoning that informed upstream decisions.
- Orchestration overhead: scheduling, message-passing, and state management consume more tokens than the actual work.
- Reassembly failure: executors receive fragments too small to produce coherent changes. A field study found agents failed 68% of multi-step transactions due to context loss and error accumulation.
- Token burn: uncoordinated agent swarms can exhaust token budgets in minutes.

**Too Coarse (Under-decomposition):**
- Agent overwhelm: a single agent asked to make 15 cross-file changes in one shot tends to hallucinate or drop edits.
- No partial recovery: if the agent fails at step 8 of 12, all work is lost. There is no checkpoint to retry from.
- Review opacity: a single monolithic diff is harder for humans (and review agents) to evaluate than scoped changes.
- SWE-EVO results confirm this: agents achieving 65% on single-issue SWE-Bench drop to 21% on multi-file evolution tasks when not given intermediate structure.

### 1.4 Decomposition in a Nested DAG Model

The traditional decomposition question — "how many sub-tasks for this issue?" — assumes
a single flat DAG. Agent Agent's nested model (Policy 01) changes the question in two
ways:

**Decomposition is per-level, not per-issue.** Each nesting level follows the standard
spine (`research → plan → orchestrate`), and the plan node at each level decides how
many parallel branches to fan out within that level's child DAG. The total work across
all levels is bounded by budget, not by a global node count.

**Iteration is recursion, not replanning.** When a test or review fails, the preloaded
research and plan nodes activate, and the orchestrate node spawns a new child DAG. This
replaces the "capped replanning" model (where a flat DAG mutates in-place) with
immutable recursion. Each attempt is a first-class, auditable execution record.

The Goldilocks zone still applies, but at a different scale: **a well-sized level is
one where the parallel branches represent a coherent unit of work that can be
integrated, tested, and reviewed together.** This typically maps to:

- 2-5 parallel code branches targeting independent scopes (files, modules, layers).
- A subsequent integration node if the branches must be reconciled.
- The standard test → review → research → plan → orchestrate tail.

---

## 2. Policy

### 2.1 Decomposition Granularity

**P1. Target the "single reviewable commit" grain size per nesting level.**

Each nesting level's code phase should produce a change that:
- Could be a standalone, meaningful commit (not "part 1 of function X").
- Touches at most 3-5 files per parallel branch in the common case.
- Has a clear, statable completion criterion ("tests pass," "endpoint returns 200," "type errors resolved").

The commit checkpoint occurs after the code phase completes at each level (per
Policy 01, P11). This means each level naturally maps to one reviewable commit — the
granularity at which human developers work and review code.

> *Rationale:* This aligns with SWE-bench's task scope (single-issue PRs) where
> agents perform best, and matches Stripe's finding that tasks succeed when they
> have "clear inputs, clear success criteria, and limited blast radius." The nested
> model preserves this grain size without requiring it to be planned upfront for the
> entire issue — each level decides its own scope.

**P2. Aim for 2-5 parallel branches within a nesting level. Treat exceeding 5 as a smell.**

When the plan node at a given level produces a child DAG with parallel branches:
- **1 branch**: The level's work is sequential and does not benefit from parallelism. This is the common case for simple changes.
- **2-5 branches**: The normal range. Each branch targets an independent scope (e.g., backend vs. database, module A vs. module B). An integration code node follows the parallel branches if their changes must be reconciled before testing.
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

> *Rationale:* Width within a level drives resource consumption (parallel agent
> invocations, token budget) and merge complexity. A downstream integration node
> that must synthesize outputs from 6+ parallel branches is likely to drop or
> conflict changes. The compounding error math is favorable at this scale: 5 parallel
> nodes at 95% per-node accuracy yield 77% combined success, and failures are
> retried via recursion into the next level rather than compounding through a chain.

**P3. Never decompose what can be done atomically.**

If the planner can describe the full change in under 200 words and it touches
fewer than 3 files, emit a single code node with no parallel branches. Do not
decompose for the sake of decomposition.

For trivially simple issues (typo fix, config change, single-function edit), the
orchestrate node at L0 may dispatch a child DAG with a single code → test → review
sequence and no fan-out.

> *Rationale:* Agentless's success demonstrates that simplicity wins on
> well-scoped problems. Orchestration overhead (state tracking, context
> serialization, agent spawning) is not free. The nested model's standard spine
> (research → plan → orchestrate) already adds structural overhead at each level;
> adding unnecessary parallel branches within a level compounds this.

### 2.2 Decomposition Criteria

**P4. A parallel branch is justified when it meets at least TWO of these criteria:**

1. **Different capability required.** E.g., schema migration vs. API handler vs. frontend component. Mixing concerns in one code node dilutes focus.
2. **Different scope/files.** Changes to independent modules, layers, or packages with no shared files. Separate scopes reduce merge conflicts in the integration node.
3. **Independent verifiability.** The branch produces changes that can be checked in isolation before integration (e.g., the migration runs cleanly, the API handler type-checks).
4. **Failure isolation.** If this branch fails, other branches' work is preserved. On the next recursion level, only the failed concern needs to be re-attempted.
5. **Different blast radius.** One branch's failure mode is fundamentally different from another's (e.g., a database migration that could corrupt data vs. an API handler that could return wrong JSON).

> *Rationale:* Requiring two criteria prevents over-splitting. A change to two
> different files alone is not enough to split; but if those files also require
> different capabilities (e.g., SQL migration vs. Python handler) or have different
> blast radii, splitting is justified. This two-of-five rule balances decomposition
> benefit against coordination cost within a single level.

**P5. Research and validation are structural, not decomposition decisions.**

In the nested DAG model, research/localization and validation are part of the
standard spine rather than decomposition choices:

- **Research and plan** are always present at every level (preloaded stubs that activate on failure). The planner does not need to "decide" to include them — they are structural.
- **Test and review** follow the code phase at every level as part of the standard sequence. The planner does not need to "decide" to include validation — it is guaranteed.

Decomposition decisions are therefore limited to: **how many parallel branches
within the code phase, and whether an integration node is needed.**

> *Rationale:* Policy 01's standard spine (research → plan → orchestrate, with
> code → test → review in child DAGs) subsumes the previous policies requiring
> explicit validation nodes (old P5) and research/implementation separation (old P6).
> Making these structural rather than discretionary eliminates a class of planner
> errors — an agent cannot skip validation by omitting it from the plan.

### 2.3 Intra-Level Structure Constraints

**P6. Maximum parallel branches per level: 5.**

No single nesting level should have more than 5 concurrent code branches. This is
enforced by the orchestrator when validating the plan node's output.

If the plan calls for more than 5 branches, the planner must either:
- Consolidate branches that share scope or capability, or
- Recommend splitting the issue into multiple issues.

> *Rationale:* Width drives resource consumption and merge complexity. More
> critically, the integration node that follows parallel branches must synthesize
> all their outputs into a coherent state. Empirically, integration difficulty
> scales super-linearly with branch count — 3 branches produce manageable merges,
> 5 branches are challenging, 7+ branches routinely produce conflicts that the
> integration agent cannot resolve without dropping changes.

**P7. An integration node is REQUIRED when parallel branches modify overlapping concerns.**

If two or more parallel branches could produce changes that interact (shared
imports, related API contracts, co-dependent config), the plan must include an
explicit integration code node between the parallel branches and the test node.
This node's sole job is to reconcile the parallel changes into a coherent state.

If parallel branches are truly independent (no shared files, no shared interfaces),
the integration node may be omitted and the branches feed directly into the test node.

> *Rationale:* Parallel branches operate without visibility into each other's
> changes (Policy 01, P4 — parallelism is horizontal). When their changes interact,
> naive concatenation produces conflicts, broken imports, or inconsistent state.
> The integration node is the fan-in point where these interactions are resolved
> by an agent that has full visibility into all branches' outputs.

### 2.4 Context Propagation

**P8. Intra-level: each node receives (a) the level's plan output, (b) direct upstream node output(s), and (c) the current git state reference.**

Within a single nesting level, context flows along DAG edges. Nodes receive typed
Pydantic outputs from their direct upstream dependencies (per Policy 01, P5 and P7).
The plan output provides the structural context ("what are we trying to accomplish
at this level"), and the git state reference (commit SHA from the code phase
checkpoint) provides the file-level ground truth.

Do not pass the full output of every node in the level. Parallel branches are
invisible to each other — only the integration node and subsequent nodes see all
branches' outputs.

> *Rationale:* Context windows are finite and attention degrades with length.
> Field studies show multi-agent systems fail when "the shared context window
> fills with noise, causing the system to lose focus on the original objective."
> Typed outputs with explicit edges (not broadcast) preserve signal.

**P9. Inter-level: the orchestrate node mediates all context between parent and child DAGs.**

The child DAG receives context from the parent exclusively through the orchestrate
node that spawned it. This context includes:
- The original issue (propagated at every level).
- The parent level's plan output (what was intended).
- The parent level's review/test output (what failed, if this is a corrective recursion).
- The current recursion depth and recursion estimate.
- The remaining budget.

The parent DAG sees only the orchestrate node's typed output — the child DAG's
internal structure is opaque (per Policy 01, P1).

> *Rationale:* The orchestrate node is the serialization point between levels.
> Passing context exclusively through it prevents information leakage that would
> couple parent and child DAG structures. It also ensures that each child DAG
> can be understood, debugged, and recovered independently — its inputs are fully
> described by the orchestrate node's context, not by arbitrary ancestor nodes.

**P10. Node outputs must be structured, not free-text.**

Every node must return a typed Pydantic result containing: status (success/failure),
files changed (list of paths), summary (max 500 words), and artifacts (diffs,
test results). Free-text narratives are not propagated between nodes.

> *Rationale:* Structured outputs are parseable by downstream nodes and the
> orchestrator. Free-text outputs accumulate noise and ambiguity across edges.
> MapCoder's success with structured inter-agent communication supports this.
> Policy 01, P7 mandates typed outputs — this policy specifies the minimum fields.

---

## 3. Quick Reference

| Constraint | Limit | Enforcement |
|-----------|-------|-------------|
| Parallel branches per level | 2-5 typical, 7 max | Planner validation |
| Max parallel branches (hard) | 5 | Orchestrator rejects wider plans |
| Integration node | Required when branches overlap | Planner validation |
| Files per branch | 1-5 typical | Advisory (planner guidance) |
| Node output size | 500-word summary max | Enforced truncation |
| Total work across levels | No node cap | Bounded by budget (Policy 09) |
| Recursion depth | Advisory estimate (default 10) | Budget exhaustion (Policy 01, P10) |
| Replanning | Expressed as recursion | Bounded by budget (Policy 01, P10) |

---

## 4. References

- Dziri et al. "Faith and Fate: Limits of Transformers on Compositionality and Reasoning." NeurIPS 2023.
- Xia et al. "[Agentless: Demystifying LLM-based Software Engineering Agents](https://arxiv.org/abs/2407.01489)." 2024.
- Yang et al. "[SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering](https://github.com/SWE-agent/SWE-agent)." NeurIPS 2024.
- Zhang et al. "[AutoCodeRover: Autonomous Program Improvement](https://arxiv.org/html/2407.01489v1)." ISSTA 2024.
- Islam et al. "[MapCoder: Multi-Agent Code Generation for Competitive Problem Solving](https://aclanthology.org/2024.acl-long.269.pdf)." ACL 2024.
- Wang et al. "[TDAG: A Multi-Agent Framework based on Dynamic Task Decomposition and Agent Generation](https://arxiv.org/abs/2402.10178)." Neural Networks, 2025.
- Cemri et al. "[Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/pdf/2503.13657)" 2025.
- Stripe Engineering. "[Minions: Stripe's One-Shot, End-to-End Coding Agents](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2)." 2025-2026.
- "[The Six Sigma Agent: Enterprise-Grade Reliability Through Consensus-Driven Decomposed Execution](https://arxiv.org/html/2601.22290)." 2026.
- Ding et al. "[SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios](https://arxiv.org/html/2512.18470v2)." 2025.
- Shrestha et al. "[SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks?](https://arxiv.org/pdf/2509.16941)" 2025.
