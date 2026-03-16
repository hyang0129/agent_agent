# Policy 07: Budget Allocation Strategy

Every DAG run has a single immutable top-level token budget set at creation time. Budget flows top-down through the DAG hierarchy: composite nodes receive a share and re-allocate to their children using the same mechanism at every depth level. Active nodes are never killed mid-execution — when a node's budget is exhausted, it completes its current API call and is then frozen, emitting its output normally. When the top-level budget drops to within 5% of exhaustion, the orchestrator evaluates whether continuing makes sense based on which stage remains (continue for cheap review stages, stop for expensive planning/implementation stages). Every allocation and freeze event is logged for empirical weight tuning.

---

### 2.1 Recursive Allocation

**P1. Every DAG run has a single, immutable top-level token budget.**

Set at DAG creation time based on issue complexity tier and environment (dev/prod). Never increased during execution.

**P2. Composite nodes receive a share of their parent's budget and re-allocate to their children.**

Budget flows top-down through the DAG hierarchy:

```
top-level budget
  └─ composite node A (receives X tokens)
       ├─ leaf node A1 (receives portion of X)
       ├─ leaf node A2 (receives portion of X)
       └─ composite node A3 (receives portion of X)
            ├─ leaf node A3a
            └─ leaf node A3b
```

Each composite node is responsible for dividing its budget among its children. The allocation strategy within a composite node (equal split, weighted by type, or something else) is a local decision.

**P3. Initial allocation weights are starting points, subject to empirical tuning.**

Type-weighted distribution (research nodes get less than implementation nodes) is a reasonable default, but actual weights need calibration through real workloads. The system MUST log allocation vs. actual usage per node type to inform tuning.

### 2.2 Node Completion, Not Termination

**P4. Never terminate an active node for budget reasons.**

An active node (one that has started executing) is never killed mid-execution. Instead, when a node's budget is exhausted, it is allowed to complete its current API call and then **frozen** — marked as complete so downstream nodes can consume its output.

**P5. Frozen nodes emit their output normally.**

A frozen node's output is treated identically to a node that completed within budget. Downstream nodes receive it, the state store records it, and the DAG continues. The only difference is metadata: the node is marked `frozen_at_budget` rather than `completed`.

### 2.3 Top-Level Budget Threshold

**P6. When the top-level budget is within 5% of exhaustion, evaluate whether continuing makes sense.**

Heuristics:
- **Continue** if only review/validation stages remain — completing review on already-implemented work is high-value, low-cost.
- **Stop** if we're back in a planning or implementation stage — 5% of budget is unlikely to produce a complete implementation.

When the decision is to stop:
1. All active nodes are frozen after their current call completes.
2. Remaining pending nodes are marked `skipped`.
3. All completed/frozen node outputs are preserved.
4. A summary is posted to the GitHub issue: what completed, what was skipped, tokens used, and estimated cost.

### 2.4 Per-Request Controls

**P7. Every API call sets `max_tokens` to the lesser of the model's maximum and the node's remaining allocation.**

This is the only server-enforced budget mechanism. As a node approaches its limit, `max_tokens` decreases, naturally encouraging more concise output.

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

These logs are the primary input for tuning allocation weights.

### 2.6 Post-Mortem (Beyond MVP)

**P9. On budget exhaustion, an automated post-mortem analyzes what happened.**

When a DAG run is stopped due to budget exhaustion, a separate post-mortem process examines the budget event log and node outputs to determine which nodes consumed disproportionate budget, whether the issue decomposition was appropriate, and whether allocation weights need adjustment.

*This is explicitly out of scope for MVP.* The event log schema must support post-mortem analysis to avoid design decisions that would make it harder to add later.

---

## Quick Reference

| Parameter | Default | Notes |
|-----------|---------|-------|
| Top-level budget | TBD per tier | Immutable once set |
| Composite node allocation | Parent subdivides | Local strategy per composite |
| Active node termination | Never | Freeze after current call completes |
| Threshold for stop evaluation | 5% remaining | Continue for review, stop for planning/impl |
| `max_tokens` per request | min(model max, node remaining) | Server-enforced |
| Budget event logging | All events | Required for weight tuning |
| Post-mortem | Beyond MVP | Design for it, don't build it yet |
