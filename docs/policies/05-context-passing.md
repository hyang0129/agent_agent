# Context Passing Policy

## Background

### Problem

In a multi-agent DAG that resolves GitHub issues, each agent operates on a subtask but needs information from upstream agents to do useful work. Without structured context passing, agents either operate in isolation (producing incoherent results) or receive the entire accumulated history (blowing up token usage, diluting signal, and hitting the "lost in the middle" problem). The challenge is passing the right context, in the right format, at the right granularity, to each downstream agent --- while never allowing information to flow backwards.

### State of the Art

#### What Gets Passed: The Spectrum

Multi-agent systems fall on a spectrum from "pass everything" to "pass structured artifacts only."

| Approach | Example | Pros | Cons |
|---|---|---|---|
| **Full transcript** | AutoGen GroupChat broadcasts all messages to all agents | Maximum context, no information loss | Token cost grows O(n), lost-in-the-middle degradation, noise drowns signal |
| **Carryover summaries** | AutoGen Sequential Chat uses LLM-generated summaries as carryover between chats | Compressed, preserves key findings | Summaries can lose critical detail; summarizer is another LLM call with its own failure modes |
| **Typed structured artifacts** | LangGraph typed state, MetaGPT structured documents, Semantic Kernel `KernelArguments` | Explicit, validatable, self-documenting, token-efficient | Requires upfront schema design; rigid if schema doesn't anticipate what downstream needs |
| **Tiered memory** | MemGPT/Letta: working memory (fixed-size) + archival memory (searchable) | Scales to long-running agents; not all context is equally relevant | Retrieval adds latency; archival queries may miss relevant context |

The strongest systems combine structured artifacts for the primary data flow with optional retrieval for edge cases.

#### LangGraph State Channels

LangGraph models context as a typed state object flowing through the graph. Each node reads from and writes to specific state keys. A reducer function controls how writes merge (append, overwrite, take latest). Nodes declare which fields they read and write, enabling the framework to optimize what gets serialized and passed. This is the closest analog to our approach: typed, directional, schema-enforced.

#### MetaGPT Structured Artifacts

MetaGPT requires agents to produce structured documents (PRDs, design docs, interface specs) rather than free-text. These artifacts serve as the communication medium between roles. The result: downstream agents parse schemas, not prose. MetaGPT's publish-subscribe message pool lets agents subscribe to relevant artifact types by role, filtering out irrelevant upstream output.

#### AutoGen Communication Patterns

AutoGen offers two patterns with different context semantics:
- **GroupChat**: all agents share a single message thread. Context is everything said so far. Works for collaborative reasoning but token cost is O(agents x messages).
- **Sequential Chat**: context passes as a "carryover" summary from one two-agent chat to the next. Compressed but lossy --- the summary is only as good as the summarizer.

#### The "Lost in the Middle" Problem

Liu et al. (2023) demonstrated that LLM performance follows a U-shaped curve: models attend strongly to information at the beginning and end of context windows but degrade by 30%+ for information positioned in the middle. This is caused by attention decay in transformer positional encodings (RoPE). For multi-agent systems, this means that naively concatenating upstream outputs into a growing context actively harms downstream agent performance. The most relevant information must be placed at the beginning or end of the agent's context, not buried in the middle of accumulated history.

#### Context Compression Research

JetBrains Research (2025) identified two primary approaches to context management for agents:
- **Observation masking**: preserve the agent's own action and reasoning history in full but truncate or mask environment observations (tool outputs, file contents).
- **LLM summarization**: compress the entire history into a compact form.

ACON (2025) introduced "context folding" --- branching into sub-trajectories and folding them back as concise summaries, achieving 10x smaller active context with equivalent accuracy. The key insight: compress observations aggressively, preserve reasoning and decisions verbatim.

#### Error Propagation in Forward-Only Flow

A fundamental tension in forward-only architectures: when a downstream agent discovers the plan was wrong, it cannot fix upstream. Research shows:
- **Error snowballing**: a single upstream mistake weakens downstream success rates exponentially (information-theoretic proof in Sherlock, 2025).
- **Task-Decoupled Planning (TDP)**: confines planning and replanning to the active DAG node, reducing error propagation and cutting token usage by ~82% vs. plan-and-act.
- **SupervisorAgent (ICLR 2026)**: monitors agent-agent and agent-memory interactions for misinformation that could poison downstream reasoning.

The practical solution for forward-only systems: downstream agents do not send corrections backwards, but they can *signal failure to the orchestrator*, which can then invalidate the affected subtree and re-plan.

#### Forward-Only vs. Bidirectional Context Flow

| Property | Forward-only (DAG) | Bidirectional (cycles/feedback) |
|---|---|---|
| **Determinism** | High --- given inputs, the DAG produces deterministic execution order | Low --- feedback loops make execution order data-dependent |
| **Debuggability** | Each node's input is fully determined by its ancestors | Hard to reconstruct what an agent saw after multiple rounds |
| **Token cost** | Linear in DAG depth | Can grow unboundedly as agents iterate |
| **Error correction** | Must re-plan from orchestrator level | Agents can self-correct in-loop |
| **Convergence** | Guaranteed (DAG is acyclic) | Risk of infinite loops without termination guards |
| **Complexity** | Lower --- topological sort determines execution | Higher --- need cycle detection, termination conditions, message dedup |

Forward-only is the right default for a GitHub issue resolution system where reliability, auditability, and bounded cost matter more than iterative refinement within a single run.

## Policy

### 1. Typed Context Objects at Every Edge

Every DAG edge carries a typed Pydantic model, not free text. The schema is defined per edge type (research-to-implement, implement-to-test, etc.). Each model declares exactly which fields the downstream agent needs. The orchestrator validates the output against the schema before passing it downstream.

```python
class ResearchResult(BaseModel):
    """Output of research agent, input to downstream agents."""
    root_cause: str | None
    relevant_files: list[str]
    architectural_notes: str
    suggested_approach: str
    constraints: list[str]

class ImplementationResult(BaseModel):
    """Output of implementation agent, input to test/review agents."""
    files_changed: list[FileChange]
    commit_sha: str
    approach_taken: str
    known_limitations: list[str]
```

Rationale: Typed schemas prevent context bloat, make inter-agent contracts explicit, and catch integration errors at the orchestrator level rather than inside a downstream agent's confused output.

### 2. Original Issue Anchors Every Node

Every agent in the DAG receives the original GitHub issue text as a top-level field in its input context, regardless of how deep in the DAG the node sits. This field is immutable and identical across all nodes in a run.

Rationale: Prevents semantic drift. By the third or fourth node in a chain, accumulated transformations can distort the original intent. The raw issue text anchors every agent to what the user actually asked for.

### 3. Immediate Parent Output in Full, Ancestors as Structured Summaries

Each node receives:
- **Immediate parent(s)**: full typed output, all fields.
- **Earlier ancestors** (grandparent and above): a structured summary containing only key decisions and artifacts (file paths, root cause, approach chosen), not the full output.
- **Original issue**: always in full (per Policy 2).

When parallel branches merge, the merge node receives the full output of each immediate parent branch, not a concatenation.

Rationale: Mirrors how humans work --- you remember the last conversation in detail and have concise notes from earlier ones. Avoids the "lost in the middle" problem by keeping the active context small and positioning the most relevant information (parent output) at the end of the prompt, where attention is strongest.

### 4. Context Flows Forward Only --- No Backflow

Downstream agents never send data, corrections, or signals to upstream agents. Information flows in one direction: from upstream to downstream, following DAG edges. If a downstream agent discovers a problem with the upstream plan:

1. The agent marks its own result as `failed` with a structured error describing the issue (e.g., `"upstream_assumption_invalid": "auth.py does not exist; research agent referenced wrong file"`).
2. The orchestrator receives the failure.
3. The orchestrator decides: retry the failed node with enriched context, invalidate the upstream subtree and re-plan, or escalate to human.

Downstream agents never modify upstream state, re-invoke upstream agents, or write to upstream context objects.

Rationale: Forward-only flow guarantees deterministic execution order, bounded token cost, debuggable traces, and convergence. Backflow introduces cycles, unbounded iteration, and makes it impossible to reconstruct what any given agent saw. The orchestrator is the only entity authorized to initiate re-execution, and it does so by creating new DAG nodes --- never by mutating completed ones.

### 5. Immutable Context Snapshots

Once a node completes, its output context is frozen. No subsequent node, retry, or orchestrator action modifies a completed node's stored output. If a node must be re-executed (e.g., after upstream re-planning), a new node instance is created with a new attempt number, producing a new immutable snapshot.

Rationale: Immutability guarantees reproducible debugging. You can always reconstruct exactly what each agent saw and produced. It also prevents race conditions when parallel branches read from the same upstream node.

### 6. Separate Agent Context from Orchestrator Metadata

Each node result contains two distinct sections:
- **Agent output**: the typed context object that downstream agents consume (research findings, code changes, test results).
- **Execution metadata**: token usage, duration, retry count, model used, cost. This is for the orchestrator, observability, and budget enforcement --- never passed to downstream agents.

```python
class NodeResult(BaseModel):
    output: AgentOutput          # Downstream agents see this
    meta: ExecutionMeta          # Orchestrator sees this
```

Rationale: Downstream agents should reason about the task, not about how long the upstream agent took or how many tokens it used. Mixing execution metadata into agent context wastes tokens and can confuse the agent.

### 7. Context Budget per Node

Each node has a maximum input context budget (in tokens). The orchestrator enforces this before dispatch:

1. Include the original issue (always).
2. Include immediate parent output(s) in full.
3. Include ancestor summaries in reverse chronological order until budget is reached.
4. If budget is exceeded, truncate the oldest ancestor summaries first.

The budget is configured per agent type --- research agents may need more context (exploring broadly), while test agents need less (focused on specific files and expected behavior).

Rationale: Prevents context window overflow in deep DAGs. Ensures that even with many ancestors, the agent's context stays within the window where LLM performance is reliable. Truncation order (oldest first) preserves the most decision-relevant recent context.

### 8. Structured Failure Signals for Upstream Problems

When a downstream agent determines that upstream output is incorrect or insufficient, it must express this as a structured field in its own (failed) result:

```python
class UpstreamIssue(BaseModel):
    source_node_id: str
    field: str                          # Which field is wrong
    description: str                    # What's wrong
    evidence: str                       # How the agent knows

class AgentOutput(BaseModel):
    status: Literal["success", "failed"]
    upstream_issues: list[UpstreamIssue] = []
    # ... other fields
```

This is not backflow --- the agent is reporting forward to the orchestrator, which decides what to do. The upstream node's output remains immutable.

Rationale: Gives the orchestrator actionable information for re-planning without violating forward-only flow. The structured format enables automated triage (e.g., if 3 downstream nodes all flag the same upstream field, the orchestrator knows to re-run that upstream node).

## Rationale Summary

This policy optimizes for the properties that matter most in a GitHub issue resolution system:

- **Reliability over iteration**: forward-only flow with orchestrator-controlled re-planning is more predictable than agent-to-agent negotiation loops.
- **Auditability**: immutable snapshots and typed schemas mean every agent's input and output is fully reconstructable.
- **Token efficiency**: structured artifacts + ancestor summarization keeps context small and focused, avoiding the lost-in-the-middle degradation that hits naive context accumulation.
- **Bounded cost**: context budgets per node and forward-only flow guarantee that token usage scales linearly with DAG size, not exponentially with iteration depth.
- **Graceful error handling**: structured failure signals let the orchestrator make informed re-planning decisions without backflow, maintaining the DAG's deterministic properties.
