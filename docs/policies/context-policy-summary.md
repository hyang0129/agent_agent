# Context Policies Summary: 05, 12, and 13

Policies 05, 12, and 13 all deal with how context moves through the DAG, but they address different layers of the problem. This document explains what each policy covers, where they overlap, and how they differ.

## Quick Comparison

| Aspect | 05 — Context Passing | 12 — Shared Context | 13 — Context Negotiation |
|---|---|---|---|
| **Core question** | How does output flow along DAG edges? | What accumulates across the entire DAG run? | How do we keep context useful in deep DAGs? |
| **Metaphor** | Passing a baton in a relay race | A shared notebook everyone writes in | A librarian who picks what each runner reads |
| **Data model** | Typed Pydantic models per edge | `SharedContext` — an append-only structured store | `ContextProvider` — an abstraction over assembly strategy |
| **Who writes** | Each agent produces a typed output; orchestrator validates and forwards | Agents propose discoveries; orchestrator is sole writer | Orchestrator (via `ContextProvider`) assembles per-node payloads |
| **Who reads** | The immediate downstream node(s) | All downstream agents, via filtered views | Each node, via a `NodeContext` assembled at dispatch |
| **Scope** | One edge at a time | Entire DAG run | Entire DAG run |
| **Conflict handling** | Not addressed (single-writer per edge) | Explicit: confidence-based auto-resolve, merge, or escalate | Not addressed (delegates to 12) |
| **Scaling strategy** | Ancestor summaries + token budget per node | 3-tier: structured compaction, observation masking, summary regeneration | 2-phase: summarization (MVP) then RAG retrieval (future) |

## Policy 05 — Context Passing

**What it covers:** The mechanics of moving typed data along DAG edges from one agent to the next.

**Key rules:**
- Every edge carries a typed Pydantic model, not free text.
- The original issue is included verbatim at every node.
- Immediate parent output is passed in full; earlier ancestors are passed as structured summaries.
- Context flows forward only — no backflow. Failures are reported forward as structured signals.
- Each node's output is immutable once complete.
- Agent output and execution metadata are strictly separated.
- Each node has a token budget; oldest ancestor summaries are truncated first.

**In short:** Policy 05 defines the *plumbing* — the shape of data on each edge and the rules for what gets forwarded.

## Policy 12 — Shared Context Accumulation

**What it covers:** A cross-cutting shared knowledge base that accumulates discoveries across all nodes in a DAG run, independent of edge topology.

**Key rules:**
- Defines what qualifies as a "discovery" (file mappings, root causes, constraints, design decisions, environment facts, negative findings) vs. what does not (raw tool output, reasoning traces, execution metadata).
- Shared context is a typed Pydantic model with append-only sections, not a free-text blob.
- Agents propose discoveries in their output; the orchestrator validates and appends them.
- Agents receive a filtered *view* of shared context at dispatch time (read-only snapshot), scoped by agent type, relevance, and recency.
- Size management uses three tiers: structured compaction, observation masking, and LLM summary regeneration.
- Contradictory discoveries from parallel agents are explicitly handled via confidence-based auto-resolve, complementary tagging, or escalation.
- Nothing is ever deleted from the stored shared context — pruning is view-level only.

**In short:** Policy 12 defines a *shared notebook* that lets any agent benefit from any earlier agent's findings, even without a direct DAG edge.

## Policy 13 — Context Negotiation

**What it covers:** How to assemble the right context for each node when DAGs get deep (10–15+ nodes), where static forwarding and naive summarization break down.

**Key rules:**
- Two-phase strategy: Phase 1 (MVP) uses consumer-driven LLM summarization; Phase 2 (future) adds RAG-based retrieval.
- Depth <= 3 from current node: pass ancestor outputs through as-is (static schemas).
- Depth > 3: an LLM summarizer, given the downstream agent's role and input schema, produces a tailored summary of older ancestors.
- Merge points (parallel branches converging) always trigger summarization, regardless of depth.
- All context assembly is mediated through a `ContextProvider` protocol, so the executor never constructs context directly — this makes the Phase 2 swap non-breaking.
- Defines a `ContextStore` protocol (write-only in Phase 1, read+write in Phase 2) for persisting and retrieving node outputs.
- Summarization budget is capped at 10% of the DAG's total token budget.

**In short:** Policy 13 defines the *strategy layer* that decides how to assemble context, adapting to DAG depth and topology.

## How They Relate

```
┌─────────────────────────────────────────────────────┐
│                 13 — Context Negotiation             │
│  "What strategy assembles context for this node?"   │
│  (ContextProvider, summarization vs. retrieval)      │
│                                                     │
│   ┌───────────────────┐  ┌────────────────────────┐ │
│   │  05 — Context      │  │  12 — Shared Context   │ │
│   │  Passing            │  │  Accumulation          │ │
│   │  "What flows along  │  │  "What is known across │ │
│   │  each edge?"        │  │  the whole DAG?"       │ │
│   └───────────────────┘  └────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

- **05 provides the foundation.** It defines typed edge schemas, forward-only flow, immutable snapshots, and per-node budgets. Both 12 and 13 inherit and reference these rules.
- **12 adds a cross-cutting layer.** Where 05 only passes data along edges, 12 provides a shared store that any node can read from regardless of graph topology. It answers: "what has the DAG learned so far?"
- **13 adds an adaptive strategy layer.** It sits on top of both 05 and 12, deciding *how* to assemble the combined context (edge data + shared context) for each node based on depth and topology. It introduces the `ContextProvider` abstraction that unifies 05's edge-passing and 12's shared views into a single dispatch payload.

## Where They Overlap

1. **Ancestor summarization.** Both 05 (§3: "ancestors as structured summaries") and 13 (§2: "consumer-driven summarization at depth > 3") discuss summarizing ancestor outputs. Policy 05 states the rule; policy 13 defines the implementation strategy and introduces the consumer-driven approach.

2. **Original issue anchoring.** 05 (§2) establishes that the original issue is always included. 13 (§8) reaffirms this as an invariant.

3. **Context budget enforcement.** 05 (§7) introduces per-node budgets. 12 (§5) caps shared context views at 25% of the node's budget. 13 (§7) adds a decision tree for what to do when the budget is exceeded (mask first, then summarize, then truncate).

4. **Observation masking.** Both 12 (§5, tier 2) and 13 (§7, step 4) reference observation masking as a context reduction technique.

## Key Differences

| Dimension | 05 | 12 | 13 |
|---|---|---|---|
| **Data flow model** | Edge-local (parent → child) | Global (any node → shared store → any later node) | Assembled per-node from both sources |
| **Who decides what to pass** | Static rules (parent in full, ancestors summarized) | Orchestrator filters by agent type + relevance | LLM summarizer tailored to consumer's schema |
| **Conflict resolution** | N/A (single producer per edge) | Tiered: auto-resolve, merge, or escalate | Delegates to 12 |
| **Future evolution** | Stable as-is | May add vector search for discovery retrieval | Phase 2 adds RAG retrieval, replacing summarization for deep ancestors |
| **Implementation cost** | Low (Pydantic models + validation) | Medium (discovery schema, write protocol, view filtering, conflict resolution) | Medium-high (summarization calls, ContextProvider protocol, future ContextStore) |

---

## Unified Policy Position

Policies 05, 12, and 13 merge into a single context management stance. Where they overlap, **Policy 13 takes priority** — its consumer-driven summarization and `ContextProvider` abstraction are the authoritative mechanism for assembling context at every level of the system.

The underlying principle: **context management is worth significant investment.** Spending tokens on summarization, structured discovery tracking, and consumer-tailored context assembly pays for itself by keeping downstream agents focused and effective. Cheap context management (pass everything, or pass nothing) produces expensive failures.

### How the Policies Apply to Composite Nodes and Nesting

**Inside the Coding Node's cycle:** The internal cycle (Programmer → Test Designer → Test Executor → Debugger → repeat) is an unrolled DAG (Policy 20 §4). The same context-passing rules apply — typed outputs on each edge, forward-only flow, immutable snapshots. Earlier cycles are summarized for later cycles, emphasizing what was tried and why it failed, so the Programmer doesn't repeat a dead approach. This is 05 §3 (immediate parent in full, ancestors as summaries) applied to the unrolled cycle, with 13's consumer-driven summarization shaping what the Programmer actually sees.

```
Cycle 3 Programmer receives:
├── Original subtask (always, per 05 §2)
├── Cycle 2 Debugger diagnosis (immediate parent — full)
├── Cycle 1-2 summary: "approaches tried and why they failed"
└── Current file state (via shared worktree, per 20 §13)
```

**Across nesting levels:** Each nesting level exists because the prior level's approach failed. The context that survives across levels — what was tried, why it failed, and what was discovered along the way — follows the same policies. Shared context (12) accumulates discoveries across the entire run. The `ContextProvider` (13) assembles what each Planning Node sees when it replans, including summarized history from prior levels. The original issue anchors every level (05 §2).

**Policy 20 complements the context policies.** It persists sub-agent outputs within composite nodes (§5), so discoveries from failed cycles are available. It defines re-invocation context (§12) as a specific context-passing rule. And it routes full cycle history to the Planning Node on failure (§6), which the `ContextProvider` then summarizes for the next level.
