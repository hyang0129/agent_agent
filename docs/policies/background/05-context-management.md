# 05 — Context Management

## Background

### Problem

Agent Agent follows a Maximum Agent Separation policy, decomposing agent roles into the smallest independently-scoped units. A single GitHub issue resolution may produce DAGs of 10--15+ nodes across multiple nesting levels. Each agent operates on a subtask but needs information from upstream agents, parallel agents, and prior nesting levels to do useful work.

Without structured context management, three failure modes emerge at scale:

1. **Full history forwarding** scales as O(N²) in token cost and suffers from the lost-in-the-middle effect. By node 8--10, early outputs are effectively invisible to the model despite being present in the prompt.

2. **Parent-only forwarding** is token-efficient (O(N) total) but causes **semantic drift**. Each node reinterprets upstream signal through its own lens. By node 5--6, the chain's understanding of intermediate findings has mutated like a game of telephone. Anchoring the original issue prevents goal drift but does not prevent drift in intermediate findings.

3. **Summarize-and-forward** creates a **lossy compression cascade**. Each summarization step discards detail that seems irrelevant at the current node but may be critical further downstream. Summarization errors compound --- a slight mischaracterization at node 2 becomes an unchallenged "fact" for all downstream nodes.

Beyond edge-based context, multi-agent DAGs accumulate knowledge that does not flow along edges: discoveries about the codebase, constraints, negative findings, root cause analyses. Without a structured shared context mechanism, this knowledge is either lost (downstream agents re-investigate what upstream agents already found) or duplicated across agent outputs (bloating context).

For DAGs of depth 3--4, these failure modes are negligible. For the deep DAGs that Maximum Agent Separation produces, they are structural problems. This policy treats context management as a first-class concern worth significant investment --- spending tokens on summarization, structured discovery tracking, and consumer-tailored context assembly pays for itself by keeping downstream agents focused and effective.

### State of the Art

#### The Spectrum of Context Approaches

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

#### MetaGPT Structured Artifacts and Global Memory

MetaGPT requires agents to produce structured documents (PRDs, design docs, interface specs) rather than free-text. MetaGPT also maintains a global memory pool storing all collaboration records. Agents subscribe to relevant artifact types by role (a testing agent subscribes to implementation artifacts, not design documents). Agents actively pull pertinent information rather than passively receiving everything --- a publish-subscribe model that filters context by relevance.

#### The Blackboard Architecture

The blackboard model, originating in HEARSAY-II (1970s), is the direct ancestor of shared context in multi-agent systems. Knowledge sources (agents) read from and write to a structured shared data store; they do not communicate directly with each other. A control component decides which agent to activate based on the current blackboard state.

LLM-Based Multi-Agent Blackboard System for Information Discovery (2026) showed 13--57% improvement over RAG and master-slave paradigms for file discovery tasks --- directly relevant to GitHub issue investigation.

#### Consumer-Driven Context Extraction

Some production systems give the context-selection LLM the downstream agent's system prompt and input schema, then ask it to extract only what that specific consumer would need. This makes context negotiation **consumer-driven** (shaped by what the receiver needs) rather than **producer-driven** (shaped by what the sender outputs). The consumer's schema acts as the negotiation protocol.

#### RAG for Agent Memory

CrewAI implements RAG-based memory (ChromaDB) where agents retrieve relevant context from a shared store via semantic search. AutoGen v0.4's Memory protocol decouples storage from retrieval, allowing swappable backends. MemGPT/Letta uses tiered memory (working memory + searchable archival store) to prevent context overflow while preserving access to historical information.

The common pattern: instead of forwarding context along edges, agents **pull** relevant context from a shared store at dispatch time. This scales independently of DAG depth --- retrieval cost is O(1) per node regardless of how many ancestors exist.

#### The "Lost in the Middle" Problem

Liu et al. (2023) demonstrated that LLM performance follows a U-shaped curve: models attend strongly to information at the beginning and end of context windows but degrade by 30%+ for information positioned in the middle. For multi-agent systems, the most relevant information must be placed at the beginning or end of the agent's context, not buried in the middle of accumulated history.

#### Context Compression Research

JetBrains Research (NeurIPS 2025) found that combining structured observation masking with selective LLM summarization achieved 7--11% cost reduction over either approach alone. The key insight: use cheap structural techniques (masking, schema filtering) as the default, and reserve expensive LLM summarization for cases where structural techniques alone cannot meet the context budget.

ACON (2025) introduced "context folding" --- branching into sub-trajectories and folding them back as concise summaries, achieving 10x smaller active context with equivalent accuracy. The key insight: compress observations aggressively, preserve reasoning and decisions verbatim.

Google's production multi-agent framework (2026) uses sliding-window summarization: when context exceeds a threshold, older events are summarized and the raw events pruned. Multi-resolution summaries allow both breadth and depth of recall.

#### Conflict Resolution for Concurrent Writes

For semantic conflicts (contradictory conclusions from parallel agents), the research consensus is orchestrator-mediated resolution: a supervisor or merge agent receives conflicting claims with their evidence and produces a reconciled view.

Multi-agent memory surveys (2025--2026) identify three strategies:
1. **Last-write-wins**: simple but discards potentially correct earlier findings.
2. **Evidence-weighted merge**: a resolver ranks conflicting claims by supporting evidence and selects or synthesizes.
3. **Orchestrator arbitration**: the control component decides, optionally escalating to a human.

#### Error Propagation in Forward-Only Flow

A fundamental tension: when a downstream agent discovers the plan was wrong, it cannot fix upstream. Error snowballing weakens downstream success rates exponentially (Sherlock, 2025). The practical solution: downstream agents signal failure to the orchestrator, which invalidates the affected subtree and re-plans.

#### Forward-Only vs. Bidirectional Context Flow

| Property | Forward-only (DAG) | Bidirectional (cycles/feedback) |
|---|---|---|
| **Determinism** | High --- given inputs, the DAG produces deterministic execution order | Low --- feedback loops make execution order data-dependent |
| **Debuggability** | Each node's input is fully determined by its ancestors | Hard to reconstruct what an agent saw after multiple rounds |
| **Token cost** | Linear in DAG depth | Can grow unboundedly as agents iterate |
| **Error correction** | Must re-plan from orchestrator level | Agents can self-correct in-loop |
| **Convergence** | Guaranteed (DAG is acyclic) | Risk of infinite loops without termination guards |

Forward-only is the right default for a GitHub issue resolution system where reliability, auditability, and bounded cost matter more than iterative refinement within a single run.

---

## Policy

### 1. Three Layers of Context

Context management operates at three layers. All three are active simultaneously:

| Layer | What It Does | Mechanism |
|---|---|---|
| **Edge context** | Typed data flowing along DAG edges from one node to the next | Pydantic models per edge type, validated by orchestrator |
| **Shared context** | Cross-cutting knowledge base accumulating discoveries across the entire DAG run | Append-only structured store, orchestrator as sole writer |
| **Context assembly** | Adaptive strategy that assembles the right context for each node at dispatch time | `ContextProvider` protocol, consumer-driven summarization (MVP), RAG retrieval (future) |

The context assembly layer is authoritative. When edge context rules and shared context rules produce a payload that exceeds the node's budget or is suboptimal for the consumer, the context assembly layer decides what to include, summarize, mask, or omit.

### 2. Typed Context Objects at Every Edge

Every DAG edge carries a typed Pydantic model, not free text. The schema is defined per edge type. Each model declares exactly which fields the downstream agent needs. The orchestrator validates the output against the schema before passing it downstream.

```python
class NodeResult(BaseModel):
    output: AgentOutput          # Downstream agents see this
    meta: ExecutionMeta          # Orchestrator sees this --- never passed downstream
```

Agent output and execution metadata (token usage, duration, retry count, model, cost) are strictly separated. Downstream agents reason about the task, not about upstream agent performance.

### 3. The Original Issue Anchors Every Node

Every agent receives the original GitHub issue text as a top-level field in its input context, regardless of how deep in the DAG or how many nesting levels removed. This field is immutable and identical across all nodes in a run. It is never summarized, truncated, or omitted.

### 4. Context Flows Forward Only

Downstream agents never send data, corrections, or signals to upstream agents. Information flows in one direction: from upstream to downstream, following DAG edges. If a downstream agent discovers a problem with the upstream plan:

1. The agent marks its own result as `failed` with a structured error describing the issue.
2. The orchestrator receives the failure.
3. The orchestrator decides: retry the failed node with enriched context, invalidate the upstream subtree and re-plan, or escalate to human.

Downstream agents never modify upstream state, re-invoke upstream agents, or write to upstream context objects.

This rule applies at every level: outer DAG edges, sub-agent edges within composite nodes, and across nesting levels. Within a Coding Node's unrolled cycle, the Debugger's diagnosis feeds cycle N+1's Programmer forward --- it never flows backwards to cycle N's Test Designer.

### 5. Immutable Context Snapshots

Once a node completes, its output context is frozen. No subsequent node, retry, or orchestrator action modifies a completed node's stored output. If a node must be re-executed, a new node instance is created with a new attempt number, producing a new immutable snapshot.

This applies to sub-agent outputs within composite nodes (per Policy 20 §5). The full history of sub-agent outputs is persisted and available.

### 6. Structured Failure Signals

When a downstream agent determines that upstream output is incorrect or insufficient, it expresses this as a structured field in its own (failed) result:

```python
class UpstreamIssue(BaseModel):
    source_node_id: str
    field: str                          # Which field is wrong
    description: str                    # What's wrong
    evidence: str                       # How the agent knows

class AgentOutput(BaseModel):
    status: Literal["success", "failed"]
    upstream_issues: list[UpstreamIssue] = []
    discoveries: list[Discovery] = []   # New findings for shared context
    # ... agent-type-specific fields ...
```

This is not backflow --- the agent reports forward to the orchestrator. The upstream node's output remains immutable.

---

### 7. Shared Context: What Accumulates Across the DAG

Shared context is a typed Pydantic model that accumulates discoveries across all agents in a DAG run, independent of edge topology. It gives later agents access to earlier agents' findings without requiring direct edges between every pair of nodes.

#### What Qualifies as a Discovery

A **discovery** is a structured fact or artifact that meets two criteria: (a) it was not present in the original issue or the agent's input context, and (b) a downstream agent would need it to do correct work.

| Category | Examples | Why It Matters |
|---|---|---|
| **File mappings** | `auth.py:45` contains the token validation logic; `tests/test_auth.py` covers this path | Downstream agents need to know where to look and what already exists |
| **Root cause analysis** | "The bug is caused by an unchecked None return from `get_user()` on line 112" | The single most important discovery --- it anchors all implementation work |
| **Constraints discovered** | "This module has no test coverage"; "The function is called from 3 places" | Prevents downstream agents from making changes that violate unstated invariants |
| **Design decisions made** | "Chose to fix via null check rather than refactoring the caller" | Prevents later agents from undoing or contradicting earlier deliberate choices |
| **Environment facts** | "Python 3.11 required"; "The repo uses pytest, not unittest" | Prevents agents from generating incompatible code |
| **Negative findings** | "Checked `utils.py` --- not relevant to this issue" | Prevents downstream agents from re-investigating dead ends |

The following are **not** discoveries and must not be written to shared context:
- Raw tool output (full file contents, command stdout/stderr) --- observations, not findings.
- Agent reasoning traces or chain-of-thought --- belong in the agent's own output.
- Execution metadata (token counts, timing, retries) --- belong in `ExecutionMeta`.
- Unchanged or already-known information (restating the issue, repeating a parent's output).

#### Shared Context Structure

```python
class SharedContext(BaseModel):
    """Accumulated discoveries across all agents in a DAG run."""

    # Immutable --- set once at DAG creation
    issue: IssueContext
    repo_metadata: RepoMetadata

    # Append-only sections --- agents add entries, never modify existing ones
    file_mappings: list[FileMapping]
    root_causes: list[RootCause]
    constraints: list[Constraint]
    design_decisions: list[DesignDecision]
    negative_findings: list[NegativeFinding]

    # Derived --- recomputed by orchestrator, not written by agents
    summary: str
    active_plan: str
```

Each entry in the append-only sections includes a `source_node` field identifying which agent produced it, enabling provenance tracking and conflict attribution.

#### Write Protocol

Agents do not write to shared context directly. Each agent's typed output includes a `discoveries` field:

```python
class Discovery(BaseModel):
    category: DiscoveryCategory
    content: dict                         # Category-specific structured data
    confidence: float                     # 0.0--1.0, agent's self-assessed confidence
    evidence: str                         # What supports this claim
```

The **orchestrator** is the sole writer to shared context. After a node completes:
1. Validate each discovery against the category schema.
2. Check for conflicts with existing entries (see §9).
3. Append validated, non-conflicting discoveries to the appropriate section.
4. Regenerate the `summary` field if context has grown significantly.

Agents propose discoveries; the orchestrator accepts them. This ensures shared context is always consistent, validated, and conflict-checked.

#### Read Protocol

Agents receive a **view** of shared context, not the full object. The `ContextProvider` (§10) constructs each agent's view based on agent type, relevance, and recency. The view is read-only --- agents receive a snapshot at dispatch time and cannot query shared context dynamically during execution.

---

### 8. Context Size Management

Context size is managed through a tiered strategy, applied progressively until the node's budget is met:

**Tier 1: Structured compaction (always active).** Discoveries are stored as structured fields, not prose. A file mapping is `{"path": "auth.py", "role": "token validation", "lines": "40-60"}`, not a paragraph. This inherently limits per-entry size.

**Tier 2: Observation masking (at the view level).** When constructing an agent's context view, the orchestrator masks bulky evidence fields. The agent sees `evidence: "[masked]"` for entries older than its immediate parent. Full evidence remains in storage for audit.

**Tier 3: Consumer-driven summarization.** When tiers 1--2 cannot meet the budget, an LLM summarization step produces a structured summary tailored to the downstream consumer (see §11).

**Tier 4: Truncation.** If summarization still exceeds budget, truncate the oldest ancestor summaries first.

**Per-node budget:** Each node has a maximum input context budget (in tokens), configured per agent type. The orchestrator enforces this before dispatch.

**Shared context cap:** Shared context views are capped at 25% of the target agent's context budget.

**Pruning order** (what gets removed first):
1. Evidence fields on entries from non-parent nodes (mask them).
2. Negative findings older than 3 nodes back (summarize into a single line).
3. Redundant file mappings (if multiple agents discovered the same file, keep the most recent entry).
4. Full entries from grandparent+ nodes (replace with the summary).

### 9. Conflict Resolution

When the orchestrator detects that a new discovery contradicts an existing entry in shared context, it applies a tiered resolution strategy.

**Detection**: Two entries conflict when they target the same subject (same file, same function, same behavioral claim) but assert incompatible facts. The orchestrator performs shallow matching on `category` + key fields to flag potential conflicts.

**Resolution tiers**:

| Tier | Condition | Action |
|---|---|---|
| **Auto-resolve: higher confidence** | Confidence scores differ by >= 0.3 | Accept the higher-confidence entry; archive the lower one with `superseded_by` reference |
| **Auto-resolve: more recent evidence** | One references code state after the other's changes | Accept the entry based on post-change evidence |
| **Merge: complementary** | Entries address different aspects of the same subject | Keep both, tagged as `complementary` |
| **Escalate: genuine conflict** | Similar confidence, same evidence base, incompatible conclusions | Create a `ConflictRecord` and dispatch a resolution agent or escalate to human |

```python
class ConflictRecord(BaseModel):
    entry_a_node: str
    entry_b_node: str
    category: DiscoveryCategory
    description: str
    resolution: str | None
    resolved_by: str | None               # "auto", node_id, or "human"
```

Unresolved conflicts are surfaced to downstream agents explicitly. This prevents downstream agents from silently adopting one side.

### 10. Provenance and Auditability

Every entry in shared context carries:
- `source_node`: which agent wrote it.
- `timestamp`: when it was accepted.
- `confidence`: agent's self-assessed confidence (0.0--1.0).
- `superseded_by`: reference to a newer entry if this one was overridden.

The orchestrator never deletes entries from the stored shared context. Pruning and masking happen only at the view level. The full shared context is persisted in SQLite, providing a complete audit trail.

Every `ContextSummary` and every `RetrievedFact` records its source nodes and generation metadata. The raw upstream outputs are always preserved in the state store. Context negotiation never modifies upstream outputs --- summarization and retrieval produce new derived artifacts.

---

### 11. Context Assembly: The ContextProvider

All context assembly is mediated through a `ContextProvider` protocol. The orchestrator's executor never directly constructs agent input context --- it delegates to the provider. This is the authoritative mechanism for deciding what each agent sees.

```python
class ContextProvider(Protocol):
    """Assembles the context payload for a DAG node before dispatch."""

    async def get_context(
        self,
        node: DAGNode,
        dag: DAG,
        shared_context: SharedContext,
    ) -> NodeContext:
        ...

class NodeContext(BaseModel):
    """The complete context payload delivered to an agent at dispatch time."""
    issue: IssueContext                    # Always present
    parent_outputs: dict[str, AgentOutput] # Full outputs from immediate parents
    ancestor_context: AncestorContext      # Summary or retrieved context from earlier nodes
    shared_context_view: SharedContextView # Filtered view of shared context
    context_budget_used: int               # Tokens consumed by this payload

class AncestorContext(BaseModel):
    """Context from non-parent ancestors. Polymorphic."""
    strategy: Literal["summary", "retrieval", "none"]
    summary: ContextSummary | None = None
    retrieved_facts: list[RetrievedFact] | None = None
    source_node_ids: list[str]
```

### 12. Two-Phase Strategy: Summarization Now, Retrieval Later

Context assembly is implemented in two phases. Phase 1 (MVP) uses consumer-driven LLM summarization. Phase 2 adds RAG-based retrieval. Phase 1 must be implemented such that Phase 2 can be added by swapping the `ContextProvider` implementation, not by refactoring the executor.

#### Phase 1 (MVP): Consumer-Driven Summarization

**For linear edges (depth <= 3 from current node):**
Static typed schemas. The immediate parent's full typed output is passed. Grandparent and great-grandparent outputs are passed as-is if the total fits within the node's context budget.

**For linear edges (depth > 3 from current node):**
An LLM summarization step is inserted before dispatch. The summarizer receives:
- All ancestor outputs beyond depth 3
- The downstream agent's system prompt (role description)
- The downstream agent's input schema (declared fields)

The summarizer produces a structured summary tailored to the downstream consumer. This is **consumer-driven summarization** --- the summary is shaped by what the receiver needs, not just what the sender produced.

```python
class ContextSummary(BaseModel):
    """LLM-generated summary of upstream context, tailored to a specific consumer."""
    consumer_agent_type: str
    consumer_node_id: str
    source_node_ids: list[str]
    summary: str
    key_facts: list[str]
    warnings: list[str]
    generated_at: datetime
    token_cost: int
```

**For merge points (parallel branches converging):**
An LLM summarization step is always inserted, regardless of depth. The summarizer receives all incoming branch outputs and the downstream agent's role/schema. It integrates findings across branches, flags contradictions, and filters to what the consumer needs.

**Summarization model selection:**
Summarization calls use the cheapest model that can follow a structured output schema (Haiku-tier). Summarization is a context-selection task, not a reasoning task.

#### Phase 2 (Future): RAG-Based Context Retrieval

Phase 2 replaces the depth > 3 summarization step with retrieval from a shared context store:

1. **Index** every node's output into a context store.
2. **Generate a retrieval query** using the downstream agent's task description and input schema.
3. **Retrieve** the top-k most relevant facts/outputs from the store.
4. **Inject** retrieved context into the agent's prompt alongside the immediate parent's full output.

This replaces the lossy compression cascade with targeted retrieval. Context that was "summarized away" at depth 4 is still available at depth 12 if it's relevant.

#### Context Store Interface

The context store is defined as a protocol from day one, even though Phase 1 only uses it for writes (persisting node outputs for audit). Phase 2 activates reads.

```python
class ContextStore(Protocol):
    """Persistent store for node outputs, queryable for retrieval."""

    async def store(
        self,
        node_id: str,
        dag_id: str,
        agent_type: str,
        output: AgentOutput,
    ) -> None: ...

    async def retrieve(
        self,
        query: str,
        dag_id: str,
        top_k: int = 5,
        exclude_node_ids: list[str] | None = None,
    ) -> list[RetrievedFact]: ...
```

Phase 1: `SqliteContextStore` stores node outputs as JSON rows. `retrieve` uses structured field matching (category, file paths, function names).

Phase 2: Replace or augment with a vector-store-backed implementation for semantic similarity retrieval. The protocol does not change.

### 13. When to Summarize vs. When to Pass Through

Not every edge needs summarization. The decision tree:

1. **Immediate parent edge, depth <= 3:** Pass through. Use static typed schema. No summarization.
2. **Immediate parent edge, depth > 3:** Pass parent output in full. Summarize grandparent+ outputs using consumer-driven summarization.
3. **Merge point (2+ incoming edges):** Always summarize. Parallel branches produce independent context that must be integrated, not concatenated.
4. **Context budget exceeded at any depth:** Apply observation masking first (§8, tier 2). If still over budget, summarize. If still over budget, truncate oldest ancestor summaries.

### 14. Summarization Budget

Summarization calls are tracked and budgeted:

- Each summarization call's token cost is recorded in `ContextSummary.token_cost` and charged against the DAG's total token budget.
- Summarization cost is capped at 10% of the DAG's total budget. If summarization would exceed this cap, the system falls back to structural techniques only (observation masking + schema filtering).
- The orchestrator logs a warning when summarization costs exceed 5% of the DAG budget, as an early signal that DAG depth may warrant Phase 2 retrieval.

### 15. Summary Regeneration Protocol

The `summary` field on `SharedContext` is regenerated by the orchestrator at two trigger points:

1. **After any node completion that adds 3+ discoveries.** A batch of new findings warrants a refreshed summary.
2. **Before dispatching any node whose context view would exceed the size budget without summarization.**

The summary prompt is fixed (not agent-authored):

```
Given the following discoveries from a GitHub issue resolution DAG,
produce a concise summary (max 500 tokens) covering:
- The root cause (if identified)
- Key files and their roles
- Design decisions made so far
- Active constraints
- Unresolved conflicts (if any)

Discoveries:
{json_serialized_discoveries}
```

The summary is a derived artifact --- never a source of truth. If an agent needs precise discovery content, it reads the structured entry, not the summary.

---

### 16. Context Within Composite Nodes

Composite nodes (Policy 20) contain internal DAGs. The same context rules apply inside composite nodes as between outer DAG nodes --- the internal structure is an unrolled DAG with typed edges, forward-only flow, and immutable snapshots.

**Within the Coding Node's cycle:** The cycle (Programmer → Test Designer → Test Executor → Debugger → repeat) is an unrolled DAG. Each sub-agent produces typed output consumed by the next. The Debugger's diagnosis feeds cycle N+1's Programmer forward. Earlier cycles are summarized for later cycles, emphasizing what was tried and why it failed, so the Programmer avoids repeating the same approach.

```
Cycle 3 Programmer receives:
├── Original subtask (always)
├── Cycle 2 Debugger diagnosis (immediate parent — full)
├── Cycle 1--2 summary: "approaches tried and why they failed"
└── Current file state (via shared worktree, per Policy 20 §13)
```

**Within the Planning Node (MVP):** The single Planner agent runs a tool-use loop. Context assembly delivers the `NodeContext` at dispatch time; the agent's internal tool calls (file reads, code search) are its own concern, not managed by the `ContextProvider`.

**Sub-agent discoveries:** Sub-agents within composite nodes can produce discoveries. These are collected by the composite node's internal executor and proposed to the orchestrator when the node completes. Per Policy 20 §5, sub-agent outputs are persisted, so discoveries survive even if the composite node ultimately fails.

### 17. Context Across Nesting Levels

Each nesting level exists because the prior level's approach failed or requires further decomposition. Context flows across nesting levels through the same mechanisms:

- **Shared context** accumulates across the entire run, not per nesting level. Discoveries from L1's research remain available to L2's agents.
- **The `ContextProvider`** assembles context for each node regardless of which nesting level it occupies, using the same depth thresholds and summarization rules.
- **The terminal Planning Node** at each level receives the full output of the Coding Node and Review. When it replans and spawns the next level, its output (including what was tried and why it failed) flows forward as edge context to the new level.

The same consumer-driven summarization applies across levels. If cumulative depth across nesting levels exceeds the depth threshold, the `ContextProvider` summarizes older-level outputs for the consumer.

---

### 18. Invariants

The following hold at all times, across both phases, all nesting levels, and inside composite nodes:

1. **The original issue is always included verbatim.** Never summarized, truncated, or omitted.
2. **Immediate parent outputs are always included in full.** Summarization only applies to grandparent+ ancestors and merge-point integration.
3. **Context assembly is the orchestrator's responsibility.** Agents never fetch, query, or negotiate context themselves. They receive a `NodeContext` at dispatch time and work with what they're given.
4. **All context transformations are auditable.** Every summary and every retrieved fact records its source nodes and generation metadata. Raw upstream outputs are always preserved.
5. **Context management never modifies upstream outputs.** Summarization and retrieval produce new derived artifacts. Original node outputs remain immutable in storage.
6. **The orchestrator is the sole writer to shared context.** Agents propose discoveries; the orchestrator validates and appends.
7. **Forward-only flow is absolute.** No agent, sub-agent, or orchestrator action sends information backwards along an edge.

---

## Rationale

This policy unifies three concerns --- edge-based context passing, cross-cutting shared knowledge, and adaptive context assembly --- into a single framework. The underlying stance is that context management is worth significant investment:

- **Consumer-driven summarization costs tokens but saves failures.** A Haiku-tier summarization call is cheap relative to a Sonnet/Opus agent invocation that produces garbage because it received irrelevant or overwhelming context.

- **Structured shared context prevents re-investigation.** Without it, the third agent in a chain re-reads files that the first agent already analyzed, wasting both tokens and wall-clock time.

- **The `ContextProvider` abstraction is the real deliverable.** Even if Phase 2 (RAG retrieval) is never built, the protocol makes context assembly testable, swappable, and decoupled from execution logic.

- **Forward-only flow with orchestrator-controlled re-planning** is more predictable than agent-to-agent negotiation loops. Reliability and auditability matter more than iterative refinement within a single run.

- **Immutable snapshots and typed schemas** mean every agent's input and output is fully reconstructable. This is critical for debugging failed resolutions and for learning from mistakes across runs.

- **Explicit conflict handling** prevents downstream agents from silently adopting one side of a disagreement. Surfacing conflicts (even unresolved) lets agents reason about uncertainty.

- **Context budgets bound cost.** Token usage scales linearly with DAG size, not exponentially with iteration depth.

The two-phase approach is pragmatic: Phase 1 (consumer-driven summarization) reuses existing Pydantic schemas and is cheap to build. Phase 2 (RAG retrieval) replaces the lossy compression cascade with targeted retrieval, activated by swapping a `ContextProvider` implementation. The SQLite-based store from Phase 1 provides a working retrieval backend that validates the integration before investing in vector search.

---

## Interaction with Other Policies

### Supersedes

- **Policy 05 (Context Passing):** All rules from 05 are restated here (§§2--6). The typed edge schemas, forward-only flow, immutable snapshots, and budget rules are unchanged. Consumer-driven summarization (§12) replaces 05's static ancestor summarization rule where they differ.
- **Policy 12 (Shared Context Accumulation):** All rules from 12 are restated here (§§7--10, 15). Discovery definitions, write/read protocols, conflict resolution, and provenance are unchanged.
- **Policy 13 (Context Negotiation):** All rules from 13 are restated here (§§11--14). The `ContextProvider` protocol, two-phase strategy, summarization budget, and invariants are unchanged. Where 05 and 13 overlapped (ancestor summarization, budget enforcement), 13's mechanisms take priority.

### Complements

- **Policy 01 (DAG Orchestration):** This policy governs what data moves through the DAG. Policy 01 governs DAG structure, nesting, and traversal.
- **Policy 09 (Budget Allocation):** Budget allocation and enforcement are unchanged. This policy defines how context consumes budget and what happens when budget is exceeded.
- **Policy 20 (Node Execution Model):** This policy defines context flow within and across composite nodes. Policy 20 defines composite node structure, persistence, and failure handling. The two complement each other directly: 20 §5 (sub-agent persistence) enables this policy's §16 (composite node context), and 20 §6 (cycle history flows forward) feeds this policy's cross-level context assembly.
