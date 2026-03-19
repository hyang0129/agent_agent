"""Component tests for CodingComposite — real SDK calls.

These tests require:
- ANTHROPIC_API_KEY or equivalent SDK auth
- A real git repo with a bare remote for push verification

All tests use the @pytest.mark.sdk marker and $1 hard budget cap.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_agent.agents.coding import CodingComposite
from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.models.context import (
    AncestorContext,
    IssueContext,
    NodeContext,
    RepoMetadata,
    SharedContextView,
)
from agent_agent.state import StateStore
from agent_agent.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "env": "test",
        "model": "claude-haiku-4-5-20251001",
        "max_budget_usd": 1.0,
        "git_push_enabled": True,
        "programmer_max_turns": 100,
        "tester_max_turns": 100,
        "debugger_max_turns": 100,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=merged_env,
    )
    return result.stdout.strip()


@pytest.fixture()
def repo_with_remote(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create a git repo with a bare remote and an initial commit.

    Returns (repo_path, bare_remote_path, branch_name).
    """
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }

    # Create bare remote
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "-b", "main", env=env)

    # Create working repo
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main", env=env)
    _git(repo, "config", "user.email", "t@t.com", env=env)
    _git(repo, "config", "user.name", "Test", env=env)

    # Minimal but runnable Python project so the Test Executor can invoke pytest
    # and get a clean result, rather than burning all turns on "no tests found".
    (repo / "CLAUDE.md").write_text("# Test Repo\n")
    (repo / "README.md").write_text(
        "# test-repo\n\nA minimal Python project used in agent_agent component tests.\n"
    )
    (repo / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\npythonpath = [\".\"]\n"
    )
    (repo / "main.py").write_text(
        "def hello(name: str) -> str:\n"
        "    \"\"\"Return a greeting string.\"\"\"\n"
        "    return f\"Hello, {name}!\"\n"
    )
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_main.py").write_text(
        "from main import hello\n\n\n"
        "def test_hello_returns_greeting() -> None:\n"
        "    assert hello(\"World\") == \"Hello, World!\"\n"
    )
    _git(repo, "add", ".", env=env)
    _git(repo, "commit", "-m", "initial commit", env=env)

    # Add bare as remote and push
    _git(repo, "remote", "add", "origin", str(bare), env=env)
    _git(repo, "push", "-u", "origin", "main", env=env)

    # Prune worktrees to prevent stale conflicts
    _git(repo, "worktree", "prune", env=env)

    return repo, bare, "main"


@pytest.fixture()
async def state_store() -> StateStore:
    """In-memory StateStore for component tests."""
    store = StateStore(":memory:")
    await store.init()
    return store


# ---------------------------------------------------------------------------
# Component tests
# ---------------------------------------------------------------------------


@pytest.mark.sdk
class TestCodingCompositeFullCycle:
    """Test 1: Full cycle — Programmer writes code, Test Executor runs tests, returns CodeOutput."""

    async def test_full_cycle_returns_code_output(
        self,
        repo_with_remote: tuple[Path, Path, str],
        state_store: StateStore,
    ) -> None:
        from datetime import datetime, timezone
        from agent_agent.models.dag import DAGRun

        repo, bare, _ = repo_with_remote
        settings = _make_settings(git_push_enabled=False)  # Don't push in basic test
        budget = BudgetManager(dag_run_id="run-1", total_budget_usd=1.0)
        budget.allocate(["code-node"])

        await state_store.create_dag_run(DAGRun(
            id="run-1",
            issue_url="https://github.com/org/repo/issues/1",
            repo_path=str(repo),
            budget_usd=1.0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))

        wt_mgr = WorktreeManager(str(repo.parent / "worktrees"))
        worktree = await wt_mgr.create_coding_worktree(
            repo_path=str(repo),
            dag_run_id="run-12345678",
            node_id="code-node",
            n=1,
        )

        node_context = NodeContext(
            issue=IssueContext(
                url="https://github.com/org/repo/issues/1",
                title="Add a greeting function",
                body="Add a greet(name) function to main.py that returns 'Hello, {name}!'",
            ),
            repo_metadata=RepoMetadata(
                path=str(repo),
                default_branch="main",
                language="python",
                framework=None,
                claude_md="# Test Repo",
            ),
            parent_outputs={},
            ancestor_context=AncestorContext(),
            shared_context_view=SharedContextView(),
        )

        composite = CodingComposite(
            settings=settings,
            state=state_store,
            budget=budget,
            worktree=worktree,
            repo_path=str(repo),
            issue_number="1",
            node_id="code-node",
        )

        result, cost = await composite.execute(
            node_context=node_context,
            dag_run_id="run-1",
            node_id="code-node",
        )

        assert result.type == "code"
        assert result.branch_name == worktree.branch
        assert cost > 0.0

        # Cleanup
        await wt_mgr.remove_worktree(str(repo), worktree.path)


@pytest.mark.sdk
class TestCodingCompositePushVerified:
    """Test 2: After composite exits, branch exists on bare remote."""

    async def test_push_creates_remote_branch(
        self,
        repo_with_remote: tuple[Path, Path, str],
        state_store: StateStore,
    ) -> None:
        repo, bare, _ = repo_with_remote
        settings = _make_settings(git_push_enabled=True)
        budget = BudgetManager(dag_run_id="run-1", total_budget_usd=1.0)
        budget.allocate(["code-node"])

        wt_mgr = WorktreeManager(str(repo.parent / "worktrees"))
        worktree = await wt_mgr.create_coding_worktree(
            repo_path=str(repo),
            dag_run_id="run-12345678",
            node_id="code-node",
            n=1,
        )

        node_context = NodeContext(
            issue=IssueContext(
                url="https://github.com/org/repo/issues/1",
                title="Add a greeting function",
                body="Add a greet(name) function to main.py.",
            ),
            repo_metadata=RepoMetadata(
                path=str(repo),
                default_branch="main",
                language="python",
                framework=None,
                claude_md="# Test Repo",
            ),
            parent_outputs={},
            ancestor_context=AncestorContext(),
            shared_context_view=SharedContextView(),
        )

        composite = CodingComposite(
            settings=settings,
            state=state_store,
            budget=budget,
            worktree=worktree,
            repo_path=str(repo),
            issue_number="1",
            node_id="code-node",
        )

        result, _ = await composite.execute(
            node_context=node_context,
            dag_run_id="run-1",
            node_id="code-node",
        )

        # Verify branch exists on bare remote
        remote_branches = _git(bare, "branch", "--list")
        assert worktree.branch in remote_branches

        # Cleanup
        await wt_mgr.remove_worktree(str(repo), worktree.path)


@pytest.mark.sdk
class TestCodingCompositeSubAgentsPersisted:
    """Test 3: Sub-agent outputs are persisted in state store [P10.5]."""

    async def test_outputs_persisted_in_state(
        self,
        repo_with_remote: tuple[Path, Path, str],
        state_store: StateStore,
    ) -> None:
        repo, bare, _ = repo_with_remote
        settings = _make_settings(git_push_enabled=False)
        budget = BudgetManager(dag_run_id="run-1", total_budget_usd=1.0)
        budget.allocate(["code-node"])

        wt_mgr = WorktreeManager(str(repo.parent / "worktrees"))
        worktree = await wt_mgr.create_coding_worktree(
            repo_path=str(repo),
            dag_run_id="run-12345678",
            node_id="code-node",
            n=1,
        )

        # Need a dag_run in DB for foreign key constraints
        from datetime import datetime, timezone

        from agent_agent.models.dag import DAGRun

        dag_run = DAGRun(
            id="run-1",
            issue_url="https://github.com/org/repo/issues/1",
            repo_path=str(repo),
            budget_usd=1.0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await state_store.create_dag_run(dag_run)

        node_context = NodeContext(
            issue=IssueContext(
                url="https://github.com/org/repo/issues/1",
                title="Add a greeting function",
                body="Add a greet(name) function to main.py.",
            ),
            repo_metadata=RepoMetadata(
                path=str(repo),
                default_branch="main",
                language="python",
                framework=None,
                claude_md="# Test Repo",
            ),
            parent_outputs={},
            ancestor_context=AncestorContext(),
            shared_context_view=SharedContextView(),
        )

        composite = CodingComposite(
            settings=settings,
            state=state_store,
            budget=budget,
            worktree=worktree,
            repo_path=str(repo),
            issue_number="1",
            node_id="code-node",
        )

        await composite.execute(
            node_context=node_context,
            dag_run_id="run-1",
            node_id="code-node",
        )

        # Verify sub-agent outputs were persisted
        entries = await state_store.list_shared_context("run-1")
        assert len(entries) >= 2  # At least programmer + tester
        categories = [e["category"] for e in entries]
        assert all(c == "sub_agent_output" for c in categories)

        # Cleanup
        await wt_mgr.remove_worktree(str(repo), worktree.path)
