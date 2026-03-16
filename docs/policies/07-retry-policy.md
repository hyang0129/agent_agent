# 07 — Retry Policy

## Background / State of the Art

Retries are deceptively simple. A naive `for i in range(3)` loop around an LLM call will burn tokens repeating the same mistake. Effective retry policy requires answering three questions: *what* failed, *why* it failed, and *what to change* on the next attempt.

### When Self-Correction Works (and When It Doesn't)

**Reflexion (Shinn et al., NeurIPS 2023)** demonstrated that LLM agents can improve across attempts when given verbal feedback about prior failures stored in episodic memory. On coding tasks (HumanEval), Reflexion improved pass rates by 11% over multiple trials. The key mechanism: the agent receives a natural-language reflection on *why* the previous attempt failed, not just the raw error.

**Self-Debugging (Chen et al., ICLR 2024)** showed that LLMs can fix their own code when given execution feedback (error messages, failing test output, execution traces). Simple "try again" prompts without feedback showed negligible improvement. Execution traces — concrete evidence of what went wrong — produced consistent gains. The implication for agent retries: the *quality of error context* provided on retry determines whether the retry is useful or wasteful.

**Huang et al. (ICLR 2024), "Large Language Models Cannot Self-Correct Reasoning Yet"** established an important constraint: *intrinsic* self-correction (asking an LLM to reconsider its answer without external signal) can actually degrade performance. LLMs second-guess correct answers as often as they fix incorrect ones. Self-correction only reliably works when grounded in external feedback — test failures, type errors, API error messages, tool output.

**Practical implication for agent_agent:** Retries must always provide concrete external feedback (error messages, test output, validation failures). Never retry with a bare "try again" prompt. If no external signal is available to ground the retry, the failure should escalate rather than retry.

### How Leading Frameworks Handle Retries

**LangGraph** provides a declarative `RetryPolicy` per node with exponential backoff, jitter, and a predicate function that determines which exceptions are retryable. Multiple policies can be attached to a single node for different exception types. When retries are exhausted, the error is persisted in the checkpoint for inspection. LangGraph also supports "fallback edges" — alternative graph paths when a node fails terminally.

**AutoGen** relies on agent-level retry logic. Agents can inspect their own message history to detect repeated failures. Debugging is supported through message logs and mid-execution intervention, but there is no built-in DAG-level retry or checkpoint-based recovery.

**CrewAI** provides a `max_retry_limit` per agent (default: 2). If an agent fails, the framework retries the entire agent invocation. There is no sub-task recovery or context enrichment on retry — it is effectively a blind retry, which the research above shows is the least effective strategy.

### Diminishing Returns

Research consistently shows diminishing returns on retries:

- **Attempt 2** (first retry) captures the majority of recoverable failures — transient errors resolve, and agents given error context self-correct most fixable mistakes.
- **Attempt 3** catches a smaller tail — cases where the first retry produced a different but still incorrect result, and the second retry with accumulated context succeeds.
- **Attempts 4+** rarely succeed if attempts 2–3 failed. By this point, the failure is likely structural (wrong approach, missing capability, ambiguous requirements) rather than incidental. Additional retries waste tokens and delay escalation.

This pattern holds across the Reflexion results, Self-Debugging benchmarks, and production agent systems. The default policy should reflect this: 2–3 attempts total, then escalate.

---

## Policy

### 1. Failure Classification

Every agent failure MUST be classified into one of the following categories before a retry decision is made:

| Category | Definition | Examples | Action |
|---|---|---|---|
| **Transient** | External, time-dependent failure likely to resolve on retry | API rate limit (429), network timeout, connection reset, temporary GitHub API outage | Retry with exponential backoff |
| **Agent Logic** | The agent produced output, but it was incorrect or malformed | Output failed Pydantic validation, hallucinated file path, invalid diff format, test assertion failure in generated code | Retry with error context enrichment |
| **Deterministic** | The failure will recur on every attempt regardless of retry | Authentication error (401/403), file/repo not found, invalid configuration, permission denied | Fail immediately, escalate |
| **Budget** | Token or cost limit reached | Node budget exceeded, DAG budget exceeded | Fail immediately, preserve partial results |
| **Unknown** | Unclassified exception | Unexpected exception types | Retry once with context, then escalate |

Classification is performed by the executor's `classify_error()` function (see `error-handling-and-recovery.md`). New error types MUST be added to the classifier, not treated as Unknown long-term.

### 2. Retry Limits

| Agent Type | Max Attempts | Rationale |
|---|---|---|
| Research | 3 | Read-only, low side-effect risk, benefits from iterative search refinement |
| Implement | 2 | Side effects (commits), higher token cost per attempt; structural failures unlikely to resolve with more attempts |
| Test | 3 | Test failures provide clear external signal (pass/fail + output), making error-context retries effective |
| Review | 2 | Subjective output; repeated review of the same code yields diminishing returns |
| Planner | 2 | Planning errors are usually structural (misunderstood issue scope); retrying with the same inputs rarely helps |

These limits represent *total attempts* (initial + retries). A limit of 3 means 1 initial attempt + 2 retries.

### 3. Backoff Strategy

Retries MUST use exponential backoff with jitter for **transient** failures:

```
wait = min(initial_backoff * (multiplier ^ attempt), max_backoff) + random_jitter
```

Defaults:
- `initial_backoff`: 2 seconds
- `multiplier`: 2.0
- `max_backoff`: 60 seconds
- `jitter`: uniform random 0–1 second

**Agent logic** failures do NOT use time-based backoff (the failure is not time-dependent). They retry immediately with enriched context.

### 4. Context Enrichment on Retry (Mandatory)

Every retry MUST include error context from the prior attempt. Blind retries are prohibited.

The retry prompt MUST contain:

1. **What failed:** The error category and a one-line summary.
2. **Concrete evidence:** The actual error message, failing test output, validation error detail, or API error body. Not a paraphrase — the raw signal.
3. **Attempt history:** Which attempt this is (e.g., "This is attempt 2 of 3").
4. **Prior output (when relevant):** For agent-logic failures, include the relevant portion of the prior attempt's output so the agent can see what it produced and correct it. Truncate to the failing section, not the full output.

Template:

```
## Retry Context (Attempt {n} of {max})

Your previous attempt failed.

**Error type:** {category}
**Error detail:**
```
{raw_error_message_or_test_output}
```

**Your previous output (relevant section):**
```
{truncated_prior_output}
```

Adjust your approach based on the above. Do not repeat the same mistake.
```

For **transient** failures, context enrichment is optional (the retry is expected to succeed because the external condition resolved, not because the agent changed its approach).

### 5. Reflection Before Retry (Agent Logic Failures Only)

For agent-logic failures where the error context alone may be insufficient, the orchestrator MAY invoke a lightweight reflection step before retrying. This is the Reflexion pattern adapted for single-turn use:

1. Send the prior attempt's output + error to the same model with a reflection prompt: *"Analyze why this attempt failed and describe specifically what should change."*
2. Append the reflection output to the retry prompt as an additional `## Reflection` section.
3. This costs one additional (small) LLM call but significantly improves retry success rate for complex failures.

Reflection is NOT used for:
- Transient failures (no agent-side change needed)
- Simple validation failures where the error message is self-explanatory (e.g., "field `summary` is required")
- Budget-constrained nodes where the extra LLM call is not justified

The orchestrator decides whether to use reflection based on the error complexity and remaining budget.

### 6. Idempotency on Retry

Agents that produce side effects (branch creation, commits, PR comments) MUST be idempotent. On retry:

- Check if the branch already exists before creating it.
- Check if an equivalent commit already exists before committing.
- Check if a comment with the same content already exists before posting.

The executor MUST pass a `prior_side_effects` field to the agent on retry, listing side effects from previous attempts, so the agent can skip or update rather than duplicate.

### 7. Escalation After Max Retries

When all retry attempts are exhausted:

1. Transition the node to `dead_letter` state.
2. Record the full attempt history (all inputs, outputs, errors, token usage) in the event log.
3. Evaluate downstream impact:
   - If no downstream nodes depend on this node's output, mark it as `failed` and continue the DAG.
   - If downstream nodes exist but can operate with degraded input (e.g., review can proceed without one of several implementation nodes), continue with a warning.
   - If downstream nodes cannot proceed, halt the affected subtree and escalate.
4. Post a GitHub issue comment summarizing: what was attempted, how many times, what failed, and what the human should look at.
5. The human can then: fix the issue and trigger a re-run of the failed node, skip the node and unblock dependents manually, or abort the DAG.

### 8. Per-Node Retry Policy Override

The planner MAY override default retry limits for specific nodes when decomposing an issue. For example, a node that the planner identifies as high-risk or exploratory may receive `max_attempts: 1` (no retries — fail fast and escalate). A node performing a well-understood transformation may receive `max_attempts: 3`.

Overrides are recorded in the DAG definition and logged for auditability.

### 9. Retry Budget Accounting

Each retry attempt consumes budget. The orchestrator MUST:

- Check remaining node-level and DAG-level budget before dispatching a retry.
- If the estimated cost of the retry exceeds remaining budget, skip the retry and transition directly to `dead_letter` with reason `budget_exhausted`.
- Include all retry attempts in the cost summary reported to the user.

Retries are not "free" second chances — they are budgeted work.

### 10. Logging and Observability

Every retry MUST be logged with:

- Node ID, DAG run ID, attempt number
- Error classification
- Whether context enrichment was applied
- Whether reflection was used
- Token usage for the retry attempt
- Outcome (success, failed again, escalated)

This data feeds into post-hoc analysis to tune retry limits and identify patterns (e.g., "implement agents never recover from X error type on retry — reclassify as deterministic").

---

## Rationale

This policy is shaped by three research findings and one operational reality:

1. **External feedback is required for effective self-correction.** Huang et al. showed that intrinsic self-correction (retry without new information) degrades performance. Every retry in this system provides concrete external signal — error messages, test output, validation details. This is what makes retries productive rather than wasteful.

2. **Diminishing returns are steep after attempt 2–3.** The Reflexion and Self-Debugging results both show the majority of recoverable failures are caught in the first retry. Our default limits (2–3 total attempts) reflect this empirical ceiling. Going beyond 3 attempts is almost always wasted budget.

3. **Error classification determines retry strategy.** Not all failures are equal. Transient failures need backoff; logic failures need context; deterministic failures need immediate escalation. The classification table prevents the most common anti-pattern: retrying a deterministic failure N times and then failing anyway.

4. **GitHub issue resolution has natural escalation paths.** Unlike autonomous systems that must solve every problem or fail entirely, agent_agent operates in a human-in-the-loop context. The cost of escalating a hard failure to the developer is low — a GitHub comment with full context. The cost of burning tokens on hopeless retries is high. The policy is deliberately biased toward fast escalation over aggressive retry.
