# Policy 07: Budget Allocation Strategy

Every DAG run has a token budget set at creation time and distributed top-down through the DAG hierarchy. The budget may be increased via human-approved escalation [P6.4] or mid-execution budget pause [P9.6], but the orchestrator never increases the budget autonomously. Active nodes are never killed mid-execution — when a node's budget is exhausted, it completes its current API call and is frozen, emitting its output normally. When the top-level budget drops to within 5% of exhaustion, the orchestrator evaluates whether continuing makes sense based on which stage remains: continue for cheap review stages, stop before expensive planning or implementation stages. Every allocation and freeze event is logged for empirical weight tuning.

---

### P7.1 Every DAG run has a token budget set at creation time

Set at DAG creation time based on issue complexity tier and environment (dev/prod). The budget may be increased via human-approved escalation [P6.4, option `[b]`] or via a mid-execution budget pause [P9.6], but the orchestrator never increases it autonomously. All increases are recorded as `BudgetEvent` entries.

### P7.2 Composite nodes receive a share of their parent's budget and re-allocate to their children

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

### P7.3 Initial allocation weights are starting points, subject to empirical tuning

Type-weighted distribution (Research nodes get less than Code nodes) is a reasonable default, but actual weights need calibration through real workloads. The system MUST log allocation vs. actual usage per node type to inform tuning.

### P7.4 Never interrupt an active node for budget reasons

An active node (one that has started executing) is always allowed to run to completion, regardless of its budget allocation or the run-level budget state. Budget allocations are soft limits — they guide `max_tokens` at call time, but once a node is dispatched, it is never killed, interrupted, or frozen mid-execution.

When a node finishes execution after exceeding its allocation, it is marked `frozen_at_budget` and its output is preserved normally. Freezing happens at node boundaries only — never within a node.

### P7.5 Frozen nodes emit their output normally

A frozen node's output is treated identically to a node that completed within budget. Downstream nodes receive it, the state store records it, and the DAG continues. The only difference is metadata: the node is marked `frozen_at_budget` rather than `completed`.

### P7.6 When the top-level budget is within 5% of exhaustion, pause the run

When the 5% threshold is reached, the executor sets `DAGRunStatus.PAUSED` and stops dispatching after the current node boundary. Pending nodes are left in `NodeStatus.PENDING` — they are not skipped or cancelled. The DAG is frozen at a clean node boundary; the run can be resumed or inspected without loss of state. Escalate to human [P6.1b] with a summary of what completed, what is pending, tokens used, and estimated cost.

**Stage-aware continuation (preferred, not required):** If the implementation has visibility into which stage comes next, it is preferable to continue when only review/validation nodes remain (completing review on already-implemented work is high-value, low-cost) and to pause when planning or implementation nodes remain. This is an optimisation — the safe default is always to pause.

**Relationship to P6 and P9:** The budget handling across policies applies at different thresholds in sequence:
- [P9.6] fires first: the orchestrator pauses when **projected** spend will exceed the budget, giving the human a chance to increase it before exhaustion.
- [P7.6] fires next: at 5% remaining, pause and escalate.
- [P6.1b] fires last: escalate to human for disposition (budget increase, resume, or close).

### P7.7 Every API call sets `max_tokens` to the model's maximum

Nodes always run to completion. `max_tokens` is set to the model's maximum on every call — not the node's remaining allocation. The budget allocation per node informs scheduling decisions and post-run analysis but does not constrain individual API calls.

### P7.8 All allocation and freeze events are logged as structured budget events

```python
class BudgetEvent(BaseModel):
    dag_run_id: str
    node_id: str | None
    event_type: Literal["allocate", "reallocate", "increase", "freeze", "skip", "exhaust"]
    tokens_before: int
    tokens_after: int
    reason: str | None          # For "increase": records who approved and why
    timestamp: datetime
```

These logs are the primary input for tuning allocation weights.

### P7.9 On budget exhaustion, an automated post-mortem analyzes what happened (beyond MVP)

When a DAG run is stopped due to budget exhaustion, a separate post-mortem process examines the budget event log and node outputs to determine which nodes consumed disproportionate budget, whether the issue decomposition was appropriate, and whether allocation weights need adjustment.

*This is explicitly out of scope for MVP.* The event log schema (including the `reason` field on `BudgetEvent`) must support post-mortem analysis from day one.

---

### Violations

- The orchestrator increasing the budget autonomously without human approval.
- Interrupting or killing an active node mid-execution for any budget reason.
- Setting `max_tokens` to anything less than the model's maximum.
- Discarding or skipping pending nodes at the 5% threshold instead of leaving them in `PENDING` for resumption [P7.6].
- Not logging allocation or increase events as `BudgetEvent` records.

### Quick Reference

| Parameter | Default | Notes |
|-----------|---------|-------|
| Top-level budget | Set per complexity tier | May be increased via human approval only |
| Composite node allocation | Parent subdivides | Local strategy per composite |
| Active node interruption | Never | Always run to completion; freeze at node boundary [P7.4] |
| 5% threshold action | Pause run, leave pending nodes PENDING, escalate [P7.6] | Stage-aware continuation is preferred but optional |
| `max_tokens` per request | Model maximum | Nodes always run to completion [P7.7] |
| Budget event logging | All events including increases | Required for weight tuning [P7.8] |
| Post-mortem | Beyond MVP | Design for it now [P7.9] |
