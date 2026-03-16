# Composite Nodes

## Problem Statement

The current architecture has two structural problems:

1. **The code-test cycle contradicts acyclicity.** The system models `CODE -> TEST -> CODE` as a "cycle with max iterations at DAG level" (Policy 04, Section 6). A cycle in a Directed *Acyclic* Graph is a contradiction. The system either violates its own acyclicity constraint (Policy 01, P3) or hides the cycle behind retry semantics that obscure what is actually an iterative refinement process.

2. **Research, Plan, and Orchestrate are tightly coupled but modeled as separate nodes.** These three agents form a fixed pipeline (`research → plan → orchestrate`) that always executes in sequence, never in parallel with other work at the same level. They share a single concern — understanding the situation and deciding what to do next — but the current design treats them as three independent scheduling units, adding coordination overhead without benefit.

This policy introduces two **composite nodes** that encapsulate agent execution within single outer DAG nodes:

- The **Planning Node** is implemented in the MVP as a single agent that performs research, planning, and orchestration in one tool-use loop. The target architecture decomposes this into an internal acyclic subgraph (Research → Plan → Orchestrate), but this decomposition is deferred — a single agent with the right tools and a clear prompt can perform all three activities coherently in one pass, and the SOTA evidence supports this approach.
- The **Coding Node** runs an internal budget-gated cyclic subgraph (Programmer ↔ Test Designer ↔ Test Executor ↔ Debugger ↔ Commit).

The outer DAG becomes a clean two-node-plus-review structure at each nesting level. Both nodes are opaque to the outer DAG — it sees only typed inputs and outputs.

## Background & State of the Art

### Composite Nodes and Structural Indirection

The pattern of encapsulating multi-agent execution within a single outer node is well-established across workflow orchestration and multi-agent systems:

**Netflix Maestro** (open-sourced 2024) supports both acyclic and cyclic workflows. Complex behavior is encapsulated in parameterized sub-workflows dispatched from fixed top-level DAG nodes. The top-level graph stays deterministic; the sub-workflow's internal execution is opaque to the parent. This is structural indirection — the parent sees a single invocation, the child runs arbitrarily complex logic.

**LangGraph** models each node as a function that may internally run an agentic loop (including cycles). The graph structure is deterministic; what happens inside a node is not. A node can be a simple transform or a full ReAct agent with tools. LangGraph's `StateGraph` passes typed state between nodes, so even if a node runs an internal cycle, its output is a structured state update.

**Temporal** child workflows are invoked by a parent workflow as a single activity. The child can run arbitrarily complex logic — including loops, retries, and multi-step orchestration — while the parent sees only a single invocation with typed input and output. The child's execution is independently recoverable.

**Airflow TaskGroups** bundle multiple tasks into a single visual and logical unit. The group is a node in the outer DAG; its internal structure is a sub-DAG. TaskGroups were introduced specifically to replace the more heavyweight SubDAG pattern, providing encapsulation without the scheduling overhead of a separate DAG run.

### Multi-Agent Coding Cycles

Three systems demonstrate that separating the code-test loop into specialized agents that cycle improves performance:

**AgentCoder** (Huang et al., 2023) separates Programmer, Test Designer, and Test Executor into three roles. The Programmer writes code, the Test Designer generates test cases that probe edge cases and failure modes, and the Test Executor runs the tests and reports results. When tests fail, the Programmer receives the failure output and iterates. The paper showed this separation improved code generation quality because each agent focuses on a single concern without mode-switching. Critically, the Test Designer is a separate role from the Test Executor — designing good tests requires different reasoning than running them.

**MapCoder** (Islam et al., ACL 2024) adds an explicit Debug role to the cycle. After the code fails tests, the Debug agent analyzes the failure, identifies the root cause, and produces a diagnosis that the code-writing agent uses to fix the issue. This separation contributed to MapCoder's 93.9% on HumanEval. The insight: diagnosing a failure is a different cognitive task than fixing it, and LLMs perform better when each task is isolated.

**Self-Debugging** (Chen et al., ICLR 2024) showed that LLMs can fix their own code only when given concrete execution feedback — error messages, failing test output, execution traces. Bare "try again" prompts showed negligible improvement. This validates the cycle's information flow: the Test Executor produces external signal, the Debugger interprets it, the Programmer acts on the interpretation.

### Planning as a Cohesive Unit

Leading multi-agent systems consistently treat the research-plan-decide pipeline as a cohesive unit, not as three independent steps:

**TDAG** (Wang et al., Neural Networks 2025) uses a "main agent" that performs research, decomposition, and dispatch as a single unified operation. The main agent reads the task, gathers context, produces a sub-task graph, and dispatches it — all within one invocation. Separating these into independent nodes would break the main agent's ability to iteratively refine its understanding and plan in a single reasoning pass.

**Agyn** (2026) assigns a Manager role that researches the issue, produces a plan, and delegates to workers — all as a single cohesive activity. The Manager's value comes from maintaining a unified mental model across understanding, planning, and delegation. Fragmenting this across three separate agents with context passed through typed outputs would lose the reasoning coherence.

**MetaGPT** (Hong et al., 2023) chains Product Manager → Architect as a planning pipeline, but these agents operate within a tightly coupled SOP sequence where each agent's output is immediately consumed by the next. The pipeline is effectively a single planning phase with internal structure, not three independent scheduling units.

**Anthropic's guidance** (December 2024, updated 2025) recommends that orchestration logic — deciding what to do, how to decompose, when to stop — should live in deterministic code paths ("workflows") rather than in autonomous agent loops. The Planning Node follows this: its internal structure (Research → Plan → Orchestrate) is a fixed pipeline, not a dynamic agent conversation.

### Budget-Gating vs. Iteration Caps

**Agent Contracts** (2026) formalizes resource contracts with conservation laws for autonomous AI systems. A composite agent receives a budget allocation from its parent; its internal execution can use any strategy (including cycles) as long as it respects the budget boundary. The BALANCED mode (medium effort, 90s timeout) achieves 86% success vs. 70% for URGENT mode, investing 75% more tokens for a 16-percentage-point improvement. The key insight: the relationship between resource investment and success is non-linear and problem-dependent. Fixed iteration caps cannot capture this.

**Reflexion** (Shinn et al., NeurIPS 2023) showed that the number of productive retry iterations varies by problem difficulty. Easy problems are solved in 1-2 iterations; hard problems benefit from 4-5. Budget-gating naturally adapts: easy problems exit early and return unspent budget; hard problems consume more but keep going as long as the allocation permits.

**Huang et al. (ICLR 2024)** established that intrinsic self-correction (without external signal) degrades performance. The Coding Node's cycle is productive because each iteration incorporates external feedback (test results). Budget-gating pairs well with this: each cycle iteration is useful work, not blind repetition.

### Work Preservation in Iterative Systems

**Temporal** preserves workflow state through event sourcing — every activity completion is an immutable event. On crash recovery, the workflow replays from the event log.

**Flyte** caches task outputs by version. If a cached output exists for a given task version and input hash, the task is skipped on re-execution.

**Git** is a work-preservation system. Commits are immutable snapshots. Committing intermediate work to a branch creates a checkpoint that can be restored without re-executing the work that produced it.

For the Coding Node, work preservation means: after the Programmer produces a promising (but not yet passing) set of changes, the Commit agent snapshots that state. If the node is later interrupted, the next attempt can start from the snapshot rather than from scratch. This is future functionality — see Policy Section 9.

---

## Policy

### 1. The outer DAG uses two composite nodes and one simple node per nesting level.

The outer DAG structure at each nesting level is:

```
[Planning Node] → [Coding Node] → Review
```

At L0 (issue intake), only the Planning Node executes. It produces the initial decomposition and spawns L1. At subsequent levels, all three nodes participate:

```
L0: [Planning Node]
         └─ L1: [Coding Node] → Review → [Planning Node]
                                                └─ L2: [Coding Node] → Review → [Planning Node]
                                                                                      └─ null (done)
```

Each composite node is opaque to the outer DAG. The outer DAG sees only typed inputs and outputs. It allocates a budget to each node and waits for a result. The outer DAG is strictly acyclic at every nesting level. Policy 01, P3 is satisfied.

### 2. MVP: The Planning Node is a single agent that researches, plans, and orchestrates.

```
┌──────────────────────────────────────────────────────┐
│                   Planning Node                       │
│                                                      │
│   ┌──────────────────────────────────────────────┐   │
│   │              Planner Agent                    │   │
│   │                                              │   │
│   │  1. Research: read code, issues, context     │   │
│   │  2. Plan: decompose into subtasks            │   │
│   │  3. Orchestrate: spawn child DAG or halt     │   │
│   │                                              │   │
│   │  (single tool-use loop, all three phases)    │   │
│   └──────────────────────────────────────────────┘   │
│                                                      │
└──────────────────────────────────────────────────────┘
```

The MVP Planning Node runs a **single Planner agent** that performs research, planning, and orchestration within one tool-use loop. The agent:

1. **Researches** the problem — reads code, issues, documentation, and (at levels > L0) the prior Review's feedback and the prior Coding Node's failure context.
2. **Plans** the work — decomposes the problem into subtasks, determines parallel branch structure for the Coding Node, and defines acceptance criteria.
3. **Orchestrates** — either produces a child DAG specification (the next nesting level) or returns null (signaling that the issue is resolved).

This single-agent approach is justified by SOTA evidence:

- **TDAG**'s main agent performs research, decomposition, and dispatch as a single unified operation. Separating these would break the agent's ability to iteratively refine its understanding and plan in one reasoning pass.
- **Agyn**'s Manager role researches, plans, and delegates as a single cohesive activity. Its value comes from maintaining a unified mental model across all three phases.
- A single agent can discover during research that its initial direction is wrong and pivot immediately — without waiting for a typed output to propagate through a pipeline. This is especially valuable when the plan depends on discovered context (e.g., "the function I expected doesn't exist, so the approach must change").

#### Planner Agent permissions

| Agent | Responsibility | Can | Cannot |
|-------|---------------|-----|--------|
| **Planner** | Understand the problem, decompose into subtasks, produce child DAG spec or signal completion | Read files, search code, read git history, read GitHub issues/PRs, read prior Review output, create child DAG in state store | Write files, run tests, execute code, touch git, comment on PRs |

#### Future: decompose into three sub-agents

The target architecture decomposes the Planner agent into three specialized sub-agents (Research → Plan → Orchestrate) running as an internal acyclic subgraph. This decomposition is deferred because:

- The single-agent approach works well for the MVP's scope (single-developer, moderate-complexity issues).
- Decomposing introduces the problem identified in the Problem Statement: a plan may require additional research, creating a potential need for an internal cycle that contradicts the acyclic intent.
- The composite pattern established by the Coding Node provides the structural template — when decomposition is warranted, the Planning Node can adopt it without outer DAG changes.

Signals that would trigger decomposition:
- The Planner agent consistently hits its iteration cap because research and planning compete for tool-use budget.
- Post-hoc analysis shows the agent producing low-quality plans because it rushes research to save iterations for planning.
- The system scales to more complex issues where research scope exceeds what a single agent pass can cover.

### 3. The Coding Node runs an internal budget-gated cyclic subgraph of five agents.

```
┌──────────────────────────────────────────────────────────────────┐
│                     Composite Coding Node                        │
│                                                                  │
│   ┌────────────┐    ┌───────────────┐    ┌───────────────┐       │
│   │ Programmer │───→│ Test Designer │───→│ Test Executor │       │
│   └────────────┘    └───────────────┘    └───────────────┘       │
│         ↑                                       │                │
│         │           ┌──────────┐                │                │
│         │       ┌───│ Debugger │←───────────────┘ (on failure)   │
│         │       │   └──────────┘                                 │
│         │       │                                │                │
│         │       │   ┌────────┐                   │                │
│         │       └──→│ Commit │                   ↓                │
│         │           └────────┘              (on success) → EXIT   │
│         │               │                                        │
│         └───────────────┘                                        │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

The cycle flow:

1. **Programmer** receives the subtask (from the Planning Node's output) and produces file changes.
2. **Test Designer** examines the changes and writes or identifies test cases that validate the acceptance criteria. This includes edge cases and regression tests, not just happy-path assertions.
3. **Test Executor** runs the test suite and reports pass/fail with full output.
4. **On success:** the node exits with a successful result.
5. **On failure:** the **Debugger** receives the failing test output, the Programmer's changes, and the Test Designer's test cases. It diagnoses the root cause and produces a structured diagnosis (what failed, why, what should change).
6. **Commit** (future functionality) snapshots the current working state for preservation.
7. **Programmer** receives the Debugger's diagnosis and iterates on the changes.
8. The cycle repeats from step 2.

#### Sub-agent permissions within the Coding Node

| Sub-Agent | Responsibility | Can | Cannot |
|-----------|---------------|-----|--------|
| **Programmer** | Produce file changes that address the subtask or the Debugger's diagnosis | Read files, write files | Run tests, touch git, execute arbitrary commands |
| **Test Designer** | Design test cases that validate the acceptance criteria against the Programmer's changes | Read files, read the Programmer's output, write test files | Modify source files, run tests, touch git |
| **Test Executor** | Execute the test suite and report structured results | Read files, run pytest and other test commands | Write any files, touch git |
| **Debugger** | Diagnose test failures and produce actionable fix instructions | Read files, read test output, read the Programmer's changes | Write files, run tests, touch git |
| **Commit** | Snapshot working state to a branch for preservation | Read files (to verify state), git add, git commit on the node's working branch | Write/modify source files, run tests, push, create PRs |

The principle of least privilege (Policy 14) applies within both composite nodes. No sub-agent has both mutation and persistence capabilities.

### 4. The Coding Node's cycle is bounded by budget, not by iteration count.

The Coding Node receives a token budget allocation from the outer DAG's budget allocation strategy (Policy 09). The inner cycle runs until:

- **Success:** All tests pass. The node exits with a successful result and returns unspent budget to the reserve pool.
- **Budget exhaustion:** The node's allocation is consumed. The node exits with a failure result containing: the last Programmer output, the last test results, and how many cycles completed. The outer DAG applies its standard failure handling (escalation per Policy 08, or retry of the entire Coding Node per Policy 07).

There is no fixed iteration cap on the inner cycle. A simple fix may pass on the first cycle. A hard bug may require five cycles. The budget gate naturally adapts to problem difficulty.

### 5. The Planning Node is bounded by budget and iteration cap.

The Planning Node receives a budget allocation and runs its single Planner agent within a tool-use loop. The agent either completes its research-plan-orchestrate pass or exhausts its budget/iteration cap.

Budget allocation weights for the outer DAG:

| Node Type | Weight | Notes |
|-----------|--------|-------|
| Planning Node | 1.5 | Replaces Research (1.0) + Planner (0.5). Fixed pipeline, predictable cost. |
| Coding Node | 3.0 | Replaces Implement (2.5) + Test (0.7), minus coordination overhead savings. Variable cost due to inner cycle. |
| Review | 1.2 | Unchanged from Policy 09. |

### 6. Agent invocations are bounded per-invocation.

While the Coding Node's cycle count is budget-gated, each individual agent invocation is bounded by a tool-use iteration cap:

**Planning Node (single agent):**

| Agent | Max Tool-Use Iterations | Rationale |
|-------|------------------------|-----------|
| Planner | 50 | Must read many files, analyze the problem, and produce a decomposition in one pass |

**Coding Node (sub-agents, per cycle):**

| Sub-Agent | Max Tool-Use Iterations | Rationale |
|-----------|------------------------|-----------|
| Programmer | 40 | Needs to read context, write files, validate syntax |
| Test Designer | 20 | Reads changes, identifies test targets, writes test cases |
| Test Executor | 15 | Runs tests, reads output |
| Debugger | 20 | Reads failing output, analyzes root cause, writes diagnosis |
| Commit | 5 | Mechanical: stage, commit |

If an agent hits its iteration cap:
- **Planning Node:** the node fails and the outer DAG applies retry policy.
- **Coding Node:** the cycle marks the current cycle as failed and routes to the Debugger (if the failure is in Programmer, Test Designer, or Test Executor) or exits with failure (if Commit fails).

### 7. MVP: Review is a simple (non-composite) node. Future: composite with Policy Review Agent.

For the MVP, Review is a single agent. Its job — evaluate code quality, correctness, and standards adherence — is handled in one pass. It reads diffs, reads context, and produces structured feedback.

Review sits between the Coding Node and the Planning Node in the outer DAG. Its output feeds the Planning Node at the next iteration: if Review passes, the Planner agent sees a clean result and returns null (done). If Review fails, the Planner activates fully to analyze the failure and produce a revised approach.

In a future iteration, Review becomes a composite node with at least two parallel sub-agents: a **Code Reviewer** (quality, correctness, style) and a **Policy Reviewer** (policy compliance, drift detection, permission scope). See the Rationale section for the full design. The architectural trigger for this decomposition is evidence that the monolithic reviewer consistently misses policy violations that a human catches during PR review — this indicates the single agent's attention is spread too thin across competing concerns.

| Review | Responsibility | Can | Cannot |
|--------|---------------|-----|--------|
| **Review** | Evaluate code quality, correctness, and adherence to standards | Read files, read diffs, read git history, comment on PRs | Write files, touch git, run tests, merge PRs |

### 8. MVP: no work preservation within the Coding Node. Restart from inputs on failure.

For the MVP implementation, the Commit sub-agent is defined in the architecture but **not invoked during the cycle**. If the Coding Node fails (budget exhaustion), the entire node is retried from its original inputs — the subtask description, research output, and file context. No intermediate state from the failed cycle is preserved.

This means:

- **On budget exhaustion:** the inner cycle's partial work (Programmer's changes, test results, Debugger's diagnoses) is discarded. The outer DAG may retry the Coding Node with a fresh budget allocation (per Policy 07), starting the cycle from scratch.
- **On crash recovery:** the Coding Node is re-invoked from its persisted inputs. There is no checkpoint within the cycle to resume from.
- **Cost implication:** a Coding Node that fails after 4 productive cycles and then succeeds on retry will re-do all the work. This is acceptable for the MVP because it is simple, correct, and avoids the complexity of managing intermediate git state within the cycle.

The Commit sub-agent's architecture slot exists so that work preservation can be added without structural changes (see Section 9).

### 9. Future: the Commit sub-agent enables incremental work preservation.

In a future iteration, the Commit sub-agent activates within the cycle. After each cycle where the Debugger identifies that the Programmer has made forward progress (tests moved from failing to partially passing, or new tests pass that previously didn't), the Commit agent snapshots the working state to the node's working branch.

This enables:

- **Budget exhaustion recovery:** instead of restarting from scratch, the retry loads the last committed snapshot and resumes from there.
- **Crash recovery:** the orchestrator detects the Coding Node's working branch, loads the last commit, and resumes the cycle from the Debugger step.
- **Cost savings:** work from productive early cycles is preserved. Only the failing tail-end of the cycle is re-executed.

The criteria for "forward progress" (which triggers a Commit snapshot) is a design decision deferred to the implementation of this feature.

---

## Interaction with Existing Policies

### Policy 01 (DAG Orchestration)
The outer DAG remains strictly acyclic at every nesting level (P3 satisfied). Both nodes are opaque from the outer DAG's perspective. This follows the structural indirection pattern from Netflix Maestro and Temporal child workflows.

The standard spine (`research → plan → orchestrate` from P2) is absorbed into the Planning Node as a single agent's workflow. The spine's concerns still exist but are handled within one tool-use loop, not as three separate outer DAG nodes. The nesting model (P1) is unchanged — the Planning Node spawns child DAGs exactly as before.

### Policy 03 (Agent Type Taxonomy)
The five-type outer taxonomy (Research, Code, Test, Commit, Review) becomes three node types (Planning Node, Coding Node, Review). The MVP has 7 distinct agent roles: 1 Planner agent + 5 Coding Node sub-agents + 1 Review agent. The outer DAG's coordination surface shrinks from five node types to three.

### Policy 04 (Agent-to-Node Mapping)
The 1:1 rule (one node = one agent invocation) holds for the Planning Node (one agent) and Review (one agent). The Coding Node is the sole exception: a composite node that internally runs multiple agent invocations in a cycle. The 1:1 rule still applies within the Coding Node — each sub-agent invocation maps to one step in the internal cycle.

### Policy 07 (Retry Policy)
Retry semantics split into two levels:
- **Within the Coding Node:** the Programmer-Debugger cycle is the primary execution model, not a retry mechanism. It runs until success or budget exhaustion.
- **At the outer DAG level:** if a composite node fails entirely (budget exhaustion, unrecoverable error), the outer DAG may retry the whole node per Policy 07. MVP: restart from inputs. Future: restart from last checkpoint.

The Planning Node is a single agent invocation, so its retry semantics are straightforward — the outer DAG retries the entire node on failure.

### Policy 09 (Budget Allocation)
Three nodes replace five individual allocations. The Planning Node (weight 1.5) absorbs Research (1.0) and Planner (0.5). The Coding Node (weight 3.0) absorbs Implement (2.5) and Test (0.7). Review (weight 1.2) is unchanged. Internal budget distribution within the Coding Node is managed by the node's own executor, not by the outer DAG's allocator.

### Policy 14 (Granular Agent Decomposition)
The mutation/persistence separation principle is preserved within the Coding Node. The Programmer writes files but cannot touch git. The Commit agent touches git but cannot modify source files. The Planning Node's single Planner agent is read-only (no file writes, no git operations), so mutation/persistence separation is not applicable. The decomposition checklist (Section 4) still applies to each agent definition.

---

## Rationale

### Why three node types

The outer DAG's three-node structure (`[Planning Node] → [Coding Node] → Review`) maps to the three fundamentally different modes of work in issue resolution:

1. **Understand and decide** (Planning Node) — read-only, analytical, produces a plan. MVP: single agent in one tool-use loop.
2. **Produce working code** (Coding Node) — read-write, iterative, produces tested changes. Internal structure is a budget-gated cycle.
3. **Evaluate** (Review) — read-only, judgmental, produces accept/reject. Single agent, no internal structure needed.

These three modes have different execution characteristics (single-pass analysis vs. cycle vs. single-pass evaluation), different budget profiles (predictable vs. variable vs. modest), and different failure modes (bad plan vs. broken code vs. missed issue).

### Why the Planning Node is a single agent for MVP

Research, planning, and orchestration are tightly coupled activities where each phase informs the others. A single agent that can read code, analyze the problem, and produce a plan in one reasoning pass maintains coherence that would be lost when serializing through typed outputs between three separate agents. TDAG and Agyn both validate this — their highest-performing configurations use a single "main agent" or "manager" that handles the full understand-plan-decide workflow.

The risk of decomposing prematurely is real: a Plan agent that receives a Research output may discover it needs *more* research, creating a need for an internal cycle that contradicts the acyclic intent. A single agent handles this naturally — it reads more files mid-reasoning without any structural complication.

If the Plan is bad, the system discovers this after the Coding Node and Review execute — at which point the *next* Planning Node (at the next nesting level) activates with the failure context. Correction happens at the outer DAG level through nesting, not within the Planning Node.

### Why a composite node instead of a DAG-level cycle for coding

Policy 01 requires acyclicity at every nesting level. The previous workaround — modeling `CODE -> TEST -> CODE` as a "cycle with max iterations at DAG level" — is a contradiction that the architecture cannot cleanly express. The composite node resolves this by placing the cycle inside a node boundary. The outer DAG is genuinely acyclic. The inner cycle is genuinely cyclic. Each structure plays to its strengths: the DAG provides deterministic scheduling, cost attribution, and failure isolation; the cycle provides iterative refinement that adapts to problem difficulty.

### Why five sub-agents in the Coding Node

The separation follows from both empirical evidence and the principle of least privilege:

- **Programmer vs. Test Designer** (from AgentCoder): Writing code and designing tests are different cognitive tasks. A combined agent mode-switches between "produce a solution" and "find ways to break it" — adversarial reasoning that LLMs perform better when isolated.
- **Test Designer vs. Test Executor** (from AgentCoder): Designing tests requires reasoning about edge cases and failure modes. Executing tests requires running commands and parsing output. The designer should not be influenced by execution mechanics; the executor should not be designing tests.
- **Debugger as a separate role** (from MapCoder): Diagnosing a failure is different from fixing it. The Debugger analyzes test output and produces a structured diagnosis; the Programmer acts on it. This separation contributed to MapCoder's 93.9% on HumanEval.
- **Commit as a separate role** (from Policy 14): The mutation/persistence separation. Even inside the cycle, the agent that modifies source files must not be the agent that persists state to git.

### Why budget-gating instead of iteration caps for the Coding Node

Iteration caps are a proxy for the real constraint (cost). A cap of 3 iterations is either too many for a simple fix or too few for a hard bug. Budget-gating aligns the termination condition with the actual resource being consumed. The Agent Contracts paper formalizes this: resource contracts with conservation laws ensure bounded execution without arbitrary structural limits.

Budget-gating also composes cleanly with the outer DAG's budget allocation. The Coding Node's allocation is part of the DAG's total budget. Unspent budget returns to the reserve pool. Over-consumed budget triggers the standard exhaustion behavior (Policy 09, P8). No new budget mechanism is needed.

### Why Review is not composited in the MVP (but should be)

Review is implemented as a single agent for the MVP, but we believe a composite Review node would outperform a monolithic reviewer at catching **drift** — the gradual divergence between what the codebase should be (as defined by policies, conventions, and architectural decisions) and what the code actually becomes through incremental changes.

A monolithic Review agent must simultaneously evaluate code quality, correctness, test coverage, style, *and* policy compliance. This is the same mode-switching problem that motivated decomposing the Coding Node: an agent asked to do five things at once does each one worse than five agents doing one thing each. The AgentCoder and MapCoder results on coding apply equally to review — specialized agents with narrower scope produce more consistent, thorough output.

#### Future: Policy Review Agent

The highest-value decomposition within a composite Review node is a **Policy Review Agent** — a sub-agent that evaluates changes exclusively against the codebase's policies (CLAUDE.md, design policies, security policies, permission models).

The Policy Review Agent catches a class of problems that a general-purpose reviewer routinely misses: changes that are *correct and well-written* but violate a policy. Examples:

- A change introduces an agent with more permissions than it needs — the code works, tests pass, but it violates the least-privilege policy (Policy 14).
- A refactor combines two agents into one for convenience — cleaner code, but violates the Maximum Agent Separation design principle.
- A new endpoint skips authentication because it's "internal only" — functional, but violates the security boundary policy.

These are precisely the kinds of drift that accumulate silently. A general reviewer focuses on "does this code work?" and "is this code clean?" — it is not primed to cross-reference every change against a set of policy documents. A dedicated Policy Review Agent receives the policy corpus as primary context and evaluates every change through that lens.

The composite Review node would look like:

```
┌──────────────────────────────────────────────────────┐
│                   Review Node (future)                │
│                                                      │
│   ┌─────────────────┐    ┌───────────────────┐       │
│   │ Code Reviewer   │    │ Policy Reviewer   │       │
│   │ (quality,       │    │ (policy compliance,│      │
│   │  correctness,   │    │  drift detection,  │      │
│   │  style)         │    │  permission scope)  │     │
│   └────────┬────────┘    └────────┬──────────┘       │
│            └──────────┬───────────┘                   │
│                       ▼                               │
│              ┌──────────────┐                         │
│              │   Merge      │                         │
│              │  (combine    │                         │
│              │   verdicts)  │                         │
│              └──────────────┘                         │
│                                                      │
└──────────────────────────────────────────────────────┘
```

The two sub-agents run in parallel (their concerns are independent), and a merge step combines their verdicts. A rejection from either sub-agent rejects the change. This is the fan-out/fan-in pattern already used for parallel code branches in the Coding Node.

This decomposition is deferred to post-MVP for the same reason as the Planning Node decomposition: the single-agent approach is adequate for initial scope, and the composite node pattern provides the structural template when the time comes.

### Why defer work preservation to post-MVP

Work preservation within the Coding Node's cycle requires managing git state across sub-agent invocations, defining "forward progress" heuristics, implementing snapshot-aware restart logic, and handling merge conflicts. Each is solvable but unjustified until the system produces data on how often Coding Nodes fail mid-cycle. The Commit sub-agent's architectural slot ensures work preservation is a feature addition, not a redesign.
