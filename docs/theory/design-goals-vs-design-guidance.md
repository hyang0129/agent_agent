# Design Goals vs. Design Guidance

## Why the Distinction Matters

Agent Agent's documentation uses two related but distinct terms: **design goals** and **design guidance**. Conflating them leads to confused policy discussions — someone argues "we should do X because it's our goal" when X is actually a development preference, or dismisses a goal as "just a guideline." This document defines both terms and explains when each applies.

## Design Goals

A design goal describes **what the system should do and why it should do it.** Goals are about the product — its capabilities, its behavior from the user's perspective, and the problems it exists to solve. Goals answer:

- What outcome does this system produce?
- Why does that outcome matter?
- What would failure look like?

Goals are **non-negotiable in intent** (though their implementation is flexible). If you remove a design goal, the system is solving a different problem.

### Examples

| Goal | What | Why |
|------|------|-----|
| Resolve GitHub issues autonomously | The system takes an issue and produces a working PR with code changes, tests, and documentation | A single developer should be able to offload well-scoped implementation work to the system |
| Operate within token budgets | Every DAG run tracks and enforces token spend limits | Uncontrolled API costs make the system impractical for regular use |
| Preserve human authority over merges | Agents never merge to main; PRs require human approval | The system augments a developer, it does not replace their judgment on what ships |
| Produce auditable execution traces | Every agent invocation, context transformation, and decision is logged with enough detail to reconstruct what happened | When something goes wrong, the developer needs to understand why without re-running the entire pipeline |

### How to evaluate a design goal

Ask: "If we dropped this, would the system still solve the problem it's meant to solve?" If the answer is no, it's a goal.

## Design Guidance

Design guidance describes **how we develop the system and what we prioritize when making trade-offs.** Guidance is about the development process — the principles, policies, and preferences that shape implementation decisions. Guidance answers:

- When two valid approaches conflict, which do we pick?
- What do we optimize for when we can't have everything?
- What constraints do we impose on ourselves even when best practices suggest otherwise?

Guidance is **contextual and revisable.** It reflects the team's current priorities, the project's current stage, and lessons learned. Guidance can be overridden when circumstances change — goals generally cannot.

### Examples

| Guidance | How / What to Prioritize | Why It Might Contradict "Best Practices" |
|----------|--------------------------|------------------------------------------|
| Maximum Agent Separation | Decompose agent roles into the smallest independently-scoped units, even when a combined agent would be simpler | Industry best practice often favors fewer, more capable agents to reduce coordination overhead. We accept that overhead because isolated failure modes and minimal permissions matter more at this stage. |
| Fail loudly over fail gracefully | Agents return structured errors and the orchestrator halts dependents rather than attempting degraded operation | Many production systems favor graceful degradation. We prefer loud failure because silent partial results from an AI agent are more dangerous than a stopped pipeline. |
| Cheap model for non-reasoning tasks | Use Haiku-tier models for summarization, classification, and context selection — not the agent execution model | The obvious approach is to use the best model everywhere. We accept slightly lower quality on support tasks to keep costs proportional to the value each task adds. |
| SQLite over Postgres for MVP | Use async SQLite for all state persistence, even though the schema will eventually need a real database | Best practice for a server is to start with Postgres. We choose SQLite because the MVP is single-developer, local-only, and the migration cost later is lower than the ops cost now. |
| No backwards-compatibility shims | When a design changes, update all call sites. Don't add adapter layers or deprecation paths. | Standard library design favors backwards compatibility. We skip it because the codebase is pre-1.0, has one consumer (us), and shims accumulate into permanent complexity. |

### How to evaluate design guidance

Ask: "If we changed this, would the system still solve the same problem, just differently?" If the answer is yes, it's guidance.

## The Interaction Between Goals and Guidance

Goals constrain guidance — guidance should never undermine a goal. But guidance shapes how goals are achieved, and sometimes guidance forces a specific implementation path that wouldn't be obvious from the goal alone.

**Example:** The goal "operate within token budgets" says nothing about *how* to stay within budget. The guidance "cheap model for non-reasoning tasks" is one specific strategy for achieving that goal. A different team might achieve the same goal by using one model everywhere but capping retries. The goal is the same; the guidance differs.

**Example:** The goal "preserve human authority over merges" is compatible with many approval flows. The guidance "exactly two human checkpoints" (issue approval + PR review) is a specific policy choice that serves the goal while also reflecting a separate guidance priority: minimize interruptions to the developer.

## When They Conflict

Occasionally, following a piece of guidance to the letter would undermine a goal. **Goals always win.** But how the conflict is handled depends on the severity of the guidance being overridden.

### Strict guidance (policy architecture): human decision required

Strict guidance includes formalized policies — agent decomposition rules, context-passing protocols, checkpoint requirements, merge ordering. These exist because past reasoning concluded they were load-bearing for correctness or safety.

When a goal conflicts with strict guidance, **the system must stop and escalate to a human before proceeding.** The human decides whether to override the guidance for this case or revise the goal's implementation to avoid the conflict. This is a blocking checkpoint.

**Example:** Maximum Agent Separation says to decompose agents into minimal units. A goal says to stay within the token budget. If a DAG would require 30+ nodes and blow the budget, the system cannot silently consolidate agents — that violates a policy. It must surface the conflict: "Token budget requires consolidating these 8 nodes into 3, which violates Maximum Agent Separation. Approve override or adjust budget?"

### General guidance (development preferences): human notification, non-blocking

General guidance includes development preferences and trade-off heuristics — "use cheap models for non-reasoning tasks," "SQLite over Postgres for MVP," "no backwards-compatibility shims." These reflect current priorities, not architectural invariants.

When a goal conflicts with general guidance, **the system should notify the human but proceed with the goal.** The notification is informational — it creates a record that guidance was deviated from and why, so the team can decide later whether the guidance needs updating.

**Example:** The guidance "cheap model for non-reasoning tasks" says to use Haiku for summarization. But a particular DAG's context summarization is producing low-quality results that cause downstream agents to fail, threatening the goal of resolving the issue. The system can escalate to a better model, log the deviation ("used Sonnet for summarization on DAG #47 — Haiku output quality insufficient for this merge point"), and continue.

### Why the distinction matters

Treating all guidance conflicts the same creates two failure modes:

1. **Everything blocks on a human** — general preferences become chokepoints, and the system spends more time asking for permission than doing work.
2. **Nothing blocks on a human** — architectural policies get silently violated, and the system drifts into states that are hard to debug or recover from.

The severity split ensures that the system is autonomous where it can be and cautious where it must be.

Document all conflicts regardless of severity. A pattern of repeated conflicts with the same guidance — strict or general — is a signal that either the guidance or the goals need revision.
