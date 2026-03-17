"""CLI unit tests — no GITHUB_TOKEN required, all GitHub calls mocked.

Tests:
  - self-repo rejection
  - missing CLAUDE.md error
  - missing docs/policies/POLICY_INDEX.md → exit with clear error
    (assert message does NOT mention "bootstrap" or "agent-agent bootstrap")
  - bootstrap stub exit (code=2 + message)
  - port conflict → exit with clear error
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from agent_agent.cli import app

runner = CliRunner()


@pytest.fixture()
def target_repo(tmp_path: Path) -> Path:
    """Create a minimal target repo with CLAUDE.md and policy index."""
    repo = tmp_path / "target"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# Target repo\n")
    (repo / "docs" / "policies").mkdir(parents=True)
    (repo / "docs" / "policies" / "POLICY_INDEX.md").write_text("# Policies\n")
    return repo


# ------------------------------------------------------------------
# bootstrap stub
# ------------------------------------------------------------------


def test_bootstrap_exits_with_code_2() -> None:
    result = runner.invoke(app, ["bootstrap"])
    assert result.exit_code == 2
    assert "bootstrap is not yet implemented" in result.output
    assert "agent-agent --help" in result.output


# ------------------------------------------------------------------
# Self-repo rejection
# ------------------------------------------------------------------


def test_run_rejects_self_repo(tmp_path: Path) -> None:
    """--repo pointing to agent_agent's own tree should be rejected."""
    # The agent_agent package lives under src/agent_agent/
    self_repo = Path(__file__).resolve().parent.parent.parent  # tests -> repo root
    result = runner.invoke(app, ["run", "--issue", "https://github.com/a/b/issues/1",
                                  "--repo", str(self_repo)])
    assert result.exit_code == 1
    assert "agent_agent's own installation directory" in result.output


# ------------------------------------------------------------------
# Missing CLAUDE.md
# ------------------------------------------------------------------


def test_run_rejects_missing_claude_md(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    # No CLAUDE.md created
    (repo / "docs" / "policies").mkdir(parents=True)
    (repo / "docs" / "policies" / "POLICY_INDEX.md").write_text("# Policies\n")

    result = runner.invoke(app, ["run", "--issue", "https://github.com/a/b/issues/1",
                                  "--repo", str(repo)])
    assert result.exit_code == 1
    assert "CLAUDE.md" in result.output


# ------------------------------------------------------------------
# Missing docs/policies/POLICY_INDEX.md
# ------------------------------------------------------------------


def test_run_rejects_missing_policy_index(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# Target\n")
    # No policy index

    result = runner.invoke(app, ["run", "--issue", "https://github.com/a/b/issues/1",
                                  "--repo", str(repo)])
    assert result.exit_code == 1
    assert "POLICY_INDEX.md" in result.output
    # Must NOT mention "bootstrap" or "agent-agent bootstrap" [§2 prohibition]
    assert "bootstrap" not in result.output.lower()


# ------------------------------------------------------------------
# Port conflict
# ------------------------------------------------------------------


def test_run_port_conflict(target_repo: Path) -> None:
    """Should exit with clear error when the port is already bound."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    try:
        with patch.dict(os.environ, {"AGENT_AGENT_PORT": str(port)}):
            # Clear cached settings so new env var takes effect
            from agent_agent.config import get_settings
            get_settings.cache_clear()

            result = runner.invoke(app, ["run", "--issue", "https://github.com/a/b/issues/1",
                                          "--repo", str(target_repo)])
            assert result.exit_code == 1
            assert "already in use" in result.output
    finally:
        sock.close()
        from agent_agent.config import get_settings
        get_settings.cache_clear()


# ------------------------------------------------------------------
# Nonexistent --repo path
# ------------------------------------------------------------------


def test_run_rejects_nonexistent_repo() -> None:
    result = runner.invoke(app, ["run", "--issue", "https://github.com/a/b/issues/1",
                                  "--repo", "/nonexistent/path/abc123"])
    assert result.exit_code == 1
    assert "does not exist" in result.output
