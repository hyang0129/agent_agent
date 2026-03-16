# DAG as Orchestration Model

## Background / State of the Art

### Why DAGs Dominate Agent Orchestration

A Directed Acyclic Graph (DAG) models work as nodes (tasks) connected by directed edges (dependencies), with the constraint that no path loops back to a previously visited node. This structure has become the default orchestration model for multi-agent AI systems, and the convergence is not accidental. DAGs inherit decades of proven use in build systems (Make, Bazel), data pipelines (Airflow, Prefect, Spark), CI/CD (GitHub Actions), and compiler optimization (instruction scheduling). The properties that make DAGs effective in those domains transfer directly to agent orchestration.

The core insight is that most engineering work has a natural partial order: research must precede implementation, implementation must precede testing, testing must precede review. Some of these steps are independent and can run in parallel (two files changed in separate modules), while others are strictly sequential (you cannot test code that has not been written). A DAG captures this partial order precisely, without over-constraining execution order for independent work.

### Properties of Software Engineering Tasks That Favor DAGs

Software engineering tasks, particularly issue resolution, exhibit several properties that make DAGs a strong fit:

**Natural partial ordering.** Most issue resolution workflows decompose into phases with clear dependency relationships: understand the problem, identify affected code, plan changes, implement changes, run tests, review results. Within each phase, subtasks are often independent (changing file A and file B can happen in parallel).

**No circular dependencies at the task level.** While iterative refinement exists (fix a test failure, re-run tests), the iteration happens within a bounded retry loop at a single node, not as a structural cycle in the task graph. A test node may retry its agent invocation, but it does not send control back to the implement node and re-enter from the top.

**Deterministic scheduling from topology.** A topological sort of the DAG produces a valid execution order. Combined with dependency tracking, the orchestrator knows exactly which nodes are ready to dispatch at any point. This eliminates scheduling ambiguity and enables maximal parallelism.

**Deadlock freedom by construction.** The acyclic constraint guarantees that no set of nodes can form a circular wait. If all edges point forward, every node will eventually become unblocked as its predecessors complete.

**Clear failure boundaries.** When a node fails, the impact is scoped to its downstream subtree. Nodes on unrelated branches are unaffected. This makes partial completion meaningful: a DAG run can produce useful work (a branch with some files changed and tested) even if one subtree fails.

### Industry Frameworks and Academic Work

**LangGraph (LangChain).** LangGraph represents agent workflows as state machines over graphs. While it supports cyclic graphs for iterative refinement patterns (reflect-retry loops), the planning and scheduling of multi-agent work still follows DAG semantics: nodes execute when their upstream dependencies complete, and the graph is traversed topologically. LangGraph adds cycle support primarily for single-agent reasoning loops, not for inter-agent dependency structure.

**MetaGPT (Hong et al., 2023).** MetaGPT models the software development lifecycle as an assembly line with role-specialized agents (Product Manager, Architect, Engineer, QA). Tasks flow through this pipeline with structured output contracts between stages. The dependency structure is a DAG: requirements feed architecture, architecture feeds implementation, implementation feeds testing. MetaGPT achieves a high task completion rate by encoding Standard Operating Procedures (SOPs) into agent prompts and requiring structured output at each stage, preventing the ambiguity that derails less structured approaches. Published at ICLR 2024.

**TDAG (Wang et al., 2024; Neural Networks 2025).** The TDAG (Task Decomposition and Agent Generation) framework explicitly decomposes complex tasks into DAGs, where each node is assigned to a dynamically generated specialist agent. TDAG's key contribution is dynamic replanning: remaining subtasks are reformulated after each completion using the update rule `t_i' = Update(t_i, r_1, ..., r_{i-1})`, incorporating results from all prior completions. Error analysis shows dynamic replanning reduces cascading task failures from 34.78% (static plans) to 4.35%. Completed nodes are preserved for reference; only pending nodes are mutable.

**S-DAG (2025).** Subject-Based Directed Acyclic Graph for multi-agent heterogeneous reasoning. Extends the DAG model by partitioning nodes by subject domain, enabling agents with different reasoning capabilities to operate on their respective subgraphs while maintaining the global DAG constraint.

**CrewAI.** Uses a role-based abstraction where agents are assigned to crews with defined tasks. Under the hood, task dependencies form a DAG. The framework handles sequential and parallel execution based on declared dependencies.

**AutoGen (Microsoft).** Focuses on conversational agent patterns. AutoGen v0.4+ supports structured workflows but historically emphasized chat-based coordination over graph-based orchestration. The conversational model works well for brainstorming and exploration but provides weaker guarantees about execution order and completion than DAG-based approaches.

**Airflow / Prefect / Temporal / Flyte.** The data engineering and workflow orchestration ecosystems have refined DAG execution for over a decade. Key patterns that agent orchestrators inherit: per-node retry policies, failure state machines (pending/running/completed/failed/crashed), checkpoint-based recovery, and topological dispatch with maximal parallelism. These systems diverge on mutability: Airflow and Flyte freeze DAG structure at execution time (Flyte enforces immutable versioned workflow definitions); Prefect discovers the graph at runtime with no upfront DAG; Temporal makes the entire execution history an immutable append-only event log with deterministic replay. See the "DAG Mutability" section for detailed analysis.

**ALAS (2025).** A Stateful Multi-LLM Agent Framework for Disruption-Aware Planning. Implements a three-layer architecture (Workflow Blueprint, Agent Factory, Runtime Monitor) with transactional semantics: immutable execution logs, idempotency keys, and compensation handlers. Distinguishes between plan mutations (incremental changes to pending work) and plan replacement (full regeneration). Local compensation over global replanning minimizes disruption while preserving completed work.

**Netflix Maestro (open-sourced 2024).** Netflix's next-generation workflow orchestrator supports both acyclic and cyclic workflows. Handles dynamic behavior through parameterized sub-workflows rather than structural mutation: the top-level DAG is fixed, but it dispatches to different sub-workflows based on runtime parameters evaluated via a secure expression language (SEL). This is structural indirection, not graph mutation.

### Alternatives to DAGs and Their Trade-offs

**Linear pipelines.** A strict sequence of steps: A then B then C then D. Simple to implement and reason about. The limitation is that independent work cannot run in parallel. For a three-file change where each file is independent, a linear pipeline serializes what could be concurrent, wasting time and budget. Linear pipelines are a degenerate case of DAGs (a DAG with no branching), so adopting DAGs subsumes them without loss.

**Flat parallel fan-out.** All tasks run concurrently with no dependency structure. Fast but only works when tasks are truly independent. In issue resolution, tasks are rarely fully independent: implementation depends on research, testing depends on implementation. Fan-out without dependency tracking leads to agents working with stale or missing context.

**Trees (hierarchical task networks).** A tree structure where a parent task decomposes into child tasks, each of which may further decompose. Trees are a special case of DAGs where each node has at most one parent. The limitation is that trees cannot represent convergence: if two independent research tasks both feed into a single implementation task, that requires two parents, which is a DAG but not a tree. Software engineering tasks routinely converge (merge results from parallel analysis into a single implementation plan), making trees insufficient.

**Blackboard architecture.** A shared knowledge base that all agents read from and write to, with no explicit dependency edges. Agents volunteer to work based on the current state of the blackboard rather than being dispatched by topology. Recent work (Google Research, 2025) shows blackboard systems can outperform rigid hierarchical approaches by 13-57% on exploratory tasks where the solution path is unknown. However, blackboard systems sacrifice execution determinism: the order in which agents act depends on runtime conditions, making it difficult to predict completion time, enforce budgets, or guarantee that prerequisite work finishes before dependent work starts. For issue resolution, where the workflow is largely predictable (research, implement, test, review), the flexibility of a blackboard is not needed and its non-determinism is a liability.

**Cyclic graphs.** Graphs that allow loops, enabling iterative refinement patterns: generate code, test it, if tests fail loop back to generation. LangGraph supports this. The advantage is native representation of retry-until-success patterns. The disadvantages are significant: termination is no longer guaranteed by construction (you need explicit loop bounds), scheduling becomes more complex, and debugging state transitions through cycles is harder. The practical solution used by most production systems is to model iteration as bounded retries within a DAG node rather than as structural cycles in the graph.

### DAG Mutability: Patterns from Industry and Research

The question of whether a DAG should be mutable during execution is one of the most consequential design decisions in workflow orchestration. Industry systems and academic frameworks have converged on a spectrum of approaches, each with distinct trade-offs for crash recovery, auditability, and adaptability.

#### The Immutable-Structure Pattern

**Apache Airflow.** Airflow treats DAG structure as immutable at execution time. The DAG definition is parsed before a run begins, and the resulting graph of tasks and edges is frozen for that run. Dynamic Task Mapping (introduced in Airflow 2.3) allows a task template to expand into multiple parallel instances at runtime via `expand()`, but this is instance multiplication within a fixed structural slot — the graph topology itself does not change. As the Airflow documentation states: "Changing the structure of a DAG at runtime is simply not possible. Even dynamic task and group mapping does not change the structure; it just makes nodes in the graph have multiple instances, but the graph remains as-is." This constraint simplifies the scheduler, makes crash recovery trivial (reload the frozen DAG, check task states, resume), and ensures the execution record is a faithful representation of the plan.

**Apache Spark.** Spark's DAG of RDD transformations is immutable by design. Once the execution plan is materialized into stages and tasks, the structure is fixed. Fault tolerance is achieved through lineage: because each RDD records the deterministic transformation that produced it, lost partitions can be recomputed from their ancestors without modifying the DAG. Spark retries failed stages (up to 4 times by default) but never restructures the graph. This immutability-plus-lineage model provides recovery guarantees without the complexity of versioned graph state.

**Flyte.** Flyte enforces immutability at the workflow definition level: "Registered workflows are immutable, meaning an instance of a workflow defined by a specific {Project, Domain, Name, Version} combination cannot be updated." When a workflow definition changes, a new version is created while the original remains available. In-progress executions continue on the old version unaffected. Flyte's caching system leverages this immutability — because tasks are versioned and deterministic, completed outputs can be reused across DAG versions without re-execution. Flyte 2.0 introduces dynamic workflow composition (higher-order functions that produce subgraphs at runtime), but each instantiated subgraph is itself immutable once execution begins.

**Temporal.** Temporal takes immutability further by making the entire execution history an immutable, append-only event log. Workflow code must be deterministic: given the same input, it must emit the same sequence of Commands (scheduling activities, starting timers, spawning child workflows) in the same order. During replay, emitted Commands are compared against the existing Event History — any mismatch produces a non-deterministic error. When workflow definitions need to change for in-flight executions, Temporal provides two mechanisms: Worker Versioning (binding workers to specific code revisions so old and new executions run different code) and Patching (embedding version checks within workflow code to conditionally branch). Neither mechanism mutates the execution graph; instead, they ensure new code produces a Command sequence compatible with the existing event history.

#### The Event-Sourcing Analogy

The Temporal model reflects a broader architectural pattern: event sourcing. In event-sourced systems, all state changes are captured as an immutable, append-only sequence of events. The current state is a derived projection — a materialized view that can be destroyed and reconstructed by replaying the event log. This separation of immutable log from mutable projection provides crash recovery (replay from the last checkpoint), auditability (the full history is preserved), and flexibility (new projections can be built from the same event stream).

Applied to DAG orchestration, the analogy suggests: the DAG definition and node execution events should be immutable records in the state store. The orchestrator's in-memory view of "what to do next" is a mutable projection derived from those records. On crash, the projection is reconstructed from the immutable log. This model allows the orchestrator to adapt its behavior (e.g., skip unreachable nodes, replan remaining work) without mutating the historical record.

#### The Mutable-Structure Pattern

**Prefect.** Prefect 2+ takes the opposite approach: there is no DAG upfront. The execution graph is discovered as the flow function runs — tasks can be created, skipped, or rerun based on conditional logic and the outputs of prior tasks. The graph is inherently mutable because it does not exist until runtime constructs it incrementally. This provides maximum flexibility for dynamic workflows but sacrifices the ability to inspect or validate the full execution plan before any work begins.

**Dagster.** Dagster supports dynamic outputs via `DynamicOut`, which allows a graph to expand at runtime based on data. Like Airflow's task mapping, this is structural expansion within a defined template rather than arbitrary graph mutation. Dagster's asset-centric model provides a middle ground: the dependency graph of assets is declared upfront, but the computation graph for materializing those assets can vary per run.

**LangGraph.** LangGraph supports runtime graph mutation as a first-class capability: agents can dynamically spawn nodes, restructure edges, and re-enter previous nodes. State is managed through explicit, reducer-driven schemas where each node reads from and writes to a shared state object. LangGraph provides checkpointing for persistence and human-in-the-loop inspection. This model prioritizes adaptability — an agent detecting missing context can trigger a sub-agent, redirect flow, then resume — but trades away the scheduling determinism and bounded-cost guarantees that immutable DAGs provide.

**Netflix Maestro.** Maestro (open-sourced July 2024) supports both acyclic and cyclic workflows and handles dynamic behavior through parameterized sub-workflows: rather than mutating the DAG at runtime, users define sub-workflow invocations where the sub-workflow ID is a parameter evaluated at runtime. This is structural indirection rather than mutation — the top-level DAG is fixed, but it can dispatch to different sub-workflows based on runtime state. Maestro also supports a secure expression language (SEL) for dynamic parameter injection, enabling state sharing between steps without modifying graph topology.

#### The Hybrid Pattern: Immutable Spine, Mutable Future

**TDAG (Wang et al., Neural Networks 2025).** TDAG dynamically updates remaining subtasks based on the results of completed ones. The update rule is explicit: `t_i' = Update(t_i, r_1, r_2, ..., r_{i-1})` — each subsequent subtask is reformulated by the main agent incorporating results from all earlier completions. Completed subtasks remain in the task list for reference, but pending subtasks are mutable. Error analysis shows this approach reduces cascading task failures from 34.78% (static plans) to 4.35%, validating the core claim that dynamic replanning outperforms static DAGs on complex tasks. Crucially, TDAG does not revisit completed work — it only modifies the future.

**ALAS (Arxiv 2505.12501, May 2025).** ALAS implements a three-layer architecture — Workflow Blueprint (DAG definition), Agent Factory (dynamic agent instantiation), and Runtime Monitor (execution tracking) — with an explicit distinction between plan mutations and plan replacement. Plan mutations are incremental modifications to the existing DAG: removing failed tasks, adjusting parameters, inserting recovery steps while preserving planning context and executing only changed branches. Plan replacement is complete regeneration when mutations are insufficient. ALAS prefers mutations for efficiency but escalates to replacement when constraint violations or cascading failures make incremental fixes infeasible. The framework's transactional semantics — immutable execution logs, idempotency keys per task, and compensation handlers that execute in reverse topological order — ensure that partial failures never leave the system in an ambiguous state. The reactive compensation protocol detects downstream conflicts, evaluates alternative execution paths, and validates against temporal, resource, and domain constraints before applying changes. This "local compensation over global replanning" principle minimizes disruption while preserving completed work.

**Kubernetes Reconciliation Pattern.** Kubernetes controllers implement a reconciliation loop that continuously compares desired state (the spec) against observed state (the status) and takes actions to converge them. The spec is the immutable declaration of intent; the status is the mutable reality. Applied to DAG orchestration, this pattern suggests separating the plan (immutable intent) from execution state (mutable observation), with the orchestrator acting as a reconciliation controller that drives observed state toward the plan. When the plan itself needs to change, Kubernetes's approach is to create a new resource version rather than mutate the existing one — the equivalent of DAG versioning rather than DAG mutation.

#### Convergent Principles

Across these systems, several principles emerge:

1. **The execution record must be immutable.** Whether through event sourcing (Temporal), lineage (Spark), versioned definitions (Flyte), or append-only execution logs (ALAS), production systems universally treat the historical record of what happened as immutable. This is non-negotiable for crash recovery and auditability.

2. **Structural immutability simplifies crash recovery.** Systems that freeze the DAG structure at execution start (Airflow, Spark, Flyte) have simpler recovery: reload the structure, check node states, resume. Systems that allow structural mutation (LangGraph, Prefect) must persist every structural change and reconcile on recovery.

3. **Adaptation is necessary but should be scoped.** Static plans fail when execution reveals information the planner did not have (TDAG's 34.78% cascading failure rate for static plans). But unrestricted mutation sacrifices cost predictability and deterministic scheduling. The most robust systems scope adaptation to the future: completed work is immutable, pending work is replannnable.

4. **Versioning beats mutation.** When the plan must change, creating a new version of the plan (Flyte, Kubernetes, ALAS's plan replacement) is architecturally cleaner than mutating the existing plan in place. Versioned plans can be diffed, audited, and rolled back. Mutated plans require tracking every incremental change.

5. **Replanning must be bounded.** TDAG, ALAS, and Agent Agent's own Policy 10 (planning agent) all cap replanning events. Unbounded replanning creates oscillation risk and unpredictable cost. The consensus range is 2-3 replanning events per execution.

### Known Limitations of DAG-Based Orchestration

**Static structure assumption.** A DAG is typically planned before execution begins. If the task changes mid-execution (e.g., research reveals the issue is fundamentally different than expected), the DAG must be replanned. Frameworks like TDAG address this with dynamic replanning (reducing cascading failures from 34.78% to 4.35%), and ALAS demonstrates that scoped plan mutations with transactional semantics can adapt pending work while preserving completed results. Agent Agent addresses this through versioned DAG succession (Policy 6), where replanning creates a new DAG version for remaining work rather than mutating the executing DAG. See the "DAG Mutability" section for a full analysis of industry approaches.

**Poor fit for exploratory work.** When the solution path is unknown and agents need to explore multiple hypotheses concurrently, pruning dead ends and expanding promising ones, a DAG's predetermined structure is constraining. Blackboard or Monte Carlo Tree Search approaches are better suited to open-ended exploration.

**Overhead for simple tasks.** If an issue can be resolved by a single agent in a single step, constructing a DAG adds planning overhead with no benefit. The planner should recognize trivially simple issues and bypass DAG construction.

**Rigid inter-agent communication.** DAG edges define data flow: upstream outputs feed downstream inputs. Agents cannot easily request ad-hoc information from siblings or non-adjacent nodes without breaking the DAG abstraction. Workarounds include shared context stores, but these reintroduce some of the coupling problems that DAGs are designed to avoid.

**Iteration modeled as retries, not structure.** Real software development involves iteration: write code, run tests, fix failures, re-run tests. DAGs handle this via retry loops within a node, but this collapses the iteration into a single node's execution history rather than representing it as first-class structure. For deeply iterative tasks (e.g., getting a complex algorithm to pass edge-case tests), this can mean a single node accumulates many expensive retries.

### When DAGs Are Not the Right Choice

- **Conversational or negotiation workflows** where agents need to go back and forth (code review with multiple rounds of feedback). Model as a single node with internal dialogue, or use a cyclic graph.
- **Open-ended research** where the scope of work is unknown upfront. Use a planner-executor loop or blackboard.
- **Single-step tasks** that do not require decomposition. Skip the DAG, dispatch one agent directly.
- **Real-time event-driven systems** that must react to external events during execution. DAGs assume batch-style execution from a known starting state.

---

## Policy

The following policies govern how Agent Agent uses DAGs to orchestrate issue resolution.

### 1. Every issue resolution MUST be modeled as an immutable, recursively nested DAG

When the orchestrator receives a GitHub issue, it begins execution with a top-level DAG. DAGs are immutable once persisted — no nodes are added, removed, or re-executed, and no edges are modified. Adaptation to runtime discoveries is achieved through **nesting**: an orchestrate node may spawn a child DAG as its internal execution, following the Temporal Child Workflow model. The parent DAG sees only the orchestrate node's typed inputs and outputs; the child DAG's structure is opaque to the parent.

Each level of the hierarchy is an independent, immutable, acyclic graph. Iteration and replanning are expressed as recursion through nesting, not as structural cycles or mutations to an executing DAG.

### 2. The standard node sequence is: research → plan → orchestrate

Every DAG level follows the same spine:

```
research → plan → orchestrate
```

The **orchestrate** node is the decision point. It examines the plan and either:
- Returns `null` (the work is complete, no child DAG needed), or
- Spawns a child DAG to execute the plan.

A typical issue resolution produces a recursion like:

```
L0: research → plan → orchestrate
                          └─ L1: code → test → review → research → plan → orchestrate
                                                                              └─ L2: code → test → review → research → plan → orchestrate
                                                                                                                                └─ L3: git → PRReview → research → plan → orchestrate
                                                                                                                                                                            └─ null (done)
```

At each level after L0, the sequence begins with the work phase (code/test/review or git/PRReview) followed by the research → plan → orchestrate spine. The research and plan nodes at each level are **preloaded**: they are always present in the DAG structure but execute as stubs (pass-through with minimal output) if the preceding review/test passes. If the review/test fails, they activate fully — research investigates the problems indicated by the failure, and plan produces a revised approach for the next orchestrate node to execute.

### 3. DAGs MUST be acyclic at every nesting level

No DAG at any level of the hierarchy may contain cycles. The acyclic constraint provides termination guarantees, deadlock freedom, and predictable cost bounds within each level. What would traditionally be modeled as a cycle (test fails → fix → re-test) is instead expressed as recursion: the orchestrate node spawns a new child DAG that includes a new code → test → review sequence. Each child DAG is independently acyclic.

### 4. Parallelism is horizontal within a level, never vertical across levels

The orchestrator dispatches all nodes whose upstream dependencies are satisfied concurrently, up to the configured concurrency limit. This parallelism is strictly **intra-level**: independent branches within a single DAG (e.g., `code(module_a)` and `code(module_b)`) run concurrently. The concurrency limit is configurable per environment (dev: lower for debuggability, prod: higher for speed).

**Cross-level parallelism does not exist.** Because all branches within a DAG must converge into a single orchestrate node (Policy 6), and only that orchestrate node can spawn the next nesting level, recursion is strictly sequential. Level N+1 cannot begin until level N's orchestrate node executes, which cannot happen until all of level N's parallel branches have completed. There is never a case where two child DAGs at different depths run simultaneously from the same lineage.

This is a direct consequence of the convergence rule, not a separate constraint. It means the execution tree has a simple shape: wide within levels, deep across levels, but never both simultaneously.

### 5. The planner MUST declare explicit edges for every data dependency

No implicit dependencies. If node B needs output from node A, there must be a directed edge from A to B in the DAG definition. The orchestrator validates the DAG at construction time: every node's declared input types must be satisfiable by the output types of its upstream nodes. This applies at each nesting level independently.

### 6. Every branch MUST terminate in an orchestrate node

When a plan produces parallel branches (e.g., code changes to independent modules), every branch must converge into a single orchestrate node as its terminal node. No branch may terminate in a non-orchestrate node. A branch ending in, say, a test or review node would assert that no further work could possibly be needed beyond that point — an assumption the system must not make. The orchestrate node is the only node authorized to make the completion decision (return `null` or spawn a child DAG).

This means all fan-out within a DAG level must fan back in to a single orchestrate node:

```
plan → orchestrate
           ├─ code(module_a) → test(module_a) → review(module_a) ─┐
           └─ code(module_b) → test(module_b) → review(module_b) ─┤
                                                                   └─ research → plan → orchestrate
```

A corollary: because exactly one orchestrate node terminates each level, exactly one child DAG can be spawned per level. This eliminates multi-level parallelism by construction — the orchestrate node is a serialization point between nesting levels.

### 7. Every node MUST produce a typed, structured output

Agent outputs are Pydantic models, not free text. The output schema is determined by the agent type (ResearchOutput, CodeOutput, TestOutput, ReviewOutput, PlanOutput, OrchestrateOutput). Downstream nodes receive typed inputs derived from upstream outputs. This enforces interface contracts between agents, enables validation before downstream dispatch, and makes context passing explicit and auditable.

The orchestrate node's output is either `null` (signaling completion) or a reference to the child DAG it spawned. The parent DAG records only this reference and the child's eventual typed result — not the child's internal structure.

### 8. Every DAG at every nesting level MUST be persisted before execution begins

The full DAG definition (nodes, edges, agent types, parent DAG reference, nesting depth) is written to the state store before the first node is dispatched. Each DAG record includes a reference to its parent orchestrate node (null for the top-level DAG). This enables crash recovery at any nesting level: on restart, the orchestrator reconstructs the DAG tree from the state store and resumes from the last completed node at each level. No in-memory-only DAG state.

Completed nodes within a nested DAG can be resumed deterministically. While node internals are generally non-deterministic (an agent may produce different output on re-invocation), the persisted DAG structure and completed node outputs constitute a deterministic checkpoint. On recovery, the orchestrator does not re-execute completed nodes — it reloads their outputs from the state store and resumes execution from the first incomplete node. This is the same dynamic-discovery pattern used by Temporal's event history replay: the structure is discovered at runtime but, once recorded, becomes the deterministic ground truth for recovery.

### 9. Topological order MUST be the sole determinant of execution order within a DAG level

The orchestrator traverses each DAG level in topological order. No priority hints, no manual ordering overrides, no agent-type-based scheduling preferences. If two nodes are both ready (all upstream dependencies met), both are dispatched concurrently. This applies independently at each nesting level — the parent DAG's topological order governs the parent; the child DAG's topological order governs the child.

### 10. Recursion depth is estimated, not hard-capped

The orchestrator maintains a **recursion estimate** (default: 10) representing the expected maximum nesting depth for a given issue. This estimate is passed as context to research and plan nodes at every level, along with the current depth. As execution approaches the estimate, research and plan agents can make better-informed decisions: simplifying plans, consolidating remaining work, or recommending escalation to a human.

The estimate is advisory, not a hard ceiling. The orchestrator does not kill an execution that exceeds the estimate. However, budget constraints (which are hard limits) naturally bound recursion depth — deeper nesting consumes budget, and budget exhaustion triggers escalation regardless of depth.

### 11. Git state MUST be preserved after code changes, before testing

When a code node completes, the working tree state is preserved in a recoverable form before the test node executes. This is the checkpoint boundary: the code for that level is "locked in" at this point. The test, review, and all subsequent nodes at that level operate against this fixed snapshot.

The preservation mechanism may be a git commit, stash, or patch file — the specific form is an implementation detail. The requirement is that the state can be reconstructed exactly. The code node records the git state reference (commit SHA, stash ref, etc.) as part of its output, and downstream nodes receive it as context.

This produces a checkpoint stack aligned with the code phases across recursion levels: each level's code node locks in its changes, and if a child DAG at level N+1 fails catastrophically, the orchestrator can restore the working tree to the state locked in by level N's code node. The test and review nodes never modify the working tree — they only read and evaluate it.

---

## Rationale

### Policy 1: Immutable recursively nested DAGs

**Why:** This policy resolves the fundamental tension between immutability and adaptability identified in the SOTA section. Pure immutability (Airflow, Spark) provides simple crash recovery and faithful audit trails but cannot adapt when execution reveals information the planner did not have (TDAG shows 34.78% cascading failure rate for static plans). Pure mutability (LangGraph, Prefect) provides adaptation but sacrifices scheduling determinism and complicates recovery.

The nested model achieves both properties simultaneously by placing the immutability boundary at the DAG level and expressing adaptation as recursion. Each DAG is individually immutable — Flyte's principle that "registered workflows are immutable per {Project, Domain, Name, Version}" applies to each nesting level. Adaptation happens by spawning a new child DAG, not by mutating the parent. This follows Temporal's Child Workflow model directly: the parent sees only a single invocation record (the orchestrate node's output); the child's internal structure is opaque and independently recoverable.

The event-sourcing analogy holds cleanly: the state store's collection of persisted DAGs (one per nesting level) is the immutable event log. The orchestrator's runtime view of "what to do next" is a mutable projection reconstructed from that log on recovery.

### Policy 2: Standard node sequence (research → plan → orchestrate)

**Why:** A uniform spine at every level makes the execution tree comprehensible to both humans and agents troubleshooting failures. Every level follows the same pattern: understand the situation (research), decide what to do (plan), do it (orchestrate → child DAG). The preloaded research/plan nodes that stub out on success add minimal overhead (a stub node produces a pass-through output in negligible time and tokens) while ensuring the decision point always exists if needed.

This design also means that a failing execution produces a readable narrative at every level: "we researched X, planned Y, executed Z, Z's review failed, so we researched the failure, planned a fix, executed the fix..." — each level is a self-contained chapter.

### Policy 3: Acyclic at every level

**Why:** The same guarantees that make acyclicity valuable for flat DAGs (termination, deadlock freedom, bounded cost) apply at each nesting level independently. Recursion through nesting replaces structural cycles: what was previously "retry the implement node" becomes "the orchestrate node spawns a new child DAG that includes a new code → test → review sequence." This makes each attempt a first-class, auditable execution record rather than a hidden retry buried inside a node's internal state.

### Policy 4: Horizontal parallelism only

**Why:** Parallelism within a level (running `code(module_a)` and `code(module_b)` concurrently) is safe because these branches operate on independent scopes with no data dependency between them — the DAG's explicit edges guarantee this. Cross-level parallelism (running level N+1 while level N is still executing) is impossible by construction: level N+1 can only be spawned by level N's orchestrate node, and that node can only execute after all of level N's branches have converged into it.

This means the execution shape at any point in time is: one active DAG level with potentially many parallel nodes, plus a stack of suspended parent orchestrate nodes waiting for their child DAGs to complete. The stack is strictly linear. This simplifies reasoning about resource consumption, budget allocation, and git state — at any given moment, only one level is actively modifying the working tree.

### Policy 6: Every branch terminates in orchestrate

**Why:** The orchestrate node is the system's only decision point for "is this done?" Any branch that terminates without an orchestrate node is asserting completion without verification — it claims that the work produced by that branch requires no further action under any circumstance. This is an unsafe assumption for non-trivial work. The orchestrate node's job is precisely to evaluate whether the accumulated work is sufficient or whether another recursion level is needed.

The convergence requirement (all branches fan back into a single orchestrate) ensures that the decision about "what's next" is made with full visibility into all parallel branches' results. An orchestrate node that only sees one branch's output cannot make an informed decision about the overall state.

The single-orchestrate-per-level rule also eliminates multi-level parallelism as a structural invariant rather than a runtime check. Because there is exactly one terminal node per level, there is exactly one possible child DAG per level. This makes the recursion tree a strict chain (L0 → L1 → L2 → ...), never a tree of concurrent child DAGs competing for the same working tree and budget.

### Policy 8: Persistence and deterministic resumption

**Why:** Node internals are non-deterministic — the same agent invoked twice with the same inputs may produce different outputs. However, once a node completes and its output is persisted, that output becomes the deterministic ground truth. The nested DAG's structure is discovered at runtime (the orchestrate node decides whether and what child DAG to spawn), but once discovered and persisted, the structure is fixed.

This is the same model as Temporal's event history: workflow code is re-executed on replay, but Commands are compared against the existing Event History rather than being acted on again. In Agent Agent's case, completed node outputs are loaded from the state store rather than being re-computed. The nesting structure (which orchestrate nodes spawned which child DAGs) is similarly reconstructed from persisted parent references.

This constitutes a controlled exception to the general principle that node internals are non-deterministic. The orchestrate node's internal behavior (spawning and managing a child DAG) is deterministic on recovery because the child DAG and its completed nodes are persisted. The non-determinism occurred during the original execution; recovery replays the deterministic record.

### Policy 10: Recursion estimate, not hard cap

**Why:** A hard recursion depth limit (like the previous Policy 10's depth cap of 6) forces the planner to compress work into fewer, coarser steps as the limit approaches — potentially producing a single monolithic node that is too broad to succeed. An estimate provided as context is more useful: the research and plan agents can see "we're at depth 7 of an estimated 10" and make informed tradeoffs about granularity, consolidation, and when to recommend escalation.

Budget constraints provide the actual hard bound. Deeper recursion consumes more tokens. When the budget runs out, execution stops regardless of depth. This aligns termination with the real constraint (cost) rather than an arbitrary structural limit.

### Policy 11: Git state preservation after code, before test

**Why:** The code node is the only node that modifies the working tree. Test and review nodes are read-only evaluators. By checkpointing immediately after code completes, we establish a clean boundary: everything before the checkpoint is mutable creative work; everything after is immutable evaluation of that work.

This placement matters for two reasons:

1. **Rollback granularity.** If test or review fails and the next recursion level also fails, the orchestrator needs to restore the working tree to a known-good state. That state is "after code completed at level N" — not "before orchestrate spawned level N+1" (which may include test artifacts or partial review state). The code node's output is the atomic unit of change at each level.

2. **Troubleshooting.** The checkpoint stack shows exactly what each level's code node produced, independent of whether test/review passed or what the next level attempted. An agent or human investigating a failure can walk the stack to see how the working tree evolved through each code phase, without noise from test/review side effects.
