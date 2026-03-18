# Phase 4 Plan — Human Questions (Resolved)

*Extracted from phase-4-plan.md revision pass (2026-03-17)*
*Source reviews: phase-4-technical-review.md, phase-4-policy-review.md*
*All questions resolved by human feedback on 2026-03-17.*

---

## HG-1: Push failure handling (P1.11) — RESOLVED

**Plan section:** Phase 4c — Coding Composite, `_push_branch()` method

**Question:** Should push failure (a) trigger escalation, (b) trigger rerun, or (c) block review gate?

**Human decision:** Option (c) + add a single wait and retry attempt before setting branch_name = None.

**Applied:** `_push_branch()` now attempts push twice with 5s wait between attempts. Both fail → branch_name = None → review gate blocks.

---

## HG-2: 25% SharedContextView cap enforcement location (P5.8) — RESOLVED

**Human decision:** ok (auto-decision stands). Cap enforced in `ContextProvider.build_context()`.

---

## HG-3: max_tokens SDK default behavior (P7.7) — RESOLVED (policy updated)

**Human decision:** Modify policy to remove max_tokens requirement and focus on max_budget_usd.

**Applied:** P7.7 updated to use `max_budget_usd` for enforcement. `max_tokens` is no longer set or constrained. POLICY_INDEX.md updated.

---

## HG-4: Crash recovery reconstruction logic (P10.5) — RESOLVED

**Human decision:** ok (auto-decision stands). Write-only persistence for Phase 4; full reconstruction deferred to Phase 6.

---

## HG-5: Bash path validation fragility — RESOLVED

**Human decision:** Whitelist approach for this phase, further improvements in Phase 6.

**Applied:** `_validate_worktree_path()` now uses a `SAFE_PREFIXES` whitelist instead of regex heuristic. Absolute paths not matching the whitelist are rejected.

---

## HG-6: Read-only Bash blocklist limitations — RESOLVED

**Human decision:** ok (auto-decision stands). Accept blocklist limitations for MVP.

---

## HG-7: SDK backstop budget multiplier — RESOLVED

**Human decision:** Not total_budget * 2. Use: `min(node_allocation * 2, node_allocation + 2.5% of total_budget)`.

**Applied:** `compute_sdk_backstop()` helper added. All composite call sites updated to use proportional backstop.

---

## HG-8: SDK `output_format` contract verification — RESOLVED

**Human decision:** ok (auto-decision stands). Verify during SDK API prerequisite; fall back to system prompt if unsupported.

---

## HG-9: Minimum viable budget per child node — RESOLVED

**Human decision:** ok (auto-decision stands). `MIN_NODE_BUDGET_USD = 0.10` check in `allocate_child()`.

---

## HG-10: $1 budget cap per component test — RESOLVED

**Human decision:** ok (auto-decision stands). Use haiku model; $1 should suffice.

---

## HG-11: Multi-level recursion test coverage — RESOLVED

**Human decision:** Two levels of recursion for Phase 4 testing: plan→code→review→plan→code→review→plan.

**Applied:** Phase 4e component test `test_e2e_phase4.py` now exercises full two-level flow (L0→L1→L2).

---

## HG-12: serialize_node_context parseability — RESOLVED

**Human decision:** ok (auto-decision stands). No explicit parsing test needed for Phase 4.
