# Escalation Policy

## Core Philosophy

### Issue In, PR Out

The system's contract is: accept a GitHub issue, produce a PR. Any human intervention
between those two points means the system's policies, CLAUDE.md, or agent definitions
were insufficient. Escalation is not a feature to be optimized — it is evidence of a
policy gap that should be closed.

This does not mean escalation never happens. It means every escalation is treated as a
signal that something upstream needs fixing — a missing policy, an under-specified agent
prompt, or an unhandled edge case. The goal is to drive the escalation rate toward zero
by improving the system, not by suppressing alerts.

### The Cost That Matters

The traditional framing of escalation quality is FP/FN tradeoff: false escalations
waste human attention, missed escalations let bad code through. But this framing treats
both costs as symmetric. They are not.

**The cost that dominates is shipping bad code.** A false escalation spends budget — the
agent re-runs, the human glances at a message, the system burns tokens. That cost is
bounded and recoverable. Shipped bad code has unbounded downstream cost: broken
deployments, user-facing bugs, trust erosion, rollback effort.

The system is best understood as a **variable-effort true-positive detector**: it
expends compute to identify genuinely problematic outputs. As false positives increase,
the detector's effectiveness degrades — not because the FPs themselves are costly, but
because they consume the budget and attention that should go toward catching real
problems. The practical failure mode is not "too many alerts" but "ran out of budget
before finishing the job."

### Optimize for Recall

Given asymmetric costs, the escalation policy **prioritizes recall (true positive rate)
over precision**. The system should catch every genuinely problematic output, even if
that means some false escalations. A flawed intermediate result that gets flagged and
re-run is a budget cost. A flawed result that reaches the PR is a shipped-code cost.

Concretely:
- When uncertain whether an output is problematic, **escalate** (flag for re-examination
  or retry). The worst case is wasted tokens.
- When uncertain whether an output is safe, **do not pass it downstream**. The worst
  case of passing it is a bad PR.
- Semantic anomaly checks (empty diffs, vacuous tests, contradictory reviews) should be
  **sensitive, not specific**. A check that occasionally flags good output is preferable
  to one that occasionally passes bad output.

### Sting Operations (Future, Not MVP)

To calibrate the detector's recall over time, the system can run **sting operations**:
intentionally introduce changes that appear correct on the surface but contain genuine
problems, then submit them through the normal review pipeline. These are not synthetic
tests — they are real problematic changes routed through production-grade review.

If the review pipeline catches them: the detector is working. If it doesn't: the
detection policies need tightening.

This mechanism addresses the fundamental calibration problem: you cannot measure recall
without known-positive examples, and organic true positives are rare enough that the
sample size is too small for statistical confidence.

**Not in MVP scope.** The escalation policy is designed to accommodate this mechanism
when it is built, but no sting infrastructure is required for initial deployment.

### Graceful Degradation: Partial Results vs. Full Stop

When a failure occurs mid-DAG, completed work has value and should be preserved:

- Research node failed, nothing downstream ran: full stop (no useful partial output).
- Code node failed after research succeeded: escalate with research findings attached.
- Test node failed after code succeeded: escalate with diff attached; human can review
  the code manually.
- Review node failed after tests passed: deliver the PR; human review is the natural
  fallback for a failed review agent.

## Policy

### 1. Escalation Triggers

The orchestrator MUST escalate to the human when any of the following conditions are met:

**1.1 Retry exhaustion.** A DAG node reaches `dead_letter` state (max retries exhausted
with context enrichment). This is the primary escalation trigger.

**1.2 Budget exhaustion.** The DAG run's remaining token budget is insufficient to
dispatch the next required node, and the DAG cannot complete with the remaining budget.

**1.3 Critical-path blockage.** A node on the critical path has failed (even on first
attempt) with a `DETERMINISTIC` error type -- an error that retries cannot fix (e.g.,
file not found, permission denied, invalid configuration).

**1.4 Safety violation.** An agent attempted an action outside its permission profile
(blocked git flag, out-of-scope file write, unauthorized network access). Escalate
immediately; do not retry.

**1.5 Semantic anomaly.** A node completed "successfully" but its output fails a
semantic sanity check:
  - Implementation node produced an empty diff
  - Test node produced zero test assertions
  - Review node approved code that the test node marked as failing
  - Any node's output contradicts its input requirements

**1.6 DAG planning failure.** The planner itself fails to produce a valid DAG, or
produces a DAG that the orchestrator determines is malformed (cycles, missing
dependencies, nodes referencing non-existent agent types).

The orchestrator SHOULD NOT escalate for:
- Transient infrastructure errors within retry budget (API rate limits, network timeouts)
- Agent format errors on first attempt (hallucinated tool calls, invalid output schema)
  — these are retried with context enrichment first

Note: non-critical-path `dead_letter` nodes are logged but do NOT silently continue.
Their outputs are flagged in the final PR description so the human reviewer has full
visibility. The principle is: a flawed result that spends budget is acceptable; a flawed
result that ships silently is not.

### 2. Escalation Decision Tree

When a node fails or an anomaly is detected, the orchestrator follows this decision
tree:

```
Node failure or anomaly detected
    |
    +-- Is it a safety violation (trigger 1.4)?
    |     YES --> ESCALATE immediately (severity: CRITICAL)
    |             Action: pause entire DAG, notify human
    |
    +-- Is it a deterministic error (trigger 1.3)?
    |     YES --> Is the node on the critical path?
    |               YES --> ESCALATE (severity: HIGH)
    |               NO  --> Mark dead_letter, skip dependents, continue DAG
    |
    +-- Is it a semantic anomaly (trigger 1.5)?
    |     YES --> ESCALATE (severity: HIGH)
    |             Action: pause DAG, include anomaly details
    |
    +-- Are retries remaining?
    |     YES --> Retry with context enrichment
    |             (do NOT escalate yet)
    |
    +-- Retries exhausted (trigger 1.1)?
    |     YES --> Is the node on the critical path?
    |               YES --> ESCALATE (severity: MEDIUM)
    |               NO  --> Mark dead_letter, evaluate partial delivery
    |
    +-- Budget exhausted (trigger 1.2)?
          YES --> ESCALATE (severity: MEDIUM)
                  Action: present partial results + budget breakdown
```

### 3. Escalation Severity Levels

| Level | Meaning | DAG State | Human Response Time Expectation |
|-------|---------|-----------|-------------------------------|
| CRITICAL | Safety violation or data integrity risk | Paused, no further execution | Immediate (blocks all work) |
| HIGH | Critical-path failure or semantic anomaly | Paused at failed node | Within the session (minutes) |
| MEDIUM | Retry exhaustion or budget exhaustion | Paused, partial results preserved | At convenience (hours OK) |
| LOW | Non-critical dead_letter, informational | DAG continues, notification sent | Async review (next session) |

### 4. Escalation Communication Format

Every escalation MUST be delivered as a structured message via the configured channel
(CLI prompt for MVP, GitHub issue comment for async mode). The format is:

```
=== ESCALATION: {severity} ===

Issue:    #{issue_number} -- {issue_title}
DAG Run:  {dag_run_id}
Node:     {node_id} ({agent_type})
Trigger:  {trigger_description}

--- What Happened ---
{concise_description_of_failure}

--- Attempt History ---
Attempt 1: {error_type} -- {error_summary}
Attempt 2: {error_type} -- {error_summary} (with context enrichment)
...

--- DAG Impact ---
Completed nodes: {list_of_completed_nodes_with_status}
Blocked nodes:   {list_of_nodes_that_cannot_proceed}
Partial results:  {summary_of_usable_outputs}

--- Budget Status ---
Used: {tokens_used} / {tokens_budget} ({percentage}%)
Estimated remaining cost: {estimate_if_resumed}

--- Options ---
[r] Retry the failed node (with optional guidance: "retry {node_id}")
[s] Skip this node and continue (available only if non-critical-path)
[m] Modify the DAG plan
[g] Provide guidance (text input appended to agent context)
[b] Increase budget by N tokens
[a] Abort DAG run (triggers compensation/cleanup)
[p] Accept partial results and create PR with what we have
```

### 5. Partial Result Handling

When an escalation occurs, the orchestrator MUST preserve and present all usable
partial results:

**5.1** Completed nodes' outputs are never discarded. They are stored in the state
store and summarized in the escalation message.

**5.2** If the human chooses "accept partial results" (`[p]`), the orchestrator:
  - Collects outputs from all completed nodes
  - Creates a PR with available code changes (if any implementation nodes completed)
  - Adds a PR comment documenting which nodes failed and what work remains
  - Labels the PR with `partial` and `needs-human-completion`

**5.3** The PR description for partial results MUST include:
  - What was completed automatically
  - What was not completed and why
  - Specific guidance for the human on what remains

**5.4** If no code changes were produced (e.g., only research completed), the
orchestrator posts the research findings as a comment on the original issue rather
than creating an empty PR.

### 6. Escalation Channel Configuration

```python
class EscalationConfig(BaseModel):
    channel: Literal["cli", "github_comment", "webhook"] = "cli"
    webhook_url: str | None = None
    github_mention: str | None = None  # @username to mention on GH comments
    timeout_seconds: int = 300         # For CLI: how long to wait for response
    timeout_action: Literal["abort", "accept_partial"] = "abort"
    severity_filter: EscalationSeverity = EscalationSeverity.LOW  # Min severity to escalate
```

**6.1** In CLI mode (MVP), escalations block execution and prompt the user directly.
A timeout of 5 minutes (configurable) applies; if exceeded, the `timeout_action`
determines behavior.

**6.2** In GitHub comment mode (async), the orchestrator posts the escalation as a
comment on the issue, mentions the configured user, and pauses the DAG. The DAG can
be resumed via a webhook or API call when the human responds.

**6.3** The `severity_filter` allows suppressing LOW-severity escalations for
experienced users who prefer minimal interruption. LOW-severity events are still
logged to the event store.

### 7. Anti-Fatigue Measures

Escalation fatigue is real, but the response is to fix the causes, not suppress the
symptoms. These measures manage operational noise while preserving recall.

**7.1 Batch non-urgent escalations.** If multiple non-critical nodes fail in the same
DAG run, batch them into a single escalation message rather than interrupting the
human once per node.

**7.2 Rate-limit escalations.** No more than one MEDIUM-severity escalation per DAG
run within a 60-second window. If multiple triggers fire in rapid succession,
consolidate them.

**7.3 Every escalation is a policy improvement signal.** Track the human's response to
each escalation (retry, skip, abort, guidance). If the human consistently skips a
particular class of failure, that class should be handled by policy — update the agent
prompts, retry logic, or CLAUDE.md so the system handles it autonomously next time.
The goal is to eliminate the escalation category, not to suppress it.

**7.4 Never escalate for transient infrastructure errors within retry budget.** API
rate limits and network timeouts are handled silently by the retry policy. This is the
only class of event that is unconditionally suppressed.

### 8. Post-Escalation Tracking

**8.1** Every escalation event is recorded in the `node_events` table with
`event_type = 'escalated'` and a payload containing the full escalation message,
severity, and trigger.

**8.2** The human's response is recorded with `event_type = 'escalation_response'`
and a payload containing the chosen action and any guidance text.

**8.3** Time-to-response is tracked for each escalation. This metric informs timeout
configuration and helps identify whether escalations are being addressed or ignored.

**8.4** Escalation outcomes (did the human's intervention lead to successful DAG
completion?) are tracked to measure whether escalations are adding value or just adding
delay.

## Rationale

This policy fills the gap between the error-handling doc (which defines retries and
dead-letter states) and the human-checkpoints doc (which defines proactive approval
gates). Escalation is the reactive counterpart: the system has tried to handle a
problem autonomously and failed, and now needs human judgment.

**Recall-first design.** The decision tree is biased toward escalation when outcomes are
uncertain. The cost asymmetry — budget-spend vs. shipped-bad-code — means the optimal
operating point is high recall with tolerable false-positive rate, not the balanced
precision/recall point that traditional alert systems target.

**Every escalation is a policy bug.** The anti-fatigue measures (batching, rate-limiting,
filtering) are necessary for operational sanity, but they are band-aids. The real
anti-fatigue strategy is to treat each escalation as a signal that a policy, prompt, or
agent boundary needs improvement. Over time, the escalation rate should decrease not
because alerts are suppressed, but because the system handles more cases autonomously.

**Partial results preserve value.** A PR with 80% of the implementation is more useful
than no PR at all, provided the human knows exactly what is missing. Completed node
outputs are never discarded.

**Sting-ready architecture.** The escalation tracking infrastructure (event logging,
outcome measurement, recall metrics) is designed so that sting operations can be layered
on without structural changes. When stings are implemented, the same event pipeline that
records organic escalations will record sting results, enabling direct recall
measurement.
