# Cost & Token Budgets

## Problem Statement

Autonomous agents can consume API credits rapidly and unpredictably. A single poorly-scoped agent can enter a loop, retry excessively, or explore irrelevant context — burning through tokens with no useful output. Without budget controls, a misconfigured DAG or a single hallucinating agent can cost more than the entire project's monthly allocation in one run.

## State of the Art

### Anthropic Claude API Usage Controls
The Claude API provides token counts in every response (`input_tokens`, `output_tokens`). The `max_tokens` parameter caps output per request. There is no built-in server-side budget enforcement across multiple requests — the caller must track cumulative usage. Rate limits exist but are per-minute throughput caps, not budget caps.

### OpenAI Usage Tiers and Limits
OpenAI provides organization-level monthly spend limits configurable in the dashboard. Projects can have individual budget caps. API responses include token usage. This is the closest thing to a "native" budget system, but it operates at the organization/project level, not at the task/agent level.

### LangSmith / LangChain Callbacks
LangChain provides callback handlers that track token usage per chain/agent invocation. LangSmith (the observability platform) aggregates usage across runs. Budgets are not enforced natively — you must implement enforcement in your callback handler by raising an exception when the budget is exceeded.

### Helicone / Portkey / LLM Gateway Proxies
Third-party API proxies that sit between your application and the LLM provider. They track usage, enforce rate limits, and can set hard budget caps. Helicone can alert or block requests when a budget threshold is reached. These work at the HTTP level and are provider-agnostic.

**Key insight:** Budget enforcement at the proxy layer is more reliable than application-level enforcement because it catches all API calls, including those from misbehaving agents that might bypass application-level checks.

### CrewAI Budget Limiting
CrewAI added `max_rpm` (requests per minute) and cost tracking per agent. The framework tracks cumulative token usage and can stop execution when a limit is hit. However, enforcement is cooperative — agents that make direct API calls bypass it.

## Best Practices

### 1. Three-Level Budget Hierarchy

```
DAG-level budget (total for the entire issue)
  └── Node-level budget (per subtask)
       └── Request-level budget (per API call via max_tokens)
```

Each level acts as a circuit breaker. A single agent can't exceed its node budget, and the total of all node budgets can't exceed the DAG budget.

### 2. Budget-Aware Planning

The planner should estimate costs before execution:

```python
class SubTask(BaseModel):
    description: str
    agent_type: AgentType
    estimated_input_tokens: int
    estimated_output_tokens: int
    max_retries: int

    @property
    def estimated_cost(self) -> float:
        return calculate_cost(
            self.estimated_input_tokens,
            self.estimated_output_tokens,
            self.max_retries
        )
```

If the total estimated cost exceeds the DAG budget, the planner can simplify the DAG, reduce retries, or ask the human before proceeding.

### 3. Real-Time Tracking, Not Post-Hoc

Track usage in real time, not after the DAG completes:

```python
class BudgetTracker:
    def __init__(self, limit_tokens: int):
        self.limit = limit_tokens
        self.used = 0

    def record(self, usage: TokenUsage) -> None:
        self.used += usage.total
        if self.used >= self.limit:
            raise BudgetExceeded(used=self.used, limit=self.limit)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)
```

### 4. Graceful Degradation on Budget Exhaustion

When a budget is hit, don't just crash:

1. Complete the current streaming response (don't cut mid-output)
2. Save the partial result to state store
3. Mark the node as `budget_exceeded`
4. Evaluate if downstream nodes can proceed with partial results
5. Report to human with usage breakdown

### 5. Different Budgets for Different Agent Types

Research agents need large input context (reading many files) but modest output. Implementation agents need moderate input but large output. Set budgets that reflect these profiles:

| Agent Type | Input Budget | Output Budget | Typical Ratio |
|---|---|---|---|
| Research | High | Low | 10:1 input:output |
| Implement | Moderate | High | 1:3 input:output |
| Test | Low | Low | 1:1 |
| Review | High | Moderate | 3:1 |

### 6. Log Usage for Post-Hoc Analysis

Even if you don't enforce budgets strictly in MVP, log every API call with:

- Agent type and subtask ID
- Input/output token counts
- Model used
- Cache hit/miss (for prompt caching)
- Wall clock duration

This data is essential for tuning budgets and identifying wasteful patterns.

### 7. Prompt Caching

Anthropic's prompt caching can reduce input token costs by up to 90% for repeated prefixes (system prompts, large context blocks). Structure prompts so that the shared context (issue description, repo overview) is in a cacheable prefix, and the per-agent instructions are in the variable suffix.

## Previous Stable Approach

### No Budget Controls (Common Default)
Most early LLM agent frameworks had no budget controls at all. Users discovered costs post-hoc via their provider dashboard. Horror stories of $500+ bills from a single runaway AutoGPT session in 2023 made this a known hazard.

### API Key Rotation / Separate Keys
A crude but effective approach: create a separate API key with a low spending limit for autonomous agents. If the key hits its limit, calls fail with a 429. The application catches this and stops. This provides hard enforcement but with poor granularity — you can't set per-task budgets.

### Manual Token Counting
Before native token counting in API responses, developers used `tiktoken` or similar libraries to estimate token counts before sending requests. This was error-prone (tokenizer mismatches, off-by-one on special tokens) but was the only option.

### Request-Level max_tokens
Setting `max_tokens` on each API call is the oldest and simplest budget control. It prevents a single response from being excessively long but doesn't control cumulative usage across multiple calls or input token growth from expanding context.
