# Policy 05: Context Management

Context management operates at three simultaneous layers: typed Pydantic objects flowing along DAG edges (edge context), an append-only shared knowledge base accumulating discoveries across the entire run (shared context), and a `ContextProvider` that assembles the right context for each node at dispatch time (context assembly). Information flows forward only — downstream agents never send data or signals to upstream agents. The original GitHub issue is always included verbatim in every agent's context. When context exceeds budget, the system applies a tiered compaction strategy (structured masking → consumer-driven LLM summarization → truncation) to fit within per-node token budgets.

---

### 1. Three Layers of Context

Context management operates at three layers. All three are active simultaneously:

| Layer | What It Does | Mechanism |
|---|---|---|
| **Edge context** | Typed data flowing along DAG edges from one node to the next | Pydantic models per edge type, validated by orchestrator |
| **Shared context** | Cross-cutting knowledge base accumulating discoveries across the entire DAG run | Append-only structured store, orchestrator as sole writer |
| **Context assembly** | Adaptive strategy that assembles the right context for each node at dispatch time | `ContextProvider` protocol, consumer-driven summarization (MVP), RAG retrieval (future) |

The context assembly layer is authoritative. When edge context rules and shared context rules produce a payload that exceeds the node's budget, the context assembly layer decides what to include, summarize, mask, or omit.

### 2. Typed Context Objects at Every Edge

Every DAG edge carries a typed Pydantic model, not free text. Agent output and execution metadata are strictly separated:

```python
class NodeResult(BaseModel):
    output: AgentOutput          # Downstream agents see this
    meta: ExecutionMeta          # Orchestrator sees this — never passed downstream
```

### 3. The Original Issue Anchors Every Node

Every agent receives the original GitHub issue text as a top-level field in its input context, regardless of how deep in the DAG or how many nesting levels removed. This field is immutable and identical across all nodes in a run. It is never summarized, truncated, or omitted.

### 4. Context Flows Forward Only

Downstream agents never send data, corrections, or signals to upstream agents. If a downstream agent discovers a problem with the upstream plan:

1. The agent marks its own result as `failed` with a structured error describing the issue.
2. The orchestrator receives the failure.
3. The orchestrator decides: retry the failed node with enriched context, invalidate the upstream subtree and re-plan, or escalate to human.

### 5. Immutable Context Snapshots

Once a node completes, its output context is frozen. No subsequent node, retry, or orchestrator action modifies a completed node's stored output. If a node must be re-executed, a new node instance is created with a new attempt number.

### 6. Structured Failure Signals

When a downstream agent determines that upstream output is incorrect or insufficient, it expresses this as a structured field in its own (failed) result:

```python
class UpstreamIssue(BaseModel):
    source_node_id: str
    field: str                          # Which field is wrong
    description: str                    # What's wrong
    evidence: str                       # How the agent knows
```

---

### 7. Shared Context: What Accumulates Across the DAG

Shared context is a typed Pydantic model accumulating discoveries across all agents in a DAG run, independent of edge topology.

**What qualifies as a discovery** — a structured fact that (a) was not present in the original issue or agent input, and (b) a downstream agent would need to do correct work:

| Category | Examples |
|---|---|
| File mappings | `auth.py:45` contains token validation logic |
| Root cause analysis | "The bug is caused by an unchecked None return from `get_user()` on line 112" |
| Constraints discovered | "This module has no test coverage" |
| Design decisions made | "Chose to fix via null check rather than refactoring the caller" |
| Environment facts | "Python 3.11 required"; "The repo uses pytest, not unittest" |
| Negative findings | "Checked `utils.py` — not relevant to this issue" |

**Not discoveries**: raw tool output, agent reasoning traces, execution metadata, or already-known information.

#### Shared Context Structure

```python
class SharedContext(BaseModel):
    issue: IssueContext               # Immutable
    repo_metadata: RepoMetadata       # Immutable
    file_mappings: list[FileMapping]  # Append-only
    root_causes: list[RootCause]      # Append-only
    constraints: list[Constraint]     # Append-only
    design_decisions: list[DesignDecision]  # Append-only
    negative_findings: list[NegativeFinding]  # Append-only
    summary: str                      # Derived, recomputed by orchestrator
    active_plan: str                  # Derived
```

#### Write Protocol

Agents do not write to shared context directly. Each agent's typed output includes a `discoveries` field. The **orchestrator** is the sole writer: it validates, conflict-checks, and appends discoveries after each node completes.

#### Read Protocol

Agents receive a **view** of shared context, not the full object. The view is read-only and is a snapshot at dispatch time.

---

### 8. Context Size Management

**Tier 1: Structured compaction (always active).** Discoveries stored as structured fields, not prose.

**Tier 2: Observation masking (at the view level).** Bulky evidence fields are masked for entries older than the immediate parent. Agent sees `evidence: "[masked]"`.

**Tier 3: Consumer-driven summarization.** An LLM summarization step produces a structured summary tailored to the downstream consumer's role and input schema.

**Tier 4: Truncation.** If summarization still exceeds budget, truncate the oldest ancestor summaries first.

**Per-node budget:** Each node has a maximum input context budget (in tokens), configured per agent type.

**Shared context cap:** Shared context views are capped at 25% of the target agent's context budget.

**Pruning order:**
1. Evidence fields on entries from non-parent nodes (mask them).
2. Negative findings older than 3 nodes back.
3. Redundant file mappings.
4. Full entries from grandparent+ nodes (replace with summary).

### 9. Conflict Resolution

When a new discovery contradicts an existing entry in shared context:

| Tier | Condition | Action |
|---|---|---|
| Auto-resolve: higher confidence | Confidence scores differ by >= 0.3 | Accept higher-confidence entry; archive the lower one |
| Auto-resolve: more recent evidence | One references code state after the other's changes | Accept the post-change entry |
| Merge: complementary | Entries address different aspects of same subject | Keep both, tagged `complementary` |
| Escalate: genuine conflict | Similar confidence, same evidence base, incompatible conclusions | Create `ConflictRecord`, dispatch resolution agent or escalate to human |

Unresolved conflicts are surfaced to downstream agents explicitly.

### 10. Provenance and Auditability

Every entry in shared context carries: `source_node`, `timestamp`, `confidence` (0.0–1.0), and `superseded_by`. The orchestrator never deletes entries from stored shared context — pruning and masking happen only at the view level.

---

### 11. Context Assembly: The ContextProvider

All context assembly is mediated through a `ContextProvider` protocol. The orchestrator's executor never directly constructs agent input context — it delegates to the provider.

```python
class NodeContext(BaseModel):
    issue: IssueContext
    parent_outputs: dict[str, AgentOutput]
    ancestor_context: AncestorContext
    shared_context_view: SharedContextView
    context_budget_used: int
```

### 12. Two-Phase Strategy: Summarization Now, Retrieval Later

**Phase 1 (MVP): Consumer-driven summarization.**
- Depth ≤ 3 from current node: pass parent output in full, no summarization.
- Depth > 3: LLM summarization step using downstream agent's system prompt and input schema as the negotiation protocol.
- Merge points (parallel branches converging): always summarize regardless of depth.
- Summarization calls use the cheapest model (Haiku-tier).

**Phase 2 (Future): RAG-based retrieval.** Replaces depth > 3 summarization with vector-store retrieval. Swapped in by replacing the `ContextProvider` implementation, not by refactoring the executor.

### 13. When to Summarize vs. When to Pass Through

1. **Immediate parent edge, depth ≤ 3:** Pass through. No summarization.
2. **Immediate parent edge, depth > 3:** Pass parent in full. Summarize grandparent+ outputs.
3. **Merge point (2+ incoming edges):** Always summarize.
4. **Context budget exceeded at any depth:** Mask first, then summarize, then truncate.

### 14. Summarization Budget

- Each summarization call's token cost is charged against the DAG's total token budget.
- Summarization cost is capped at 10% of the DAG's total budget.
- Log a warning when summarization costs exceed 5% of the DAG budget.

### 15. Summary Regeneration Protocol

The `summary` field on `SharedContext` is regenerated at two trigger points:
1. After any node completion that adds 3+ discoveries.
2. Before dispatching any node whose context view would exceed the size budget without summarization.

The summary is a derived artifact — never a source of truth.

---

### 16. Context Within Composite Nodes

The same context rules apply inside composite nodes as between outer DAG nodes. Within the Coding Node's cycle, earlier cycles are summarized for later cycles, emphasizing what was tried and why it failed.

### 17. Context Across Nesting Levels

Shared context accumulates across the entire run, not per nesting level. The `ContextProvider` assembles context for each node regardless of which nesting level it occupies, using the same depth thresholds and summarization rules.

---

### 18. Invariants

1. **The original issue is always included verbatim.** Never summarized, truncated, or omitted.
2. **Immediate parent outputs are always included in full.** Summarization only applies to grandparent+ ancestors and merge-point integration.
3. **Context assembly is the orchestrator's responsibility.** Agents never fetch, query, or negotiate context themselves.
4. **All context transformations are auditable.** Every summary and retrieved fact records its source nodes and generation metadata.
5. **Context management never modifies upstream outputs.** Summarization and retrieval produce new derived artifacts.
6. **The orchestrator is the sole writer to shared context.**
7. **Forward-only flow is absolute.** No agent, sub-agent, or orchestrator action sends information backwards along an edge.
