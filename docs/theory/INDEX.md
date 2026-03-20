# Theory — Index

This directory contains documents that explore conceptual distinctions and design philosophy. Theory documents do not make binding decisions — they explain the reasoning behind how the system is designed to be thought about.

## Documents

| Document | Summary |
|----------|---------|
| [design-goals-vs-design-guidance.md](design-goals-vs-design-guidance.md) | Distinguishes design goals (what the system must do, non-negotiable in intent) from design guidance (how we develop it, revisable trade-off heuristics), and defines when each type of conflict requires a human checkpoint. Keywords: goals, guidance, policy, conflict resolution, escalation, autonomy. |
| [policies-as-solution-space-constraints.md](policies-as-solution-space-constraints.md) | Explains why policies exist primarily to restrict the solution space available to agents, preventing architectural drift across successive invocations — not to encode best practices or describe good code. Covers the policy-vs-best-practice distinction, when a policy is warranted, and the relationship to PolicyReviewer. Keywords: policy, drift, solution space, constraints, best practices, PolicyReviewer, authorship. |
