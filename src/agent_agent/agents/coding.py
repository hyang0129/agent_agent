"""Coding composite — iterative nested DAG of sub-agents.

Internal cycle (max 3 cycles) [P10.4]:
  Programmer -> Test Designer -> Test Executor -> Debugger

Each cycle is a 4-node acyclic DAG persisted before execution [P1.8].
Sub-agent outputs are persisted after each step for resumption [P10.5].
Programmer and Debugger handle own git staging/committing [P10.13].
Push-on-exit is composite-level [P10.13].
"""

from __future__ import annotations

import asyncio
import json
import subprocess

import structlog

from ..budget import BudgetManager
from ..config import Settings
from ..dag.executor import AgentError
from ..models.agent import AgentTestOutput, CodeOutput
from ..models.context import NodeContext
from ..observability import EventType, emit_event
from ..state import StateStore
from ..worktree import WorktreeRecord
from .base import SubAgentConfig, compute_sdk_backstop, invoke_agent
from .prompts import DEBUGGER, PROGRAMMER, TEST_DESIGNER, TEST_EXECUTOR
from .tools import (
    debugger_allowed_tools,
    programmer_allowed_tools,
    test_designer_allowed_tools,
    test_executor_allowed_tools,
)

_logger = structlog.get_logger(__name__)

MAX_CYCLES = 3  # P10.4: max Coding composite cycles


class CodingComposite:
    """Executes a Coding composite node with iterative nested DAG cycles.

    The composite owns the worktree lifecycle (already created by the executor)
    and performs push-on-exit.
    """

    def __init__(
        self,
        settings: Settings,
        state: StateStore,
        budget: BudgetManager,
        worktree: WorktreeRecord,
        repo_path: str,
        issue_number: str,
        node_id: str,
    ) -> None:
        self._settings = settings
        self._state = state
        self._budget = budget
        self._worktree = worktree
        self._repo_path = repo_path
        self._issue_number = issue_number
        self._node_id = node_id  # needed for push failure state update

    async def execute(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
    ) -> tuple[CodeOutput, float]:
        """Run the Coding composite's iterative nested DAG.

        Returns the final (CodeOutput, total_cost_usd) from the composite.
        Push-on-exit is performed regardless of success/failure [P1.11].

        If push succeeds, CodeOutput.branch_name == self._worktree.branch.
        If push fails, CodeOutput.branch_name is None so the executor writes
        None to the state store and the review gate blocks [HG-1].
        """
        total_cost = 0.0
        last_code_output: CodeOutput | None = None
        last_test_output: AgentTestOutput | None = None
        push_succeeded: bool = True

        try:
            for cycle in range(MAX_CYCLES):
                emit_event(
                    EventType.NODE_STARTED,
                    dag_run_id,
                    node_id=node_id,
                    cycle=cycle + 1,
                    max_cycles=MAX_CYCLES,
                )

                # --- Programmer ---
                programmer_output, cost = await self._invoke_programmer(
                    node_context,
                    dag_run_id,
                    node_id,
                    cycle,
                    last_test_output=last_test_output,
                )
                total_cost += cost
                last_code_output = programmer_output
                # Persist sub-agent output for resumption [P10.5]
                await self._persist_sub_agent_output(
                    dag_run_id, node_id, cycle, "programmer", programmer_output
                )

                # --- Test Designer ---
                test_plan_output, cost = await self._invoke_test_designer(
                    node_context,
                    dag_run_id,
                    node_id,
                    cycle,
                    code_output=programmer_output,
                )
                total_cost += cost
                await self._persist_sub_agent_output(
                    dag_run_id, node_id, cycle, "test_designer", test_plan_output
                )

                # --- Test Executor ---
                test_results, cost = await self._invoke_test_executor(
                    node_context,
                    dag_run_id,
                    node_id,
                    cycle,
                    test_plan=test_plan_output,
                )
                total_cost += cost

                # --- Post-Test Executor validation: net-zero source changes [P3.3] ---
                await self._validate_no_source_modifications(dag_run_id, node_id, cycle)

                last_test_output = test_results
                await self._persist_sub_agent_output(
                    dag_run_id, node_id, cycle, "test_executor", test_results
                )

                # Check if tests pass -> done
                if test_results.passed:
                    last_code_output = CodeOutput(
                        summary=programmer_output.summary,
                        files_changed=programmer_output.files_changed,
                        branch_name=self._worktree.branch,
                        commit_sha=programmer_output.commit_sha,
                        tests_passed=True,
                        discoveries=programmer_output.discoveries,
                    )
                    break

                # Tests failed, cycles remain -> invoke Debugger
                if cycle + 1 < MAX_CYCLES:
                    debugger_output, cost = await self._invoke_debugger(
                        node_context,
                        dag_run_id,
                        node_id,
                        cycle,
                        code_output=programmer_output,
                        test_results=test_results,
                    )
                    total_cost += cost
                    last_code_output = debugger_output
                    await self._persist_sub_agent_output(
                        dag_run_id, node_id, cycle, "debugger", debugger_output
                    )

            # If we exhausted cycles without passing, set tests_passed=False
            if last_code_output and not last_code_output.tests_passed:
                last_code_output = CodeOutput(
                    summary=last_code_output.summary,
                    files_changed=last_code_output.files_changed,
                    branch_name=self._worktree.branch,
                    commit_sha=last_code_output.commit_sha,
                    tests_passed=False,
                    discoveries=last_code_output.discoveries,
                )

        finally:
            # Push-on-exit [P1.11/P10.13] — always push regardless of success/failure
            push_succeeded = await self._push_branch()

        if last_code_output is None:
            # Should not happen, but handle gracefully
            last_code_output = CodeOutput(
                summary="Coding composite produced no output",
                files_changed=[],
                branch_name=self._worktree.branch,
                commit_sha=None,
                tests_passed=False,
            )

        # If push failed, null branch_name so the executor writes None to the
        # state store and the review gate blocks [HG-1].
        if not push_succeeded:
            last_code_output = last_code_output.model_copy(update={"branch_name": None})

        return last_code_output, total_cost

    # ------------------------------------------------------------------
    # Sub-agent invocations
    # ------------------------------------------------------------------

    async def _invoke_programmer(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        last_test_output: AgentTestOutput | None,
    ) -> tuple[CodeOutput, float]:
        system_prompt = PROGRAMMER.format(worktree_path=self._worktree.path)

        # On cycle > 0, include previous test failure in context
        augmented_context = self._augment_context(node_context, last_test_output, cycle)

        config = SubAgentConfig(
            name="programmer",
            system_prompt=system_prompt,
            allowed_tools=programmer_allowed_tools(),
            output_model=CodeOutput,
            max_turns=self._settings.programmer_max_turns,
        )

        output, cost = await invoke_agent(
            config=config,
            node_context=augmented_context,
            model=self._settings.model,
            sdk_budget_backstop_usd=compute_sdk_backstop(
                self._budget.remaining_node(node_id),
                self._settings.max_budget_usd,
            ),
            cwd=self._worktree.path,
            dag_run_id=dag_run_id,
            node_id=f"{node_id}-cycle{cycle}-programmer",
        )
        if not isinstance(output, CodeOutput):
            raise AgentError(f"Programmer expected CodeOutput, got {type(output).__name__}")
        return output, cost

    async def _invoke_test_designer(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        code_output: CodeOutput,
    ) -> tuple[AgentTestOutput, float]:
        # Add Programmer's CodeOutput to context
        augmented = self._augment_context_with_output(node_context, "programmer", code_output)

        config = SubAgentConfig(
            name="test_designer",
            system_prompt=TEST_DESIGNER.format(worktree_path=self._worktree.path),
            allowed_tools=test_designer_allowed_tools(),
            output_model=AgentTestOutput,
            max_turns=self._settings.test_designer_max_turns,
        )

        output, cost = await invoke_agent(
            config=config,
            node_context=augmented,
            model=self._settings.model,
            sdk_budget_backstop_usd=compute_sdk_backstop(
                self._budget.remaining_node(node_id),
                self._settings.max_budget_usd,
            ),
            cwd=self._worktree.path,
            dag_run_id=dag_run_id,
            node_id=f"{node_id}-cycle{cycle}-test_designer",
        )
        if not isinstance(output, AgentTestOutput):
            raise AgentError(f"TestDesigner expected AgentTestOutput, got {type(output).__name__}")
        return output, cost

    async def _invoke_test_executor(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        test_plan: AgentTestOutput,
    ) -> tuple[AgentTestOutput, float]:
        system_prompt = TEST_EXECUTOR.format(worktree_path=self._worktree.path)
        augmented = self._augment_context_with_output(node_context, "test_designer", test_plan)

        config = SubAgentConfig(
            name="test_executor",
            system_prompt=system_prompt,
            allowed_tools=test_executor_allowed_tools(),
            output_model=AgentTestOutput,
            max_turns=self._settings.test_executor_max_turns,
        )

        output, cost = await invoke_agent(
            config=config,
            node_context=augmented,
            model=self._settings.model,
            sdk_budget_backstop_usd=compute_sdk_backstop(
                self._budget.remaining_node(node_id),
                self._settings.max_budget_usd,
            ),
            cwd=self._worktree.path,
            dag_run_id=dag_run_id,
            node_id=f"{node_id}-cycle{cycle}-test_executor",
        )
        if not isinstance(output, AgentTestOutput):
            raise AgentError(f"TestExecutor expected AgentTestOutput, got {type(output).__name__}")
        return output, cost

    async def _invoke_debugger(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        code_output: CodeOutput,
        test_results: AgentTestOutput,
    ) -> tuple[CodeOutput, float]:
        system_prompt = DEBUGGER.format(worktree_path=self._worktree.path)
        # Include both CodeOutput and TestOutput in context
        augmented = self._augment_context_with_outputs(
            node_context,
            {"programmer": code_output, "test_executor": test_results},
        )

        config = SubAgentConfig(
            name="debugger",
            system_prompt=system_prompt,
            allowed_tools=debugger_allowed_tools(),
            output_model=CodeOutput,
            max_turns=self._settings.debugger_max_turns,
        )

        output, cost = await invoke_agent(
            config=config,
            node_context=augmented,
            model=self._settings.model,
            sdk_budget_backstop_usd=compute_sdk_backstop(
                self._budget.remaining_node(node_id),
                self._settings.max_budget_usd,
            ),
            cwd=self._worktree.path,
            dag_run_id=dag_run_id,
            node_id=f"{node_id}-cycle{cycle}-debugger",
        )
        if not isinstance(output, CodeOutput):
            raise AgentError(f"Debugger expected CodeOutput, got {type(output).__name__}")
        return output, cost

    # ------------------------------------------------------------------
    # Post-test validation
    # ------------------------------------------------------------------

    async def _validate_no_source_modifications(
        self,
        dag_run_id: str,
        node_id: str,
        cycle: int,
    ) -> None:
        """Validate that the Test Executor did not net-modify tracked source files [P3.3].

        After Test Executor completes, run ``git diff`` in the worktree to check
        if any committed files were modified. If modifications are detected, revert
        them with ``git checkout .`` and raise AgentError.
        """
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "diff", "--name-only", "HEAD"],
            cwd=self._worktree.path,
            capture_output=True,
            text=True,
        )

        modified_files = [f for f in result.stdout.strip().split("\n") if f]
        if modified_files:
            _logger.warning(
                "coding_composite.test_executor_modified_files",
                dag_run_id=dag_run_id,
                node_id=node_id,
                cycle=cycle,
                modified_files=modified_files,
            )
            emit_event(
                EventType.TOOL_DENIED,
                dag_run_id,
                node_id=f"{node_id}-cycle{cycle}-test_executor",
                reason="test_executor_modified_source_files",
                files=modified_files,
            )
            # Revert source file changes to restore Programmer's committed state
            await asyncio.to_thread(
                subprocess.run,
                ["git", "checkout", "."],
                cwd=self._worktree.path,
                check=True,
            )
            raise AgentError(
                f"Test Executor modified tracked source files: {modified_files}. "
                "Changes reverted. Test results may be unreliable."
            )

    # ------------------------------------------------------------------
    # Context augmentation
    # ------------------------------------------------------------------

    def _augment_context(
        self,
        base: NodeContext,
        test_output: AgentTestOutput | None,
        cycle: int,
    ) -> NodeContext:
        """Add previous cycle's test results to parent_outputs for the Programmer."""
        if test_output is None or cycle == 0:
            return base
        augmented_outputs = dict(base.parent_outputs)
        augmented_outputs[f"prev-cycle-{cycle - 1}-test"] = test_output
        return base.model_copy(update={"parent_outputs": augmented_outputs})

    def _augment_context_with_output(
        self,
        base: NodeContext,
        key: str,
        output: CodeOutput | AgentTestOutput,
    ) -> NodeContext:
        augmented_outputs = dict(base.parent_outputs)
        augmented_outputs[key] = output
        return base.model_copy(update={"parent_outputs": augmented_outputs})

    def _augment_context_with_outputs(
        self,
        base: NodeContext,
        outputs: dict[str, CodeOutput | AgentTestOutput],
    ) -> NodeContext:
        augmented_outputs = dict(base.parent_outputs)
        augmented_outputs.update(outputs)
        return base.model_copy(update={"parent_outputs": augmented_outputs})

    # ------------------------------------------------------------------
    # Push and persistence
    # ------------------------------------------------------------------

    async def _push_branch(self) -> bool:
        """Push the worktree's branch to remote [P1.11].

        Push is attempted regardless of success/failure. Returns True on
        success, False on failure. If push fails, sets branch_name=None in
        the state store so the review gate catches it [HG-1].
        """
        if not self._settings.git_push_enabled:
            _logger.info(
                "coding_composite.push_skipped",
                reason="git_push_enabled=False",
                branch=self._worktree.branch,
            )
            return True  # treat skip as success; branch_name stays set

        last_exc: subprocess.CalledProcessError | None = None
        for attempt in range(2):  # 1 attempt + 1 retry [HG-1]
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["git", "push", "-u", "origin", self._worktree.branch],
                    cwd=self._worktree.path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                _logger.info(
                    "coding_composite.push_success",
                    branch=self._worktree.branch,
                    attempt=attempt + 1,
                )
                return True
            except subprocess.CalledProcessError as exc:
                last_exc = exc
                if attempt == 0:
                    _logger.warning(
                        "coding_composite.push_retry",
                        branch=self._worktree.branch,
                        stderr=exc.stderr,
                    )
                    await asyncio.sleep(5)  # wait before retry

        # Both attempts failed — set branch_name = None so review gate catches it [HG-1].
        _logger.error(
            "coding_composite.push_failed",
            branch=self._worktree.branch,
            stderr=last_exc.stderr if last_exc else "unknown",
            attempts=2,
        )
        await self._state.update_dag_node_worktree(
            self._node_id, self._worktree.path, None
        )
        return False

    async def _persist_sub_agent_output(
        self,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        sub_agent: str,
        output: CodeOutput | AgentTestOutput,
        attempt: int = 0,
    ) -> None:
        """Persist sub-agent output for resumption [P10.5].

        Stores as a SharedContext entry keyed by composite_node_id + cycle +
        sub_agent + attempt. The attempt number prevents INSERT conflicts when
        a sub-agent is retried within the same cycle due to transient retry.
        """
        await self._state.append_shared_context(
            entry_id=f"{node_id}-cycle{cycle}-{sub_agent}-attempt{attempt}",
            dag_run_id=dag_run_id,
            source_node_id=node_id,
            category="sub_agent_output",
            data={
                "composite_node_id": node_id,
                "cycle": cycle,
                "sub_agent": sub_agent,
                "output": json.loads(output.model_dump_json()),
            },
        )
