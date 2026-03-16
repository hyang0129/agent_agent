# Shared Context Accumulation Policy

## Background

### Problem

Agent Agent uses a shared context object that accumulates discoveries as a DAG executes. Multiple agents --- research, implement, test, review --- write findings into this shared state as they complete their subtasks. But three critical questions are unanswered:

1. **What qualifies as a discovery?** Without criteria, agents dump everything into shared context (verbose logs, intermediate reasoning, raw tool output), bloating token usage and drowning signal in noise. Alternatively, they write too little, and downstream agents lack information that earlier agents already uncovered.

2. **How is context size managed?** A shared context that only grows eventually exceeds the context window of downstream agents. GitHub issue resolution DAGs can involve 5--15 nodes, each contributing discoveries. Without pruning or summarization, the accumulated context becomes the dominant token cost and degrades agent performance via the lost-in-the-middle effect.

3. **How are conflicts resolved when parallel agents write contradictory information?** Two research agents investigating the same codebase may reach different conclusions about root cause. Two implementation agents may record conflicting constraints. Without conflict resolution, downstream agents receive contradictory context and produce incoherent output.

### State of the Art

#### The Blackboard Architecture (Classical AI)

The blackboard model, originating in the HEARSAY-II speech recognition system (1970s) and formalized through the 1980s, is the direct ancestor of shared context in multi-agent systems. Three components define it:

- **Knowledge sources** (agents): independent modules that read from and write to the blackboard. They do not communicate directly with each other.
- **Blackboard** (shared state): a structured data store partitioned into levels or regions. All agent-generated information lives here.
- **Control component**: decides which knowledge source to activate next, based on the current blackboard state.

Recent LLM research has revived this pattern. Exploring Advanced LLM Multi-Agent Systems Based on Blackboard Architecture (2025) demonstrated that blackboard-based LLM agent systems achieve competitive performance with state-of-the-art dynamic multi-agent systems while spending fewer tokens. LLM-Based Multi-Agent Blackboard System for Information Discovery (2026) showed 13--57% improvement over RAG and master-slave paradigms for file discovery tasks --- directly relevant to GitHub issue investigation.

The key property: agents write structured findings to a shared store and read selectively, rather than passing full transcripts between themselves.

#### LangGraph Reducer-Driven State

LangGraph models shared state as a typed object with reducer functions controlling how concurrent writes merge. Each state key can have its own reducer: append to a list, take the latest value, merge dictionaries, or apply custom logic. This gives fine-grained control over conflict resolution at the field level.

LangGraph also enforces access patterns --- nodes declare which state keys they read and write, enabling the framework to detect conflicting writes and apply reducers deterministically. State is checkpointed after every node, providing crash recovery and time-travel debugging.

#### MetaGPT Global Memory Pool

MetaGPT maintains a global memory pool storing all collaboration records. Agents subscribe to relevant artifact types by role (a testing agent subscribes to implementation artifacts, not design documents). Agents actively pull pertinent information rather than passively receiving everything --- a publish-subscribe model that filters context by relevance.

The structured artifact requirement (PRDs, design docs, interface specs rather than free text) ensures that shared memory entries are parseable and schema-conformant.

#### CrewAI Tiered Memory

CrewAI implements four memory types: short-term (ChromaDB + RAG for the current task), long-term (SQLite for cross-session persistence), entity memory (RAG-based knowledge about recurring entities), and contextual memory (an orchestration layer combining the others). Different agents can weight the same memory differently --- a planning agent weights importance while an execution agent weights recency.

A known production limitation: agents accumulating context across many tasks degrade as stale information pollutes recent decisions. CrewAI's documentation explicitly warns that memory pruning strategies are required for long-running crews.

#### AutoGen v0.4 Memory Protocol

AutoGen v0.4 (2025) introduced a Memory protocol with explicit lifecycle methods: `query` (retrieve relevant context), `update_context` (inject retrieved memories into the agent's prompt), `add` (store new memories), and `clear`. This decouples memory storage from memory retrieval, allowing different backends (vector stores, databases, knowledge graphs) without changing agent logic.

#### Context Size Management Research

JetBrains Research (NeurIPS 2025) empirically compared two approaches to context management for LLM agents:

- **Observation masking**: preserve the agent's action and reasoning history but truncate environment observations (tool outputs, file contents). Effective because agent context is heavily skewed toward observations.
- **LLM summarization**: compress the full history into compact form. Higher quality but adds a summarization LLM call per compression.
- **Hybrid**: combine both --- summarize older history, mask observations in recent history. Achieved 7--11% cost reduction over either approach alone.

The key finding: simple observation masking matched or outperformed LLM summarization in 4 of 5 setups, at lower cost. Summarization is only worth its overhead when preserving nuanced reasoning across many steps.

Google's production multi-agent framework (2026) uses sliding-window summarization: when context exceeds a threshold, older events are summarized and the raw events pruned. Multi-resolution summaries --- a global summary plus agent-specific fine-grained logs --- allow both breadth and depth of recall.

#### Conflict Resolution for Concurrent Writes

CodeCRDT (2025) applied Conflict-Free Replicated Data Types to multi-agent code generation, enabling lock-free concurrent writes with deterministic convergence. While CRDTs guarantee eventual consistency, they are designed for data structures (counters, sets, sequences), not for semantic claims like "the root cause is X."

For semantic conflicts (contradictory conclusions from parallel agents), the research consensus is orchestrator-mediated resolution: a supervisor or merge agent receives conflicting claims with their evidence and produces a reconciled view. This is analogous to the "conflict resolution node" pattern in LangGraph, where a dedicated graph node receives conflicting state and applies domain-specific resolution logic.

Multi-agent memory surveys (2025--2026) identify three conflict resolution strategies:
1. **Last-write-wins**: simple but discards potentially correct earlier findings.
2. **Evidence-weighted merge**: a resolver ranks conflicting claims by supporting evidence and selects or synthesizes.
3. **Orchestrator arbitration**: the control component (orchestrator) decides, optionally escalating to a human.

## Policy

### 1. Definition: What Qualifies as a Discovery

A **discovery** is a structured fact or artifact that meets two criteria: (a) it was not present in the original issue or the agent's input context, and (b) a downstream agent would need it to do correct work. Specifically, the following categories are persisted to shared context:

| Category | Examples | Why It Matters |
|---|---|---|
| **File mappings** | `auth.py:45` contains the token validation logic; `tests/test_auth.py` covers this path | Downstream agents need to know where to look and what already exists |
| **Root cause analysis** | "The bug is caused by an unchecked None return from `get_user()` on line 112" | The single most important discovery --- it anchors all implementation work |
| **Constraints discovered** | "This module has no test coverage"; "The function is called from 3 places"; "There is a type: ignore comment suppressing a real error" | Prevents downstream agents from making changes that violate unstated invariants |
| **Design decisions made** | "Chose to fix via null check rather than refactoring the caller"; "Added a new helper function rather than modifying the existing one" | Prevents later agents from undoing or contradicting earlier deliberate choices |
| **Environment facts** | "Python 3.11 required"; "The repo uses pytest, not unittest"; "CI runs on GitHub Actions" | Prevents agents from generating incompatible code |
| **Negative findings** | "Checked `utils.py` --- not relevant to this issue"; "The existing test at line 80 does NOT cover the error path" | Prevents downstream agents from re-investigating dead ends |

The following are **not** discoveries and must not be written to shared context:
- Raw tool output (full file contents, command stdout/stderr) --- these are observations, not findings.
- Agent reasoning traces or chain-of-thought --- these belong in the agent's own output, not shared state.
- Execution metadata (token counts, timing, retries) --- these belong in `ExecutionMeta`, per context-passing policy 6.
- Unchanged or already-known information (restating the issue description, repeating a parent's output).

### 2. Shared Context Structure

Shared context is a typed Pydantic model, not a free-text blob. It is partitioned into sections that correspond to the discovery categories above:

```python
class SharedContext(BaseModel):
    """Accumulated discoveries across all agents in a DAG run."""

    # Immutable --- set once at DAG creation
    issue: IssueContext
    repo_metadata: RepoMetadata          # language, framework, test runner, CI

    # Append-only sections --- agents add entries, never modify existing ones
    file_mappings: list[FileMapping]      # path, role, discovered_by_node
    root_causes: list[RootCause]         # description, evidence, confidence, source_node
    constraints: list[Constraint]         # description, source_node, category
    design_decisions: list[DesignDecision]  # decision, alternatives_considered, rationale, source_node
    negative_findings: list[NegativeFinding]  # what_was_checked, why_irrelevant, source_node

    # Derived --- recomputed by orchestrator, not written by agents
    summary: str                          # LLM-generated summary, updated after each node
    active_plan: str                      # Current approach, updated on re-planning
```

Each entry in the append-only sections includes a `source_node` field identifying which agent produced it, enabling provenance tracking and conflict attribution.

### 3. Write Protocol: How Agents Contribute Discoveries

Agents do not write to shared context directly. Instead, each agent's typed output includes a `discoveries` field:

```python
class AgentOutput(BaseModel):
    status: Literal["success", "failed"]
    # ... agent-type-specific fields ...
    discoveries: list[Discovery]          # New findings for shared context

class Discovery(BaseModel):
    category: DiscoveryCategory           # file_mapping, root_cause, constraint, etc.
    content: dict                         # Category-specific structured data
    confidence: float                     # 0.0--1.0, agent's self-assessed confidence
    evidence: str                         # What supports this claim
```

The **orchestrator** is the sole writer to shared context. After a node completes:
1. Validate each discovery against the category schema.
2. Check for conflicts with existing entries (see Policy 6).
3. Append validated, non-conflicting discoveries to the appropriate section.
4. Regenerate the `summary` field if context has grown significantly.

This indirection ensures that shared context is always consistent, validated, and conflict-checked. Agents propose discoveries; the orchestrator accepts them.

### 4. Read Protocol: How Agents Consume Shared Context

Agents receive a **view** of shared context, not the full object. The orchestrator constructs each agent's view based on:

- **Agent type**: research agents see all file mappings and constraints; test agents see implementation results and file changes; review agents see everything.
- **Relevance**: entries related to the agent's assigned files or subtask are prioritized.
- **Recency**: newer entries appear closer to the end of the context (where LLM attention is strongest).

The view is read-only. Agents cannot query shared context dynamically during execution --- they receive a snapshot at dispatch time. This preserves the forward-only, immutable-snapshot guarantees from the context-passing policy.

### 5. Context Size Management

Shared context is managed through a three-tier strategy:

**Tier 1: Structured compaction (always active).** Discoveries are stored as structured fields, not prose. A file mapping is `{"path": "auth.py", "role": "token validation", "lines": "40-60"}`, not a paragraph describing the file. This inherently limits per-entry size.

**Tier 2: Observation masking (at the view level).** When constructing an agent's context view, the orchestrator masks bulky evidence fields. The agent sees `evidence: "[masked --- available on request]"` for entries older than its immediate parent. The full evidence remains in the stored shared context for audit purposes but is not passed to the agent.

**Tier 3: Summary regeneration (at threshold).** When shared context exceeds a configurable token threshold (default: 8,000 tokens), the orchestrator regenerates the `summary` field by calling an LLM to produce a concise summary of all discoveries. Downstream agents receive: (a) the summary, (b) full entries from their immediate parent(s), and (c) masked entries from earlier nodes. The summary replaces direct inclusion of older entries in the agent's view.

**Size budget**: shared context views are capped at 25% of the target agent's context window budget (per context-passing policy 7). The orchestrator enforces this by progressively applying tiers 2 and 3 until the view fits.

**What gets pruned first** (in order):
1. Evidence fields on entries from non-parent nodes (mask them).
2. Negative findings older than 3 nodes back (these are "dead ends already explored" --- summarize into a single line).
3. Redundant file mappings (if multiple agents discovered the same file, keep the most recent entry).
4. Full entries from grandparent+ nodes (replace with the summary).

### 6. Conflict Resolution for Contradictory Discoveries

When the orchestrator detects that a new discovery contradicts an existing entry in shared context, it applies a tiered resolution strategy:

**Detection**: Two entries conflict when they target the same subject (same file, same function, same behavioral claim) but assert incompatible facts. The orchestrator performs shallow matching on `category` + key fields (file path, function name) to flag potential conflicts. This is a conservative check --- it flags potential conflicts for review rather than attempting deep semantic comparison.

**Resolution tiers**:

| Tier | Condition | Action |
|---|---|---|
| **Auto-resolve: higher confidence** | Confidence scores differ by >= 0.3 | Accept the higher-confidence entry; archive the lower one with `superseded_by` reference |
| **Auto-resolve: more recent evidence** | Both agents cite evidence, but one references code state after the other's changes | Accept the entry based on post-change evidence (it reflects current reality) |
| **Merge: complementary** | Entries are not truly contradictory but address different aspects of the same subject | Keep both, tagged as `complementary` |
| **Escalate: genuine conflict** | Similar confidence, same evidence base, incompatible conclusions | Create a `ConflictRecord` and either (a) dispatch a dedicated resolution agent that sees both entries + evidence and produces a reconciled finding, or (b) escalate to human at the next checkpoint |

**Conflict records** are stored in shared context alongside discoveries:

```python
class ConflictRecord(BaseModel):
    entry_a_node: str                     # Source node of first claim
    entry_b_node: str                     # Source node of second claim
    category: DiscoveryCategory
    description: str                      # What the conflict is
    resolution: str | None                # How it was resolved, if resolved
    resolved_by: str | None               # "auto", node_id, or "human"
```

Unresolved conflicts are surfaced to downstream agents explicitly: "Note: there is an unresolved conflict about X. Entry A claims Y (from node N1), Entry B claims Z (from node N2)." This prevents downstream agents from silently adopting one side.

### 7. Provenance and Auditability

Every entry in shared context carries:
- `source_node`: which agent wrote it.
- `timestamp`: when it was accepted into shared context.
- `confidence`: agent's self-assessed confidence (0.0--1.0).
- `superseded_by`: reference to a newer entry if this one was overridden.

The orchestrator never deletes entries from the stored shared context. Pruning and masking happen only at the view level (what agents see). The full shared context is persisted in SQLite as a JSON column on the `dags` table, providing a complete audit trail of everything discovered during the run.

### 8. Summary Regeneration Protocol

The `summary` field on `SharedContext` is regenerated by the orchestrator (not by task agents) at two trigger points:

1. **After any node completion that adds 3+ discoveries.** A small number of discoveries are cheap to read directly; a batch of new findings warrants a refreshed summary.
2. **Before dispatching any node whose context view would exceed the size budget without summarization.**

The summary prompt is fixed and deterministic (not agent-authored):

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

The summary is a derived artifact --- it is never treated as a source of truth. If a downstream agent needs the precise content of a discovery, it reads the structured entry, not the summary.

## Rationale

This policy fills a specific gap in the agent_agent architecture. The context-passing policy (05) defines how typed context flows along DAG edges. This policy defines what accumulates across the DAG as a whole --- the shared knowledge base that gives later agents access to earlier agents' findings without requiring direct edges between every pair of nodes.

The design choices reflect the constraints of a GitHub issue resolution system:

- **Structured over unstructured**: Issue resolution produces specific, referenceable facts (file paths, root causes, constraints), not open-ended narratives. Structured storage matches the domain.
- **Orchestrator as sole writer**: Prevents race conditions from parallel agents and ensures every entry is validated and conflict-checked before becoming part of the shared state.
- **Append-only with view-level pruning**: Preserves the full audit trail (critical for debugging failed resolutions) while keeping agent context windows focused and within budget.
- **Explicit conflict handling**: Parallel research agents will disagree. Ignoring conflicts produces incoherent downstream work. Surfacing conflicts explicitly (even when unresolved) lets downstream agents reason about uncertainty rather than blindly adopting one side.
- **Evidence-based confidence**: Agents self-report confidence, which provides a lightweight signal for conflict resolution and pruning priority. Low-confidence entries are pruned first; high-confidence entries survive summarization.
- **Observation masking over aggressive summarization**: Following JetBrains' empirical findings, we prefer masking bulky evidence fields (cheap, lossless for the structured claim) over summarizing everything (expensive, lossy). Summarization is reserved for when masking alone cannot meet the size budget.
