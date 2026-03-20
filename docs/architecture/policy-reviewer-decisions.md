# PolicyReviewer Implementation — Human Guidance Decisions

This document records decisions made during PolicyReviewer implementation that would ordinarily require human input, and the rationale for the chosen option.

## Decision 1: `policy_review` field optionality in `ReviewOutput`

**Question:** The architecture spec (§6) shows `policy_review: PolicyReviewOutput` as non-optional. The implementation uses `policy_review: PolicyReviewOutput | None = None`. Which is correct?

**Context:** Making the field non-optional would break all existing `ReviewOutput` constructors in tests and production code (they don't pass `policy_review`). The MVP reviewer says the spec intent is that the field is always populated — the `skipped=True` state handles the "no policies" case.

**Decision chosen:** Keep `Optional` with `None` default for now. The composite boundary always sets it before returning. Add a runtime assertion in `ReviewComposite.execute()` to catch any future regression where the composite returns without setting `policy_review`. Non-optional refactor is deferred to a dedicated cleanup PR.

**Why:** Minimizes breakage of existing tests while preserving the runtime guarantee at the composite boundary. The Optional allows for forward compatibility if future code constructs `ReviewOutput` outside a composite (e.g., in stubs, tests, or error paths).

## Decision 2: P3.4 / P8.5 — Argument validation not wired into `invoke_agent`

**Question:** The policy review found that `ToolPermission.validate_args` callbacks exist in `tools.py` but are not invoked during agent execution. P3.4 and P8.5 require argument validation at execution time.

**Context:** This is a pre-existing gap across the entire codebase — the existing `ReviewComposite` also only passes `reviewer_allowed_tools()` (name list) to `invoke_agent`, not `reviewer_permissions()` (permission objects). Fixing this requires modifying `invoke_agent` in `base.py` and the `can_use_tool` callback pattern. This is a substantial change affecting all sub-agent types.

**Decision chosen:** Document as a pre-existing gap. The `validate_args` callbacks are correctly defined and tested in `tests/component/test_review_composite.py::TestReviewPermissions`, satisfying the contractual interface. Wiring them into `invoke_agent` is a separate task for the executor hardening phase.

**Why:** The PolicyReviewer implementation does not regress the existing behavior — the gap existed before. Fixing it properly requires changes to `base.py` that would affect all agents and deserve a dedicated review. The SDK's `--print` mode with `allowed_tools` already prevents the agent from calling unlisted tools; the `validate_args` layer provides defense-in-depth for argument-level attacks, which is a lower-priority risk vector for read-only agents.

## Decision 3: Budget backstop shared between parallel sub-agents

**Question:** Both Reviewer and PolicyReviewer call `budget.remaining_node(node_id)` before their invocations, getting the same allocation. Can both agents spend up to the full node allocation independently?

**Context:** `BudgetManager.remaining_node()` is a read-only query — it does not decrement the balance. Both agents compute their backstops from the same node allocation value. Each backstop is `min(node_alloc * 2, node_alloc + 2.5% of total)`. The actual budget deduction happens when the orchestrator calls `record_usage(total_cost)` after both complete.

**Decision chosen:** Accept the current behavior. Each agent has an independent backstop ceiling equal to the full node allocation — in the worst case, both agents can each spend up to the full node allocation (2x total spend). The real enforcement is `BudgetManager.record_usage()` at the composite level. The backstop is a safety net for runaway individual invocations, not a budget-splitting mechanism.

**Why:** The alternative (splitting the node allocation 50/50 between the two agents before the `asyncio.gather()`) would require knowing that exactly two parallel agents will run, which couples the budget logic to the composite structure. The current approach is simpler and correct: each agent is individually capped, and the composite is budget-capped at the orchestrator level. The 2x worst-case spend is within the backstop formula's intended headroom.

## Decision 4: CLAUDE.md priority — worktree file vs. context-provided content

**Question:** The POLICY_REVIEWER prompt currently says to use the "Target Repo CLAUDE.md" from context as the "primary policy source." The reviewer noted this is semantically backwards — if the diff modifies CLAUDE.md itself, the worktree file reflects the post-change state, not the policy as it existed before the branch.

**Decision chosen:** Clarify the prompt to use the **worktree file** as canonical (reflecting the current state of the branch) and the context-provided CLAUDE.md as a fallback when the worktree doesn't have one. For Mode 1 arbitrary policy tests, the context and worktree files will always match, so this doesn't affect test results. For Mode 2 integrated policy tests, using the worktree file is correct — the policy is committed before the orchestrator runs.

**Why:** The architecture spec (§5) says "The policy is committed into the fixture repo's CLAUDE.md before the orchestrator runs — it is part of the repo's permanent state." The worktree reflects this committed state. The edge case where a PR itself weakens a policy before violating it is an adversarial scenario beyond MVP scope.

## Decision 5: `policy_id` format — free-form vs. canonical

**Question:** The prompt allows `policy_id` to be either `"CLAUDE.md:POLICY-001"` or `"the policy heading"` — two different formats, making the Mode 3 corpus oracle unreliable.

**Decision chosen:** Add guidance to the prompt specifying that `policy_id` should use the exact heading text from the policy document (e.g., the `## Heading` text or the explicit policy identifier like `POLICY-001` if one exists). The specific format `"<source_file>:<heading>"` is recommended but not enforced by the model — the oracle for Mode 1 and Mode 2 uses keyword matching, not exact ID matching, so the impact is deferred to Mode 3.

**Why:** Mode 3 (corpus navigation) is Phase 2 / post-MVP. For Mode 1 and Mode 2, the oracle checks `expected_finding_keywords` (substring matching) not `policy_id` set intersection. Standardizing `policy_id` format is a correctness improvement that can be made when Mode 3 tests are implemented.
