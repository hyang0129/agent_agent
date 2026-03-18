# Phase 4 Technical Review

*Reviewed: phase-4-plan.md*
*Reviewer: Agent 2 — Technical Reviewer*
*Date: 2026-03-17*

---

## Internal Consistency

1. **[FIX]** `invoke_agent()` returns `tuple[AgentOutput, float]` but `PlanComposite.execute()` asserts `isinstance(output, PlanOutput)` and annotates its return as `tuple[PlanOutput, float]`. The `AgentOutput` discriminated union uses `Field(discriminator="type")` where `PlanOutput.type` is `Literal["plan"]`. Since `invoke_agent()` parses via `config.output_model.model_validate(raw_output)`, it will return a `PlanOutput` instance, but the static return type is `AgentOutput`. The `assert isinstance` calls are correct at runtime but the `invoke_agent()` return annotation should remain `tuple[AgentOutput, float]` — the composites narrow via runtime assertion. This is fine, but the plan never explicitly states this contract. A coding agent may attempt to change the return type to match the composite's annotation.
   **Resolution:** Add a comment in `invoke_agent()` docstring: "Returns AgentOutput (union); callers narrow via isinstance/assert."

2. **[FIX]** `_run_with_transient_retry()` currently receives `(dag_run, node, all_nodes, reruns_used)` but the plan says to add `dag_run` and `all_nodes` parameters so it can call `_dispatch_composite()`. However, `_run_with_transient_retry()` already receives `dag_run` as a parameter (line 341 of executor.py). The plan's Phase 4e section says "This requires passing `dag_run` and `all_nodes` through to `_run_with_transient_retry`. Update the method signatures accordingly." — but `dag_run` is already there. Only `all_nodes` needs to be added. A coding agent following the plan literally may introduce a duplicate parameter or break the existing signature.
   **Resolution:** Explicitly state: "`dag_run` is already a parameter of `_run_with_transient_retry`; only `all_nodes` needs to be added."

3. **[FIX]** `BudgetManager.allocate()` raises `RuntimeError("Budget already allocated for this run.")` if `self._allocations` is non-empty. The plan introduces `allocate_child()` which appends to `self._allocations` without clearing it. After Phase 4e's `_spawn_child_dag()` calls `allocate_child()`, the original allocation keys remain in `_allocations`. This is correct behavior — child nodes get additional allocations. However, if `allocate()` is ever called again (e.g., in a test), it will raise because `_allocations` is non-empty due to `allocate_child()` entries. This is not a bug per se, but the guard in `allocate()` assumes it is the only allocation method. If future phases reuse `allocate()` for child DAGs, it will break.
   **Resolution:** Document that `allocate()` is one-shot for root nodes only; `allocate_child()` is for child DAGs. The guard is intentional.

4. **[FIX]** `_build_child_dag_nodes()` mutates `node.parent_node_ids` in-place when applying sequential edges (line 1724: `node.parent_node_ids.append(from_review_id)`). `DAGNode` is a Pydantic `BaseModel`, so `parent_node_ids` is a `list[str]` field. Mutating it in-place after construction works but is fragile and violates the spirit of immutable models. More critically, the same `DAGNode` objects are later persisted to the state store — the mutation happens before `create_dag_node()` is called, so it is persisted correctly. But if `DAGNode` ever uses `frozen=True` (a Pydantic v2 option), this will fail silently or raise.
   **Resolution:** Build parent lists fully before constructing `DAGNode` instances, or use `model_copy(update=...)` for sequential edge wiring.

5. **[FIX]** The plan's `serialize_node_context()` references `entry.summarized` (line 229: `"(summarized)" if entry.summarized else "(full)"`). The actual `AncestorEntry` model in `models/context.py` has `summarized: bool = False`, which matches. However, the plan's data-models.md definition shows `output: AgentOutput | str` without a `summarized` field. The implementation diverges from the spec document. A coding agent consulting data-models.md will not find `summarized`.
   **Resolution:** Note in the plan that `AncestorEntry` has a `summarized` field in the implementation (`models/context.py`) that is not in data-models.md. Either update data-models.md or reference the source file as canonical.

6. **[FIX]** The plan's `_dispatch_review()` fetches the coding node's branch from the state store via `db_node = await self._state.get_dag_node(coding_node.id)` and asserts `db_node.branch_name is not None`. But this is for the review worktree branch checkout. Meanwhile, the coding node's `branch_name` in the state store is set in two places: (a) `update_dag_node_status()` after the executor processes `CodeOutput` (executor.py line ~273), and (b) the new `update_dag_node_worktree()` method called at worktree creation. The branch set at worktree creation is the *worktree branch* (e.g., `agent-{short_id}-code-{n}`), while the branch in `CodeOutput.branch_name` could potentially differ (the agent sets it). The review worktree should check out the branch that was actually pushed — which is `self._worktree.branch` from the coding worktree, not `CodeOutput.branch_name`. The plan uses `db_node.branch_name`, which was set by `update_dag_node_status()` from `CodeOutput.branch_name`. This could be inconsistent if the SDK agent outputs a different branch name than the one assigned at worktree creation.
   **Resolution:** The review worktree checkout should use the branch name set by `update_dag_node_worktree()` (the worktree's actual branch), not the one from `CodeOutput`. In `CodingComposite.execute()`, the final `CodeOutput` already forces `branch_name=self._worktree.branch`, so they will match in practice. Document this invariant explicitly: "The CodeOutput.branch_name must always equal the worktree's assigned branch."

7. **[RISK]** The plan introduces `_persist_sub_agent_output()` which calls `self._state.append_shared_context()` to store sub-agent outputs for resumption. The `append_shared_context()` method stores data with an `entry_id` that is `{node_id}-cycle{cycle}-{sub_agent}`. If a sub-agent is retried (e.g., Programmer fails, is re-invoked in the same cycle due to transient retry), the same `entry_id` would be used for the second attempt, causing a SQLite `INSERT` conflict. The existing `append_shared_context` uses `INSERT` (not `INSERT OR REPLACE`).
   **Resolution:** Either use `INSERT OR REPLACE` in `append_shared_context` for sub-agent outputs, or include the attempt number in the `entry_id`.

---

## Best Practice Deviation

8. **[RISK]** The `can_use_tool` callback defined in `invoke_agent()` is synchronous (`Callable[[str, dict], bool]`), but the plan's step 2 defines it as `async def can_use_tool(...)`. The SDK's `ClaudeAgentOptions.can_use_tool` signature determines which is correct. The plan declares `async def` but the `ToolPermission.validate_args` field is `Callable[[str, dict[str, Any]], bool]` (synchronous). If the SDK expects an async callback, the synchronous `validate_args` call inside it works fine. But if the SDK expects a sync callback, the `async def` will not work. The plan assumes async but does not verify the SDK's actual contract.
   **Resolution:** Before implementation, verify whether `ClaudeAgentOptions.can_use_tool` expects `Callable` or `Awaitable[Callable]`. The plan should explicitly state which.

9. **[RISK]** The `_validate_worktree_path()` function uses regex `r'/[\w/.-]+'` to detect absolute paths in Bash commands. This is extremely fragile: it will fail on paths with spaces, will match partial strings inside longer paths, and will not detect relative path escapes (`cd ../../../etc/passwd`). The plan acknowledges this with "Phase 6 adds more sophisticated parsing," but the current heuristic will produce false positives (rejecting valid commands) and false negatives (allowing path traversal). During component testing with real SDK agents, Programmer agents will frequently hit this validator on legitimate commands like `git status`, `python -m pytest`, etc., which may contain system paths.
   **Resolution:** Consider a whitelist approach for Phase 4 instead: allow `cwd`-relative paths and a set of known-safe absolute prefixes. Document the known false-positive and false-negative risks for the Phase 5 e2e test.

10. **[RISK]** The `_validate_read_only_bash()` function uses substring matching against a blocklist (`"git commit" in cmd`, `"> " in cmd`, etc.). This will reject commands like `echo "this file was > expected"` or `git log --format="%H"` (if other patterns match substrings). More dangerously, it will not catch writes via Python one-liners (`python -c "open('foo','w').write('bar')"`), piped redirects with file descriptors (`cmd 1>file`), or `tee` variants.
    **Resolution:** Accept the limitations for Phase 4 MVP but document them. The Plan and Test Designer sub-agents are low-risk (no worktree mutation consequences). The real guard is that the Reviewer's worktree is not the source of truth — the Coding composite's pushed branch is.

11. **[RISK]** The plan uses `assert isinstance(output, PlanOutput)` / `assert isinstance(output, CodeOutput)` in composite `execute()` methods. In production, Python assertions can be disabled with `-O` flag (`PYTHONOPTIMIZE`). If the system is ever run with optimizations, these assertions become no-ops.
    **Resolution:** Replace `assert isinstance(...)` with explicit `if not isinstance(...): raise AgentError(...)` or use a type narrowing helper that always runs.

12. **[RISK]** Every sub-agent invocation within `CodingComposite` uses `sdk_budget_backstop_usd=self._settings.max_budget_usd * 2`. This means each of the 4 sub-agents in each of up to 3 cycles (12 invocations total) gets a backstop of `2x` the entire DAG budget. While the backstop is described as a "never expected to trigger" safety net, having 12 invocations each allowed to spend `2x` the total budget means the theoretical maximum spend is `24x` the budget before the backstop triggers. If a single sub-agent runs away, it can consume `2x` the total budget before being stopped.
    **Resolution:** Consider setting the backstop per sub-agent to something proportional to the node's allocated budget rather than the total. E.g., `budget.remaining_node(node_id) * 3` or `total_budget_usd * 0.5` per sub-agent. The architecture spec says "SDK's `max_budget_usd` is set to `total_budget_usd * 2` per invocation" so this matches the spec, but the spec may need revisiting. Flag for Phase 6 hardening at minimum.

13. **[RISK]** The `output_format` in `invoke_agent()` is built as `{"type": "json", "schema": config.output_model.model_json_schema()}`. The Claude Code Agent SDK's `output_format` field may not accept a raw JSON Schema object — it may require the schema in a specific format or wrapper. The plan does not reference SDK documentation for this field's expected shape.
    **Resolution:** Verify the exact SDK `output_format` contract before implementation. If the SDK does not support `output_format`, structured output will need to be enforced via system prompt instructions and post-hoc parsing.

---

## Deferral Assessment

14. **[DEFER-OK]** Full policy context in system prompts is deferred to Phase 6. The MVP prompts contain role and output format instructions but not policy text. This is reasonable — the agents have enough context from `NodeContext` (which includes `CLAUDE.md` content) to operate.

15. **[DEFER-OK]** Stage-aware budget pause (continue Review composites at 5% remaining) is deferred. The flat halt is sufficient for Phase 4 testing and the Phase 5 e2e happy path.

16. **[DEFER-OK]** Conflict detection in SharedContext (last-write-wins for MVP) is reasonable. Real conflicts are unlikely in single-worker MVP runs.

17. **[DEFER-BAD]** The `can_use_tool` rejection is logged as `TOOL_DENIED` but not escalated as a Safety Violation. The plan says: "Phase 4 logs the denial; the SDK prevents the call from executing. If the agent repeatedly attempts denied tools, it will exhaust its iteration cap (Resource Exhaustion)." This means a safety violation (agent attempting unauthorized actions) is misclassified as Resource Exhaustion. The escalation record will have the wrong severity (HIGH instead of CRITICAL) and the wrong trigger. The Phase 5 e2e test will pass because it is a happy path, but any real-world safety violation during Phase 5 testing will be misreported. This should be fixed before Phase 5 because escalation reporting is part of the e2e test observability.
    **Resolution:** In `can_use_tool`, track denial count per agent invocation. If denials exceed a threshold (e.g., 3), raise `SafetyViolationError` explicitly rather than waiting for iteration cap exhaustion. This can be a simple counter in the `invoke_agent` closure.

18. **[DEFER-BAD]** The plan does not implement worktree cleanup on executor failure. If the executor crashes (e.g., unhandled exception in `_spawn_child_dag()`), worktrees created by `_dispatch_coding()` and `_dispatch_review()` are cleaned up via `finally` blocks in those methods. But if the executor itself crashes between creating a worktree and entering the `try` block, or if the process is killed, orphaned worktrees accumulate under `WORKTREE_BASE_DIR`. The Phase 6 deferred item says "Startup scan: remove orphaned worktrees from prior crashed runs." However, during Phase 4 and Phase 5 development, frequent crashes will leave orphaned worktrees that interfere with subsequent test runs (git worktree name conflicts).
    **Resolution:** Add a `cleanup_orphaned_worktrees()` call at the start of `Orchestrator.run()` or in the test fixtures. This does not need to be the full Phase 6 implementation — a simple `git worktree prune` in the target repo before each run would suffice.

---

## Missing Error Paths

19. **[FIX]** `invoke_agent()` step 6 collects `result_message` by iterating through all messages and keeping the last one. If `query()` yields zero messages (e.g., SDK error returns empty iterator), `result_message` remains `None`, and `result_message.total_cost_usd` on line 164 will raise `AttributeError`. The plan does not handle the empty-iterator case.
    **Resolution:** After the async for loop, check `if result_message is None: raise AgentError("SDK returned no messages")`.

20. **[FIX]** `invoke_agent()` step 7 calls `json.loads(result_message.content)`. If `result_message.content` is not valid JSON (e.g., the SDK returns a text message instead of structured output), this raises `json.JSONDecodeError`. The plan maps Pydantic validation failure to `AgentError` but does not mention JSON parsing failure. A `JSONDecodeError` would fall through to the generic `except Exception` handler and be classified as `UNKNOWN`, not `AgentError`.
    **Resolution:** Wrap the `json.loads()` call in a try/except that catches `json.JSONDecodeError` and raises `AgentError("Failed to parse SDK output as JSON: ...")`.

21. **[FIX]** `_push_branch()` silently swallows push failures (logs error, does not raise). This means the Review composite will be dispatched against a branch that does not exist on the remote. The `_can_dispatch()` review gate checks `branch_name is not None` in the state store, but `branch_name` is set by `update_dag_node_status()` from the `CodeOutput`, not by the push succeeding. A push failure will leave `branch_name` set but the branch absent from the remote. The review worktree creation (`git worktree add ... existing_branch`) will fail because the branch exists locally (in the coding worktree) but the review worktree tries to check it out — git may or may not allow this depending on whether the coding worktree still exists at that point.
    **Resolution:** Two options: (a) Make push failure set `branch_name = None` in the state store so the review gate catches it, or (b) make push failure raise an exception that the executor classifies as `AgentError`, triggering a rerun. Option (a) is simpler and sufficient for MVP.

22. **[RISK]** `_dispatch_coding()` creates a worktree, then enters a `try/finally` that removes it. But `CodingComposite.execute()` also has a `finally` block that calls `_push_branch()`. The sequence is: `_push_branch()` runs (in `CodingComposite.execute`'s finally), then `remove_worktree()` runs (in `_dispatch_coding`'s finally). If `_push_branch()` is slow or hangs, the worktree is not removed until it completes. If `remove_worktree()` runs before the push finishes (which cannot happen due to `await` ordering, but consider cancellation), the push would fail because the worktree is gone.
    **Resolution:** The current ordering is correct (push before remove), but document the dependency explicitly. Consider adding a timeout to `_push_branch()` to prevent indefinite hangs.

23. **[RISK]** `_spawn_child_dag()` calls `self._budget.allocate_child()` with child node IDs, but `allocate_child()` uses `self.remaining_dag()` to compute the per-node allocation. If the parent Plan node consumed significant budget (e.g., extended reasoning), the remaining budget may be very small. With 5+ child nodes plus a terminal Plan, each node might get `< $0.10` of budget — potentially insufficient for a real SDK invocation. There is no check for minimum viable budget per node.
    **Resolution:** Add a minimum budget check in `allocate_child()`. If `per_node < MIN_NODE_BUDGET_USD` (e.g., $0.10), raise `ResourceExhaustionError` rather than spawning underfunded nodes.

24. **[RISK]** The plan does not specify what happens when `validate_child_dag_spec()` raises `ValueError` inside `_spawn_child_dag()`. The `ValueError` is not in the executor's exception hierarchy (`TransientError`, `AgentError`, etc.) — it would be caught by the generic `except Exception` handler in `_run_with_transient_retry()` and classified as `UNKNOWN`. But `_spawn_child_dag()` is called *after* `_dispatch_node()` returns `True` (success path, line ~303 of executor.py). It is called inside the success branch, not inside the transient retry loop. An exception here would propagate up to `execute()` unhandled.
    **Resolution:** Wrap the `validate_child_dag_spec()` call with a try/except that raises `AgentError` on `ValueError` (the Plan agent produced an invalid spec). Alternatively, move validation into the Plan composite's `execute()` method so it is covered by the retry/rerun logic.

---

## Test Coverage Gaps

25. **[FIX]** No unit test verifies the error mapping table (SDK exceptions to P10.7 failure taxonomy). The plan specifies a detailed mapping (rate limit -> TransientError, auth failure -> DeterministicError, etc.) but the unit tests for Phase 4a (`test_agents_base.py`) do not include tests for these mappings. The component tests (`test_sdk_wrapper.py`) test `max_turns` exhaustion but not rate limits, auth failures, or other error paths.
    **Resolution:** Add unit tests to `test_agents_base.py` that mock SDK exceptions and verify the correct executor exception type is raised. At minimum: rate limit -> TransientError, auth failure -> DeterministicError, empty result -> AgentError.

26. **[FIX]** No test covers the case where `CodingComposite._push_branch()` fails. Unit test 4 ("Push-on-exit is called in finally block (even on exception)") verifies the push is *called* but not what happens when the push *fails*. Since push failure is silently swallowed, the downstream Review composite will attempt to check out a branch that may not exist on the remote.
    **Resolution:** Add a unit test: mock `subprocess.run` to raise `CalledProcessError` during push; verify the composite still returns `CodeOutput` and the push failure is logged.

27. **[RISK]** The component tests all use `$1 hard budget cap` per test. With real SDK calls (especially extended reasoning for the Plan composite), $1 may be insufficient for a meaningful test, particularly if the model is `sonnet` in production. The test suite may be flaky due to budget exhaustion.
    **Resolution:** Use the `haiku` model for component tests (already the default in `AGENT_AGENT_ENV=test` per the config table) and document the expected cost range per test. Consider parameterizing the budget per test based on observed costs.

28. **[RISK]** No test covers the child DAG recursion path with more than one level of nesting. The Phase 4e component test says "when Review returns needs_rework, terminal Plan spawns child DAG" — but this only tests one level of recursion (L0 -> L1 -> L2). The 4-level nesting cap is tested via a unit test (test 6: "level > 4 raises ResourceExhaustionError"), but the actual recursive execution through multiple levels is not tested.
    **Resolution:** Accept this gap for Phase 4 but ensure the Phase 5 e2e test exercises at least one iteration of rework (L0 -> L1 -> L2). Add a note in the Phase 5 plan.

29. **[RISK]** No test verifies that `serialize_node_context()` output is parseable by the SDK. The serialization format is a custom markdown-like string. If the format causes the SDK agent to misinterpret the context (e.g., treating JSON blocks as instructions), the agent may produce incorrect output. This is an integration concern, not a unit test concern, but the component tests do not explicitly verify context parsing quality.
    **Resolution:** Accept for Phase 4. The component tests will implicitly verify this (if the agent produces valid output, the context was parsed correctly enough).

---

## Feasibility

30. **[FIX]** The plan references `claude-agent-sdk` as the package name but the actual Claude Code SDK package may have a different name (e.g., `claude-code-sdk` or `anthropic-agent-sdk`). The import path `from claude_agent_sdk import ClaudeAgentOptions, query` and the class names are speculative. If the actual SDK has different exports, every file in Phase 4a will need modification.
    **Resolution:** Before starting Phase 4a implementation, verify the actual SDK package name, import paths, class names, and method signatures. Pin the verified information at the top of the plan. The entire plan hinges on the SDK API being as described.

31. **[FIX]** The plan assumes the SDK's `query()` returns an `AsyncIterator[Message]` where the last message is a `ResultMessage` with `total_cost_usd`. If the SDK API is different (e.g., `query()` returns a `Result` object directly, or cost is accessed differently), the entire `invoke_agent()` implementation needs to change. This is the single highest-risk assumption in the plan.
    **Resolution:** Same as item 30. Verify SDK API before implementation.

32. **[RISK]** Phase 4e modifies `executor.py` extensively. The existing `DAGExecutor.__init__` signature changes (adding `worktree_manager`, `repo_path`, `issue_number` keyword arguments), and `_run_with_transient_retry()` needs an additional `all_nodes` parameter. The plan says "Update the method signatures accordingly" without showing the complete new signatures. A coding agent will need to trace all call sites — there are at least 3 callers of `_run_with_transient_retry()` (within `_dispatch_node()`) and multiple tests that construct `DAGExecutor`. The plan lists "Backward compat: executor with agent_fn still works" as test 11, but does not show how backward compatibility is maintained when the signature changes.
    **Resolution:** Show the complete updated `__init__`, `_dispatch_node()`, and `_run_with_transient_retry()` signatures. Explicitly note that all existing test call sites that construct `DAGExecutor` must be updated with the new optional kwargs (defaulting to `None`).

33. **[RISK]** The plan introduces `_persist_sub_agent_output()` which calls `self._state.append_shared_context()` with a `category="sub_agent_output"`. The existing `shared_context` table has columns `(id, dag_run_id, source_node_id, category, data, timestamp)`. The `category` column does not have a schema constraint, so `"sub_agent_output"` will be accepted. However, the `ContextProvider._build_shared_view()` reads from `SharedContext` (the in-memory Pydantic model), not from the `shared_context` table. Sub-agent outputs persisted to the DB via `append_shared_context()` are never loaded back into the `SharedContext` model. This means the persistence is write-only — it enables forensic investigation but not actual resumption.
    **Resolution:** Document that sub-agent output persistence in Phase 4 is for audit/debugging only, not functional resumption. Actual resumption (re-reading persisted sub-agent outputs and continuing from the last completed step) is a Phase 6 concern. If the plan intends resumption to work in Phase 4, a read path must also be implemented.

34. **[RISK]** The `_build_child_dag_nodes()` method constructs child `DAGNode` objects with `child_node_ids` set (e.g., coding nodes point to review nodes). However, when new nodes are created during `_spawn_child_dag()`, the parent Plan node's `child_node_ids` is not updated to include the new child nodes. The Plan node that triggered the spawn still has `child_node_ids=[]` (terminal Plan in the stub DAG) or the original value. This could cause issues for any code that traverses the DAG by following `child_node_ids` from the parent.
    **Resolution:** Either update the parent Plan node's `child_node_ids` to include the first child-level nodes, or document that `child_node_ids` is not authoritative for cross-level edges (only `parent_node_ids` is traversed by the executor).

---

## Summary

**Critical issues requiring fix before implementation (FIX): 12**
- Items 1, 2, 4, 5, 6, 7, 19, 20, 21, 24, 25, 30, 31

**Risks likely to cause problems during implementation (RISK): 12**
- Items 8, 9, 10, 11, 12, 13, 22, 23, 27, 28, 29, 32, 33, 34

**Reasonable deferrals (DEFER-OK): 3**
- Items 14, 15, 16

**Deferrals that should be addressed now (DEFER-BAD): 2**
- Items 17, 18

**Highest-priority items for the implementing agent:**
1. Verify SDK package name and API contract (items 30, 31) — blocks all of Phase 4a
2. Fix `result_message is None` handling (item 19) — will crash on first SDK error
3. Fix JSON parse error handling (item 20) — will misclassify structured output failures
4. Fix push failure propagation to review gate (item 21) — will cause cascading failures in Phase 4e integration
5. Fix child DAG spec validation error path (item 24) — will crash the executor on invalid Plan output
6. Add worktree prune to test fixtures (item 18) — will block development iteration
