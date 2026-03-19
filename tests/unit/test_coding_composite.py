"""Unit tests for agents/coding.py — CodingComposite iterative nested DAG."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_agent.agents.coding import MAX_CYCLES, CodingComposite
from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.dag.executor import AgentError
from agent_agent.models.agent import AgentTestOutput, AgentTestRole, CodeOutput
from agent_agent.models.context import (
    AncestorContext,
    IssueContext,
    NodeContext,
    RepoMetadata,
    SharedContextView,
)
from agent_agent.worktree import WorktreeRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKTREE_PATH = "/tmp/worktrees/agent-abc12345-code-1"
_BRANCH = "agent-abc12345-code-1"


def _make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "env": "test",
        "model": "claude-haiku-4-5-20251001",
        "max_budget_usd": 5.0,
        "git_push_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_budget(total: float = 5.0, node_ids: list[str] | None = None) -> BudgetManager:
    mgr = BudgetManager(dag_run_id="run-1", total_budget_usd=total)
    mgr.allocate(node_ids or ["code-node"])
    return mgr


def _make_worktree() -> WorktreeRecord:
    return WorktreeRecord(
        path=_WORKTREE_PATH,
        branch=_BRANCH,
        dag_run_id="run-1",
        node_id="code-node",
        readonly=False,
    )


def _make_node_context() -> NodeContext:
    return NodeContext(
        issue=IssueContext(
            url="https://github.com/org/repo/issues/42",
            title="Test issue",
            body="Fix the bug.",
        ),
        repo_metadata=RepoMetadata(
            path="/workspaces/target",
            default_branch="main",
            language="python",
            framework="fastapi",
            claude_md="# CLAUDE.md",
        ),
        parent_outputs={},
        ancestor_context=AncestorContext(),
        shared_context_view=SharedContextView(),
    )


def _make_state_mock() -> AsyncMock:
    state = AsyncMock()
    state.append_shared_context = AsyncMock()
    state.update_dag_node_status = AsyncMock()
    state.update_dag_node_worktree = AsyncMock()
    return state


def _make_code_output(
    summary: str = "implemented changes",
    branch: str = _BRANCH,
    tests_passed: bool | None = None,
) -> CodeOutput:
    return CodeOutput(
        summary=summary,
        files_changed=["src/main.py"],
        branch_name=branch,
        commit_sha="abc123",
        tests_passed=tests_passed,
    )


def _make_test_results(passed: bool = True) -> AgentTestOutput:
    return AgentTestOutput(
        type="test",
        role=AgentTestRole.TESTER,
        summary="Tests passed" if passed else "Tests failed",
        test_plan="Run pytest on the worktree",
        passed=passed,
        total_tests=5,
        failed_tests=0 if passed else 2,
        failure_details=None if passed else "AssertionError in test_foo",
    )


def _make_composite(
    settings: Settings | None = None,
    state: AsyncMock | None = None,
    budget: BudgetManager | None = None,
    worktree: WorktreeRecord | None = None,
) -> CodingComposite:
    return CodingComposite(
        settings=settings or _make_settings(),
        state=state or _make_state_mock(),
        budget=budget or _make_budget(),
        worktree=worktree or _make_worktree(),
        repo_path="/workspaces/target",
        issue_number="42",
        node_id="code-node",
    )


def _clean_subprocess_mock(cmd: list[str], **kwargs: Any) -> MagicMock:
    """Default subprocess.run mock: git diff returns empty (no modifications)."""
    result = MagicMock()
    result.stdout = ""
    result.returncode = 0
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCodingCompositeOneCycle:
    """Test 1: Runs 1 cycle when tests pass on first try."""

    @patch("agent_agent.agents.coding.subprocess.run", side_effect=_clean_subprocess_mock)
    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_single_cycle_on_pass(self, mock_invoke: AsyncMock, _mock_sub: MagicMock) -> None:
        code_out = _make_code_output()
        test_results = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),  # programmer
            (test_results, 0.05),  # tester
        ]

        composite = _make_composite()
        result, total_cost = await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="code-node",
        )

        assert result.tests_passed is True
        assert result.branch_name == _BRANCH
        assert mock_invoke.call_count == 2  # no debugger
        assert total_cost == pytest.approx(0.15)


class TestCodingCompositeMaxCycles:
    """Test 2: Runs up to MAX_CYCLES when tests keep failing."""

    @patch("agent_agent.agents.coding.subprocess.run", side_effect=_clean_subprocess_mock)
    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_exhausts_cycles_on_failure(
        self, mock_invoke: AsyncMock, _mock_sub: MagicMock
    ) -> None:
        code_out = _make_code_output()
        test_fail = _make_test_results(passed=False)
        debugger_out = _make_code_output(summary="debugger fix")

        # 3 cycles: each has Programmer + Tester
        # Cycles 0 and 1 also get Debugger (cycle 2 is last, no debugger)
        side_effects = []
        for cycle in range(MAX_CYCLES):
            side_effects.append((code_out, 0.10))  # programmer
            side_effects.append((test_fail, 0.05))  # tester
            if cycle + 1 < MAX_CYCLES:
                side_effects.append((debugger_out, 0.08))  # debugger

        mock_invoke.side_effect = side_effects

        composite = _make_composite()
        result, total_cost = await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="code-node",
        )

        assert result.tests_passed is False
        # 3 * (programmer + tester) + 2 debugger = 8
        assert mock_invoke.call_count == 8


class TestCodingCompositeSkipsDebuggerLastCycle:
    """Test 3: Skips Debugger on the last cycle."""

    @patch("agent_agent.agents.coding.subprocess.run", side_effect=_clean_subprocess_mock)
    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_no_debugger_on_last_cycle(
        self, mock_invoke: AsyncMock, _mock_sub: MagicMock
    ) -> None:
        code_out = _make_code_output()
        test_fail = _make_test_results(passed=False)
        debugger_out = _make_code_output(summary="debugger fix")

        side_effects = []
        for cycle in range(MAX_CYCLES):
            side_effects.append((code_out, 0.10))
            side_effects.append((test_fail, 0.05))
            if cycle + 1 < MAX_CYCLES:
                side_effects.append((debugger_out, 0.08))

        mock_invoke.side_effect = side_effects

        composite = _make_composite()
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="code-node",
        )

        # Verify the last cycle's calls don't include debugger
        # Last 2 calls should be: programmer, tester (no debugger)
        last_two_configs = [
            call.kwargs["config"].name for call in mock_invoke.call_args_list[-2:]
        ]
        assert last_two_configs == ["programmer", "tester"]


class TestCodingCompositePushOnExit:
    """Test 4: Push-on-exit is called in finally block (even on exception)."""

    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_push_called_on_exception(self, mock_invoke: AsyncMock) -> None:
        mock_invoke.side_effect = AgentError("SDK boom")

        settings = _make_settings(git_push_enabled=True)
        composite = _make_composite(settings=settings)

        # Mock subprocess.run for push (will be called in finally)
        with patch("agent_agent.agents.coding.subprocess.run") as mock_sub:
            with pytest.raises(AgentError, match="SDK boom"):
                await composite.execute(
                    node_context=_make_node_context(),
                    dag_run_id="run-1",
                    node_id="code-node",
                )

            # Push was attempted even though the agent errored
            push_calls = [c for c in mock_sub.call_args_list if "push" in str(c)]
            assert len(push_calls) >= 1


class TestCodingCompositePushSkipped:
    """Test 5: Push is skipped when git_push_enabled=False."""

    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_push_skipped(self, mock_invoke: AsyncMock) -> None:
        code_out = _make_code_output()
        test_results = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),
            (test_results, 0.05),
        ]

        settings = _make_settings(git_push_enabled=False)
        composite = _make_composite(settings=settings)

        with patch("agent_agent.agents.coding.subprocess.run") as mock_sub:
            await composite.execute(
                node_context=_make_node_context(),
                dag_run_id="run-1",
                node_id="code-node",
            )

            # No subprocess calls for push
            push_calls = [c for c in mock_sub.call_args_list if "push" in str(c)]
            assert len(push_calls) == 0


class TestCodingCompositeSubAgentPersistence:
    """Test 6: Sub-agent outputs are persisted after each step."""

    @patch("agent_agent.agents.coding.subprocess.run", side_effect=_clean_subprocess_mock)
    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_outputs_persisted(self, mock_invoke: AsyncMock, _mock_sub: MagicMock) -> None:
        code_out = _make_code_output()
        test_results = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),
            (test_results, 0.05),
        ]

        state = _make_state_mock()
        composite = _make_composite(state=state)

        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="code-node",
        )

        # 2 persist calls: programmer, tester
        assert state.append_shared_context.call_count == 2
        categories = [
            call.kwargs["category"] for call in state.append_shared_context.call_args_list
        ]
        assert all(c == "sub_agent_output" for c in categories)


class TestCodingCompositeContextAugmentation:
    """Test 7: Context augmentation: cycle > 0 includes previous test results."""

    @patch("agent_agent.agents.coding.subprocess.run", side_effect=_clean_subprocess_mock)
    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_cycle_1_includes_prev_test(
        self, mock_invoke: AsyncMock, _mock_sub: MagicMock
    ) -> None:
        code_out = _make_code_output()
        test_fail = _make_test_results(passed=False)
        debugger_out = _make_code_output(summary="debugger fix")
        test_pass = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),    # cycle 0: programmer
            (test_fail, 0.05),   # cycle 0: tester
            (debugger_out, 0.08),  # cycle 0: debugger
            (code_out, 0.10),    # cycle 1: programmer
            (test_pass, 0.05),   # cycle 1: tester
        ]

        composite = _make_composite()
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="code-node",
        )

        # The cycle 1 programmer call (4th call, index 3) should have augmented context
        cycle1_programmer_call = mock_invoke.call_args_list[3]
        ctx = cycle1_programmer_call.kwargs["node_context"]
        assert "prev-cycle-0-test" in ctx.parent_outputs


class TestCodingCompositeProgrammerConfig:
    """Test 8: Programmer config: max_turns from settings, write tools."""

    @patch("agent_agent.agents.coding.subprocess.run", side_effect=_clean_subprocess_mock)
    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_programmer_config(self, mock_invoke: AsyncMock, _mock_sub: MagicMock) -> None:
        code_out = _make_code_output()
        test_results = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),
            (test_results, 0.05),
        ]

        composite = _make_composite()
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="code-node",
        )

        programmer_config = mock_invoke.call_args_list[0].kwargs["config"]
        assert programmer_config.name == "programmer"
        assert programmer_config.max_turns == _make_settings().programmer_max_turns
        assert programmer_config.output_model is CodeOutput

        # Write tools: should include Edit, Write
        assert "Edit" in programmer_config.allowed_tools
        assert "Write" in programmer_config.allowed_tools


class TestCodingCompositeTesterConfig:
    """Test 9: Tester config: max_turns from settings, write tools."""

    @patch("agent_agent.agents.coding.subprocess.run", side_effect=_clean_subprocess_mock)
    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_tester_config(self, mock_invoke: AsyncMock, _mock_sub: MagicMock) -> None:
        code_out = _make_code_output()
        test_results = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),
            (test_results, 0.05),
        ]

        composite = _make_composite()
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="code-node",
        )

        tester_config = mock_invoke.call_args_list[1].kwargs["config"]
        assert tester_config.name == "tester"
        assert tester_config.max_turns == _make_settings().tester_max_turns

        assert "Write" in tester_config.allowed_tools
        assert "Edit" in tester_config.allowed_tools
        assert "Bash" in tester_config.allowed_tools
        assert "Read" in tester_config.allowed_tools


class TestCodingCompositeDebuggerConfig:
    """Test 10: Debugger config: max_turns from settings, write tools."""

    @patch("agent_agent.agents.coding.subprocess.run", side_effect=_clean_subprocess_mock)
    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_debugger_config(self, mock_invoke: AsyncMock, _mock_sub: MagicMock) -> None:
        code_out = _make_code_output()
        test_fail = _make_test_results(passed=False)
        debugger_out = _make_code_output(summary="debugger fix")
        # Second cycle passes
        test_pass = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),    # cycle 0: programmer
            (test_fail, 0.05),   # cycle 0: tester
            (debugger_out, 0.08),  # cycle 0: debugger
            (code_out, 0.10),    # cycle 1: programmer
            (test_pass, 0.05),   # cycle 1: tester
        ]

        composite = _make_composite()
        await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="code-node",
        )

        dbg_config = mock_invoke.call_args_list[2].kwargs["config"]
        assert dbg_config.name == "debugger"
        assert dbg_config.max_turns == _make_settings().debugger_max_turns
        assert dbg_config.output_model is CodeOutput

        assert "Edit" in dbg_config.allowed_tools
        assert "Write" in dbg_config.allowed_tools


class TestCodingCompositeBranchName:
    """Test 11: Branch name in output matches worktree.branch."""

    @patch("agent_agent.agents.coding.subprocess.run", side_effect=_clean_subprocess_mock)
    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_branch_name_matches(self, mock_invoke: AsyncMock, _mock_sub: MagicMock) -> None:
        code_out = _make_code_output(branch="some-other-branch")
        test_results = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),
            (test_results, 0.05),
        ]

        composite = _make_composite()
        result, _ = await composite.execute(
            node_context=_make_node_context(),
            dag_run_id="run-1",
            node_id="code-node",
        )

        # Even though programmer returned "some-other-branch", composite forces worktree.branch
        assert result.branch_name == _BRANCH


class TestCodingCompositePushFailure:
    """Test 12: Push failure — verify composite still returns, push logged, state updated."""

    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_push_failure_handled(self, mock_invoke: AsyncMock) -> None:
        code_out = _make_code_output()
        test_results = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),
            (test_results, 0.05),
        ]

        state = _make_state_mock()
        settings = _make_settings(git_push_enabled=True)
        composite = _make_composite(settings=settings, state=state)

        # Mock subprocess: git diff returns clean, git push raises
        def _push_fail_mock(cmd: list[str], **kwargs: Any) -> MagicMock:
            if "push" in cmd:
                raise subprocess.CalledProcessError(1, "git push", stderr="fatal: remote rejected")
            # git diff and other commands: return clean
            result = MagicMock()
            result.stdout = ""
            result.returncode = 0
            return result

        with patch("agent_agent.agents.coding.subprocess.run", side_effect=_push_fail_mock):
            # Mock asyncio.sleep to avoid 5s wait in tests
            with patch("agent_agent.agents.coding.asyncio.sleep", new_callable=AsyncMock):
                result, _ = await composite.execute(
                    node_context=_make_node_context(),
                    dag_run_id="run-1",
                    node_id="code-node",
                )

        # Composite still returns CodeOutput
        assert result.tests_passed is True

        # Push failure nulls branch_name in the returned output [HG-1]
        assert result.branch_name is None

        # State store was called to null out branch_name on push failure [HG-1]
        state.update_dag_node_worktree.assert_called_once_with(
            "code-node", _WORKTREE_PATH, None
        )


class TestCodingCompositeGitDiffModified:
    """Test 13: Post-test git diff returns modified files -> AgentError + revert."""

    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_git_diff_modified_raises(self, mock_invoke: AsyncMock) -> None:
        code_out = _make_code_output()
        test_results = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),
            (test_results, 0.05),
        ]

        composite = _make_composite()

        def mock_subprocess_run(cmd: list[str], **kwargs: Any) -> MagicMock:
            if "diff" in cmd:
                result = MagicMock()
                result.stdout = "src/main.py\nsrc/utils.py\n"
                result.returncode = 0
                return result
            if "checkout" in cmd:
                if kwargs.get("check"):
                    return MagicMock(returncode=0)
                return MagicMock(returncode=0)
            return MagicMock(returncode=0)

        with patch("agent_agent.agents.coding.subprocess.run", side_effect=mock_subprocess_run):
            with pytest.raises(AgentError, match="modified tracked source files"):
                await composite.execute(
                    node_context=_make_node_context(),
                    dag_run_id="run-1",
                    node_id="code-node",
                )


class TestCodingCompositeGitDiffClean:
    """Test 14: Post-test git diff returns empty -> no error, cycle continues."""

    @patch("agent_agent.agents.coding.invoke_agent", new_callable=AsyncMock)
    async def test_git_diff_clean_continues(self, mock_invoke: AsyncMock) -> None:
        code_out = _make_code_output()
        test_results = _make_test_results(passed=True)

        mock_invoke.side_effect = [
            (code_out, 0.10),
            (test_results, 0.05),
        ]

        composite = _make_composite()

        def mock_subprocess_run(cmd: list[str], **kwargs: Any) -> MagicMock:
            result = MagicMock()
            result.stdout = ""
            result.returncode = 0
            return result

        with patch("agent_agent.agents.coding.subprocess.run", side_effect=mock_subprocess_run):
            result, _ = await composite.execute(
                node_context=_make_node_context(),
                dag_run_id="run-1",
                node_id="code-node",
            )

        assert result.tests_passed is True
