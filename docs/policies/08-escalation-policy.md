# Escalation Policy

## Background / State of the Art

### The Problem

Agent Agent's error-handling doc states that "persistent failures escalate to human" but
does not define when, how, or through what channel. Without a concrete escalation policy,
the system either escalates too often (causing human fatigue and eroding trust in
autonomy) or too rarely (causing silent failures that waste budget and produce broken
PRs). Both failure modes are well-documented in production agent systems and analogous
domains like security operations centres.

### Research: When Should an Agent Escalate?

The literature on human-in-the-loop AI systems converges on a set of escalation triggers
that apply across domains (SOC alert triage, medical AI, customer service agents, and
code-generation orchestrators):

**1. Confidence thresholds.** An agent should escalate when its confidence in a decision
drops below a calibrated threshold. In practice this means the orchestrator (not the
agent itself) evaluates structured signals: Did the agent's output pass validation? Did
it hedge or express uncertainty? Did it produce a result that contradicts upstream
context? The threshold must be tuned per agent type -- research agents have inherently
lower confidence on ambiguous issues than test agents running deterministic checks.

**2. Retry exhaustion.** After max retries with context enrichment, the agent has
demonstrated that it cannot self-correct. Continuing to retry wastes budget and delays
the human review that was always going to be needed. The error-handling doc already
defines max retries per agent type (2-3); escalation is the mandatory next step after
`dead_letter`.

**3. Critical-path failure.** Not all node failures are equal. A failed node on the
critical path (one with downstream dependents that cannot proceed without its output)
is more urgent than a failed leaf node. The escalation urgency should reflect this.

**4. Budget exhaustion.** When a DAG run exhausts its token budget before completing,
the human must decide whether to extend the budget, accept partial results, or abort.
The system cannot make this call autonomously.

**5. Semantic failure.** The agent "succeeded" (returned a valid result) but the result
is semantically suspect: empty diff, test suite that tests nothing, review that approves
obviously broken code. These are harder to detect but critical to escalate because they
represent silent failures that propagate downstream.

**6. Safety-boundary violations.** Any attempt by an agent to exceed its permissions,
access blocked paths, or use blocked git flags should be escalated immediately, as it
may indicate prompt injection or a fundamental misunderstanding of the task.

### The Cost of Getting It Wrong

**False escalations (over-escalation)** cause alert fatigue. Research from security
operations shows that 62% of alerts are ignored when volume is high, and accuracy drops
40% after extended shifts (IBM, 2025). In our context: if the system escalates on every
transient API timeout or minor validation failure, the developer will stop reading
escalation messages. The system's escalations become noise.

**Missed escalations (under-escalation)** cause silent failures. The agent proceeds with
a flawed result, downstream agents build on it, and the developer discovers the problem
only at PR review -- after the entire budget has been spent. Worse, if the developer has
learned to trust the system, they may approve a broken PR (automation complacency, well-
documented in human-AI collaboration research: Springer, 2025; Parasuraman & Manzey,
2010).

**The calibration goal:** escalate only when human judgment will change the outcome, and
provide enough context that the human can act quickly without re-investigating from
scratch.

### How Leading Frameworks Handle Escalation

**LangGraph** uses `interrupt()` to pause graph execution and return control to the
caller. The graph state is fully serialized, so the pause can last indefinitely. Recent
patterns include adaptive interrupts (escalate based on confidence), hierarchical
approval (route to different reviewers by risk level), and timeout-with-default (proceed
if no human response within N minutes).

**AutoGen** supports three human-input modes (`NEVER`, `TERMINATE`, `ALWAYS`) and a
`handoff` mechanism for permanent escalation to a human or specialist agent. The handoff
pattern transfers full context and responsibility.

**CrewAI** uses `human_input=True` per task and a hierarchical process where a manager
agent can reassign failed tasks. When the manager hits its delegation limit, it returns
partial results.

**Common pattern across all three:** escalation is a state transition, not a crash. The
system preserves its state, packages context for the human, and waits. The human can
approve, modify, redirect, or abort. The system resumes from where it paused.

### Graceful Degradation: Partial Results vs. Full Stop

The software engineering principle of graceful degradation applies directly: a system
that provides partial value under failure is better than one that provides nothing. In
our context, if 3 of 5 DAG nodes completed successfully before a failure, those results
(research findings, partial code changes, test results) have value and should be
preserved and presented.

The decision between "deliver partial results" and "full stop" depends on which node
failed and what the partial results represent:

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

The orchestrator MUST NOT escalate for:
- Transient errors that are within retry budget (API rate limits, network timeouts)
- Agent logic errors on first or second attempt (hallucinated tool calls, invalid output
  format) -- these are retried with context enrichment first
- Nodes that fail but have no downstream dependents (log the failure, mark as
  `dead_letter`, continue)

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

To prevent escalation fatigue:

**7.1 Batch non-urgent escalations.** If multiple non-critical nodes fail in the same
DAG run, batch them into a single escalation message rather than interrupting the
human once per node.

**7.2 Rate-limit escalations.** No more than one MEDIUM-severity escalation per DAG
run within a 60-second window. If multiple triggers fire in rapid succession,
consolidate them.

**7.3 Escalation history informs future runs.** Track the human's response to each
escalation (retry, skip, abort, guidance). If the human consistently skips a
particular class of failure, surface this pattern and suggest adjusting retry policies
or checkpoint levels.

**7.4 Never escalate for transient errors within retry budget.** This is the single
most important anti-fatigue rule. API rate limits and network timeouts are handled
silently by the retry policy.

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

The decision tree is designed to minimize false escalations (the primary driver of human
fatigue and automation distrust) while ensuring that critical failures, safety
violations, and budget exhaustion always reach a human. The severity levels map to the
developer's workflow: CRITICAL and HIGH interrupt immediately because the cost of delay
exceeds the cost of interruption; MEDIUM and LOW are async because the human can batch
their review without risk.

The partial-results policy reflects the principle that completed work has value even when
the full DAG cannot finish. A PR with 80% of the implementation is more useful than no
PR at all, provided the human knows exactly what is missing.

The anti-fatigue measures draw directly from alert-fatigue research in security
operations, where high-volume, low-signal alerts cause operators to ignore critical
events. By batching, rate-limiting, and filtering escalations, the system keeps its
signal-to-noise ratio high enough that developers continue to trust and act on
escalation messages.
