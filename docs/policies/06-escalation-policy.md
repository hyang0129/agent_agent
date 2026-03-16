# Policy 06: Escalation Policy

The system's contract is "issue in, PR out" with minimal human involvement between those two points — but when autonomous resolution fails, the orchestrator escalates through a structured decision tree rather than silently continuing with bad output. Because every node at every nesting level feeds into the terminal Plan composite, all nodes are on the critical path: there is no such thing as a non-critical-path node. Escalation is optimized for recall over precision: it is better to escalate a borderline case than to silently ship bad code. Every escalation is treated as evidence of a policy gap that should be closed. Non-critical partial results are preserved and delivered even when the run cannot complete.

---

### P6.1 Escalation triggers

The orchestrator MUST escalate to the human when any of the following conditions are met:

**P6.1a Retry exhaustion.** A DAG node reaches `dead_letter` state (max retries exhausted with context enrichment).

**P6.1b Budget exhaustion.** After stage-aware evaluation [P7.6] determines that continuing is not worthwhile, the remaining token budget is insufficient to dispatch the next required node.

**P6.1c Deterministic error.** A node has failed with a `DETERMINISTIC` error type — an error that retries cannot fix (e.g., file not found, permission denied, invalid configuration).

**P6.1d Safety violation.** An agent attempted an action outside its permission profile. Escalate immediately; do not retry under any circumstances.

**P6.1e Semantic anomaly.** A node completed "successfully" but its output fails a semantic sanity check:
  - Implementation node produced an empty diff.
  - Test node produced zero test assertions.
  - Review node approved code that the test node marked as failing.
  - Any node's output contradicts its input requirements.

**P6.1f DAG planning failure.** The Plan composite fails to produce a valid DAG, or produces a DAG that is malformed (cycles, missing dependencies, nodes referencing non-existent agent types).

**P6.1g Depth limit reached.** A Plan composite at depth 4 determines more work is needed but cannot spawn a child DAG [P1.10].

The orchestrator SHOULD NOT escalate for:
- Transient infrastructure errors within retry budget (API rate limits, network timeouts).
- Agent format errors on first attempt — these are retried with context enrichment first.

### P6.2 Escalation decision tree

```
Node failure or anomaly detected
    |
    +-- Is it a safety violation [P6.1d]?
    |     YES --> ESCALATE immediately (CRITICAL)
    |
    +-- Is it a semantic anomaly [P6.1e]?
    |     YES --> ESCALATE (HIGH)
    |
    +-- Is it a deterministic error [P6.1c]?
    |     YES --> ESCALATE (HIGH)
    |
    +-- Is it a depth limit reached [P6.1g]?
    |     YES --> ESCALATE (HIGH)
    |
    +-- Are retries remaining?
    |     YES --> Retry with context enrichment (do NOT escalate yet)
    |
    +-- Retries exhausted [P6.1a]?
    |     YES --> ESCALATE (MEDIUM)
    |
    +-- Budget exhausted after stage-aware evaluation [P6.1b]?
          YES --> ESCALATE (MEDIUM)
```

### P6.3 Escalation severity levels

| Level | Meaning | DAG State | Human Response Time |
|-------|---------|-----------|---------------------|
| CRITICAL | Safety violation | Paused, no further execution | Immediate |
| HIGH | Deterministic error, semantic anomaly, or depth limit reached | Paused at failed node | Within the session (minutes) |
| MEDIUM | Retry exhaustion or budget exhaustion | Paused, partial results preserved | At convenience (hours OK) |
| LOW | Advisory: budget warning, informational anomaly | DAG continues, notification sent | Async review (next session) |

### P6.4 Escalation communication format

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
[s] Skip this node
[m] Modify the DAG plan
[g] Provide guidance
[b] Increase budget by N tokens
[a] Abort DAG run
[p] Accept partial results and create PR with what we have
```

### P6.5 Partial result handling

**P6.5a** Completed nodes' outputs are never discarded.

**P6.5b** If the human chooses "accept partial results" (`[p]`), the orchestrator:
  - Collects outputs from all completed nodes.
  - Creates a PR with available code changes.
  - Adds a PR comment documenting which nodes failed and what work remains.
  - Labels the PR with `partial` and `needs-human-completion`.

**P6.5c** The PR description for partial results MUST include:
  - What was completed automatically.
  - What was not completed and why.
  - Specific guidance for the human on what remains.

**P6.5d** If no code changes were produced, the orchestrator posts research findings as a comment on the original issue rather than creating an empty PR.

### P6.6 Escalation channel configuration

```python
class EscalationConfig(BaseModel):
    channel: Literal["cli", "github_comment", "webhook"] = "cli"
    webhook_url: str | None = None
    github_mention: str | None = None
    timeout_seconds: int = 300
    timeout_action: Literal["abort", "accept_partial"] = "abort"
    severity_filter: EscalationSeverity = EscalationSeverity.LOW
```

**P6.6a** In CLI mode (MVP), escalations block execution and prompt the user. A 5-minute timeout applies.

**P6.6b** In GitHub comment mode (async), the orchestrator posts the escalation and pauses the DAG.

**P6.6c** The `severity_filter` allows suppressing LOW-severity escalations. LOW events are still logged.

### P6.7 Anti-fatigue measures

**P6.7a Batch non-urgent escalations.** If multiple nodes fail in the same run, batch them into a single escalation message.

**P6.7b Rate-limit escalations.** No more than one MEDIUM-severity escalation per DAG run within a 60-second window.

**P6.7c Every escalation is a policy improvement signal.** Track the human's response to each escalation. If the human consistently handles a particular class of failure the same way, that class should be handled by policy. The goal is to eliminate the escalation category, not suppress it.

**P6.7d Never escalate for transient infrastructure errors within retry budget.**

### P6.8 Post-escalation tracking

**P6.8a** Every escalation event is recorded in the `node_events` table with `event_type = 'escalated'`.

**P6.8b** The human's response is recorded with `event_type = 'escalation_response'`.

**P6.8c** Time-to-response is tracked for each escalation.

**P6.8d** Escalation outcomes (did the human's intervention lead to successful DAG completion?) are tracked to measure whether escalations are adding value.

---

### Violations

- Silently continuing after a semantic anomaly [P6.1e].
- Escalating for transient errors within retry budget.
- Delivering an unstructured escalation message (no attempt history, no DAG impact, no options menu).
- Discarding completed node outputs when a run fails.
- Retrying after a safety violation [P6.1d] — safety violations escalate immediately, no retry.
- Escalating for a deterministic error without first checking whether retries were exhausted (deterministic errors skip the retry step entirely).

### Quick Reference

| Trigger | Severity | Retry first? |
|---------|----------|-------------|
| Safety violation [P6.1d] | CRITICAL | Never |
| Semantic anomaly [P6.1e] | HIGH | No |
| Deterministic error [P6.1c] | HIGH | No |
| Depth limit reached [P6.1g] | HIGH | No |
| Retry exhaustion [P6.1a] | MEDIUM | N/A (retries are done) |
| Budget exhaustion [P6.1b] | MEDIUM | No |
| Transient error | — | Yes (up to retry budget) |
