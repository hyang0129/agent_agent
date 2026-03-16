# 20 — Node Execution Model

## Background / State of the Art

### The Two Execution Models

LLM agent systems use two fundamentally different execution models:

**Single-shot (workflow) model.** The orchestrator calls the LLM once per step with a fully-specified prompt. The LLM returns a structured result. No tool-use loop, no iteration. This is the "workflow" pattern from Anthropic's *Building Effective Agents* guide: "systems where LLMs and tools are orchestrated through predefined code paths." Each step is deterministic in structure, cost-bounded by a single `max_tokens` call, and trivially replayable.

**Tool-use loop (agentic) model.** The orchestrator hands the LLM a set of tools and lets it loop: think, call a tool, observe the result, repeat until the LLM emits a final answer with no tool calls. This is the "agent" pattern: "systems where LLMs dynamically direct their own processes and tool usage." The loop length is unbounded by default, cost is variable, and replay requires re-executing all tool calls.

Most production systems use a hybrid. The question is where each model applies.

### How Leading Frameworks Handle This

**LangGraph** models each node as a function that may internally run an agentic loop. A node can be a simple transform (single-shot) or a full ReAct agent with tools. The graph structure is deterministic; what happens inside a node is not. LangGraph's `StateGraph` passes typed state between nodes, so even if a node runs an internal loop, its output is a structured state update. This is the closest model to what agent_agent uses.

**CrewAI** assigns one agent per task in a sequential or parallel pipeline. Each agent runs a tool-use loop internally to complete its task. The pipeline structure is fixed, but each agent's execution is open-ended. CrewAI added `max_iter` to cap the number of internal reasoning steps per agent, acknowledging the cost-control problem of unbounded loops.

**AutoGen** treats agent interaction as conversation. Multiple agents exchange messages in rounds until a termination condition is met. There is no fixed graph structure — agents decide when to hand off. This is maximally flexible but makes cost prediction, debugging, and replay significantly harder.

**MetaGPT** assigns software engineering roles (PM, architect, engineer, QA) to agents and coordinates them via structured message passing. Each role produces a defined artifact (spec, design, code, test results). The mapping is 1:1 between role-steps and agent invocations, with structured schemas enforcing output format. This is closest to agent_agent's philosophy.

**Anthropic's own guidance** (December 2024, updated 2025): "Start with simple prompts, optimize them with comprehensive evaluation, and add multi-step agentic systems only when simpler solutions fall short." Their recommended progression is: single LLM call -> prompt chain -> workflow with routing -> agentic loop. The agentic loop is the last resort, not the default.

### The Tool-Use Loop: Benefits and Costs

The agentic tool-use loop (think-act-observe-repeat) is powerful because it lets the agent adapt to what it discovers. A research agent reading code can decide to follow an import, read a test, or check git blame — decisions that cannot be fully anticipated in advance.

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

### Multi-Agent Coding Cycles

Three systems demonstrate that separating the code-test loop into specialized agents that cycle improves performance:

**AgentCoder** (Huang et al., 2023) separates Programmer, Test Designer, and Test Executor into three roles. The Programmer writes code, the Test Designer generates test cases that probe edge cases and failure modes, and the Test Executor runs the tests and reports results. When tests fail, the Programmer receives the failure output and iterates. The paper showed this separation improved code generation quality because each agent focuses on a single concern without mode-switching. Critically, the Test Designer is a separate role from the Test Executor — designing good tests requires different reasoning than running them.

**MapCoder** (Islam et al., ACL 2024) adds an explicit Debug role to the cycle. After the code fails tests, the Debug agent analyzes the failure, identifies the root cause, and produces a diagnosis that the code-writing agent uses to fix the issue. This separation contributed to MapCoder's 93.9% on HumanEval. The insight: diagnosing a failure is a different cognitive task than fixing it, and LLMs perform better when each task is isolated.

**Self-Debugging** (Chen et al., ICLR 2024) showed that LLMs can fix their own code only when given concrete execution feedback — error messages, failing test output, execution traces. Bare "try again" prompts showed negligible improvement. This validates the cycle's information flow: the Test Executor produces external signal, the Debugger interprets it, the Programmer acts on the interpretation.

### Cycle Limits and Diminishing Returns

**Reflexion** (Shinn et al., NeurIPS 2023) showed that the number of productive iterations varies by problem difficulty. Easy problems are solved in 1-2 iterations; hard problems benefit from 3-5. However, returns diminish steeply — the majority of recoverable failures are caught by attempt 2-3. Beyond that, the failure is typically structural (wrong approach) rather than incidental (wrong implementation), and more cycles of the same approach rarely help.

**Self-Debugging** (Chen et al., ICLR 2024) confirmed this pattern: LLMs given execution feedback improve significantly on the first retry, modestly on the second, and negligibly thereafter. The implication for the Coding Node: 3 cycles captures nearly all recoverable failures. If the code still doesn't pass, the right response is to replan (change approach), not to keep debugging (same approach, more attempts).

**Huang et al. (ICLR 2024)** established that intrinsic self-correction (without external signal) degrades performance. The Coding Node's cycle is productive because each iteration incorporates external feedback (test results). But even with external feedback, the information gain per cycle decreases — cycle 3's Debugger diagnosis is unlikely to reveal something fundamentally new that cycles 1-2 missed.

### Planning as a Cohesive Unit

Leading multi-agent systems consistently treat the research-plan-decide pipeline as a cohesive unit:

**TDAG** (Wang et al., Neural Networks 2025) uses a "main agent" that performs research, decomposition, and dispatch as a single unified operation. Separating these would break the agent's ability to iteratively refine its understanding and plan in one reasoning pass.

**Agyn** (2026) assigns a Manager role that researches the issue, produces a plan, and delegates to workers — all as a single cohesive activity. The Manager's value comes from maintaining a unified mental model across understanding, planning, and delegation.

**Anthropic's guidance** (December 2024, updated 2025) recommends that orchestration logic — deciding what to do, how to decompose, when to stop — should live in deterministic code paths ("workflows") rather than in autonomous agent loops.

### When Self-Correction Works (and When It Doesn't)

**Reflexion (Shinn et al., NeurIPS 2023)** demonstrated that LLM agents can improve across attempts when given verbal feedback about prior failures stored in episodic memory. On coding tasks (HumanEval), Reflexion improved pass rates by 11% over multiple trials. The key mechanism: the agent receives a natural-language reflection on *why* the previous attempt failed, not just the raw error.

**Self-Debugging (Chen et al., ICLR 2024)** showed that LLMs can fix their own code when given execution feedback (error messages, failing test output, execution traces). Simple "try again" prompts without feedback showed negligible improvement. Execution traces — concrete evidence of what went wrong — produced consistent gains.

**Huang et al. (ICLR 2024), "Large Language Models Cannot Self-Correct Reasoning Yet"** established an important constraint: *intrinsic* self-correction (asking an LLM to reconsider its answer without external signal) can actually degrade performance. LLMs second-guess correct answers as often as they fix incorrect ones. Self-correction only reliably works when grounded in external feedback — test failures, type errors, API error messages, tool output.

**Practical implication for agent_agent:** Re-invocation must always provide concrete external feedback (error messages, test output, validation failures). Never re-invoke with a bare "try again" prompt. If no external signal is available to ground the re-invocation, the failure should escalate rather than retry.

---

## Policy

### 1. One Node = One Agent

Each DAG node maps to exactly one agent invocation. The agent receives tools appropriate to its type, calls them as needed within a tool-use loop, and returns a typed, structured result. The node boundary is the agent boundary — no node spawns multiple agents, and no agent spans multiple nodes.

```
DAG Node "research_auth_module"
  └── 1 agent invocation (type: RESEARCH)
       ├── tool call: read_file("src/auth.py")
       ├── tool call: read_file("src/middleware.py")
       ├── tool call: grep("validate_token")
       └── structured output: ResearchOutput{...}
```

### 2. One Composite Node = Multiple Nodes

A composite node is a DAG node whose internal execution is itself a DAG. The outer DAG sees only typed inputs and outputs — the internal structure is opaque. This follows the structural indirection pattern from Temporal child workflows and Netflix Maestro sub-workflows.

A node is composite not because it currently contains multiple agents, but because it **is designed to contain multiple agents** — even if the MVP implements it as a single agent. The composite designation reserves the structural slot for future decomposition without changing the outer DAG.

Agent_agent has three composite nodes:

| Composite Node | MVP Implementation | Target Architecture |
|---|---|---|
| **Planning Node** | Single Planner agent with extended reasoning | Research → Plan → Orchestrate (acyclic internal DAG) |
| **Coding Node** | 4 agents in a cyclic DAG (Programmer → Test Designer → Test Executor → Debugger) | Same |
| **Review Node** | Single Review agent | Code Reviewer ∥ Policy Reviewer → Merge (acyclic internal DAG with fan-out) |

The DAG is nested across two levels:

- **Level 0 (root):** A single composite Planning Node. It receives the GitHub issue, performs research and planning, and produces the Level 1 DAG.
- **Level 1 (inner):** The Planner's output DAG. Coding and Review nodes (potentially branched for independent subtasks) consolidate into a terminal Planning Node that evaluates results and either accepts or replans.

```
Level 0:  [Planning Node]
               │
               ▼
Level 1:  [Coding Node(s)] → [Review Node(s)] → [Planning Node]
               │                                       │
               └── (parallel branches possible) ───────┘
```

The terminal Planning Node at Level 1 receives all completed Review outputs. If reviews pass, the run completes. If reviews fail or Coding Nodes were exhausted, the terminal Planner can replan with a different approach — this is the system's primary correction mechanism for approach errors (as distinct from the Coding Node's internal cycle, which corrects execution errors).

### 3. Iteration Caps

Every agent invocation — whether it runs inside a simple node or inside a composite node — is bounded by an iteration cap on its tool-use loop.

**Simple nodes and composite node sub-agents:**

| Agent | Max Tool-Use Iterations | Rationale |
|---|---|---|
| Planner (MVP) | 50 | Must read many files, analyze the problem, and produce a decomposition in one pass |
| Programmer | 40 | Read context, write files, validate syntax |
| Test Designer | 20 | Read changes, identify test targets, write test cases |
| Test Executor | 15 | Run tests, read output |
| Debugger | 20 | Read failing output, analyze root cause, write diagnosis |
| Review | 20 | Read diffs, check style, compose feedback |

Hitting an iteration cap is a resource exhaustion — the agent consumed its allocated tool-use budget without completing. The action is the same as for any resource exhaustion: fail the node, preserve its output so far, and let the DAG handle it (see §6).

### 4. Composite Nodes Define Collaboration as DAGs

The internal structure of a composite node is a DAG — acyclic or cyclic. The collaboration pattern between sub-agents is expressed through edges in that internal DAG, not through free-form conversation.

#### Acyclic Collaboration

Sub-agents execute in topological order. Each produces typed output consumed by the next. No iteration.

```
Planning Node (target):    Research → Plan → Orchestrate
Review Node (target):      Code Reviewer ─┐
                           Policy Reviewer┘→ Merge
```

#### Cyclic Collaboration

Sub-agents execute in a fixed cycle. Each cycle runs all agents in order. The cycle repeats until an exit condition is met.

```
Coding Node:
  Cycle 1..N:  Programmer → Test Designer → Test Executor → Debugger
  Exit: tests pass OR cycle cap reached OR budget exhausted
```

**A cyclic composite node is just an unrolled DAG.** The Coding Node with `max_cycles: 3` is structurally equivalent to a DAG with 12 nodes (4 agents × 3 cycles), where edges flow forward from each agent to the next, and from cycle N's Debugger to cycle N+1's Programmer. The cycle cap determines the DAG's length, not a runtime loop counter. This means:

- Each sub-agent invocation is a node with its own inputs and outputs
- Completed sub-agent outputs are persisted in the state store
- On failure or crash, the composite node resumes from the last completed sub-agent, not from scratch

The exit condition (tests pass) is evaluated after Test Executor in each unrolled cycle. If tests pass, the remaining nodes in the unrolled DAG are skipped.

### 5. Composite Node Resumption

Because a composite node's internal structure is a DAG with persisted node outputs, the composite node naturally supports resumption:

- **On sub-agent failure:** The failed sub-agent is re-invoked with its original inputs plus failure context. Other completed sub-agents are not re-executed.
- **On crash recovery:** The composite node's internal DAG is reconstructed from the state store. Completed sub-agents are loaded from persistence. Execution resumes from the first incomplete sub-agent.
- **On cycle-cap exhaustion (Coding Node):** The composite node exits with failure. Its output includes the full history of completed sub-agent outputs across all cycles — every Programmer's changes, every test result, every Debugger diagnosis. This history flows forward to the Planning Node.

This replaces the previous policy's "no work preservation within the Coding Node" rule. Work preservation falls out naturally from treating the composite node's internals as a DAG with persistence.

### 6. Re-Invocation Is for Nodes, Not Composite Nodes

When a **simple node** (single agent) fails, re-invocation means: invoke the agent fresh with its original inputs plus a failure summary from the previous attempt. The agent starts clean — no resumed state, no continuation of the prior tool-use loop. This prevents contamination from hallucinating prior attempts.

```python
class ReInvocationContext(BaseModel):
    original_input: NodeInput
    previous_attempts: list[FailureSummary]  # What went wrong, not the full trace
    attempt_number: int
    remaining_budget_tokens: int
```

When a **composite node** fails, the executor does not re-invoke the entire composite from scratch. Instead:

- **Sub-agent failure within a composite:** Re-invoke the specific sub-agent that failed (it is a node within the internal DAG). Completed sub-agents are not re-executed.
- **Cycle-cap exhaustion (Coding Node):** The composite node's allocated correction opportunities are consumed. This is resource exhaustion, not an agent logic error. The composite exits with failure and its history flows forward in the outer DAG to the Planning Node, which can replan with a different approach.
- **Budget exhaustion:** Same as cycle-cap — resource consumed, exit with partial results.
- **Transient failure (API timeout, rate limit) during a sub-agent:** Re-invoke that specific sub-agent with exponential backoff. No cycle is consumed. The transient failure is invisible to the composite node's cycle counter.

The key insight: **the cycle *is* the correction mechanism** within a Coding Node. After 3 cycles of Programmer → Debugger feedback, the problem is an approach error (the Planning Node's domain), not an execution error (the Coding Node's domain). Retrying the entire composite from scratch repeats the same approach — exactly the pattern that the research shows doesn't work.

### 7. Failure Classification

Every failure MUST be classified before deciding the response:

| Category | Definition | Examples | Action |
|---|---|---|---|
| **Transient** | External, time-dependent failure likely to resolve | API rate limit (429), network timeout, connection reset | Re-invoke the specific sub-agent/node with exponential backoff. Does not consume a cycle or attempt. |
| **Agent Error** | The agent produced output that was incorrect or malformed | Failed Pydantic validation, hallucinated file path, invalid diff format | Re-invoke the node with failure context (error message, prior output). Consumes an attempt. |
| **Resource Exhaustion** | A bounded resource was consumed | Iteration cap hit, cycle cap hit, token budget exceeded | Fail the node/composite. Preserve outputs. No re-invocation — the resource is gone. |
| **Deterministic** | The failure will recur on every attempt | Auth error (401/403), file not found, permission denied | Fail immediately. Escalate per Policy 08. |
| **Unknown** | Unclassified exception | Unexpected exception types | Re-invoke once with context. If it fails again, escalate. |

The previous "Budget" category is generalized to **Resource Exhaustion**, which covers iteration caps, cycle caps, and token budgets uniformly. They are all the same thing: the agent operated correctly but could not converge within its resource envelope. The correct response is always to stop and let the next layer handle it — not to re-invoke the same work with the same resource constraints.

### 8. Backoff for Transient Failures

Transient failures use exponential backoff with jitter:

```
wait = min(initial_backoff * (multiplier ^ attempt), max_backoff) + random_jitter
```

Defaults:
- `initial_backoff`: 2 seconds
- `multiplier`: 2.0
- `max_backoff`: 60 seconds
- `jitter`: uniform random 0–1 second

Transient retries are invisible to the node's attempt counter and the composite node's cycle counter. A network timeout during the Programmer's invocation does not consume a cycle — the Programmer is re-invoked after backoff and the cycle continues.

Max transient retries per sub-agent invocation: 3. After 3 transient retries, reclassify as Deterministic and escalate.

### 9. Re-Invocation Limits

| Node / Agent | Max Attempts (for Agent Error) | Rationale |
|---|---|---|
| Planner (MVP) | 2 | Planning errors are structural; retrying same inputs rarely helps |
| Programmer | 2 | Within a cycle, the Debugger provides the correction path, not re-invocation |
| Test Designer | 2 | If test design fails validation twice, the problem is in the task spec |
| Test Executor | 1 | Execution is mechanical — if it fails, it's a transient or deterministic issue |
| Debugger | 2 | Diagnosis failures usually indicate insufficient context, not a fixable error |
| Review | 2 | Subjective output; repeated review yields diminishing returns |

These limits apply to **Agent Error** re-invocations only. Transient retries and Resource Exhaustion are handled by §8 and §7 respectively.

### 10. The DAG Is the Collaboration Mechanism

Agent collaboration happens through DAG structure — both the outer DAG and composite nodes' internal DAGs. Not through free-form conversation.

| Desired Interaction | DAG Modeling |
|---|---|
| Agent A refines Agent B's work | B → A (A receives B's output as input) |
| Two agents review independently | Fan-out: same input → [A, B] → merge node |
| Iterative code-test-debug loop | Coding Node's internal cyclic DAG |
| Agent needs information from a non-adjacent agent | Shared context store (Policy 12), not a direct edge |

This is more constrained than free-form agent conversation but more predictable, auditable, and cost-controllable.

### 11. The Planning Node Uses Extended Reasoning in MVP

The MVP Planning Node runs a single Planner agent that performs research, planning, and orchestration within one tool-use loop. The agent uses extended reasoning (thinking/reflection) to maintain coherence across the understand-plan-decide workflow.

This is justified by SOTA evidence (TDAG, Agyn) that a single agent maintaining a unified mental model across research, planning, and delegation outperforms a pipeline of three separate agents — particularly when the plan depends on discovered context that would be lost in serialization through typed outputs.

The Planning Node is still designated composite (§2) because the target architecture decomposes it into Research → Plan → Orchestrate when:
- The Planner agent consistently hits its iteration cap because research and planning compete for tool-use budget
- Post-hoc analysis shows the agent producing low-quality plans because it rushes research
- Issue complexity exceeds what a single agent pass can cover

### 12. Context on Re-Invocation

Every re-invocation MUST include failure context. Blind re-invocations are prohibited.

The re-invocation prompt MUST contain:

1. **What failed:** The error category and a one-line summary.
2. **Concrete evidence:** The actual error message, failing test output, or validation error. Not a paraphrase — the raw signal.
3. **Attempt number:** "This is attempt 2 of 2."
4. **Prior output (when relevant):** For Agent Error failures, include the relevant portion of the prior output so the agent can see what it produced and correct it.

For **transient** failures, context enrichment is optional (the failure resolved externally, not through agent behavior change).

### 13. Idempotency on Re-Invocation

Each Coding Node executes in its own git worktree, providing filesystem-level isolation between composite node invocations. This makes file-write idempotency a non-issue for the common case — each invocation operates on an isolated copy of the repository.

Within a Coding Node's internal cycle (Programmer → Test Designer → Test Executor → Debugger), sub-agents share the same worktree across cycles. This is intentional: the Programmer's changes in cycle 1 must be visible to the Test Executor in cycle 1 and to the Debugger's fix in cycle 2. Idempotency within a cycle is managed by the cycle structure itself — the Programmer overwrites its own prior output, not another agent's.

Git operations (branch creation, commits, push) are handled by the outer DAG after composite nodes complete — sub-agents inside composite nodes never touch git (Policy 14).

### 14. Metrics

Track and review to validate this policy:

| Metric | What It Tells You | Alert Threshold |
|---|---|---|
| **Iterations per agent invocation** (p50, p90 by agent type) | Whether iteration caps are calibrated | p90 > 80% of cap |
| **Cycle count per Coding Node** (p50, p90) | Whether the cycle cap is calibrated; whether replanning is happening too late or too early | p90 = max_cycles (most Coding Nodes exhaust cycles → cap too low or decomposition too coarse) |
| **Re-invocation rate by failure category** | Whether failure classification is accurate | High Agent Error re-invocations that fail again → probably Resource Exhaustion misclassified |
| **Transient retry rate** | External service reliability | Sustained > 10% of invocations |
| **Resource exhaustion by type** (iteration cap / cycle cap / budget) | Which resource is the binding constraint | If one type dominates, rebalance allocations |
| **Time from Coding Node failure to Planning Node re-plan** | Whether the failure-to-replan path is efficient | Sustained high latency suggests composite resumption isn't working |
| **Success rate by cycle number** | Diminishing returns curve for this codebase | If cycle 3 success rate ≈ 0%, reduce default max_cycles to 2 |
| **Escalation rate** | Whether the system is self-correcting or punting to humans | Track per issue complexity tier |

These metrics feed a feedback loop: if a particular agent type consistently exhausts resources, the response is better decomposition by the Planner (smaller subtasks), not higher caps.

---

## Rationale

### Why composite nodes resume instead of restart

The previous policy (Policy 19 §8) stated "no work preservation within the Coding Node — restart from inputs on failure." This was motivated by simplicity: avoiding the complexity of managing intermediate state. But it was also wasteful: a Coding Node that completes 2 successful cycles and fails on cycle 3 would discard all prior work on re-invocation.

The new model treats composite node internals as a DAG with persistence — the same persistence model used for the outer DAG. Each sub-agent invocation is a node with persisted inputs and outputs. This means resumption is not a new mechanism; it is the same crash-recovery logic the outer DAG already uses (Policy 01 §8), applied recursively.

The simplicity cost is modest: the state store already persists node outputs; extending this to sub-agent outputs within a composite node adds rows to the same table, not a new system.

### Why Resource Exhaustion is not Agent Error

When a Coding Node hits its cycle cap, the agents inside it operated correctly — the Programmer wrote code, the Test Executor ran tests, the Debugger diagnosed failures. The system consumed its allocated correction opportunities and could not converge. This is fundamentally different from an agent producing malformed output.

Classifying cycle-cap exhaustion as Agent Error leads to re-invoking the entire composite node, which repeats the same approach — exactly the pattern the research shows doesn't work (Huang et al.: intrinsic self-correction without new external signal degrades performance). The correct response is to flow the failure forward to the Planning Node, which can change the approach.

The generalized Resource Exhaustion category treats iteration caps, cycle caps, and token budgets uniformly. They are all bounded resources. When consumed, the answer is to stop and let the next layer decide — not to re-allocate the same resource and try again.

### Why re-invocation limits are lower than the old retry limits

The old policy (Policy 07) set max attempts at 2-3 per agent type. But those limits were set without accounting for the correction mechanisms already present in composite nodes. A Programmer agent inside a Coding Node already gets corrected by the Debugger across cycles — that is the primary correction path. Re-invocation is a secondary mechanism for cases where the agent's output is structurally invalid (not where its approach didn't work). Two attempts is sufficient for structural errors; approach errors belong to the Planner.

### Why the DAG is the collaboration mechanism

The 1:1 mapping with bounded tool-use loops is a deliberate trade-off:

**What we gain:**
- **Cost predictability.** Each node has a known budget ceiling. No runaway agent conversation can blow through the budget.
- **Debuggability.** Every node has a single agent's tool trace. When something goes wrong, you look at one trace.
- **Re-invocation simplicity.** Failed node = re-invoke one agent. No need to reason about which agent in a multi-agent conversation caused the failure.
- **Auditability.** Each node maps to one agent type with one permission profile. No permission escalation through agent collaboration.
- **Parallelism.** Independent nodes run in parallel trivially. No shared conversation state to coordinate.

**What we give up:**
- **Within-node debate.** Two agents cannot argue within a single node. This is modeled as sequential nodes (A → B) with explicit disagreement handling.
- **Emergent collaboration.** Agents cannot discover collaborations at runtime. All collaboration paths must be anticipated in the DAG structure.
- **Mid-task delegation.** A coding agent that discovers it needs research cannot delegate. It must work with its context or fail so the system replans.

These trade-offs are appropriate for issue resolution where issues are decomposed upfront, cost control matters, and human review of agent actions is required.

## Interaction with Existing Policies

### Supersedes

- **Policy 04 (Agent-to-Node Mapping):** Fully replaced. The 1:1 rule, iteration caps, retry-is-re-invocation, and DAG-as-collaboration are all restated here with updated semantics for composite nodes.
- **Policy 07 (Retry Policy):** Fully replaced. Failure classification, re-invocation limits, backoff strategy, context enrichment, and idempotency are all restated here. The key change: Resource Exhaustion replaces the conflation of cycle-cap failure with Agent Error, and composite nodes resume instead of restart.
- **Policy 19 §8 (No Work Preservation):** Reversed. Composite nodes now persist sub-agent outputs and resume from the last completed sub-agent.

### Complements

- **Policy 01 (DAG Orchestration):** This policy governs execution within and across nodes. Policy 01 governs DAG structure, nesting, and traversal. The persistence model for composite node internals extends Policy 01 §8's crash-recovery model.
- **Policy 08 (Escalation):** Escalation triggers (dead_letter, budget exhaustion, safety violation) are unchanged. This policy defines when a node reaches those states; Policy 08 defines what happens after.
- **Policy 09 (Budget):** Budget allocation and enforcement are unchanged. This policy defines Resource Exhaustion as a failure category that triggers budget-related node termination.
- **Policy 14 (Granular Agent Decomposition):** Permission scoping per agent is unchanged. This policy defines the node/agent boundary where permissions are enforced.
