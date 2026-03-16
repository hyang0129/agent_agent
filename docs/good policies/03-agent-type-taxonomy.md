# Agent Type Taxonomy

## Problem Statement

A multi-agent system that resolves GitHub issues must decide how many agent types to define, what each type is responsible for, and what capabilities each type receives. Too few types and agents accumulate permissions they do not need, increasing blast radius and making failures harder to diagnose. Too many types and the orchestrator spends more tokens on coordination overhead than on useful work. The taxonomy must balance specialization against coordination cost while covering the full lifecycle of issue resolution.

## Background: State of the Art

### Role Taxonomies in Leading Systems

The following table summarizes how prominent multi-agent coding systems partition roles:

| System | Roles | Notes |
|--------|-------|-------|
| **MetaGPT** (Hong et al., 2023; ICLR 2024 Oral) | Product Manager, Architect, Project Manager, Engineer, QA Engineer | Mirrors a software company org chart. Encodes Standardized Operating Procedures (SOPs) into prompt sequences. Heavy on planning roles (3 of 5 are non-coding). |
| **ChatDev** (Qian et al., 2023) | CEO, CTO, CPO, Programmer, Designer, Tester, Reviewer | Simulates a full company hierarchy. Seven roles across four phases (design, development, testing, documentation). High coordination overhead. |
| **AgentCoder** (Huang et al., 2023) | Programmer, Test Designer, Test Executor | Minimal taxonomy focused on the code-test loop. Test Designer and Test Executor are separated to isolate generation from execution. |
| **MapCoder** (Pramanik et al., 2024; ACL 2024) | Retrieval, Plan, Code, Debug | Four-role pipeline following the human programming cycle. Achieved 93.9% on HumanEval. Debug is a first-class role. |
| **AdaCoder** (2025) | Programming Assistant, Code Evaluator, Debug Specialist, Prompt Engineer | Four roles with adaptive planning. Separates evaluation from debugging. |
| **Agyn** (2026) | Manager, Researcher, Engineer, Reviewer | Four roles. Achieved 72.2% on SWE-bench Verified — a 7.2% gain over the single-agent baseline using the same model, attributed entirely to team structure. |
| **CrewAI** | User-defined roles (researcher, writer, analyst, etc.) | Role-based model with structured task delegation. Emphasizes specialization over generalist flexibility. |
| **AutoGen** (Microsoft) | Flexible conversational agents | Less prescriptive about roles. Agents collaborate through message-passing. A 2025 study showed 43% reduction in debugging time for multi-agent review processes. |
| **SWE-Agent** (Princeton, NeurIPS 2024) | Single agent with Agent-Computer Interface | Monolithic — one agent with broad tool access. 12.29% on SWE-bench. Outperformed by multi-agent systems. |
| **Devin** (Cognition, 2025) | Single agent with planner + executor subsystems | Internally structured but externally monolithic. Strong on well-scoped tasks (4-8 hour junior engineer work). |

### Convergent Patterns

Across these systems, several patterns emerge:

1. **Four roles is a common sweet spot.** MapCoder, AdaCoder, and Agyn all converge on four agent types. MetaGPT and ChatDev use more, but their extra roles (CEO, CTO, CPO, Project Manager) handle planning and coordination that in a DAG-based orchestrator is performed by the planner and DAG engine, not by agents.

2. **Research/retrieval is always present.** Every system with more than two roles has a dedicated information-gathering step before code generation. The terminology varies (Retrieval, Researcher, Research) but the function is the same: understand the problem before attempting a solution.

3. **Test and review are separated from implementation.** No high-performing system has the code-writing agent also validate its own output. The separation is either into explicit test/review roles or into evaluator/executor pairs.

4. **Multi-agent beats single-agent on benchmarks.** Agyn's 72.2% vs. single-agent baselines, AgentCoder's improvements over solo code generation, and MapCoder's benchmark results all support this. The Agyn paper argues that organizational design matters as much as model quality.

### Specialist vs. Generalist Agents

The evidence favors specialization:

- **Agyn** demonstrated that the 7.2-percentage-point improvement over a single-agent baseline came purely from team structure, not from a better model. The same LLM, when organized into manager/researcher/engineer/reviewer roles, significantly outperformed itself as a monolith.
- **SWE-Agent** (single agent, 12.29% on SWE-bench) is outperformed by multi-agent systems like Agyn (72.2%) and Verdent (76.1%), even though the underlying models are comparable.
- **CrewAI vs. AutoGen** comparisons consistently show that CrewAI's structured role specialization produces more predictable and efficient results in production workflows than AutoGen's flexible conversational approach.
- **AgentCoder** showed that separating the programmer from the test designer from the test executor improved code generation quality, because each agent could focus on its single concern without mode-switching.

The mechanism is straightforward: a specialized agent receives a narrower system prompt, fewer tools, and a more focused task description. This reduces the decision space the LLM must navigate, leading to fewer hallucinations and more consistent outputs.

### Principle of Least Privilege for Agent Capabilities

The principle of least privilege — grant only the minimum access required to complete a task — is well-established in infrastructure security (AWS IAM, GitHub fine-grained PATs) and is directly applicable to agent systems:

- **Tool-level enforcement** (Anthropic, OpenAI): Both APIs support passing different tool lists per request. An agent literally cannot call a tool it was not given. This is the strongest form of capability scoping.
- **AWS Well-Architected Generative AI Lens** (2025): Explicitly recommends least-privilege access for agentic workflows, with permissions calculated at runtime based on task scope and context.
- **FINOS AI Governance Framework**: Defines an Agent Authority Least Privilege Framework specifically for autonomous AI systems operating across multiple services.

For a coding agent system, least privilege means: a research agent cannot write files, a coding agent cannot push to git, a test agent cannot modify source code, and a review agent cannot merge PRs. Each type gets exactly the tools it needs and nothing more. This is not just a security measure — it improves agent performance by reducing the action space and preventing the LLM from attempting actions outside its competence.

### Commonly Missing Roles

Reviewing the literature, several roles are underrepresented in current taxonomies:

| Role | Present in | Absent from | Assessment |
|------|-----------|-------------|------------|
| **Debug** | MapCoder, AdaCoder | MetaGPT, ChatDev, Agyn | Useful but often subsumed by test-then-fix loops. A dedicated debug agent adds value for complex failures. |
| **Architect** | MetaGPT (as a role) | Most others | Planning/architecture is better handled by the orchestrator's planner than by an agent, since it requires global context that individual agents lack. |
| **Refactor** | None | All systems | Not a first-class role anywhere. Refactoring is typically part of implementation. For issue resolution, standalone refactoring is rare. |
| **Deploy** | None | All systems | Out of scope for code-generation systems. Deployment is an infrastructure concern, not an agent concern. |
| **Commit/Persist** | Agent Agent (this system) | All others | Unique to systems that separate mutation from persistence. Most systems let the coding agent commit directly. |

## Policy

### 1. The system uses four primary agent types: Research, Code, Test, and Review.

These four types map to the four essential phases of issue resolution: understand the problem, produce a solution, verify the solution, and evaluate the solution for quality. This aligns with the convergent four-role pattern observed in MapCoder, AdaCoder, and Agyn — the highest-performing multi-agent coding systems on public benchmarks.

### 2. A fifth type, Commit, handles persistence as a separate concern.

Per the Maximum Agent Separation policy and the agent-permissions policy (which mandates separating mutation from persistence), the Commit agent is a distinct type that bridges the Code/Test phase and the Review phase. It receives file changes, validates them, and persists them to git. This separation is unique to this system and exists because no agent should both produce changes and make them permanent.

### 3. Each agent type has a single, well-defined responsibility.

| Type | Responsibility | Can | Cannot |
|------|---------------|-----|--------|
| **Research** | Understand the problem. Read code, issues, and documentation. Identify affected files, root causes, and constraints. | Read files, search code, read git history, read GitHub issues/PRs | Write files, run tests, execute code, touch git, comment on PRs |
| **Code** | Produce file changes that resolve the assigned sub-task. | Read files, write files, run Python/pytest for local validation | Any git operation, branch/commit/push, create or comment on PRs |
| **Test** | Execute test suites and validate that changes meet acceptance criteria. | Read files, run pytest and other test commands | Write source files, touch git, comment on PRs |
| **Commit** | Persist validated changes to git. | Read files (to verify diffs), git add/commit/push on assigned branch only | Write/modify source files, run arbitrary commands, create PRs |
| **Review** | Evaluate code quality, correctness, and adherence to standards. | Read files, read diffs, read git history, comment on PRs | Write files, touch git, run tests, merge PRs |

### 4. Agent types are defined by their tool sets, not just their prompts.

System prompts describe the agent's role, but enforcement happens at the tool layer. Each agent type receives only the tools it needs via the Anthropic API's tool parameter. The orchestrator's executor validates every tool call against the agent's permission profile before execution. A research agent that somehow attempts a file write is rejected by the executor, not just discouraged by its prompt.

### 5. No agent type covers architecture, debug, refactor, or deploy as a standalone role.

These are intentionally excluded:

- **Architecture** is the planner's job, not an agent's. The planner has full issue context and codebase understanding; individual agents do not. Delegating architecture to an agent would require giving it global context that violates the principle of minimal context per agent.
- **Debug** is handled by the Code-Test retry loop. When tests fail, the Code agent receives test output as context and iterates. A separate debug agent would duplicate the Code agent's capabilities with marginally different prompting.
- **Refactor** is a sub-case of Code. When an issue requires refactoring, the planner creates a Code sub-task with refactoring instructions. A dedicated refactor agent type would have identical permissions to the Code agent.
- **Deploy** is out of scope. This system produces PRs; humans (or CI/CD pipelines) handle deployment. Adding a deploy agent would require infrastructure permissions that violate least privilege for a code-generation system.

### 6. New agent types require justification against the decomposition checklist.

Before adding a new agent type, it must pass all four checks from the agent-permissions policy:

1. Does the proposed type have a responsibility that no existing type covers?
2. Does it require a materially different tool set than any existing type?
3. Does combining it with an existing type violate least privilege (i.e., the combined type would have tools it does not need for one of its responsibilities)?
4. Does the coordination cost of adding another DAG node outweigh the safety and clarity benefits of separation?

If a proposed type fails checks 1 or 2, it should be a variant prompt on an existing type, not a new type. If it fails check 3, it must be a new type. Check 4 is an empirical judgment call.

### 7. The taxonomy is a ceiling, not a floor.

Not every DAG requires all five types. A documentation-only issue might need only Research and Code. A test-only issue might need only Research and Test. The planner selects which agent types to include based on the issue's requirements. The taxonomy defines the maximum set of available roles, not a mandatory pipeline.

## Rationale

The four-plus-one taxonomy (Research, Code, Test, Review + Commit) is chosen for the following reasons:

**It matches the empirically validated sweet spot.** Systems with four roles (MapCoder, Agyn, AdaCoder) consistently outperform both monolithic agents and heavily subdivided systems. Four roles provide enough specialization to reduce per-agent decision complexity without introducing excessive coordination overhead.

**It maps directly to the software engineering lifecycle for issue resolution.** Every GitHub issue, regardless of complexity, follows the same abstract flow: understand, change, verify, evaluate. The four primary types correspond one-to-one with these phases. The Commit type adds a persistence boundary that no other system implements but that the agent-permissions policy requires.

**It enforces least privilege naturally.** Each type's tool set is a strict subset of the full tool set. No single agent has both read-write file access and git-write access. No single agent can both generate code and approve it. The permission boundaries emerge from the role definitions rather than being bolted on as afterthought restrictions.

**It keeps coordination cost manageable.** Each additional agent type adds at least one DAG node, which means one more Claude API call (tokens, latency, cost). Five types is the practical limit for an MVP that resolves issues within reasonable token budgets. The typical DAG is 4-6 nodes, not 10-15.

**It is extensible without restructuring.** If future evidence shows that a Debug agent or Architect agent improves outcomes, adding a sixth type requires only a new permission profile, a new system prompt, and planner awareness — no changes to the executor, DAG engine, or state store.
