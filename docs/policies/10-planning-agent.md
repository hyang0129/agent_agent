# Planning Agent Identity and Behavior

## Problem Statement

The orchestrator's Planner reads a GitHub issue and produces a sub-task DAG, but the current design leaves critical questions unresolved: Is the Planner an LLM agent or a deterministic function? What guides its decomposition decisions? Can plans be revised after execution begins? Without clear answers, the Planner becomes either a black box that produces unpredictable DAGs or a rigid template that cannot adapt to the reality discovered during execution.

A bad plan is worse than no plan. An over-decomposed DAG wastes tokens on trivial coordination. An under-decomposed DAG produces agents with scope too broad to constrain or audit. A plan that cannot be revised forces the system to complete doomed subtrees.

## State of the Art

### Planning Paradigms

There are three broad approaches to planning in AI coding systems, each with distinct trade-offs.

**No explicit plan (ReAct-style).** SWE-Agent exemplifies this. The agent interleaves reasoning and action in a loop: observe the environment, reason about what to do next, act, observe the result, repeat. There is no upfront plan; the agent discovers the path as it goes. This is flexible — the agent naturally adapts to what it finds — but produces long, meandering trajectories that are expensive, hard to audit, and prone to getting stuck in loops. SWE-Agent solves ~12.5% of SWE-bench issues, demonstrating that pure reactive execution without planning has a ceiling on complex problems.

**Plan then execute.** Devin and LangGraph's plan-and-execute pattern represent this approach. A planner LLM generates a multi-step plan upfront, then separate executors carry out each step. The plan provides structure, enables parallelism, and makes the workflow auditable. The risk is plan rigidity — if the plan was wrong (based on incomplete information), executors waste effort on doomed steps before the error surfaces. Devin 2.0+ mitigates this with replanning capabilities and self-reviewing PRs.

**Structured workflow (SOP/pipeline).** MetaGPT assigns specialized roles (Product Manager, Architect, Engineer) that follow Standard Operating Procedures, producing structured artifacts (design docs, API specs) rather than chat messages. Agentless uses a fixed three-phase pipeline: localize, repair, validate. AutoCodeRover follows a structured search-then-patch flow over the AST. These approaches trade flexibility for predictability — the workflow shape is predetermined, only the content varies. They work well for well-understood problem types but cannot adapt their structure to novel issue shapes.

### Key Systems and Their Planning Approaches

| System | Planning Style | Strengths | Weaknesses |
|--------|---------------|-----------|------------|
| SWE-Agent | None (ReAct loop) | Adapts to discoveries, simple architecture | Expensive, long trajectories, hard to audit |
| Devin | LLM plan → execute → replan | Structured, supports parallelism, auditable | Planner can hallucinate unrealistic plans |
| MetaGPT | Fixed SOP roles with structured artifacts | Predictable, reduces agent chatter | Rigid structure, cannot adapt workflow shape |
| AutoCodeRover | Structured AST search → patch | Cheap ($0.43/issue), leverages code structure | Limited to localization + patch pattern |
| Agentless | Fixed pipeline: localize → repair → validate | Simple, no agent complexity, competitive results | Cannot handle issues requiring novel workflows |
| MASAI | Modular sub-agents with tuned strategies | Per-sub-agent strategy tuning, avoids long contexts | Requires upfront decomposition design |

### Research Foundations

**Plan-and-Solve Prompting (Wang et al., ACL 2023).** Replaces "Let's think step by step" with "Let's first understand the problem and devise a plan to solve the problem." This simple change reduces missing-step errors in chain-of-thought reasoning. The key insight: explicitly prompting for a plan before execution produces more complete decompositions than letting the model reason incrementally. The plan acts as a scaffold that prevents the model from skipping steps.

**ReAct (Yao et al., ICLR 2023).** Interleaves reasoning traces with actions. Reasoning traces help the model "induce, track, and update action plans as well as handle exceptions," while actions gather information from the environment. The critical contribution is that explicit reasoning about *why* before deciding *what* improves both task completion and interpretability. For planning agents, this means the planner should articulate its reasoning about the issue before producing a DAG.

**ReWOO (Reasoning without Observation).** Plans the entire tool-call sequence upfront with variable substitution placeholders, then executes without consulting the planner between steps. Trades adaptability for speed — works well when the action sequence is predictable. Relevant to agent_agent because many GitHub issues follow predictable patterns (research → code → test → commit → review) where the workflow shape is known even if the content varies.

### Adaptive Replanning

Static plans fail when execution reveals information the planner did not have. Leading approaches to replanning include:

- **LangGraph's replan node:** After each execution step, a replan node inspects results and decides whether the remaining plan is still viable. If not, it generates a revised plan. This is the execute → replan → execute loop.
- **ALAS (Adaptive LLM Agent Scheduler):** Uses a three-layer architecture (workflow blueprint, agent factory, runtime monitor) with a reactive compensation protocol that detects downstream conflicts, evaluates alternatives, and validates constraints after each step.
- **Trigger-based replanning:** Rather than replanning after every step (expensive), replan only when a trigger fires: agent failure after max retries, output that contradicts plan assumptions, budget threshold crossed, or human checkpoint feedback.

### Plan Quality

Research and practice converge on what makes a good task decomposition:

- **Each sub-task has a clear, verifiable completion criterion.** "Research the codebase" is vague. "Identify the file(s) containing the function referenced in the error traceback" is verifiable.
- **Dependencies are explicit and minimal.** Unnecessary sequential dependencies prevent parallelism and create fragile chains.
- **Sub-tasks map to single agent capabilities.** A sub-task requiring both code generation and git operations violates the Maximum Agent Separation policy and signals the decomposition is too coarse.
- **The decomposition is proportional to issue complexity.** A typo fix should not produce a five-node DAG. A multi-file refactor should not be a single node.
- **No sub-task requires information that is unavailable at execution time.** Each node's inputs must be either static (from the issue) or produced by an upstream node.

## Policy

### 1. The Planner Is an LLM Agent with a Constrained Output Schema

The Planner is an LLM (Claude) invoked with a system prompt and structured output schema. It is not a deterministic rule engine. LLM planning is necessary because GitHub issues are natural language with unbounded variety — no rule engine can reliably decompose arbitrary issues into sub-tasks.

However, the Planner's output is constrained to a strict Pydantic schema defining DAG nodes, edges, agent type assignments, and per-node context. The Planner cannot produce free-form output. If the LLM's response does not validate against the schema, the invocation is retried with the validation error appended to the prompt (context enrichment, per the error handling policy).

### 2. The Planner Receives Structured Context, Not Raw Issue Text Alone

Before invoking the Planner LLM, the orchestrator assembles a structured context payload:

- The issue title and body.
- The repository file tree (or a summary if the repo is large).
- Labels, milestone, and any linked issues or PRs.
- The set of available agent types and their capabilities (so the Planner knows what building blocks it has).
- The token budget for this run (so the Planner can right-size the DAG).
- A history of previously failed plans for this issue, if any (so the Planner does not repeat the same decomposition).

The Planner's system prompt instructs it to reason about the issue before producing the DAG — articulating what kind of change is needed, what parts of the codebase are likely involved, and what the risk factors are. This reasoning is captured in the plan output (a `reasoning` field) for auditability, following the ReAct principle that explicit reasoning before action improves outcomes.

### 3. The Planner Uses Issue Classification to Select a Workflow Template

Not every issue requires novel planning. Most GitHub issues fall into recurring categories: bug fix, feature addition, refactor, documentation, dependency update, test addition. For each category, the system maintains a workflow template — a default DAG shape with placeholders for issue-specific content.

The Planner's first task is to classify the issue into a category. If a template exists, the Planner uses it as a starting point and adapts it (adding, removing, or reordering nodes as needed). If no template fits, the Planner generates a DAG from scratch.

This hybrid approach — template when possible, LLM when necessary — combines the predictability of structured workflows (Agentless, MetaGPT) with the flexibility of LLM planning (Devin). It also reduces token cost for common issue types.

Templates are defined as data, not code, and are versioned alongside the codebase. New templates can be added as patterns emerge from execution history.

### 4. Every Sub-Task Has a Verifiable Completion Criterion

Each DAG node produced by the Planner must include a `done_when` field: a concrete, machine- or human-evaluable description of what constitutes successful completion. Examples:

- "The file `src/auth/login.py` contains a new function `validate_token` that accepts a JWT string and returns a boolean."
- "All tests in `tests/test_auth.py` pass."
- "The PR description summarizes the changes and references issue #42."

Nodes with vague completion criteria ("understand the codebase," "improve the code") are rejected by schema validation. The `done_when` field is passed to the executing agent as part of its task context, and to the orchestrator for post-execution validation.

### 5. The DAG Respects Agent Separation Boundaries

The Planner must not produce a node that requires capabilities spanning multiple agent types. Each node maps to exactly one agent type (per the architecture principles in CLAUDE.md). The schema enforces this: each node has a single `agent_type` field.

During plan validation (before execution begins), the orchestrator checks that no node's described task implies capabilities outside its assigned agent type's permission profile. For example, a node assigned to a RESEARCH agent whose description mentions "fix the bug" is flagged as misaligned.

### 6. Plan Validation Runs Before Execution Begins

After the Planner produces a DAG, and before any node executes, the orchestrator runs a validation pass:

- **Schema validation:** The DAG conforms to the Pydantic model. All required fields are present and correctly typed.
- **Graph validation:** The DAG is a valid directed acyclic graph (no cycles, no orphaned nodes, all referenced dependencies exist). Enforced by networkx.
- **Completeness check:** The DAG has at least one terminal node whose `done_when` aligns with the original issue's acceptance criteria.
- **Proportionality check:** Heuristic bounds on DAG size relative to issue complexity. A one-label issue with a 10-node DAG, or a multi-file feature request with a single node, triggers a warning (not a hard block — the Planner may have valid reasons).
- **Budget feasibility:** The sum of estimated token costs across all nodes does not exceed the run's token budget. Estimates are derived from historical averages per agent type.
- **Agent-capability alignment:** Each node's task description is consistent with its assigned agent type's capabilities (see point 5).

Validation failures are returned to the Planner LLM for a correction attempt (up to 2 retries). If validation still fails after retries, the run is escalated to the human operator with the plan and validation errors.

### 7. Replanning Is Trigger-Based, Not Continuous

The system does not replan after every node completion (too expensive) or never (too rigid). Instead, replanning triggers fire on specific conditions:

- **Node failure after max retries:** The dead-lettered node's subtree may need restructuring. The Planner is re-invoked with the failure context and completed node outputs to produce a revised DAG for the remaining work.
- **Output contradiction:** An executing agent's output contradicts an assumption in the plan (e.g., the research agent discovers the target function does not exist, but downstream nodes assume it does). The orchestrator detects this via the `done_when` criteria and triggers a replan.
- **Human checkpoint feedback:** If a human checkpoint (per the human checkpoints policy) returns revision instructions, the Planner incorporates them into a revised plan.
- **Budget threshold:** When token usage crosses 70% of the budget with significant work remaining, the Planner is asked to produce a reduced plan that completes the most critical remaining work within budget.

On replan, the Planner receives: the original issue, the original plan, the completed nodes and their outputs, the trigger reason, and any error context. It produces a revised DAG covering only the remaining work. Completed nodes are never re-executed. The revised DAG is validated through the same checks as the original (point 6).

### 8. The Planner Has a Token Budget and Turn Limit

The Planner itself consumes tokens. To prevent runaway planning (the Planner producing increasingly elaborate plans on retry), it is subject to:

- A per-invocation token cap (default: 8,000 output tokens). Plans that require more output than this are likely over-decomposed.
- A maximum of 3 planning attempts per run (initial plan + 2 validation retries).
- A maximum of 2 replanning events per run. If the plan needs more than 2 revisions during execution, the run is escalated to the human operator. Frequent replanning signals that the issue is too ambiguous or complex for automated resolution.

### 9. Plans Are Persisted and Auditable

Every plan (original and revised) is persisted to the state store with:

- The full structured context that was provided to the Planner.
- The Planner's reasoning trace.
- The DAG definition.
- Validation results.
- The trigger reason (for replans).

This enables post-hoc analysis of planning quality: which issue types produce plans that succeed on the first attempt, which require replanning, and which consistently fail. Over time, this data informs template refinement (point 3) and identifies weaknesses in the Planner's system prompt.

### 10. The Planner Never Executes — It Only Plans

The Planner is a pure planning agent. It does not have access to tools that read files, run commands, or interact with GitHub. Its only input is the structured context payload assembled by the orchestrator. Its only output is a validated DAG.

If the Planner needs information that is not in the context payload (e.g., the contents of a specific file), it must express this as a RESEARCH node in the DAG. The Planner cannot gather information itself. This separation ensures the Planner's output is a deterministic function of its input context — given the same context, it should produce the same (or equivalent) plan. It also prevents the Planner from consuming unbounded tokens on exploratory tool use.

Exception: For issues where the Planner cannot determine the correct decomposition without codebase information, it may produce a minimal "discovery DAG" — a single RESEARCH node whose output feeds a second planning invocation. This two-phase planning pattern (scout then plan) is preferred over giving the Planner direct tool access.

## Rationale

The hybrid approach — LLM planning guided by templates, constrained by schemas, validated before execution, and revised on triggers — balances the competing demands of flexibility and predictability. Pure LLM planning is creative but unreliable. Pure template planning is reliable but brittle. The hybrid lets the system handle common issues efficiently (templates) while adapting to novel issues (LLM planning), with validation gates catching errors before they consume execution budget.

Trigger-based replanning avoids the extremes of static plans (which cannot recover from wrong assumptions) and continuous replanning (which is expensive and can oscillate). By replanning only when concrete evidence of plan failure emerges, the system spends its token budget on execution rather than planning.

The separation of the Planner from execution (point 10) is the most important architectural decision. A Planner with tool access becomes an unbounded agent — it can explore the codebase, run tests, and consume arbitrary tokens before producing a plan. By restricting the Planner to the information the orchestrator provides, planning cost is bounded and planning behavior is reproducible. When the Planner truly needs more information, the two-phase discovery pattern makes this explicit and budgetable rather than hidden inside a planning call.

Persisting plans and their outcomes (point 9) creates the feedback loop necessary to improve planning over time. Without this data, planning quality is invisible — you know whether the final PR was correct, but not whether the plan that produced it was efficient, over-decomposed, or revised three times. This data is the foundation for template refinement, prompt tuning, and eventually, learning to plan better from experience.
