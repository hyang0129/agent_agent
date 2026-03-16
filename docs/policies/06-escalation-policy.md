# Policy 06: Escalation Policy

The system's contract is "issue in, PR out" with minimal human involvement between those two points — but when autonomous resolution fails, the orchestrator escalates through a structured decision tree rather than silently continuing with bad output. Escalation is optimized for recall over precision: it is better to escalate a borderline case (wasting tokens) than to silently ship bad code (unbounded downstream cost). Every escalation is treated as evidence of a policy gap that should be closed, not a feature to be tuned away. Non-critical partial results are preserved and delivered even when the run cannot complete.

---

### 1. Escalation Triggers

The orchestrator MUST escalate to the human when any of the following conditions are met:

**1.1 Retry exhaustion.** A DAG node reaches `dead_letter` state (max retries exhausted with context enrichment).

**1.2 Budget exhaustion.** The DAG run's remaining token budget is insufficient to dispatch the next required node.

**1.3 Critical-path blockage.** A node on the critical path has failed with a `DETERMINISTIC` error type — an error that retries cannot fix (e.g., file not found, permission denied, invalid configuration).

**1.4 Safety violation.** An agent attempted an action outside its permission profile. Escalate immediately; do not retry.

**1.5 Semantic anomaly.** A node completed "successfully" but its output fails a semantic sanity check:
  - Implementation node produced an empty diff
  - Test node produced zero test assertions
  - Review node approved code that the test node marked as failing
  - Any node's output contradicts its input requirements

**1.6 DAG planning failure.** The planner fails to produce a valid DAG, or produces a DAG that is malformed (cycles, missing dependencies, nodes referencing non-existent agent types).

The orchestrator SHOULD NOT escalate for:
- Transient infrastructure errors within retry budget (API rate limits, network timeouts)
- Agent format errors on first attempt — these are retried with context enrichment first

Non-critical-path `dead_letter` nodes are logged but do NOT silently continue. Their outputs are flagged in the final PR description.

### 2. Escalation Decision Tree

```
Node failure or anomaly detected
    |
    +-- Is it a safety violation (trigger 1.4)?
    |     YES --> ESCALATE immediately (severity: CRITICAL)
    |
    +-- Is it a deterministic error (trigger 1.3)?
    |     YES --> Is the node on the critical path?
    |               YES --> ESCALATE (severity: HIGH)
    |               NO  --> Mark dead_letter, skip dependents, continue DAG
    |
    +-- Is it a semantic anomaly (trigger 1.5)?
    |     YES --> ESCALATE (severity: HIGH)
    |
    +-- Are retries remaining?
    |     YES --> Retry with context enrichment (do NOT escalate yet)
    |
    +-- Retries exhausted (trigger 1.1)?
    |     YES --> Is the node on the critical path?
    |               YES --> ESCALATE (severity: MEDIUM)
    |               NO  --> Mark dead_letter, evaluate partial delivery
    |
    +-- Budget exhausted (trigger 1.2)?
          YES --> ESCALATE (severity: MEDIUM)
```

### 3. Escalation Severity Levels

| Level | Meaning | DAG State | Human Response Time |
|-------|---------|-----------|---------------------|
| CRITICAL | Safety violation or data integrity risk | Paused, no further execution | Immediate |
| HIGH | Critical-path failure or semantic anomaly | Paused at failed node | Within the session (minutes) |
| MEDIUM | Retry exhaustion or budget exhaustion | Paused, partial results preserved | At convenience (hours OK) |
| LOW | Non-critical dead_letter, informational | DAG continues, notification sent | Async review (next session) |

### 4. Escalation Communication Format

Every escalation MUST be delivered as a structured message via the configured channel (CLI prompt for MVP, GitHub issue comment for async mode):

```
=== ESCALATION: {severity} ===

Issue:    #{issue_number} — {issue_title}
DAG Run:  {dag_run_id}
Node:     {node_id} ({agent_type})
Trigger:  {trigger_description}

--- What Happened ---
{concise_description_of_failure}

--- Attempt History ---
Attempt 1: {error_type} — {error_summary}
Attempt 2: {error_type} — {error_summary} (with context enrichment)

--- DAG Impact ---
Completed nodes: {list_of_completed_nodes_with_status}
Blocked nodes:   {list_of_nodes_that_cannot_proceed}
Partial results: {summary_of_usable_outputs}

--- Budget Status ---
Used: {tokens_used} / {tokens_budget} ({percentage}%)

--- Options ---
[r] Retry the failed node
[s] Skip this node (non-critical-path only)
[m] Modify the DAG plan
[g] Provide guidance
[b] Increase budget by N tokens
[a] Abort DAG run
[p] Accept partial results and create PR with what we have
```

### 5. Partial Result Handling

**5.1** Completed nodes' outputs are never discarded.

**5.2** If the human chooses "accept partial results" (`[p]`), the orchestrator:
  - Collects outputs from all completed nodes
  - Creates a PR with available code changes
  - Adds a PR comment documenting which nodes failed and what work remains
  - Labels the PR with `partial` and `needs-human-completion`

**5.3** The PR description for partial results MUST include:
  - What was completed automatically
  - What was not completed and why
  - Specific guidance for the human on what remains

**5.4** If no code changes were produced, the orchestrator posts research findings as a comment on the original issue rather than creating an empty PR.

### 6. Escalation Channel Configuration

```python
class EscalationConfig(BaseModel):
    channel: Literal["cli", "github_comment", "webhook"] = "cli"
    webhook_url: str | None = None
    github_mention: str | None = None
    timeout_seconds: int = 300
    timeout_action: Literal["abort", "accept_partial"] = "abort"
    severity_filter: EscalationSeverity = EscalationSeverity.LOW
```

**6.1** In CLI mode (MVP), escalations block execution and prompt the user. A 5-minute timeout applies.

**6.2** In GitHub comment mode (async), the orchestrator posts the escalation and pauses the DAG.

**6.3** The `severity_filter` allows suppressing LOW-severity escalations. LOW events are still logged.

### 7. Anti-Fatigue Measures

**7.1 Batch non-urgent escalations.** If multiple non-critical nodes fail in the same run, batch them into a single escalation message.

**7.2 Rate-limit escalations.** No more than one MEDIUM-severity escalation per DAG run within a 60-second window.

**7.3 Every escalation is a policy improvement signal.** Track the human's response to each escalation. If the human consistently skips a particular class of failure, that class should be handled by policy. The goal is to eliminate the escalation category, not suppress it.

**7.4 Never escalate for transient infrastructure errors within retry budget.**

### 8. Post-Escalation Tracking

**8.1** Every escalation event is recorded in the `node_events` table with `event_type = 'escalated'`.

**8.2** The human's response is recorded with `event_type = 'escalation_response'`.

**8.3** Time-to-response is tracked for each escalation.

**8.4** Escalation outcomes (did the human's intervention lead to successful DAG completion?) are tracked to measure whether escalations are adding value.
