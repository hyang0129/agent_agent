# Agent-to-Node Mapping

## Problem Statement

In a DAG-based orchestrator, the fundamental design decision is how agent invocations map to DAG nodes. A strict 1:1 mapping (one node = one agent invocation) provides determinism, cost predictability, and debuggability. But it also means no iterative refinement within a node and no multi-agent collaboration per task. This policy defines when the 1:1 constraint holds, when it is relaxed, and how relaxations are bounded.

## Background & State of the Art

### The Two Execution Models

LLM agent systems use two fundamentally different execution models:

**Single-shot (workflow) model.** The orchestrator calls the LLM once per step with a fully-specified prompt. The LLM returns a structured result. No tool-use loop, no iteration. This is the "workflow" pattern from Anthropic's *Building Effective Agents* guide: "systems where LLMs and tools are orchestrated through predefined code paths." Each step is deterministic in structure, cost-bounded by a single `max_tokens` call, and trivially replayable.

**Tool-use loop (agentic) model.** The orchestrator hands the LLM a set of tools and lets it loop: think, call a tool, observe the result, repeat until the LLM emits a final answer with no tool calls. This is the "agent" pattern: "systems where LLMs dynamically direct their own processes and tool usage." The loop length is unbounded by default, cost is variable, and replay requires re-executing all tool calls.

Most production systems use a hybrid. The question is where each model applies.

### How Leading Frameworks Handle This

**LangGraph** models each node as a function that may internally run an agentic loop. A node can be a simple transform (single-shot) or a full ReAct agent with tools. The graph structure is deterministic; what happens inside a node is not. LangGraph's `StateGraph` passes typed state between nodes, so even if a node runs an internal loop, its output is a structured state update. This is the closest model to what agent_agent uses.

**CrewAI** assigns one agent per task in a sequential or parallel pipeline. Each agent runs a tool-use loop internally to complete its task. The pipeline structure is fixed, but each agent's execution is open-ended. CrewAI added `max_iter` to cap the number of internal reasoning steps per agent, acknowledging the cost-control problem of unbounded loops.

**AutoGen** treats agent interaction as conversation. Multiple agents exchange messages in rounds until a termination condition is met. There is no fixed graph structure -- agents decide when to hand off. This is maximally flexible but makes cost prediction, debugging, and replay significantly harder.

**MetaGPT** assigns software engineering roles (PM, architect, engineer, QA) to agents and coordinates them via structured message passing. Each role produces a defined artifact (spec, design, code, test results). The mapping is 1:1 between role-steps and agent invocations, with structured schemas enforcing output format. This is closest to agent_agent's philosophy.

**Anthropic's own guidance** (December 2024, updated 2025): "Start with simple prompts, optimize them with comprehensive evaluation, and add multi-step agentic systems only when simpler solutions fall short." Their recommended progression is: single LLM call -> prompt chain -> workflow with routing -> agentic loop. The agentic loop is the last resort, not the default.

### The Tool-Use Loop: Benefits and Costs

The agentic tool-use loop (think-act-observe-repeat) is powerful because it lets the agent adapt to what it discovers. A research agent reading code can decide to follow an import, read a test, or check git blame -- decisions that cannot be fully anticipated in advance.

But the loop has concrete costs:

| Property | Single-shot | Tool-use loop |
|---|---|---|
| Token cost | Predictable (1 API call) | Variable (N calls, often 4-15x single) |
| Latency | Fixed | Variable, often 10-60s per loop iteration |
| Debuggability | Trivial (input -> output) | Requires replaying full tool trace |
| Determinism | High (same prompt -> similar output) | Low (tool results introduce entropy) |
| Cost control | Exact (`max_tokens`) | Approximate (iteration caps, token budgets) |
| Capability | Limited to what the prompt anticipates | Adapts to discovered context |

Production systems report that agents consume approximately 4x more tokens than single-shot calls, and multi-agent systems up to 15x (Oracle, 2025). Claude Code's production agent averages 21.2 chained tool calls per session (Anthropic, 2026), demonstrating both the power and the cost of the loop pattern.

### Academic Research

Recent surveys on LLM-based multi-agent collaboration (Tran et al., "Multi-Agent Collaboration Mechanisms: A Survey of LLMs," January 2025; Chen et al., "A Survey on LLM-based Multi-Agent System," January 2025) identify five interaction dimensions: profile definition, perception, self-action, mutual interaction, and evolution. The key finding relevant to node mapping is that **structured, schema-bounded interaction between agents produces more reliable results than free-form conversation**, particularly for software engineering tasks where outputs must be machine-parseable (code, diffs, test results).

The MetaGPT paper (Hong et al., 2023) demonstrated that role-specialized agents with structured output schemas outperform general-purpose agents on SWE-bench, even when the general agents have more powerful underlying models. This validates the 1:1 mapping with typed outputs approach.

### Anthropic's Multi-Agent Results

Anthropic's own engineering team reported (2025-2026) that a multi-agent system with Claude Opus 4 as lead and Claude Sonnet 4 subagents outperformed single-agent Claude Opus 4 by 90.2% on internal research evaluations. However, this was for read-heavy research tasks, not for the structured write-commit-review pipeline that agent_agent implements. The key insight: multi-agent collaboration helps most when the task requires exploring a large search space (research), not when the task has a well-defined input-output contract (code a specific change).

## Policy

### 1. Default: One Node = One Agent Invocation With Tool-Use Loop

Each DAG node maps to exactly one agent invocation. That invocation runs the standard tool-use loop: the agent receives tools appropriate to its type, calls them as needed, and returns a structured result when done. The invocation is bounded by both a token budget (see cost-and-token-budgets.md) and an iteration cap.

This is not a single-shot call. The agent can use tools iteratively within its invocation. The constraint is that the node boundary is the agent boundary -- no node spawns multiple agents, and no agent spans multiple nodes.

```
DAG Node "research_auth_module"
  └── 1 agent invocation (type: RESEARCH)
       ├── tool call: read_file("src/auth.py")
       ├── tool call: read_file("src/middleware.py")
       ├── tool call: grep("validate_token")
       └── structured output: ResearchOutput{...}
```

### 2. Iteration Cap Per Node

Every node has a maximum number of tool-use loop iterations, configurable per agent type:

| Agent Type | Default Max Iterations | Rationale |
|---|---|---|
| RESEARCH | 30 | May need to read many files, follow imports |
| CODE | 40 | Write files, run tests, iterate on failures |
| COMMIT | 5 | Mechanical: stage, commit, push |
| TEST | 15 | Run tests, read output, possibly re-run |
| REVIEW | 20 | Read diffs, check style, compose feedback |

If an agent hits its iteration cap, the node is marked `iteration_limit` and the orchestrator applies the same escalation logic as any other node failure (see error-handling-and-recovery.md).

### 3. No Multi-Agent Collaboration Within a Node

A single node must not invoke multiple agents, spawn sub-agents, or initiate agent-to-agent conversation. Inter-agent coordination happens exclusively through the DAG structure: upstream outputs feed downstream inputs via typed context objects (see context-passing.md).

This constraint exists because:
- It makes cost attribution unambiguous (node X used Y tokens)
- It makes retry semantics simple (retry = re-run the one agent)
- It makes debugging linear (one agent's tool trace per node)
- It prevents unbounded conversation loops between agents

If a task requires multiple perspectives (e.g., code review by two agents), model it as two nodes with the same input, not one node with two agents.

### 4. When to Split a Node vs. Allow More Iterations

If an agent frequently hits its iteration cap, the correct response is usually to **split the node into smaller subtasks**, not to raise the cap. Signs that a node should be split:

- The agent is doing two distinct phases of work (e.g., research then implement)
- The agent's output spans multiple concerns (e.g., modifying auth and adding tests)
- Token usage is consistently above 80% of the node budget
- The agent's tool trace shows a clear phase transition (reading -> writing)

Raising the iteration cap is appropriate only when:
- The task is inherently sequential and cannot be decomposed (e.g., reading a very large file set)
- The agent is making steady progress (each iteration adds value, not looping)

### 5. Retry Is Re-Invocation, Not Continuation

When a node fails and is retried, the agent is invoked fresh. It receives the original input context plus a failure summary from the previous attempt. It does not resume the previous tool-use loop. This ensures:

- No contamination from a hallucinating previous attempt
- Clean token accounting (retry cost is separate from original cost)
- Deterministic retry semantics (same input -> independent execution)

```python
class RetryContext(BaseModel):
    original_input: NodeInput
    previous_attempts: list[FailureSummary]  # What went wrong, not the full trace
    attempt_number: int
    remaining_budget_tokens: int
```

### 6. The DAG Is the Collaboration Mechanism

Agent collaboration in agent_agent happens through DAG structure, not through agent conversation. To get the effect of:

| Desired Interaction | DAG Modeling |
|---|---|
| Agent A refines Agent B's work | B -> A (A receives B's output as input) |
| Two agents debate/review | A -> B, where B receives A's output and can disagree |
| Iterative code-test loop | CODE -> TEST -> CODE (cycle with max iterations at DAG level) |
| Multiple perspectives | Fan-out: same input -> [A, B] -> merge node |

This is more constrained than free-form agent conversation but more predictable, auditable, and cost-controllable.

### 7. Exception: Planner May Use Extended Reasoning

The planner -- the agent that decomposes a GitHub issue into a DAG -- is the one agent type that may use extended context and reasoning without a tight iteration cap. The planner runs once per issue, its output (the DAG) is validated by schema before execution, and its cost is amortized across all downstream nodes. The planner still has a token budget but is allowed a higher iteration count (up to 50) to thoroughly analyze the issue, read relevant code, and construct an optimal decomposition.

### 8. Metrics That Validate This Policy

Track and review monthly:

- **Iterations per node** (p50, p90, p99 by agent type): If p90 approaches the cap, either the cap is too low or tasks need further decomposition.
- **Token cost per node vs. per DAG**: Node cost should be predictable for a given agent type. High variance indicates the agent is doing too much.
- **Retry rate by agent type**: High retry rates suggest the single-invocation model is insufficient for that task type -- consider splitting the node.
- **Iteration cap hits**: Should be rare (<5% of invocations). Frequent cap hits are a decomposition problem, not a cap problem.

## Rationale

The 1:1 mapping with bounded tool-use loops is a deliberate trade-off:

**What we gain:**
- **Cost predictability.** Each node has a known budget ceiling. The DAG's total cost is the sum of its node budgets. No runaway agent conversation can blow through the budget.
- **Debuggability.** Every node has a single agent's tool trace. When something goes wrong, you look at one trace, not a multi-agent conversation transcript.
- **Retry simplicity.** Failed node = re-invoke one agent. No need to reason about which agent in a multi-agent conversation caused the failure.
- **Auditability.** Each node maps to one agent type with one permission profile (see agent-permissions.md). No permission escalation through agent collaboration.
- **Parallelism.** Independent nodes run in parallel trivially. No need to coordinate shared conversation state between concurrent agents.

**What we give up:**
- **Within-node debate.** Two agents cannot argue about an approach within a single node. This must be modeled as sequential nodes (A -> B) with explicit disagreement handling.
- **Emergent collaboration.** Agents cannot discover useful collaborations at runtime. All collaboration paths must be anticipated in the DAG structure.
- **Complex implementation flexibility.** A coding agent that discovers mid-task that it needs research cannot delegate to a research agent. It must work with the context it received, or fail so the orchestrator can re-plan.

These trade-offs are appropriate for a GitHub issue resolution system where:
- Issues are decomposed upfront by the planner, so collaboration paths are known
- Cost control matters (every API call costs money)
- Human review of agent actions is required (auditability)
- The failure mode of "agent runs away" is worse than "agent fails and retries"

If future workloads require richer intra-node collaboration (e.g., pair-programming between two coding agents), the correct approach is to add a new node type that encapsulates the collaboration pattern with its own bounded protocol, not to relax the 1:1 constraint globally.

## Previous Stable Approach

### Unbounded Multi-Agent Conversation (AutoGen-style)
Early multi-agent systems let agents converse freely until a termination condition was met. This produced impressive demos but unpredictable costs, difficult debugging, and frequent infinite loops in production. The AutoGen team themselves added termination conditions, max-round limits, and conversation summarization to address these issues.

### Single-Shot Without Tool Use
The opposite extreme: each node gets one LLM call with no tools. Maximally predictable but severely limited -- the agent cannot read files, run tests, or verify its own output. Useful only for pure text transformation tasks (summarization, formatting).

### Monolithic Agent (One Agent, No DAG)
Give one agent the entire issue and all tools, let it work until done. This is Claude Code's architecture for human-interactive use, where a human provides ongoing guidance. Without human steering, monolithic agents on complex issues tend to lose focus, exceed budgets, and produce inconsistent results. The DAG decomposition exists specifically to avoid this failure mode.
