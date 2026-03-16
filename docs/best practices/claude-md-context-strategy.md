# CLAUDE.md and Agent Context Strategy

How to manage guidance across the three adherence dimensions — policy, architecture, and best practices — given that a static file cannot carry the full weight.

---

## The Core Problem

CLAUDE.md is persistent context: every line loads on every session and competes for the model's finite attention. The system prompt already includes ~50 Anthropic instructions; consistent adherence deteriorates above ~150–200 active rules. Agent agent has:

- 10+ design policy documents (~200 rules total)
- Architecture principles that differ per agent type
- Best practices that only apply in specific execution contexts

Loading all of this into a single CLAUDE.md would guarantee poor adherence to everything. The solution is a three-layer model where guidance is carried by the mechanism that fits its scope.

---

## The Three Layers

| Layer | Mechanism | Scope | When loaded |
|-------|-----------|-------|-------------|
| **Universal** | `CLAUDE.md` | Applies to every session and every agent | Always |
| **Agent-type** | System prompt injection | Applies to a specific agent type's responsibilities | At dispatch time, by agent type |
| **Task-specific** | DAG context injection | Applies to what this specific node is doing | At dispatch time, assembled by `ContextProvider` |

---

## Layer 1: CLAUDE.md — Universal, Sparse, Non-negotiable

CLAUDE.md should contain only what is true in every session, for every agent, without exception. The test: "Would an agent violating this rule always be wrong, regardless of its type or task?"

### What belongs here

**Dev commands and environment setup** — non-obvious flags, venv activation, how to run tests. This is the highest-value content because Claude cannot infer it from the code.

**Repository structure** — the 5–10 line map of where things live. Not narration; a pointer.

**Critical architectural invariants** — rules that are absolute and apply everywhere:
- Agents are stateless; all state lives in SQLite
- Context flows forward only; no backwards signals
- Fail loudly; never swallow errors
- The orchestrator is the sole writer to shared context

**Hard constraints on git and PRs** — agents never merge to main; branch naming convention; humans handle merges.

**Gotchas specific to this repo** — things Claude consistently gets wrong here that are not derivable from reading the code.

### What does NOT belong here

| Excluded | Where it goes instead |
|----------|----------------------|
| Full policy rules (P1–P11) | Agent-type system prompts and guidance docs |
| Per-agent-type tool permissions | Agent system prompt injection (Layer 2) |
| Context assembly rules | ContextProvider logic + Research/Code guidance docs |
| DAG construction rules | Plan composite system prompt |
| Budget and token limits | Node config + executor logic |
| Testing framework details beyond "use pytest" | Test agent system prompt |

### Target size

50–80 lines. If it exceeds 100 lines, something belongs in a lower layer.

---

## Layer 2: System Prompt Injection — Per-Agent-Type Guidance

Each agent type has a system prompt template assembled at dispatch time. This is where agent-specific policies live. The orchestrator's executor is responsible for loading the right template.

### Structure of an agent system prompt

```
[Role definition — what this agent is, what it produces]
[Capability scope — what it can and cannot do (P3.3)]
[Relevant policies — the subset of P1–P11 that governs this type]
[Output contract — the exact Pydantic model it must return]
[Failure protocol — how to signal errors (P5.6)]
```

### Policy distribution by agent type

| Agent Type | Primary Policies | Key Constraints to Include |
|------------|-----------------|---------------------------|
| **Research** | P3.3, P5.7 (what qualifies as a discovery), P5.10 (provenance) | Read-only; no writes, no git mutations; discoveries must be typed |
| **Code** | P3.3, P3.2 (git scope), P8.x (argument validation) | File writes + git within worktree only; no PR creation |
| **Test** | P3.3 | Run commands, read files; no file writes, no git |
| **Review** | P3.3 | Read + PR comments only; no merges; no file writes |
| **Plan composite** | P1 (DAG orchestration), P2 (subtask decomposition), P3.3, P8 (granular decomposition) | No source file writes; DAG construction rules; child DAG spawning |

### Guidance documents as system prompt sources

Store modular policy slices in `docs/guidance/` — one file per agent type plus shared fragments:

```
docs/guidance/
  shared/
    failure-protocol.md       ← P5.6 structured failure signals (used by all types)
    context-output-contract.md ← How to format discoveries (P5.7, P5.10)
  agents/
    research-agent.md         ← P3.3 Research row + discovery rules
    code-agent.md             ← P3.3 Code row + git scope + coding composite rules
    test-agent.md             ← P3.3 Test row + test execution rules
    review-agent.md           ← P3.3 Review row + PR comment etiquette
    plan-composite.md         ← P1, P2, P8 rules relevant to plan construction
```

The executor loads the appropriate agent guidance doc at dispatch time and injects it into the system prompt. This keeps each agent's context small and relevant.

---

## Layer 3: DAG Context Injection — Task-Specific Guidance

Some guidance only matters in specific situations. Loading it for every node wastes context budget and dilutes signal. The `ContextProvider` is the right place to conditionally inject task-specific guidance.

### What warrants conditional injection

**Issue characteristics** — if the issue involves security-sensitive code, inject a security review checklist into the Review agent's context. If it involves database migrations, inject the append-only table constraint.

**Repo-specific conventions discovered at runtime** — the Research agent may discover "this repo uses pytest fixtures in conftest.py" and add it as a shared context discovery, making it available to the Test and Code agents without pre-loading it.

**Node position in the DAG** — a Code agent at a merge point (resolving conflicts from two parallel implementation branches) should receive a conflict-resolution guidance fragment that would be noise for a Code agent working on a fresh branch.

**Retry context** — on a retry attempt, inject a "what you tried and why it failed" fragment. This is already handled by P5.16 (context within composite nodes) but should include a guidance cue: "The previous attempt failed because X. Do not repeat the same approach."

### How this maps to the ContextProvider

```python
class NodeContext(BaseModel):
    issue: IssueContext
    parent_outputs: dict[str, AgentOutput]
    ancestor_context: AncestorContext
    shared_context_view: SharedContextView
    guidance_injection: str | None  # ← assembled by ContextProvider, optional
    context_budget_used: int
```

The `guidance_injection` field carries a short, targeted guidance fragment — not a policy document, but the relevant excerpt. The ContextProvider selects it based on:

1. Agent type
2. Issue tags or category (if classified by the Plan composite)
3. Node position (merge point, retry, leaf vs. internal)
4. Shared context discoveries (e.g., "uses append-only tables" triggers that constraint)

---

## The Adherence Flywheel

Static files degrade. The right maintenance loop:

1. **When an agent violates a policy** — determine which layer the rule lived in. If it was in CLAUDE.md and still violated, the file may be too long; move less-critical rules to guidance docs. If the rule was absent from the relevant system prompt, add it to the guidance doc for that agent type.

2. **When a new policy is written** — first ask: is this universal (CLAUDE.md), agent-type-scoped (system prompt / guidance doc), or situational (ContextProvider injection)? Place it accordingly. Only universal, always-true rules belong in CLAUDE.md.

3. **When CLAUDE.md exceeds 100 lines** — treat this as a bug, not a feature. Find the rules that are agent-type-specific and move them to the appropriate guidance doc.

4. **When a class of failures recurs** — consider whether this is a context problem (agent lacks the relevant policy at execution time) or a prompt problem (rule is present but ignored). Long CLAUDE.md causes the latter; missing Layer 2/3 content causes the former.

---

## Summary: What Goes Where

```
CLAUDE.md (≤80 lines)
  ├── Dev commands
  ├── Repo structure map
  ├── Absolute architectural invariants (stateless agents, forward-only context, fail loudly)
  ├── Git hard constraints
  └── Repo-specific gotchas

docs/guidance/agents/<type>.md (loaded into system prompt at dispatch)
  ├── Role definition
  ├── Tool permissions (P3.3)
  ├── Type-relevant policies
  ├── Output contract
  └── Failure protocol

ContextProvider (runtime injection, via guidance_injection field)
  ├── Issue-category-specific constraints
  ├── Merge-point guidance
  ├── Retry guidance
  └── Runtime-discovered repo conventions (via SharedContext)
```

The principle: context that is always true belongs in CLAUDE.md; context that is true for a role belongs in the system prompt; context that is true for a situation belongs in the DAG.
