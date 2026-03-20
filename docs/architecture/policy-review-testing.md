# Policy Review Testing Architecture

## 1. Problem Statement

The Review composite contains two parallel sub-agents: `Reviewer` (code quality, correctness, test coverage) and `PolicyReviewer` (policy compliance). They are strictly separated — `Reviewer` never receives the policy corpus; `PolicyReviewer` never evaluates code quality. This separation exists because policy document sets can grow large enough to dilute the Reviewer's attention on code signals, and because each agent needs its own independent token budget allocation within the Review composite's internal DAG [P3.2, P3.3].

Testing `PolicyReviewer` requires three things simultaneously:

1. A repo with defined policies
2. A PR with **known** compliance status — so `PolicyReviewer`'s verdict can be asserted
3. A way to confirm it detected the right violation, not a spurious one

The fixture repos used for integration testing (`schema`, `schedule`, `environs`, etc.) are third-party libraries with no policies. Most real-world repos the orchestrator will encounter also lack formal policies, or have them only in a `CLAUDE.md`. This creates a catch-22: `PolicyReviewer` needs policies to review against, but adding real policies to the fixture repos conflates the test of issue resolution with the test of policy enforcement.

The solution is two independent test modes that decompose the problem differently.

---

## 2. Two Test Modes

### Mode 1 — Arbitrary Policy Testing (PolicyReviewer Isolation)

**Question answered:** Given a diff that would pass a standard code quality review, does `PolicyReviewer` correctly catch a synthetic policy violation?

**How it works:**

1. Take any existing fixture where the orchestrator produces a passing PR (correctness confirmed by the standard integration tests).
2. Draft a plausible policy that the PR's diff would violate — the policy is designed *post hoc* around the actual diff content.
3. Inject this policy into the repo's `CLAUDE.md` (in the ephemeral fixture copy — never the upstream).
4. Run only `PolicyReviewer` against the existing diff — not the full `ReviewComposite`, not the `Reviewer`.
5. Assert that `PolicyReviewOutput.approved == False` and that `policy_citations` reference the injected policy.

**What makes a good arbitrary policy:**

The policy must be:
- **Mechanically derivable from the diff** — the violation is visible in the changed lines, not a matter of architectural judgment (e.g., "all new public functions must have docstrings", "no bare `except:` clauses", "no `print()` in library code")
- **Plausible** — the policy should read as something a real project would adopt
- **Singular** — test one policy per fixture, not a policy set. Compound policies produce ambiguous verdicts

**Fixture metadata extension:**

```json
{
  "fixture_id": "schema-and-or-type-annotation",
  "arbitrary_policy_tests": [
    {
      "policy_id": "arb-001",
      "policy_text": "All new public functions must include a Google-style docstring.",
      "violation_location": "schema.py:142",
      "expected_verdict": "rejected",
      "expected_finding_keywords": ["docstring", "schema_or"]
    }
  ]
}
```

**Oracle:** The verdict is correct if:
- `PolicyReviewOutput.approved == False`
- At least one citation references `violation_location` or `expected_finding_keywords`
- `PolicyReviewer` does not flag unrelated lines as policy violations

**What this mode tests:**

- `PolicyReviewer`'s ability to detect a named violation when the policy is present and the diff is visible
- Whether it produces false positives (flags code that complies with the policy)
- Citation quality (`policy_citations` references the correct policy clause, not a hallucinated rule)

**What this mode does not test:**

- Whether the orchestrator *steers toward* compliant implementations — the coding agent never ran
- Whether the policy is discovered and retrieved correctly at scale (Mode 1 policies are injected directly into the prompt, not retrieved from a corpus)
- The `Reviewer` — code quality evaluation is tested separately via `test_review_composite.py`

---

### Mode 2 — Integrated Policy Testing (Steering)

**Question answered:** When a policy is present in the repo before the orchestrator runs, does the orchestrator produce a compliant implementation — even when the obvious implementation violates the policy?

**How it works:**

1. Create a purpose-built fixture repo (minimal codebase, typically 3–6 files) where a specific issue is planted.
2. The issue has two known solutions:
   - **Obvious solution:** the implementation a competent developer would reach for first, which violates the injected policy
   - **Compliant solution:** a non-obvious but correct alternative that satisfies the policy
3. The policy is committed into the fixture repo's `CLAUDE.md` before the orchestrator runs — it is part of the repo's permanent state, not injected at test time.
4. Run the full orchestrator (coding + review) against the issue.
5. Assert that the produced PR uses the compliant solution, not the obvious one.

**Design requirements for Mode 2 fixtures:**

- **The obvious solution must be genuinely obvious.** If a developer reaching for the obvious answer would naturally produce the compliant solution, the test has no signal. The gap between obvious and compliant must be real.
- **The compliant solution must still be correct.** The policy enforces a constraint, not an inferior design. If the compliant solution is clearly worse, the test is adversarial in a way that doesn't model real policy adoption.
- **The policy must be detectable in the diff.** If compliance can only be judged by running the code or tracing deep program semantics, the review step cannot confirm it.

**Example:**

> **Policy (in `CLAUDE.md`):** "This library must not cache results in module-level mutable state. Use an explicit cache object passed as a parameter."
>
> **Issue:** "Add LRU caching to `compute_heavy()` to avoid redundant work on repeated identical inputs."
>
> **Obvious solution:** `@functools.lru_cache` or a module-level `dict` — violates the policy.
>
> **Compliant solution:** Accept a `cache: dict | None = None` parameter and check/populate it inside the function.

**Fixture metadata:**

```json
{
  "fixture_id": "minilib-explicit-cache",
  "complexity": "easy",
  "upstream": null,
  "synthetic_issue": true,
  "policy_under_test": "no module-level mutable state for caching",
  "obvious_violation": "module-level dict or @lru_cache on compute_heavy",
  "compliant_pattern": "explicit cache parameter passed by caller",
  "expected_verdict": "approved",
  "compliance_check": "no module-level dict or lru_cache in diff"
}
```

The `upstream: null` marks this as a purpose-built fixture, not derived from a real repo. These live in a separate catalog (`tests/fixtures/policy/`) to keep them distinct from the general issue-resolution catalog.

**Oracle:**

- `ReviewOutput.approved == True` — the reviewer accepted the PR
- The diff does not contain the obvious violation pattern (checked by a simple grep on the diff)
- `ReviewOutput.findings` is empty or contains no policy references

**What this mode tests:**

- Whether the policy in `CLAUDE.md` actually influences the coding agent's implementation choices
- Whether the reviewer correctly approves a compliant implementation (not just correctly rejects violations)
- The full steering loop: policy present → coding agent reads it → produces compliant code → reviewer confirms

**What this mode does not test:**

- Policy detection in isolation (Mode 1 covers this)
- Large or complex policy corpuses (each fixture tests one policy)

---

### Mode 3 — Corpus Policy Testing (Multi-Policy Reasoning)

**Question answered:** Given a repo with N non-conflicting policies, does `PolicyReviewer` correctly identify which subset applies to a specific PR, correctly check compliance against all applicable policies, and avoid citing policies that don't apply?

**The new challenge:** Modes 1 and 2 each test one policy per fixture. Real repos accumulate policy sets — agent_agent currently has 11 policy documents, each several hundred lines. `PolicyReviewer` cannot simply load the full corpus into context for every review: at scale, this exhausts the token budget allocated to the Review composite and dilutes attention. The agent must first determine which policies are relevant to the diff, then check compliance against those.

This creates two distinct failure modes that Modes 1 and 2 do not test:
- **Omission**: `PolicyReviewer` focuses on one policy and misses a violation of another
- **Over-citation**: `PolicyReviewer` cites a policy that does not apply to the diff, producing a false positive rejection

Mode 3 tests both.

**How it works:**

1. Create a purpose-built fixture repo with a defined policy corpus of N policies (target: 5–8 for Phase 1, 10–12 for Phase 2 at agent_agent scale).
2. The policies are non-conflicting and cover different concerns (e.g., threading model, error handling style, API surface design, state management, naming conventions).
3. Plant an issue whose natural resolution touches exactly M of the N policies (M < N). For each of the M applicable policies, the solution either complies or violates.
4. Run `PolicyReviewer` in isolation against the produced diff.
5. Assert on three dimensions:
   - **Coverage**: all M applicable policies were evaluated (no omissions)
   - **Accuracy**: the verdict per applicable policy is correct (violations caught, compliant areas approved)
   - **Precision**: the N−M non-applicable policies were not cited (no false positives)

**Fixture structure:**

The fixture defines the full policy corpus inline, marks which policies are expected to apply to the planted issue, and specifies expected compliance per applicable policy:

```json
{
  "fixture_id": "multilib-corpus-test-01",
  "complexity": "medium",
  "upstream": null,
  "synthetic_issue": true,
  "issue_title": "Add LRU caching to the transform pipeline",
  "policy_corpus": [
    { "policy_id": "P1", "title": "No module-level mutable state", "file": "policies/state.md" },
    { "policy_id": "P2", "title": "Errors are values, not exceptions", "file": "policies/errors.md" },
    { "policy_id": "P3", "title": "Thread safety is caller's responsibility", "file": "policies/threading.md" },
    { "policy_id": "P4", "title": "All public functions must have type annotations", "file": "policies/types.md" },
    { "policy_id": "P5", "title": "No third-party caching libraries", "file": "policies/dependencies.md" }
  ],
  "expected_applicable_policies": ["P1", "P4", "P5"],
  "expected_violations": ["P1"],
  "expected_compliant": ["P4", "P5"],
  "expected_non_applicable": ["P2", "P3"],
  "expected_verdict": "rejected"
}
```

**Oracle — three-part assertion:**

```python
# 1. Coverage: no applicable policy was missed
cited_policy_ids = {c.policy_id for c in output.policy_citations}
assert expected_applicable_policies <= cited_policy_ids

# 2. Accuracy: violations and compliant findings are correct
for citation in output.policy_citations:
    if citation.policy_id in expected_violations:
        assert citation.is_violation == True
    elif citation.policy_id in expected_compliant:
        assert citation.is_violation == False

# 3. Precision: no non-applicable policy was cited
assert cited_policy_ids.isdisjoint(expected_non_applicable)
```

**What this mode tests:**

- Whether `PolicyReviewer` can navigate a multi-policy corpus and determine relevance per policy
- Whether applicable policies are all evaluated (no omission failures)
- Whether non-applicable policies are not cited (no false positive failures)
- How performance degrades as corpus size grows (Phase 2)

**What this mode does not test:**

- Steering (the coding agent's implementation choices) — Mode 2 covers this
- Policy retrieval from an external corpus / RAG pipeline — corpus is injected as files in the repo, not retrieved dynamically

**Corpus size phases:**

| Phase | Corpus size | PolicyReviewer strategy | Tests what |
|---|---|---|---|
| Phase 1 | 5–8 short policies | All policies fit in context; no retrieval step | Multi-policy reasoning with manageable corpus |
| Phase 2 | 10–15 policies (agent_agent scale) | Requires a relevance pre-filter pass before full review | Retrieval-then-review pipeline under realistic load |

Phase 1 can proceed without RAG infrastructure. Phase 2 requires the two-pass architecture from `agent-policy-compliance.md` (metadata tag match → hybrid retrieval → inject top 2–5 relevant policies) and is post-MVP.

---

## 3. How the Modes Complement Each Other

| Dimension | Mode 1 (Arbitrary) | Mode 2 (Integrated) | Mode 3 (Corpus) |
|---|---|---|---|
| What runs | PolicyReviewer only | Full orchestrator | PolicyReviewer only |
| Policy count | 1 | 1 | 5–12 |
| Policy origin | Post-hoc injection | Committed before run | Corpus in repo, injected as files |
| Fixture repos | Existing real-repo fixtures | Purpose-built synthetic | Purpose-built synthetic |
| Oracle | Detects the one violation | Coding agent avoids violation; reviewer approves | Correct coverage, accuracy, and precision across all N policies |
| Failure signal | "Missed a visible violation" | "Reached for obvious non-compliant solution" | "Missed an applicable policy" or "Cited an inapplicable one" |
| Speed | Fast | Slower (full pipeline) | Medium (no coding agent; larger context) |
| Cost | ~$0.05 | ~$0.50–2.00 | ~$0.10–0.30 (Phase 1); higher (Phase 2) |

Mode 1 isolates detection. Mode 2 tests steering. Mode 3 tests corpus navigation — the ability to correctly scope a review across a multi-policy document set without omissions or false positives.

A `PolicyReviewer` that passes Modes 1 and 2 but fails Mode 3 is one that works correctly with a single policy but loses track of other policies when multiple are present. This is the expected failure mode for a naive implementation that loads the full corpus without a relevance filter — context dilution causes the agent to anchor on the most salient policy and neglect others.

A `PolicyReviewer` that cites non-applicable policies is producing false positives — it is over-applying the corpus to diffs where those policies have no bearing. This is a different failure from a missed violation but equally damaging: it produces incorrect rejections and erodes trust in the review signal.

---

## 4. Policy Design Constraints

### What makes a policy testable

Both modes require policies that are **testable**. A testable policy has:

1. **A derivable violation signal** — the violation is visible in the diff or the file tree, not just in runtime behavior
2. **A clear polarity** — the policy either applies or it does not; no "it depends" boundary cases in the fixture set (boundary cases belong in Mode 1 adversarial tests, not in the regression suite)
3. **Independence** — the policy is not entangled with another policy in the same fixture; one policy per fixture
4. **Plausibility** — the policy should read as something a real project would adopt (not a nonsense rule constructed purely to be testable)

Policies that require deep semantic understanding of control flow, concurrency, or runtime state are not suitable for Mode 2. They may appear in Mode 1 only if the reviewer has access to tools that can evaluate them (e.g., static analysis output passed as context).

### The harder constraint: the policy must actually do work

A policy test only has signal if the policy materially restricts the agent's choices. A policy like "no bare `print()` statements in library code" is mechanically verifiable, but an LLM agent is already unlikely to produce `print()` statements in library code — the policy is correct but inert. The test passes trivially and tells you nothing.

A policy does work when one or both of the following is true:

**A. The obvious or best-practice solution violates it.** The agent's natural instinct, left unconstrained, would produce a non-compliant implementation. The policy forces a less-obvious path. The DAG structure policy in agent_agent is the canonical example: many valid graph structures exist (trees, cyclic graphs, dynamic runtime graphs), the simplest implementations in several frameworks are not DAGs, and the policy resolves this by closing the decision. "Use a DAG" is not best practice in general — it's a deliberate choice that restricts the solution space in a specific way.

**B. Multiple valid solutions exist with genuine tradeoffs, and the policy picks one.** There is no single obviously-correct implementation. Different developers or agents might reasonably choose differently. The policy encodes which choice this codebase has made. A policy that says "caching state must be passed explicitly as a parameter rather than held at module level" is choosing one point on a real design spectrum (explicitness vs. convenience), not declaring a universal best practice.

Without one of these conditions, the policy is a best-practice restatement. Best-practice restatements are not useful policy test fixtures: agents that follow best practices are likely to comply automatically, so the test produces no signal about the PolicyReviewer's detection capability.

### Why existing fixture repos (schema, schedule) are poor policy fixture candidates

The existing general fixture repos (`schema`, `schedule`, `environs`) are small, straightforward Python libraries implementing well-understood concepts. Their implicit policy is "follow Python best practices" — which LLM agents already do. The review composite's `Reviewer` enforces this adequately through code quality evaluation alone.

These repos lack the structural characteristic that makes a policy test meaningful: there is no decision point in their domain where the obvious solution diverges from a deliberate codebase-specific constraint. Injecting a policy into a `schema` fixture would be artificial — the policy would not reflect a real architectural choice for that codebase, and the agent's compliance or non-compliance would be an artifact of the test construction, not a signal about policy-driven design.

Mode 2 integrated policy fixtures should therefore use **purpose-built synthetic repos** or **real repos where the codebase has made a deliberate non-obvious design choice** — repos where there is a genuine tension between the obvious implementation and the codebase's chosen approach that a policy can encode and test.

---

## 5. Fixture Catalogs

Three separate catalogs, kept strictly apart:

| Catalog | Location | Purpose | Parametrizes |
|---|---|---|---|
| General | `tests/fixtures/<slug>.json` | Issue resolution evaluation | `test_issue_resolution.py` |
| Policy — Mode 1 | `tests/fixtures/policy/arbitrary/<slug>.json` | Single-policy detection (PolicyReviewer isolation) | `test_policy_arbitrary.py` |
| Policy — Mode 2 | `tests/fixtures/policy/integrated/<slug>.json` | Single-policy steering (full orchestrator) | `test_policy_integrated.py` |
| Policy — Mode 3 | `tests/fixtures/policy/corpus/<slug>.json` | Multi-policy corpus navigation | `test_policy_corpus.py` |

Policy fixtures follow the same ephemeral GitHub repo lifecycle as general fixtures (`integration-test-fixtures.md`). Mode 2 and 3 fixtures commit their policy files during Step 2 of the fixture lifecycle (before the issue is posted). Mode 3 fixtures commit policy documents as individual files under a `policies/` directory in the repo root, rather than embedding everything in `CLAUDE.md` — this mirrors how real projects with many policies structure their documentation and gives `PolicyReviewer` a realistic file-reading task.

---

## 6. Data Models

`PolicyReviewer` produces a separate `PolicyReviewOutput` — it does not contribute directly to `ReviewOutput`. The `ReviewComposite` merges both outputs after both sub-agents complete.

```python
class PolicyCitation(BaseModel):
    policy_id: str     # identifier matching the policy document (e.g. "P1", "03-agent-type-taxonomy")
    policy_text: str   # the exact clause cited, quoted from the policy document
    location: str      # file:line where the violation occurs in the diff
    finding: str       # description of the specific violation
    is_violation: bool # True if this citation is a violation; False if cited as compliant confirmation

class PolicyReviewOutput(BaseModel):
    type: Literal["policy_review"]
    approved: bool                          # False if any citation has is_violation=True
    policy_citations: list[PolicyCitation]  # one entry per evaluated policy; empty if no policies in repo
    policies_evaluated: list[str]           # policy_ids the reviewer determined were applicable to this diff
    skipped: bool                           # True if no CLAUDE.md or policy docs exist in repo
```

`policy_citations` covers only the policies the reviewer determined were applicable. `policies_evaluated` makes the relevance decision explicit and auditable — it is the set the oracle checks coverage against. A policy not in `policies_evaluated` was determined inapplicable; a policy in `policies_evaluated` must have a corresponding `PolicyCitation`.

`ReviewOutput` gains a `policy_review` field populated by the composite after merging:

```python
class ReviewOutput(BaseModel):
    type: Literal["review"]
    verdict: ReviewVerdict                  # approved / needs_rework / rejected
    summary: str
    findings: list[ReviewFinding]           # from Reviewer only — code quality findings
    downstream_impacts: list[str]
    policy_review: PolicyReviewOutput       # from PolicyReviewer; skipped=True if no policies
    discoveries: list = []
```

The composite derives the final `verdict` as `rejected` if either `Reviewer` or `PolicyReviewer` produces a blocking finding. Keeping the outputs structurally separate means the consolidation planner can distinguish "rejected for code quality reasons" from "rejected for policy reasons" — which matters for deciding what rework is required.

Mode 1 tests operate directly on `PolicyReviewOutput` (invoking `PolicyReviewer` in isolation). Mode 2 tests operate on the full `ReviewOutput.policy_review` field after a complete orchestrator run.

These models do not exist yet. Adding `PolicyReviewOutput` and the `policy_review` field to `ReviewOutput` is a prerequisite for implementing either test mode.

---

## 7. Implementation Sequence

**Phase 1 — Core PolicyReviewer (Modes 1 & 2)**

1. Add `PolicyReviewOutput` and `PolicyCitation` models to `src/agent_agent/models/agent.py`
2. Extend `ReviewOutput` with `policy_review: PolicyReviewOutput`
3. Add `POLICY_REVIEWER` prompt to `prompts.py`; update `REVIEWER` prompt to remove all policy compliance language
4. Implement `PolicyReviewer` sub-agent class (read-only tools; discovers policy files by globbing `CLAUDE.md` and `policies/` in the worktree; skips with `skipped=True` if none found)
5. Update `ReviewComposite` to run `Reviewer` and `PolicyReviewer` in parallel, then merge into final `ReviewOutput`
6. Update `AgentOutput` union type to include `PolicyReviewOutput`
7. Write two Mode 1 component tests using existing `schema` and `schedule` fixtures (`@pytest.mark.sdk`, invokes `PolicyReviewer` directly)
8. Confirm Mode 1 oracle: `approved==False`, correct citation, no false positives
9. Create first Mode 2 synthetic fixture (`blinker`-style weak-ref policy); wire into `fixture_repo` lifecycle
10. Expand Mode 2 to 3–5 fixtures covering: mutation avoidance, type annotation enforcement, error handling style, naming conventions

**Phase 2 — Corpus Navigation (Mode 3)**

11. Design and commit first Mode 3 fixture repo with 5–8 short non-conflicting policies across `policies/` directory
12. Write `test_policy_corpus.py` with the three-part oracle (coverage, accuracy, precision)
13. Validate Phase 1 assertions: `PolicyReviewer` can determine relevance without a retrieval pre-filter when the corpus fits in context
14. Add `policies_evaluated` to oracle checks
15. Measure token usage at corpus sizes 5, 8, 10, 12 — identify the size at which context pressure begins to degrade accuracy
16. Design the two-pass retrieval architecture (policy metadata indexing → relevance pre-filter → targeted injection) per `agent-policy-compliance.md`
17. Implement Phase 2 Mode 3 fixture at agent_agent policy scale (10–12 policies)

---

## 8. Out of Scope

- **Adversarial policy bypass** — red-teaming where an agent is prompted to argue around a policy. This is a security test, not a review capability test.
- **Policy conflict resolution** — two policies that yield contradictory verdicts on the same diff. Requires conflict resolution infrastructure not yet designed.
- **Dynamic policy retrieval (RAG)** — Phase 2 of Mode 3 tests retrieval under realistic corpus size, but the RAG pipeline itself (embedding index, BM25, re-ranking) is validated separately per `agent-policy-compliance.md`. Mode 3 Phase 2 only tests end-to-end correctness of the combined pipeline, not the retrieval components in isolation.
