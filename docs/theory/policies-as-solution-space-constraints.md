# Policies as Solution-Space Constraints

## The Core Claim

Policies in Agent Agent exist primarily to **restrict the solution space** available to agents. Their purpose is not to encode best practices or to document what good code looks like. Their purpose is to prevent **architectural drift** — the gradual divergence of the codebase from its intended structure as successive agent invocations each independently optimize for what looks locally correct.

---

## Why Agents Drift Without Constraints

A human developer working on a codebase accumulates context over time. When they reach for a design decision, they carry memory of past decisions, awareness of the existing architecture, and tacit knowledge about why things are the way they are. This context suppresses drift: a developer who knows the team agreed to avoid module-level mutable state will not introduce one, even if it would be the simplest solution in isolation.

An agent invocation has none of this. Each invocation is bounded by what was assembled into its context window. If the architectural intent is not present in that window — explicitly, as a constraint the agent is instructed to honor — the agent will make locally reasonable decisions that may be globally incoherent. Over many invocations on a codebase, these locally-optimized decisions compound into drift: a second state store appears alongside the first, a caching pattern that was deliberately avoided gets introduced, an abstraction boundary that was intentional gets eroded.

Policies close the gaps. They encode decisions that were made deliberately so that agents operating without the full context of those decisions will still be constrained by their outcomes.

---

## The Difference Between a Policy and a Best Practice

A **best practice** describes what good code looks like. It is aspirational and contextual — "prefer explicit parameter passing over global state" is a best practice. A senior developer would apply it by default; a junior developer might not; either might deviate from it if circumstances warranted.

A **policy** describes a decision that is no longer open. It is a constraint, not a recommendation. "This codebase does not use module-level mutable state for caching" is a policy. It does not say "prefer to avoid it." It says the decision was made, it applies here, and the agent should not reopen it.

The distinction matters for how agents should read policies. A best practice invites judgment: "is this the right pattern for this situation?" A policy forecloses judgment: "this situation has already been decided; proceed within the constraint."

When writing policies, prefer language that closes decisions rather than recommends directions:

| Recommends (best practice) | Closes (policy) |
|---|---|
| "Prefer small composites" | "Coding composites are capped at 3–5 files; this limit is not negotiable per-task" |
| "Try to avoid caching in global state" | "Module-level mutable state for caching is not permitted; use explicit cache parameters" |
| "Use structured logging where possible" | "All log output is JSON via structlog; plain-text log lines are a policy violation" |

---

## How Restricting the Solution Space Prevents Drift

Drift is not caused by agents making wrong decisions in the presence of clear constraints. It is caused by agents making unconstrained decisions that, individually, look reasonable, but collectively move the codebase in an inconsistent direction.

A policy restricts the solution space — the set of implementations the agent may choose from — at the specific decision points most likely to diverge across invocations. The goal is not to specify the implementation; it is to eliminate the paths that lead away from architectural coherence.

Consider the decision of how to handle cross-cutting state. Without a constraint, one agent might choose SQLite, another might choose an in-memory dict, another might choose a Redis-like store, depending on what each saw as the simplest solution for its specific task. Each choice is locally defensible. Together, they produce a codebase with three state stores that cannot be reasoned about as a whole.

A policy that says "all persistent state lives in the SQLite store; in-memory dicts are not permitted for state that must survive task boundaries" does not dictate *how* agents use SQLite. It eliminates the branching paths that lead to the multi-store problem. The solution space shrinks; the codebase stays coherent.

---

## Policies Are Not Performance Constraints

A policy that prevents an agent from choosing a particular implementation is not making the agent worse at its job. It is changing the job's definition: the agent's task is not "produce the best implementation" but "produce the best implementation within this solution space."

This reframing matters for evaluating whether a policy is working. The question is not "did the agent produce the implementation it would have chosen without constraints?" The question is "did the agent produce a good implementation within the constrained space, and does that implementation preserve architectural coherence with the decisions that preceded it?"

---

## Implications for Policy Authorship

Policies should be written at the decision points most likely to diverge across agent invocations — not at every decision point, and not at decision points where agent judgment is reliable and convergent.

A policy is warranted when:
- The same architectural decision has appeared multiple times across the project, with different resolutions in different contexts
- An agent, without constraint, would have a plausible reason to resolve a decision differently than the intended direction
- The cost of divergence is high: the decision touches shared infrastructure, data models, or cross-cutting concerns that are expensive to reconcile later

A policy is not warranted when:
- Agents reliably converge on the intended solution without being instructed (the constraint adds no information)
- The decision is local enough that divergence is harmless and easily corrected in review
- The constraint would be so broad that it eliminates valid solutions for legitimate edge cases

Overly broad policies are counterproductive: they force agents to produce contorted implementations in cases where the policy was not intended to apply, and they reduce the reviewer's ability to tell a genuine violation from a correctly-handled edge case.

---

## Relationship to the PolicyReviewer

The `PolicyReviewer` sub-agent exists to enforce the policy-as-constraint model at PR review time. Its job is not to evaluate whether the code is good. Its job is to determine whether the implementation stays within the solution space defined by the repo's policies.

This is why `PolicyReviewer` is structurally separated from `Reviewer`: the two evaluations answer different questions. `Reviewer` asks "is this a good implementation?" `PolicyReviewer` asks "is this implementation within the permitted solution space?" A PR can be technically excellent and still violate a policy. A PR can be uninspired and fully compliant. The verdicts are independent.

See [architecture/policy-review-testing.md](../architecture/policy-review-testing.md) for how this distinction drives the two policy test modes.
