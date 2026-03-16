# Budget Allocation Strategy

## 1. Background

A multi-agent orchestrator dispatching Claude API calls across a nested DAG of
sub-tasks needs a way to cap total spend without killing useful in-flight work.
Because everything is a DAG and DAGs nest, the allocation model is recursive:
composite nodes receive a budget and subdivide it among their children.

Exact numbers require empirical tuning. This policy defines the *mechanics* —
how budgets flow, when nodes freeze, and what happens at the edges — not the
specific token counts, which will be calibrated through testing.

---

## 2. Policy

### 2.1 Recursive Allocation

**P1. Every DAG run has a single, immutable top-level token budget.**

Set at DAG creation time based on issue complexity tier and environment
(dev/prod). Never increased during execution.

> *Rationale:* An immutable ceiling prevents runaway cost. Specific tier values
> are TBD pending empirical testing.

**P2. Composite nodes receive a share of their parent's budget and re-allocate to their children.**

Budget flows top-down through the DAG hierarchy:

```
top-level budget
  └─ composite node A (receives X tokens)
       ├─ leaf node A1 (receives portion of X)
       ├─ leaf node A2 (receives portion of X)
       └─ composite node A3 (receives portion of X)
            ├─ leaf node A3a (receives portion of A3's share)
            └─ leaf node A3b (receives portion of A3's share)
```

Each composite node is responsible for dividing its budget among its children.
The allocation strategy within a composite node (equal split, weighted by type,
or something else) is a local decision — the parent doesn't dictate it.

> *Rationale:* Recursive allocation mirrors the recursive DAG structure. A
> composite node understands its children's needs better than the top-level
> orchestrator does. This also means allocation logic is the same at every
> level of nesting — no special cases for depth.

**P3. Initial allocation weights are starting points, subject to empirical tuning.**

Type-weighted distribution (research nodes get less than implementation nodes,
etc.) is a reasonable default, but the actual weights need calibration through
real workloads. The system should log allocation vs. actual usage per node type
to inform tuning.

> *Rationale:* We know directionally that implementation agents consume more
> tokens than research agents, but we don't yet know the ratios for our
> specific workloads. Premature precision here is false confidence.

### 2.2 Node Completion, Not Termination

**P4. Never terminate an active node for budget reasons.**

An active node (one that has started executing) is never killed mid-execution.
Instead, when a node's budget is exhausted, it is allowed to complete its
current API call and then **frozen** — marked as complete so downstream nodes
can consume its output.

> *Rationale:* Killing a node mid-stream wastes all tokens spent on it and
> produces unparseable output. Freezing after completion costs at most one
> extra API response but preserves a usable artifact. The value is in the
> completed output, not the budget line item.

**P5. Frozen nodes emit their output normally.**

A frozen node's output is treated identically to a node that completed within
budget. Downstream nodes receive it, the state store records it, and the DAG
continues. The only difference is metadata: the node is marked as
`frozen_at_budget` rather than `completed`.

> *Rationale:* Downstream nodes shouldn't need to know or care whether their
> input came from a node that finished comfortably or one that was frozen.
> This keeps the DAG execution logic simple.

### 2.3 Top-Level Budget Threshold

**P6. When the top-level budget is within 5% of exhaustion, evaluate whether continuing makes sense.**

At this threshold, the orchestrator pauses before dispatching the next node and
asks: given what remains in the DAG, is spending the last 5% likely to produce
a meaningful outcome?

Heuristics:

- **Continue** if only review/validation stages remain — completing review on
  already-implemented work is high-value, low-cost.
- **Stop** if we're back in a planning or implementation stage — 5% of budget
  is unlikely to produce a complete implementation, and partial planning output
  has little standalone value.

When the decision is to stop:

1. All active nodes are frozen after their current call completes (per P4).
2. Remaining pending nodes are marked `skipped`.
3. All completed/frozen node outputs are preserved.
4. A summary is posted to the GitHub issue: what completed, what was skipped,
   tokens used, and estimated cost.

> *Rationale:* The 5% threshold creates a decision point rather than a hard
> wall. The stage-aware heuristic captures the intuition that finishing a
> review is worthwhile but starting a new implementation cycle is not. This
> avoids the worst outcome: burning remaining budget on work that can't
> complete.

### 2.4 Per-Request Controls

**P7. Every API call sets `max_tokens` to the lesser of the model's maximum and the node's remaining allocation.**

This is the only server-enforced budget mechanism. As a node approaches its
limit, `max_tokens` decreases, naturally encouraging more concise output.

> *Rationale:* All other enforcement is client-side and cooperative. Setting
> `max_tokens` per-request makes enforcement partially server-enforced.

### 2.5 Budget Event Logging

**P8. All allocation and freeze events are logged as structured budget events.**

```python
class BudgetEvent(BaseModel):
    dag_run_id: str
    node_id: str | None
    event_type: Literal["allocate", "reallocate", "freeze", "skip", "exhaust"]
    tokens_before: int
    tokens_after: int
    timestamp: datetime
```

These logs are the primary input for tuning allocation weights. Without them,
weight adjustments are guesswork.

### 2.6 Post-Mortem (Beyond MVP)

**P9. On budget exhaustion, an automated post-mortem analyzes what happened.**

When a DAG run is stopped due to budget exhaustion, a separate post-mortem
process (likely its own DAG) examines the budget event log and node outputs to
determine:

- Which nodes consumed disproportionate budget and why.
- Whether the issue decomposition was appropriate for the budget tier.
- Whether the allocation weights need adjustment.

The post-mortem may also recommend **revival** — resuming the DAG with
additional budget, picking up from the last frozen node rather than restarting.

> *This is explicitly out of scope for MVP.* Noted here to inform the data
> model (budget events need enough detail to support post-mortem analysis) and
> to avoid design decisions that would make post-mortem harder to add later.

---

## 3. Quick Reference

| Parameter | Default | Notes |
|-----------|---------|-------|
| Top-level budget | TBD per tier | Immutable once set |
| Composite node allocation | Parent subdivides | Local strategy per composite |
| Active node termination | Never | Freeze after current call completes |
| Threshold for stop evaluation | 5% remaining | Stage-aware: continue for review, stop for planning/impl |
| `max_tokens` per request | min(model max, node remaining) | Server-enforced |
| Budget event logging | All events | Required for weight tuning |
| Post-mortem | Beyond MVP | Design for it, don't build it yet |

---

## 4. Rationale Summary

1. **Recursive allocation matches recursive DAGs.** Composite nodes allocate
   to children the same way the top level allocates to composites. One
   mechanism at every depth.

2. **Never kill active work.** Freezing after completion preserves the value of
   tokens already spent. The marginal cost of one extra API response is small
   compared to discarding a node's entire output.

3. **Stage-aware stopping beats hard cutoffs.** A 5% threshold with
   context-dependent continuation avoids both premature termination (stopping
   before a cheap review stage) and futile spending (starting expensive work
   that can't complete).

4. **Empirical tuning over theoretical weights.** We log everything and adjust
   based on real data rather than guessing consumption profiles upfront.

5. **Design for post-mortem without building it.** The event log schema
   supports future automated analysis, but MVP uses it only for manual review
   and weight tuning.

---

## 5. References

- [Agent Contracts: A Formal Framework for Resource-Bounded Autonomous AI Systems](https://arxiv.org/abs/2601.08815). 2026.
- [Controlling Performance and Budget of a Centralized Multi-agent LLM System with Reinforcement Learning](https://arxiv.org/abs/2511.02755). 2025.
- Amayuelas. [Self-Resource Allocation in Multi-Agent LLM Systems](https://arxiv.org/abs/2504.02051). 2025.
- Agent Agent internal docs: [Cost & Token Budgets](../cost-and-token-budgets.md), [Error Handling and Recovery](../error-handling-and-recovery.md).
