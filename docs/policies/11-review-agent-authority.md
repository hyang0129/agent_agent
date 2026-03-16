# Review Agent Authority

## Problem Statement

Agent Agent's review agent can "comment on PR" but its authority is undefined. Can it block a PR from being created? Can it trigger rework by the code agent? Is its output advisory or binding? What standards does it evaluate against? Without answers, the review agent is either toothless (comments nobody acts on) or dangerous (infinite rework loops that burn budget and never converge).

## Background / State of the Art

### Academic Research: Automated Code Review

Microsoft Research's **CodeReviewer** (Li et al., 2022) pre-trained a Transformer encoder-decoder on large-scale code review data to automate review comment generation and code refinement. The model demonstrated that automated review can surface real defects, but its precision is bounded — roughly half of generated comments required human judgment to determine relevance.

A 2024 ICSE study on **Automated Code Review in Practice** (arXiv:2412.18531) deployed a GPT-4-based review bot (built on Qodo PR-Agent) in an industrial setting. Key findings:

- **73.8% of bot comments were resolved** by developers, showing meaningful uptake.
- **26.2% were marked "Won't Fix"** — the bot flagged issues outside the task scope or made incorrect suggestions.
- **PR closure time increased** from 5h52m to 8h20m — developers spent time addressing bot feedback without a net reduction in human review effort.
- **Recursive review was a pain point**: developers reported that after fixing bot-flagged issues, "a new review is generated" with "redundant and unhelpful" subsequent comments.
- The bot operated in **advisory mode only** — it never blocked merges.

The 2024 AIware paper **"AI-Assisted Assessment of Coding Practices in Modern Code Review"** (University of Washington / Google) found that AI review is most effective for mechanical checks (style, naming, simple bugs) and least effective for design-level feedback, where it lacks project context.

### Multi-Agent Systems: MetaGPT and ChatDev

**ChatDev** (OpenBMB, ACL 2024) implements code review as a distinct phase in its waterfall-style agent pipeline. A Reviewer agent and Programmer agent engage in multi-round dialogue. Termination conditions prevent infinite loops: the phase ends after **two consecutive rounds with no code changes** or after a hard cap of **10 communication rounds**. This dual termination (convergence detection + hard limit) is the standard pattern for preventing runaway review cycles.

**MetaGPT** (ICLR 2024) avoids dialogue-based review entirely. Agents communicate through **structured documents** (PRDs, system designs, code artifacts). Review is implicit in the structured output requirements — each agent's output must conform to a schema that downstream agents can consume. Review feedback takes the form of schema validation failures rather than free-text comments.

### AWS Evaluator-Reflect-Refine Pattern

AWS Prescriptive Guidance documents a **generator-evaluator-refiner loop** pattern for agentic AI. The evaluator checks output against a rubric, and if the output falls below a quality threshold, it is sent back to the generator with embedded critique. Termination occurs when: (1) the result meets quality criteria, (2) the result is explicitly approved, or (3) a retry limit is reached. All three conditions must be present to prevent infinite loops.

### Industry Practice: Advisory vs. Blocking Review (2025-2026)

The industry has converged on a spectrum:

- **GitHub Copilot code review**: purely advisory. Comments are suggestions; merge decisions remain with humans or CI configuration.
- **CodeRabbit "agentic pre-merge checks"**: configurable per check — each can either warn (advisory) or block (enforcement). Organizations define which checks are blocking.
- **Qodo / PR-Agent**: advisory by default with optional enforcement rules that can gate merge on specific conditions (e.g., security findings above a severity threshold).
- **Microsoft Build 2025 direction**: platform-owned enforcement where blocking conditions are explicit, role-based, and recorded in audit logs.

The consensus: **start advisory, promote to blocking only for objective, automatable checks** (security vulnerabilities, failing tests, lint violations). Subjective feedback (design quality, naming, architecture) remains advisory because AI reviewers lack sufficient project context to make binding judgments.

### The Infinite Review Loop Problem

The most dangerous failure mode in automated review systems is the **review-rework spiral**: the review agent flags an issue, the code agent fixes it, the fix introduces a new concern, the review agent flags that, and the cycle continues until budget is exhausted. This is exacerbated when:

- The review agent evaluates the **full diff** on each round (re-flagging previously-addressed issues).
- The review rubric includes subjective criteria where "good enough" is undefined.
- There is no convergence detection (the system cannot recognize that quality is no longer improving).
- The review agent and code agent have **misaligned implicit standards** (the reviewer expects patterns the coder was never instructed to produce).

ChatDev's solution (convergence + hard cap) is the minimum viable approach. More sophisticated systems track a **quality score per round** and terminate when the score plateaus or the delta between rounds falls below a threshold.

### Human vs. AI Review: Complementary Roles

Research consistently shows AI and human review catch different classes of issues:

| Category | AI Strength | Human Strength |
|---|---|---|
| Style/formatting | High — deterministic, tireless | Low — tedious, inconsistent |
| Common bug patterns | Moderate — catches known anti-patterns | Moderate — catches novel bugs from domain knowledge |
| Security vulnerabilities | Moderate — flags known CVE patterns, OWASP issues | High — understands threat model, trust boundaries |
| Design/architecture | Low — lacks project context, history | High — understands system constraints, trade-offs |
| Test adequacy | Moderate — can check coverage metrics, missing edge cases | High — knows which behaviors matter most |
| Requirements alignment | Low — cannot verify intent without spec | High — understands the "why" behind the issue |

The implication for agent_agent: the review agent handles the mechanical checks that AI does well. Human review (already enforced by the "agents never merge" policy) handles design, intent, and architectural concerns.

## Policy

### 1. Review Scope

The review agent evaluates code changes against the following categories, in priority order:

1. **Correctness**: Does the code do what the issue requested? Are there obvious logic errors, off-by-one bugs, null/undefined risks, or unhandled error paths?
2. **Security**: Are there hardcoded secrets, SQL injection vectors, path traversal risks, unsafe deserialization, or missing input validation?
3. **Test coverage**: Do tests exist for the new/changed code? Do they cover the primary path and at least one error path?
4. **Style and conventions**: Does the code follow the target repo's established patterns (naming, structure, imports, type hints)?
5. **Scope compliance**: Does the diff only touch files relevant to the issue? Are there unrelated changes?

The review agent does **not** evaluate:

- **Architecture or design decisions** — these require project context that the review agent lacks. Architectural review is a human responsibility at PR merge time.
- **Performance optimization** — unless the code introduces an obvious algorithmic issue (e.g., O(n^2) in a hot path), performance is out of scope.
- **Dependency choices** — whether to use library X vs. Y is a human decision.

### 2. Review Output Format

The review agent produces a structured result, not free-text prose:

```python
class ReviewFinding(BaseModel):
    category: Literal["correctness", "security", "test_coverage", "style", "scope"]
    severity: Literal["blocking", "advisory"]
    file_path: str
    line_range: tuple[int, int] | None
    description: str          # What the issue is
    suggestion: str | None    # How to fix it (optional)

class ReviewResult(BaseModel):
    status: Literal["approve", "request_changes"]
    findings: list[ReviewFinding]
    summary: str              # One-paragraph overall assessment
```

This structure enables the orchestrator to act on findings programmatically rather than parsing natural language.

### 3. Blocking vs. Advisory Findings

Each finding is classified by severity:

- **Blocking** (`severity: "blocking"`): The review agent has identified a defect that, if shipped, would cause incorrect behavior, a security vulnerability, or a clear violation of the issue requirements. Blocking findings trigger rework (see Policy 4).
- **Advisory** (`severity: "advisory"`): The review agent has a suggestion for improvement — better naming, a missing docstring, a style inconsistency, an additional test case. Advisory findings are posted as PR comments but do **not** trigger rework.

**Only `correctness` and `security` findings may be blocking.** Test coverage, style, and scope findings are always advisory. This prevents the review agent from sending code back for cosmetic rework.

### 4. Rework Trigger

When the review agent returns `status: "request_changes"` with one or more blocking findings:

1. The orchestrator creates a **rework node** in the DAG — a new code agent invocation that receives the original task context plus the blocking findings as additional input.
2. The rework node's output goes through a **second review** (same review agent, new invocation).
3. The second review evaluates **only the blocking findings from the first review** — it does not re-review the entire diff from scratch. The review agent receives the original findings and checks whether each was addressed.

This scoped re-review prevents the reviewer from discovering new issues on each round, which is the primary driver of infinite loops.

### 5. Loop Prevention

The following hard limits prevent runaway review-rework cycles:

| Control | Value | Rationale |
|---|---|---|
| **Maximum rework rounds** | 1 | The code agent gets one chance to fix blocking findings. If the second review still has blocking findings, the PR is created anyway with findings posted as comments for human review. |
| **Maximum blocking findings per review** | 5 | If the review agent finds more than 5 blocking issues, the code quality is too low for incremental fixes. The orchestrator marks the code node as `failed` and retries from scratch (subject to the node retry policy). |
| **Review budget cap** | 15% of DAG run token budget | Review + rework combined cannot exceed 15% of the total budget. If the cap is hit, remaining findings are posted as advisory comments. |
| **Convergence detection** | If rework produces identical or larger diff than original | The rework made things worse or no better. Terminate the loop, post findings as comments. |

The single-rework-round limit is deliberately strict. In the ICSE 2024 study, recursive review was the top developer complaint. One rework round captures the high-value fixes (real bugs, security issues). Additional rounds hit diminishing returns and compound the risk of introducing new issues.

### 6. Review Standards

The review agent's system prompt includes a **review rubric** — a concrete checklist derived from the scope in Policy 1. The rubric is parameterized by the target repository's language and conventions:

```
REVIEW RUBRIC:
- [ ] Changes address the stated issue requirements
- [ ] No obvious logic errors (null checks, boundary conditions, error handling)
- [ ] No hardcoded secrets, credentials, or API keys in diff
- [ ] No SQL/command injection vectors in user-facing input paths
- [ ] Input validation present for new external inputs
- [ ] Tests exist for new/changed public functions
- [ ] At least one error-path test exists
- [ ] Code follows existing naming and structural conventions in the repo
- [ ] Diff is scoped to the issue — no unrelated changes
```

The rubric is **not** a scoring system. Each item is binary (pass/fail). The review agent maps failed items to findings with appropriate severity. This prevents the review agent from inventing subjective quality scores that create ambiguous rework triggers.

### 7. Review Agent Cannot Self-Trigger

The review agent **cannot** create new issues, add tasks to the DAG, or modify the DAG structure. It can only:

- Return a structured `ReviewResult` to the orchestrator.
- Post comments on the PR (via `can_comment_pr` permission).

The orchestrator decides whether to act on the review. This prevents the review agent from autonomously expanding scope — a key risk in systems where reviewers can file new issues that trigger new agent runs.

### 8. Human Review Remains Authoritative

The review agent's output supplements but never replaces human review. Per the existing git workflow policy, agents never merge to main. The human reviewer at PR merge time has full authority to:

- Override any review agent finding (blocking or advisory).
- Request additional changes that the review agent did not flag.
- Merge despite unresolved advisory comments.

The review agent's role is to **reduce the burden on human reviewers** by catching mechanical issues before the human sees the PR, not to serve as the final quality gate.

## Rationale

**Why only one rework round?** The research shows diminishing returns after the first round. The ICSE 2024 study found recursive review increased cycle time by 42% without proportional quality improvement. ChatDev's 10-round cap is generous — most value comes from the first fix. A single round catches genuine bugs while preventing the spiral. If one round is insufficient, the code was fundamentally flawed and should be retried from scratch (which the node retry policy already handles).

**Why are only correctness and security findings blocking?** Style and test coverage are important but not urgent — they can be addressed by the human during PR review. Allowing style findings to trigger rework burns budget on cosmetic changes while the human reviewer may have different style preferences anyway. Security and correctness are blocking because their cost of escaping to production is high and they are objective enough for an AI reviewer to judge reliably.

**Why structured output instead of PR comments for the orchestrator?** The orchestrator needs to make programmatic decisions (rework or not, how many findings, what severity). Parsing natural language comments is fragile. The review agent produces structured data for the orchestrator and separately posts human-readable comments on the PR for the human reviewer.

**Why can't the review agent file new issues?** This is the infinite-loop nuclear option. If the review agent can create issues, and issues trigger DAG runs, and DAG runs include review, the system can enter an unbounded expansion loop. The review agent's authority is deliberately bounded to the current PR's scope.

**Why 15% budget cap for review?** Review is a quality gate, not the primary work. If review and rework consume more than 15% of the budget, either the code agent is producing low-quality output (retry it) or the review agent is too aggressive (tune the rubric). The cap forces the system to fail visibly rather than silently burning tokens on review cycles.
