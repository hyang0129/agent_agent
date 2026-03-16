# Agent Agent — Goals

## What This System Is

Agent Agent is a development team you run from a config file.

The core capability is autonomous issue resolution: given a GitHub issue and a codebase, the system reads the problem, plans the work, writes the code, tests it, reviews it, and opens a PR — without human involvement between the issue being approved and the PR appearing for review.

Everything else in this document is about who that core serves, and what gets layered on top to serve them.

---

## The Three Deployment Modes

The system is designed to serve three distinct user types. They share the same execution engine. They differ in how much upstream intelligence — architecture, requirements, design guidance — the user provides versus what the system must generate.

### Mode 1: Senior Developer / Architect

**User profile:** Knows what they're building, why they're building it, and how it should be built. Can write a precise issue, define acceptance criteria, and set architectural constraints. Building large-scale production systems. Also includes developers who are proficient in software engineering but new to the specific language or ecosystem — core SE principles transfer, and they can ramp quickly with targeted guidance (e.g., a Python developer building a C++ project).

**What the user provides:**
- Clear architecture and design decisions (in CLAUDE.md, ADRs, or design docs)
- Well-scoped issues with explicit acceptance criteria
- Technology choices and integration constraints
- Code standards and review expectations

**What the system provides:**
- Full implementation team: research, code, test, review, PR
- Faithful execution of the stated design with no creative deviation
- Parallel workstreams across independent issues
- Auditable traces so the developer can understand every decision

**Human interaction target:** Two touchpoints per issue — approve the investigation summary, then review the PR. Nothing in between.

**What failure looks like:** The system interprets or extends the design rather than executing it. It generates architectural opinions the developer didn't ask for. It asks questions the issue already answers.

---

### Mode 2: Business Analyst (Small-Scale Projects)

**User profile:** Understands the problem domain and can describe what they need in business terms. Has software development knowledge sufficient to evaluate an implementation but does not drive architecture or write detailed technical specs. Building small-to-medium production systems.

**What the user provides:**
- Business requirements and success criteria
- Functional behavior ("when a user does X, the system should Y")
- Constraints (technology, hosting, integrations)

**What the system provides, beyond Mode 1:**
- An **Architecture Agent** that translates business requirements into technical design: data models, API contracts, component boundaries, technology choices, deployment shape
- Best-practices guidance applied automatically (the user doesn't need to specify "use dependency injection" — the system knows)
- Issue generation: the system authors the implementation issues from the architecture it produced, rather than requiring the user to write them

**Human interaction target:** One additional upstream checkpoint — the user approves the architecture before development begins. Then two checkpoints per issue as in Mode 1.

**What failure looks like:** The architecture agent produces decisions the BA cannot evaluate. The system diverges from business intent by optimizing for technical elegance. Architecture is revisited mid-implementation.

---

### Mode 3: Logical Thinker (Personal Projects)

**User profile:** Technically capable and logical, but not a software professional. Can describe what they want and reason about cause-and-effect. Not fluent in software architecture or development process. Building personal tools, automations, side projects.

**What the user provides:**
- A description of the problem and the desired outcome
- Answers to clarifying questions
- Judgment calls on trade-offs when the system surfaces them

**What the system provides, beyond Mode 2:**
- An **Architecture Agent** calibrated for personal-project scale: simpler patterns, lower operational complexity, plain-language explanations of every decision (no assumed fluency in software design)
- **Requirements drift handling:** personal projects evolve as users discover what they actually want. When new requests conflict with or substantially extend the existing architecture, the agent surfaces a choice: adjust the architecture, or recommend starting a separate project
- Scope management: the system identifies when a request is growing beyond the stated goals and surfaces that for decision rather than silently expanding

**Human interaction target:** One upstream conversation to establish requirements, one to approve architecture, then standard PR review per issue. The user is not involved in implementation details.

**What failure looks like:** The system over-engineers for a personal project (microservices for a weekend tool). It surfaces technical decisions the user cannot meaningfully evaluate. It asks for information it could infer. It absorbs scope creep silently rather than surfacing the trade-off.

---

## MVP Scope

**The MVP implements Mode 1 only.**

Modes 2 and 3 (Architecture Agent, Requirements drift handling, adjusted communication levels) are post-MVP. The dev team core must work well in isolation before upstream agents are worth building.

---

## The Layering Model

The three modes are additive, not separate systems.

```
Mode 3                        [Architecture Agent + drift handling] ─┐
                                                                      │
Mode 2                        [Architecture Agent] ──────────────────┤
                                                                      │
Mode 1  ──────────────────────────────────────────────── [Dev Team Core]
                                                                      │
                                              [Research → Plan → Code → Test → Review → PR]

All modes  [Issue / Requirements Agent] ────────────────────────────────┘
```

The dev team core is the primary investment. It must work well in isolation (Mode 1) before the upstream agents are worth building. Mode 2 and Mode 3 agents are weaker than a senior developer at those upstream tasks — they are adequate, not exceptional — but they remove the requirement that the user be exceptional at those tasks.

The upstream agents produce outputs that the dev team core consumes. Their quality ceiling is lower; their purpose is to lower the bar for the human, not to compete with a human expert.

---

## Cross-Cutting: Issue / Requirements Agent

In all three modes, an **Issue / Requirements Agent** runs on every issue before execution begins. Its job is to ensure the issue is actually workable: scope is clear, acceptance criteria are present, ambiguities are resolved, and the work is bounded.

**The agent is the same across all modes.** The only variation is the level of technical language it uses when communicating with the user:

| Mode | Communication style |
|------|---------------------|
| Mode 1 — Senior Developer | Technical: precise engineering terms, references to architecture and code |
| Mode 2 — Business Analyst | Business-technical: functional language, avoids implementation jargon |
| Mode 3 — Logical Thinker | Plain language: describes behavior and outcomes, no software terminology |

**What it does:**
- Asks clarifying questions if the issue is under-specified
- Surfaces scope ambiguities and forces a decision before work begins
- Flags issues that are too large to be a single unit of work and proposes a split
- Confirms acceptance criteria are testable

**What it does not do:**
- Make scope or priority decisions unilaterally
- Rewrite the issue without user confirmation
- Block execution on minor gaps it can reasonably infer

**What failure looks like:** The agent asks questions the issue already answers. It uses jargon the user cannot evaluate. It passes a poorly-scoped issue through without challenge.

---

## Cross-Cutting Goal: Minimize Human Interaction

Across all three modes, the system's behavior should trend toward fewer human touchpoints over time, not more.

**What this means in practice:**

- Pauses are policy gaps, not supervision. Every mid-execution pause is a signal that policy or instructions are incomplete. The system fixes the gap; it does not ask the same question twice.
- Checkpoints are defined, not improvised. The number of human touchpoints per issue is fixed at design time. The system does not add checkpoints because something is uncertain — it resolves uncertainty autonomously or escalates via a structured format that forces a policy update.
- Rejected PRs improve the system. A rejected PR is not a one-off correction — it triggers a structured improvement loop that changes prompts, policies, or CLAUDE.md to prevent recurrence.
- The goal is not zero interaction. Human authority over what ships is preserved by design. The goal is that every interaction is high-value: approving scope, reviewing output, making judgment calls the system cannot make.

**What this is not:**

Minimizing human interaction does not mean minimizing human authority. The system produces work; the human decides what ships. That boundary is fixed regardless of mode.

---

## What the System Does Not Do

These are explicit non-goals, not gaps to be filled later:

- **Does not replace the human's judgment about what to build.** The system executes decisions; it does not make strategic ones.
- **Does not merge to main.** Human PR approval is always required. This is not a cost to be optimized away.
- **Does not manage competing priorities.** The system works one issue at a time at the task level. Project sequencing and prioritization are the user's job.
- **Does not guarantee correctness.** It produces work that should be reviewed. The two-checkpoint model exists because human review is load-bearing, not ceremonial.

---

## How Goals Relate to Policies

This document states what the system should do and for whom. The policies in `docs/policies/` state how it does it. Goals are non-negotiable in intent; policies are the implementation of that intent and can be revised.

When a policy conflicts with a goal, the goal wins — but the conflict must be surfaced to a human before resolution, not resolved silently. See [design-goals-vs-design-guidance.md](design-goals-vs-design-guidance.md) for the full treatment of this relationship.
