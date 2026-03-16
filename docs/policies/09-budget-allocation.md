# Budget Allocation Strategy

## 1. Background and State of the Art

### 1.1 The Problem

A multi-agent orchestrator dispatching Claude API calls across a DAG of sub-tasks
faces an asymmetric cost problem: the orchestrator commits budget before knowing
how much each agent will actually need, and different agent types have radically
different consumption profiles. Research agents read many files (high input tokens,
low output). Implementation agents produce code (moderate input, high output).
Review agents fall somewhere between. A naive equal-split strategy either starves
implementation agents or wastes budget on research agents that finish early.

Without a principled allocation strategy, the system either (a) runs out of budget
mid-DAG, abandoning expensive completed work, or (b) sets budgets so conservatively
that agents produce truncated, low-quality outputs.

### 1.2 Approaches in the Literature

| Approach | Source | Key Insight |
|----------|--------|-------------|
| **Three-level hierarchy** | Industry practice (LangChain, CrewAI) | DAG-level, node-level, and request-level budgets act as nested circuit breakers. A single agent cannot blow the global budget. |
| **RL-trained cost controller** | [Controlling Performance and Budget of a Centralized Multi-agent LLM System](https://arxiv.org/abs/2511.02755) (2025) | PPO-trained controller switches between lightweight and expert models based on budget mode. Low-budget mode uses cheap models for easy sub-tasks; high-budget mode routes to capable models. Achieves controllable performance-cost Pareto frontiers. |
| **Self-resource allocation** | [Amayuelas, 2025](https://arxiv.org/abs/2504.02051) | LLMs acting as planners outperform LLMs acting as orchestrators for resource allocation. Explicit information about worker capabilities improves allocation quality. Planners handle concurrent actions more efficiently. |
| **Agent Contracts** | [Agent Contracts: A Formal Framework for Resource-Bounded Autonomous AI](https://arxiv.org/abs/2601.08815) (2026) | Formal resource contracts with conservation laws ensure budget discipline across delegation hierarchies. BALANCED mode (medium effort, 90s timeout) achieves 86% success vs. 70% for URGENT mode, investing 75% more tokens for 16pp higher success. |
| **OPTIMA** | [Chen et al., ACL 2025](https://arxiv.org/abs/2410.08115) | Reward function balancing task performance, token efficiency, and communication readability. Achieves 2.8x performance with <10% token usage on information-exchange tasks. |
| **AgentBalance** | Pareto-optimal LLM pools (2025) | Multi-objective Bayesian Optimization assigns models to agent roles based on compatibility. Achieved 65.8% cost savings vs. homogeneous baselines. |
| **Proxy-layer enforcement** | Helicone, Portkey, LLM gateways | Budget enforcement at the HTTP proxy layer catches all API calls, including those from misbehaving agents that bypass application-level checks. More reliable than cooperative enforcement. |
| **CrewAI budget limiting** | CrewAI framework | Per-agent `max_rpm` and cumulative cost tracking. Enforcement is cooperative — agents making direct API calls bypass it. Demonstrates that framework-level enforcement has inherent gaps. |

### 1.3 Token Consumption Profiles

Empirical data from agent coding systems shows consistent patterns:

| Agent Type | Input:Output Ratio | Typical Budget Share | Why |
|------------|-------------------|---------------------|-----|
| Research   | 10:1              | 15-25%              | Reads many files, produces a short analysis. High input from codebase context. |
| Implement  | 1:3               | 35-45%              | Moderate input (issue + research output + target files), large output (code changes). Most expensive per node. |
| Test       | 1:1               | 10-15%              | Runs commands, interprets output. Modest in both directions. |
| Review     | 3:1               | 15-20%              | Reads diffs and context, produces structured feedback. |
| Planner    | 2:1               | 5-10%               | Single invocation at DAG start. Fixed overhead. |

Research from the Agent Contracts paper confirms that the relationship between token
investment and success is non-linear: a 75% increase in tokens yields only a 16
percentage point improvement in success rate. This means over-allocating to already-
adequate agents has diminishing returns, while under-allocating to struggling agents
has outsized negative impact.

### 1.4 Allocation Strategies

**Equal split** — divide total budget evenly across nodes. Simple but wasteful.
Research nodes rarely use their full allocation while implementation nodes are
starved. Only appropriate when all nodes are the same type.

**Weighted by type** — assign budget proportional to expected consumption per agent
type. Better than equal split but static; does not adapt to actual usage during
execution.

**Dynamic/adaptive** — start with type-weighted estimates, then reallocate unspent
budget from completed nodes to remaining nodes. This is the approach supported by
the literature and adopted in this policy.

---

## 2. Policy

### 2.1 Budget Structure

**P1. Every DAG run has a single, immutable top-level token budget.**

The top-level budget is set at DAG creation time based on issue complexity tier and
environment (dev/prod). It is never increased during execution. This is the hard
ceiling.

| Complexity Tier | Dev Budget | Prod Budget |
|----------------|------------|-------------|
| Simple (1-2 nodes) | 100,000 tokens | 50,000 tokens |
| Medium (3-5 nodes) | 300,000 tokens | 150,000 tokens |
| Complex (6-12 nodes) | 750,000 tokens | 500,000 tokens |

> *Rationale:* An immutable top-level budget prevents runaway cost. Dev budgets
> are 1.5-2x prod to allow experimentation and verbose logging. The tiers align
> with the decomposition policy's node count ranges (Policy 02, P2).

**P2. Each node receives an initial token allocation, not a hard cap.**

The planner assigns each node an initial allocation based on agent type weights.
The sum of all initial allocations must not exceed 85% of the DAG budget — the
remaining 15% is held as a reserve pool.

> *Rationale:* Holding 15% in reserve enables reallocation without requiring any
> node to "give back" unused budget. The Agent Contracts paper's conservation laws
> formalize this: the sum of all sub-budgets plus the reserve must always equal
> the top-level budget.

**P3. Initial allocations use type-weighted distribution.**

After reserving 15% of the DAG budget, distribute the remaining 85% using these
weights:

| Agent Type | Weight | Typical Share of Allocable Budget |
|------------|--------|----------------------------------|
| Research   | 1.0    | ~15% |
| Implement  | 2.5    | ~38% |
| Test       | 0.7    | ~10% |
| Review     | 1.2    | ~18% |
| Planner    | 0.5    | ~8% |

Formula: `node_allocation = (node_weight / sum_of_all_weights) * allocable_budget`

When multiple nodes share the same type, each gets its proportional share of that
type's total weight.

> *Rationale:* Weights reflect empirical token consumption profiles (Section 1.3).
> Implementation agents consistently consume the most tokens due to code generation.
> Research agents need less output budget. These weights are starting points — the
> dynamic reallocation mechanism (P4-P6) corrects for estimation errors.

### 2.2 Dynamic Reallocation

**P4. When a node completes under budget, its unspent tokens return to the reserve pool.**

If a research node was allocated 30,000 tokens and used 18,000, the remaining
12,000 tokens are added to the reserve pool. This happens automatically upon node
completion.

> *Rationale:* Early-finishing agents (especially research and test nodes) reliably
> underspend. Reclaiming their surplus prevents waste. This is the simplest form of
> adaptive allocation and requires no predictive model.

**P5. Before dispatching a node, the executor may augment its allocation from the reserve pool.**

The executor checks the reserve pool before each node dispatch. If the reserve
contains tokens and the node's initial allocation is below a generosity threshold
(defined as 1.5x the type-weighted baseline), the executor may top up the node's
allocation. The top-up is capped at the lesser of:

- The reserve pool balance.
- 50% of the node's initial allocation (i.e., a node can grow to at most 1.5x its original budget).

> *Rationale:* This prevents a single greedy node from draining the entire reserve.
> The 1.5x cap ensures that even with maximum top-up, the system retains reserves
> for downstream nodes. The self-resource allocation research (Amayuelas, 2025)
> shows that planners with explicit capability information make better allocation
> decisions — the type-weighted baseline serves as that capability signal.

**P6. Implementation nodes get priority access to the reserve pool.**

When multiple nodes are eligible for dispatch and the reserve pool is limited,
implementation nodes receive top-ups first, followed by review, then research,
then test.

Priority order: `implement > review > research > test`

> *Rationale:* Implementation nodes have the highest variance in token consumption
> and the highest impact on task success. A research node that finishes with a
> slightly shorter analysis degrades quality marginally; an implementation node
> that runs out of budget mid-edit produces broken code. The RL-trained controller
> research (arXiv 2511.02755) confirms that routing more budget to high-impact
> agents produces better performance-cost trade-offs.

**P7. Reallocation decisions are logged as budget events in the state store.**

Every allocation, top-up, and reclamation is recorded as a structured event:

```python
class BudgetEvent(BaseModel):
    dag_run_id: str
    node_id: str | None        # None for DAG-level events
    event_type: Literal["initial_allocation", "top_up", "reclaim", "exhaustion"]
    tokens_before: int
    tokens_after: int
    reserve_before: int
    reserve_after: int
    timestamp: datetime
```

> *Rationale:* Budget events are essential for post-hoc analysis and tuning the
> type weights. Without an audit trail, allocation improvements are guesswork.
> The observability design doc already requires per-call token logging; budget
> events extend this to allocation-level decisions.

### 2.3 Budget Exhaustion Behavior

**P8. Node-level exhaustion: graceful completion, not hard stop.**

When a node reaches 90% of its allocation, the executor sets a `budget_warning`
flag on the next API call (via reduced `max_tokens` on the request). When a node
reaches 100% of its allocation:

1. The current streaming response is allowed to complete (the tokens are already committed).
2. The node's partial output is saved to the state store.
3. The node is marked `budget_exceeded`.
4. The orchestrator evaluates whether partial output is sufficient for downstream nodes.

> *Rationale:* Cutting a response mid-stream wastes the tokens already spent on
> that request and produces unparseable output. Allowing the final response to
> complete costs at most one additional `max_tokens` worth of overshoot, but
> produces a usable artifact. The error-handling policy already defines this
> pattern for the `budget_exceeded` node state.

**P9. DAG-level exhaustion: stop dispatching, preserve completed work.**

When the DAG budget (top-level, including reserve) is exhausted:

1. No new nodes are dispatched.
2. In-flight nodes are allowed to complete (their API calls are already in progress).
3. Remaining pending nodes are marked `skipped`.
4. All completed node outputs are preserved — they represent valid, paid-for work.
5. A summary is posted to the GitHub issue: what was completed, what was skipped, tokens used, and estimated cost.

> *Rationale:* Completed upstream work (research, partial implementation) has value
> even if the full DAG cannot finish. Preserving it allows the human to continue
> manually or re-run with a higher budget. Hard-stopping in-flight nodes wastes
> their already-committed tokens. The error-handling policy (Budget Enforcement
> section) already specifies this behavior; this policy formalizes the trigger
> conditions.

**P10. After DAG-level exhaustion, emit a structured budget report.**

The report includes:

- Total tokens allocated vs. used, broken down by node.
- Which nodes completed, which were skipped.
- Estimated dollar cost (using current Claude API pricing).
- A recommendation: whether re-running with 1.5x budget is likely to succeed (based on how far the DAG progressed).

> *Rationale:* A budget report enables the developer to make an informed decision
> about re-running. If the DAG was 80% complete when budget ran out, a modest
> increase will likely succeed. If it exhausted budget on the first implementation
> node, the issue may need decomposition changes rather than more tokens.

### 2.4 Per-Request Controls

**P11. Every API call sets `max_tokens` to the lesser of the model's maximum and the node's remaining allocation.**

This ensures that no single response can exceed the node's budget. As the node
approaches its limit, `max_tokens` decreases, naturally encouraging the model to
produce more concise output.

> *Rationale:* `max_tokens` is the only server-enforced budget mechanism in the
> Claude API. All other enforcement is client-side and cooperative. Setting it
> per-request based on remaining allocation makes budget enforcement partially
> server-enforced, which is more reliable than pure application-level tracking.

**P12. Prompt caching prefixes are excluded from budget accounting for allocation purposes, but included in cost tracking.**

Cached input tokens cost 90% less than uncached tokens. For budget allocation
decisions (how much to give each node), count cached tokens at their full token
count. For cost reporting (how much was spent), use actual billed amounts.

> *Rationale:* Allocation must be based on context window consumption (which
> affects model performance), not billing. A research agent reading 50,000 cached
> tokens still has 50,000 tokens of context competing for attention, even though
> the cost is low. Cost tracking, conversely, must reflect actual spend to be
> useful for budgeting decisions.

### 2.5 Configuration and Tuning

**P13. Type weights are configurable per environment, not hardcoded.**

The weights in P3 are defaults. They are stored in the environment config
(`.env.dev`, `.env.prod`) and can be overridden per DAG run via the API.

```
BUDGET_WEIGHT_RESEARCH=1.0
BUDGET_WEIGHT_IMPLEMENT=2.5
BUDGET_WEIGHT_TEST=0.7
BUDGET_WEIGHT_REVIEW=1.2
BUDGET_WEIGHT_PLANNER=0.5
BUDGET_RESERVE_FRACTION=0.15
```

> *Rationale:* Optimal weights depend on the codebase, issue complexity
> distribution, and model capabilities — all of which change over time.
> Hardcoded weights become stale. Configurable weights allow tuning based on
> the budget event logs (P7) without code changes.

**P14. Budget allocation telemetry feeds a weekly efficiency review.**

Collect the following metrics from budget events across all DAG runs:

- Allocation efficiency: `tokens_used / tokens_allocated` per agent type.
- Reserve utilization: fraction of reserve pool consumed per run.
- Exhaustion rate: percentage of DAG runs that hit budget limits.
- Overshoot: tokens consumed beyond allocation (from P8's final-response allowance).

When allocation efficiency for a type consistently falls below 60% or above 95%,
adjust that type's weight accordingly.

> *Rationale:* Static weights are a starting point. The system should converge on
> empirically optimal weights. Below 60% efficiency means over-allocation (wasted
> headroom). Above 95% means under-allocation (agents are budget-constrained and
> likely producing lower-quality output). The 60-95% target range provides
> comfortable headroom without significant waste.

---

## 3. Quick Reference

| Parameter | Default | Configurable | Enforcement |
|-----------|---------|-------------|-------------|
| DAG budget (simple) | 50K prod / 100K dev | Yes (env config) | Hard ceiling, orchestrator |
| DAG budget (medium) | 150K prod / 300K dev | Yes (env config) | Hard ceiling, orchestrator |
| DAG budget (complex) | 500K prod / 750K dev | Yes (env config) | Hard ceiling, orchestrator |
| Reserve fraction | 15% | Yes (env config) | Orchestrator holds at allocation time |
| Max node top-up | 1.5x initial allocation | Yes (env config) | Executor checks before dispatch |
| Top-up priority | implement > review > research > test | No (policy) | Executor dispatch logic |
| Budget warning threshold | 90% of node allocation | No (policy) | Executor sets reduced max_tokens |
| Allocation efficiency target | 60-95% per type | Advisory | Weekly review of telemetry |

---

## 4. Rationale Summary

This policy adopts a **weighted-then-adaptive** allocation strategy because:

1. **Pure equal-split fails.** Agent types have 5-10x variance in token consumption.
   Equal allocation either starves implementation agents or wastes budget on research
   agents.

2. **Pure static-weighted allocation is brittle.** Even with good type weights,
   individual tasks vary. A research node on a complex codebase may need 3x more
   tokens than one on a simple module. Static allocation cannot accommodate this.

3. **Fully dynamic allocation is over-engineered for MVP.** RL-trained controllers
   and Bayesian optimization (AgentBalance) produce superior allocations but require
   training data and infrastructure we do not yet have. The weighted-then-adaptive
   approach provides 80% of the benefit with implementation complexity appropriate
   for a single-developer tool.

4. **The reserve pool is the key mechanism.** By holding 15% in reserve, the system
   can absorb variance without complex reallocation logic. Nodes that underspend
   automatically fund nodes that need more. This mirrors how human project managers
   handle contingency budgets.

5. **Graceful degradation preserves completed work.** In an LLM-based system, every
   completed node represents real monetary cost. Discarding completed work due to a
   downstream budget failure is unacceptable. The policy ensures that partial DAG
   results are always preserved and reported.

---

## 5. References

- [Controlling Performance and Budget of a Centralized Multi-agent LLM System with Reinforcement Learning](https://arxiv.org/abs/2511.02755). 2025.
- Amayuelas. [Self-Resource Allocation in Multi-Agent LLM Systems](https://arxiv.org/abs/2504.02051). 2025.
- [Agent Contracts: A Formal Framework for Resource-Bounded Autonomous AI Systems](https://arxiv.org/abs/2601.08815). 2026.
- Chen et al. [OPTIMA: Optimizing Effectiveness and Efficiency for LLM-Based Multi-Agent System](https://arxiv.org/abs/2410.08115). ACL Findings, 2025.
- [BudgetMLAgent: A Cost-Effective LLM Multi-Agent System for Automating Machine Learning Tasks](https://dl.acm.org/doi/10.1145/3703412.3703416). AIMLSystems, 2024.
- [DRAMA: A Dynamic and Robust Allocation-based Multi-Agent System for Changing Environments](https://arxiv.org/abs/2508.04332). 2025.
- [BudgetThinker: Empowering Budget-aware LLM Reasoning with Control Tokens](https://openreview.net/forum?id=ahatk5qrmB). ICLR Workshop, 2025.
- Agent Agent internal docs: [Cost & Token Budgets](../cost-and-token-budgets.md), [Error Handling and Recovery](../error-handling-and-recovery.md), [Observability](../observability.md).
