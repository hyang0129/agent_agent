# Context Negotiation Policy

## Background

### Problem

Agent Agent follows a Maximum Agent Separation policy, decomposing agent roles into the smallest independently-scoped units. A single GitHub issue resolution may produce DAGs of 10--15+ nodes. At this depth, static context-passing strategies break down in predictable ways:

1. **Full history forwarding** scales as O(N²) in token cost and suffers from the lost-in-the-middle effect. By node 8--10, early outputs are effectively invisible to the model despite being present in the prompt.

2. **Parent-only forwarding** is token-efficient (O(N) total) but causes **semantic drift**. Each node reinterprets upstream signal through its own lens. By node 5--6, the chain's understanding of intermediate findings has mutated like a game of telephone. Anchoring the original issue (context-passing policy § 6) prevents goal drift but does not prevent drift in intermediate findings.

3. **Summarize-and-forward** creates a **lossy compression cascade**. Each summarization step discards detail that seems irrelevant at the current node but may be critical further downstream. Worse, summarization errors compound --- a slight mischaracterization at node 2 becomes an unchallenged "fact" for all downstream nodes. There is no self-correction mechanism.

For DAGs of depth 3--4, these failure modes are negligible. For the deep DAGs that Maximum Agent Separation produces, they are structural problems that require a deliberate mitigation strategy.

### State of the Art

#### LLM-Mediated Context Selection

Google's "Society of Mind" (2023) used natural language debate rounds between agents to negotiate what context to share. This reduced irrelevant context transfer but added significant latency from negotiation turns --- each context handoff required multiple LLM round-trips.

AutoGen v0.4+ introduced "selector group chat" where an LLM dynamically picks which agent speaks next and what context slice to forward. This is effectively an LLM-as-router making relevance judgments at each graph edge, replacing static routing with adaptive routing.

#### Consumer-Driven Context Extraction

Some production systems give the context-selection LLM the downstream agent's system prompt and input schema, then ask it to extract only what that specific consumer would need. This makes context negotiation **consumer-driven** (shaped by what the receiver needs) rather than **producer-driven** (shaped by what the sender outputs). The consumer's schema acts as the negotiation protocol.

#### RAG for Agent Memory

CrewAI implements RAG-based memory (ChromaDB) where agents retrieve relevant context from a shared store via semantic search. AutoGen v0.4's Memory protocol decouples storage from retrieval, allowing swappable backends (vector stores, databases, knowledge graphs) without changing agent logic. MemGPT/Letta uses tiered memory (working memory + searchable archival store) to prevent context overflow while preserving access to historical information.

The common pattern: instead of forwarding context along edges, agents **pull** relevant context from a shared store at dispatch time. This scales independently of DAG depth --- retrieval cost is O(1) per node regardless of how many ancestors exist.

#### Hybrid Approaches

JetBrains Research (NeurIPS 2025) found that combining structured observation masking with selective LLM summarization achieved 7--11% cost reduction over either approach alone. The key insight: use cheap structural techniques (masking, schema filtering) as the default, and reserve expensive LLM summarization for cases where structural techniques alone cannot meet the context budget.

## Policy

### 1. Two-Phase Strategy: Summarization Now, Retrieval Later

Context negotiation is implemented in two phases. Phase 1 (MVP) uses typed schemas with LLM-powered summarization at merge points and depth thresholds. Phase 2 adds RAG-based retrieval from a shared context store. Phase 1 must be implemented such that Phase 2 can be added without refactoring the agent dispatch path.

### 2. Phase 1 (MVP): Typed Schemas with Adaptive Summarization

Phase 1 handles context passing for linear and converging edges using summarization, not retrieval.

**For linear edges (depth <= 3 from current node):**
Static typed schemas per context-passing policy § 1. The immediate parent's full typed output is passed to the child. Grandparent and great-grandparent outputs are passed as-is if the total fits within the node's context budget.

**For linear edges (depth > 3 from current node):**
An LLM summarization step is inserted before dispatch. The summarizer receives:
- All ancestor outputs beyond depth 3
- The downstream agent's system prompt (role description)
- The downstream agent's input schema (declared fields)

The summarizer produces a structured summary tailored to the downstream consumer. This is **consumer-driven summarization** --- the summary is shaped by what the receiver needs, not just what the sender produced.

```python
class ContextSummary(BaseModel):
    """LLM-generated summary of upstream context, tailored to a specific consumer."""
    consumer_agent_type: str               # Who this summary was generated for
    consumer_node_id: str                  # Which node will receive it
    source_node_ids: list[str]             # Which upstream nodes were summarized
    summary: str                           # The tailored summary text
    key_facts: list[str]                   # Extracted facts relevant to consumer
    warnings: list[str]                    # Upstream issues the consumer should know about
    generated_at: datetime
    token_cost: int                        # Cost of the summarization call
```

**For merge points (parallel branches converging):**
An LLM summarization step is always inserted, regardless of depth. The summarizer receives all incoming branch outputs and the downstream agent's role/schema. It produces a single `ContextSummary` that integrates findings across branches, flags contradictions, and filters to what the downstream consumer needs.

**Summarization model selection:**
Summarization calls use the cheapest model that can follow a structured output schema (Haiku-tier). Summarization is a context-selection task, not a reasoning task --- it does not require the full capabilities of the agent execution model.

### 3. Phase 2 (Future): RAG-Based Context Retrieval

Phase 2 replaces the depth > 3 summarization step with retrieval from a shared context store. Instead of summarizing upstream outputs into a single blob, the system:

1. **Indexes** every node's output into a context store (structured fields, not embeddings, for MVP; vector embeddings as an upgrade path).
2. **Generates a retrieval query** using the downstream agent's task description and input schema.
3. **Retrieves** the top-k most relevant facts/outputs from the store.
4. **Injects** retrieved context into the agent's prompt alongside the immediate parent's full output.

This replaces the lossy compression cascade with targeted retrieval. Context that was "summarized away" at depth 4 is still available at depth 12 if it's relevant.

### 4. Context Provider Interface

To ensure Phase 2 can be added without refactoring the dispatch path, all context assembly is mediated through a `ContextProvider` interface. The orchestrator's executor never directly constructs agent input context --- it delegates to the provider.

```python
class ContextProvider(Protocol):
    """Assembles the context payload for a DAG node before dispatch."""

    async def get_context(
        self,
        node: DAGNode,
        dag: DAG,
        shared_context: SharedContext,
    ) -> NodeContext:
        """
        Produce the full context payload for a node.

        Implementations decide how upstream outputs, shared context,
        and the original issue are assembled into the node's input.
        """
        ...

class NodeContext(BaseModel):
    """The complete context payload delivered to an agent at dispatch time."""
    issue: IssueContext                    # Always present (policy 05 § 6)
    parent_outputs: dict[str, AgentOutput] # Full outputs from immediate parents
    ancestor_context: AncestorContext      # Summary or retrieved context from earlier nodes
    shared_context_view: SharedContextView # Filtered view of shared context (policy 12 § 4)
    context_budget_used: int               # Tokens consumed by this payload

class AncestorContext(BaseModel):
    """Context from non-parent ancestors. Polymorphic --- filled by summary or retrieval."""
    strategy: Literal["summary", "retrieval", "none"]
    summary: ContextSummary | None = None          # Phase 1: LLM-generated summary
    retrieved_facts: list[RetrievedFact] | None = None  # Phase 2: RAG results
    source_node_ids: list[str]                     # Which ancestors contributed

class RetrievedFact(BaseModel):
    """A single fact retrieved from the context store. Phase 2."""
    source_node_id: str
    category: str                          # Maps to discovery categories (policy 12 § 1)
    content: dict                          # Structured fact
    relevance_score: float                 # How relevant to the consumer's task
```

**Phase 1 implementation:** A `SummarizingContextProvider` that implements the protocol using the summarization logic from § 2.

**Phase 2 implementation:** A `RetrievalContextProvider` that implements the protocol using indexed storage and retrieval from § 3. The executor's dispatch logic does not change --- only the provider implementation is swapped.

### 5. Context Store Interface

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
    ) -> None:
        """Index a node's output for future retrieval."""
        ...

    async def retrieve(
        self,
        query: str,
        dag_id: str,
        top_k: int = 5,
        exclude_node_ids: list[str] | None = None,
    ) -> list[RetrievedFact]:
        """Retrieve the most relevant facts for a query within a DAG run."""
        ...
```

**Phase 1 implementation:** `SqliteContextStore` --- stores node outputs as JSON rows in the existing SQLite state store. The `retrieve` method is implemented but returns results based on structured field matching (category, file paths, function names), not semantic search. This is sufficient for testing the retrieval path end-to-end.

**Phase 2 upgrade path:** Replace or augment with a vector-store-backed implementation that embeds node outputs and retrieves via semantic similarity. The protocol does not change.

### 6. Summarization Budget

Summarization calls are not free. They are tracked and budgeted:

- Each summarization call's token cost is recorded in `ContextSummary.token_cost` and charged against the DAG's total token budget.
- Summarization cost is capped at 10% of the DAG's total budget. If summarization would exceed this cap, the system falls back to structural techniques only (observation masking + schema filtering per shared-context policy § 5).
- The orchestrator logs a warning when summarization costs exceed 5% of the DAG budget, as an early signal that DAG depth may warrant Phase 2 retrieval.

### 7. When to Summarize vs. When to Pass Through

Not every edge needs summarization. The decision tree:

1. **Immediate parent edge, depth <= 3:** Pass through. Use static typed schema. No summarization.
2. **Immediate parent edge, depth > 3:** Pass parent output in full. Summarize grandparent+ outputs using consumer-driven summarization.
3. **Merge point (2+ incoming edges):** Always summarize. Even at shallow depth, parallel branches produce independent context that must be integrated, not concatenated.
4. **Context budget exceeded at any depth:** Apply observation masking first (shared-context policy § 5, tier 2). If still over budget, summarize. If still over budget, truncate oldest ancestor summaries.

### 8. Invariants

The following invariants hold across both phases:

- **The original issue is always included verbatim.** It is never summarized, truncated, or omitted. (Inherited from context-passing policy § 6.)
- **Immediate parent outputs are always included in full.** Summarization only applies to grandparent+ ancestors and merge-point integration.
- **Context assembly is the orchestrator's responsibility.** Agents never fetch, query, or negotiate context themselves. They receive a `NodeContext` at dispatch time and work with what they're given.
- **All context transformations are auditable.** Every `ContextSummary` and every `RetrievedFact` records its source nodes and generation metadata. The raw upstream outputs are always preserved in the state store.
- **Context negotiation never modifies upstream outputs.** Summarization and retrieval produce new derived artifacts. The original node outputs remain immutable in storage.

## Rationale

This policy addresses the specific scaling problem created by Maximum Agent Separation: deep DAGs where static context-passing degrades. The two-phase approach is pragmatic:

- **Phase 1 is cheap to build.** Consumer-driven summarization reuses the existing Pydantic schemas and shared context infrastructure. The only new component is the summarization call at deep edges and merge points, using a cheap model.
- **Phase 2 is expensive to build but easy to add.** The `ContextProvider` and `ContextStore` protocols ensure that retrieval can be activated by swapping an implementation, not by refactoring the executor. The SQLite-based store from Phase 1 provides a working (if basic) retrieval backend that validates the integration before investing in vector search.
- **The protocols are the real deliverable.** Even if Phase 2 is never built, the `ContextProvider` abstraction makes context assembly testable, swappable, and decoupled from execution logic. This is valuable independent of the retrieval feature.

The consumer-driven summarization design (giving the summarizer the downstream agent's role and schema) is the minimal form of "LLM-negotiated context." It achieves the core benefit --- context relevance shaped by the receiver's needs --- without the complexity of multi-turn negotiation or agent-to-agent debate. The downstream agent's schema is the negotiation protocol.
