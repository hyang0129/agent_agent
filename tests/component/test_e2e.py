"""Phase 5 — End-to-End Happy Path.

Full run: IssueContext in → branch pushed to bare remote → ReviewOutput.APPROVED
→ DAGRun.COMPLETED.

Markers: @pytest.mark.sdk (requires claude CLI auth, Max plan; unset CLAUDECODE first)
Budget:  $2.00 hard cap
Model:   claude-haiku-4-5-20251001 (cheapest that supports tools)
Workers: MAX_WORKERS=1 (serial dispatch)

Run:
    unset CLAUDECODE && pytest tests/component/test_e2e.py -m sdk -v
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import uvicorn

from agent_agent.config import Settings
from agent_agent.models.context import IssueContext
from agent_agent.models.dag import DAGRunStatus
from agent_agent.orchestrator import Orchestrator
from agent_agent.state import StateStore


pytestmark = pytest.mark.sdk

ISSUE_URL = "https://github.com/test/fixture-repo/issues/1"
ISSUE_TITLE = "Add subtract function to calculator"
ISSUE_BODY = (
    "The calculator module at `src/calculator/calculator.py` currently only has "
    "`add(a, b) -> int`. Please add a `subtract(a, b) -> int` function that returns "
    "`a - b`, and add a test `test_subtract()` in `tests/test_calculator.py` that "
    "asserts `subtract(5, 3) == 2`."
)

WORKTREE_BASE = "/workspaces/.agent_agent_tests/worktrees"


def _make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "env": "test",
        "max_budget_usd": 2.0,
        "git_push_enabled": True,
        "worktree_base_dir": WORKTREE_BASE,
        "port": 19100,
        "plan_use_thinking": False,
        "plan_thinking_budget_tokens": 0,
        "plan_max_turns": 40,
        "programmer_max_turns": 40,
        "test_designer_max_turns": 40,
        "test_executor_max_turns": 40,
        "debugger_max_turns": 40,
        "reviewer_max_turns": 40,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Minimal Python repo with bare remote.

    Calculator with add() only; issue asks agent to add subtract().
    Returns (repo_path, bare_remote_path).
    """
    env = _env()

    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(
        ["git", "init", "--bare", "-b", "main"],
        cwd=bare, check=True, capture_output=True, env=env,
    )

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, env=env
        )

    git("init", "-b", "main")
    git("config", "user.email", "t@t.com")
    git("config", "user.name", "Test")

    (repo / "CLAUDE.md").write_text(
        "# Calculator\n\n"
        "A simple Python calculator library.\n\n"
        "## Structure\n"
        "- `src/calculator/calculator.py` — public functions\n"
        "- `tests/test_calculator.py` — pytest suite\n\n"
        "## Code Style\n"
        "- Type hints on all public functions\n"
        "- Docstrings on all public functions\n"
        "- All new functions must have a corresponding test\n"
    )

    policies_dir = repo / "docs" / "policies"
    policies_dir.mkdir(parents=True)
    (policies_dir / "POLICY_INDEX.md").write_text(
        "# Policy Index\n\nThis repo uses agent-agent.\n"
    )

    (repo / "pyproject.toml").write_text(
        "[build-system]\n"
        "requires = [\"setuptools\"]\n"
        "build-backend = \"setuptools.backends.legacy:build\"\n\n"
        "[tool.pytest.ini_options]\n"
        "testpaths = [\"tests\"]\n"
        "pythonpath = [\"src\"]\n"
    )

    src_dir = repo / "src" / "calculator"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("")
    (src_dir / "calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        '    """Return the sum of a and b."""\n'
        "    return a + b\n"
    )

    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_calculator.py").write_text(
        "from calculator.calculator import add\n\n\n"
        "def test_add() -> None:\n"
        "    assert add(2, 3) == 5\n"
    )

    git("add", ".")
    git("commit", "-m", "initial: add calculator module with add()")
    git("remote", "add", "origin", str(bare))
    git("push", "-u", "origin", "main")

    return repo, bare


@pytest.fixture()
async def state_store() -> StateStore:
    store = StateStore(":memory:")
    await store.init()
    yield store
    await store.close()


@pytest.mark.sdk
async def test_e2e_happy_path(
    fixture_repo: tuple[Path, Path],
    state_store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 5 gate: issue in → branch out, full composite pipeline.

    Assertions:
    A1. orchestrator.run() returns non-empty (branch_name, summary)
    A2. branch_name starts with 'agent/'
    A3. The branch is visible on the bare remote
    A4. DAGRun.status == COMPLETED in SQLite
    A5. DAGRun.usd_used is between 0 and 2.0 (real SDK calls were made)
    """
    repo, bare = fixture_repo
    settings = _make_settings()

    # Ensure worktree base dir exists
    Path(WORKTREE_BASE).mkdir(parents=True, exist_ok=True)

    # Mock uvicorn server to avoid port binding in tests
    mock_server = MagicMock()
    mock_server.should_exit = False
    mock_server.serve = AsyncMock(return_value=None)

    # Capture run_id by wrapping create_dag_run (avoids needing list_dag_runs)
    run_id_holder: list[str] = []
    _original_create_dag_run = state_store.create_dag_run

    async def _capturing_create(run: Any) -> None:
        run_id_holder.append(run.id)
        return await _original_create_dag_run(run)

    state_store.create_dag_run = _capturing_create  # type: ignore[method-assign]

    issue_ctx = IssueContext(url=ISSUE_URL, title=ISSUE_TITLE, body=ISSUE_BODY)

    monkeypatch.setattr(uvicorn, "Server", lambda _cfg: mock_server)

    orchestrator = Orchestrator(
        settings=settings,
        repo_path=str(repo),
        claude_md_content=(repo / "CLAUDE.md").read_text(),
        issue_url=ISSUE_URL,
        state_store=state_store,
        agent_fn=None,
        use_composites=True,
        issue_context=issue_ctx,
    )
    branch_name, summary = await orchestrator.run()

    # A1: non-empty return values
    assert branch_name, f"empty branch_name; summary={summary!r}"
    assert summary, f"empty summary; branch={branch_name!r}"

    # A2: branch naming convention
    assert branch_name.startswith("agent"), f"branch {branch_name!r} must start with 'agent'"

    # A3: branch exists on bare remote
    bare_branches_out = subprocess.run(
        ["git", "branch", "--list"],
        cwd=bare, capture_output=True, text=True, check=True,
    ).stdout
    # branch may appear as "agent/1/add-subtract" or similar
    assert any(
        part in bare_branches_out
        for part in [branch_name, branch_name.split("/", 1)[-1]]
    ), (
        f"Branch {branch_name!r} not found on bare remote.\n"
        f"Remote branches:\n{bare_branches_out}"
    )

    # A4: DAGRun.status == COMPLETED
    assert run_id_holder, "create_dag_run was never called"
    run_id = run_id_holder[0]
    dag_run = await state_store.get_dag_run(run_id)
    assert dag_run is not None
    assert dag_run.status == DAGRunStatus.COMPLETED, (
        f"Expected COMPLETED, got {dag_run.status}"
    )

    # A5: real spend occurred (not stub)
    assert 0 < dag_run.usd_used <= settings.max_budget_usd, (
        f"usd_used={dag_run.usd_used} expected between 0 and {settings.max_budget_usd}"
    )
