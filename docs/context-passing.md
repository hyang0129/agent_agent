# Context Passing

## Problem Statement

In a multi-agent DAG, each agent operates on a subtask but needs information from upstream agents to do useful work. Without structured context passing, agents either operate in isolation (producing incoherent results) or receive the entire conversation history (blowing up token usage and diluting signal). The challenge is passing the right context, in the right format, to each agent.

## State of the Artv

### LangGraph State Channels
LangGraph models context as a typed state object that flows through the graph. Each node reads from and writes to specific state keys. A `reducer` function controls how writes are merged (e.g., append to a list, overwrite, take the latest). This gives fine-grained control over what each node sees and how its output integrates with the broader state.

**Key insight:** Context is not a blob — it's a structured object with typed fields. Nodes declare which fields they read and write, enabling the framework to optimize what gets passed.

### CrewAI Task Context
CrewAI passes the output of one task as `context` to the next task. The context is free-form text — the raw output of the upstream agent. This is simple but creates problems: downstream agents must parse unstructured text, context grows linearly with chain length, and there's no way to selectively pass fields.

### AutoGen Chat History
AutoGen agents share a chat history — each agent sees all previous messages in the conversation. This works for linear chains but explodes in DAGs where parallel branches produce independent context that must be selectively merged.

### Microsoft Semantic Kernel
Uses a `KernelArguments` dictionary that flows through the pipeline. Each function/plugin reads named arguments and writes results back. The kernel manages variable scoping — functions only see variables explicitly passed to them.

### Anthropic Claude Agent SDK
The Agent SDK supports `handoffs` between agents, where one agent transfers control to another. Context is passed via the conversation transcript — the receiving agent gets the full message history. For structured data, tool results serve as the context mechanism.

### Research: MemGPT / Letta
MemGPT implements a tiered memory system for agents: a fixed-size "main context" (working memory) plus a searchable "archival memory" (long-term store). Agents explicitly manage what stays in working memory and what gets archived. This prevents context windows from overflowing while preserving access to historical information.

**Key insight:** Not all context is equally relevant. A tiered approach (immediate context + searchable archive) scales better than passing everything forward.

## Best Practices

### 1. Typed Context Objects, Not Free Text

Define Pydantic models for context at each DAG edge:

```python
class ResearchOutput(BaseModel):
    relevant_files: list[str]
    root_cause: str | None
    architectural_notes: str
    suggested_approach: str

class ImplementInput(BaseModel):
    issue: IssueContext
    research: ResearchOutput       # From upstream research agent
    files_to_modify: list[str]
    constraints: list[str]
```

This makes context passing explicit, validatable, and self-documenting.

### 2. Context Summarization at Merge Points

When parallel branches converge, don't concatenate their full outputs. Summarize or extract the relevant fields:

```
Branch A (research): found root cause in auth.py:45
Branch B (test analysis): 3 existing tests cover this path
                    ↓
Merge node receives: { root_cause, affected_files, existing_test_coverage }
```

This keeps downstream context windows focused.

### 3. Separate "Context for the Agent" from "Context for the Orchestrator"

Agents need task-relevant context (what to do, what upstream agents found). The orchestrator needs execution metadata (token usage, timing, success/failure). Keep these separate:

```python
class NodeResult(BaseModel):
    # For downstream agents
    output: AgentOutput

    # For orchestrator only
    meta: ExecutionMeta
```

### 4. Immutable Context Snapshots

Once a node completes, its context output is immutable. Downstream nodes receive a frozen snapshot. This prevents race conditions in parallel execution and makes debugging deterministic — you can always reconstruct exactly what each agent saw.

### 5. Context Windowing for Long DAGs

For deep DAGs (many sequential nodes), don't pass the full chain of upstream outputs. Instead:

- Pass the immediate parent's output in full
- Pass a structured summary of earlier ancestors
- Make the full history available via a retrieval mechanism if the agent needs to look something up

This mirrors how humans work — you remember the last conversation in detail and have vague summaries of earlier ones, with the ability to look things up.

### 6. Include the Original Issue at Every Node

Every agent in the DAG should receive the original GitHub issue text, regardless of depth. This anchors all work to the original intent and prevents semantic drift as context gets transformed through multiple agents.

## Previous Stable Approach

### Shared Blackboard Pattern (1980s–2000s AI)
The classic approach from expert systems: a shared "blackboard" data structure that all agents read from and write to. No directed context flow — any agent can read any field at any time. Simple to implement but creates implicit coupling between agents and makes it impossible to reason about what context influenced a given decision.

### Environment Variables / Shared Files
In early CI/CD and script-based automation, context was passed between steps via environment variables or temp files written to disk. Each step would read a known file path or env var set by a previous step. Fragile, untyped, and prone to desynchronization.

### Message Queues (Pub/Sub)
Agents subscribe to topics and publish results. Downstream agents consume messages from upstream topics. Provides decoupling but introduces complexity around message ordering, exactly-once delivery, and schema evolution. Overkill for a single-machine orchestrator.

### Monolithic Prompt Chains
The earliest LLM agent patterns simply concatenated all previous outputs into a growing prompt. Simple and effective for 2-3 steps, but token usage grows quadratically with chain length and context quality degrades as the window fills with irrelevant earlier outputs.
