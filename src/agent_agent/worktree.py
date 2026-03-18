"""WorktreeManager — git worktree lifecycle for Coding and Review composites.

Rules per implementation plan:
- Naming: agent-<run-id[:8]>-code-<n>  /  review-<run-id[:8]>-<n>
- All worktrees created under settings.WORKTREE_BASE_DIR
  - dev/prod default: <repo_path>/../.agent_agent_worktrees/
  - test: /workspaces/.agent_agent_tests/worktrees/  (set via env)
- WorktreeManager raises WorktreeMisconfiguredError if base_dir is not provided
- All subprocess git calls wrapped in asyncio.to_thread() — never blocking the
  executor's async dispatch loop
- Review worktrees are read-only at the tool-selection layer (Phase 4), NOT via
  filesystem permissions — WorktreeManager creates both identically
- readonly=True flag stored on WorktreeRecord for Phase 4 use
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

_logger = structlog.get_logger(__name__)


class WorktreeError(Exception):
    """Raised when a git worktree operation fails."""


class WorktreeMisconfiguredError(WorktreeError):
    """Raised when WORKTREE_BASE_DIR is not configured."""


@dataclass
class WorktreeRecord:
    """Represents a live git worktree created for a composite node."""

    path: str
    branch: str
    dag_run_id: str
    node_id: str
    readonly: bool  # True for Review composites; enforced via tool selection in Phase 4


class WorktreeManager:
    """Manages git worktrees for Coding and Review composites.

    All subprocess calls run in asyncio.to_thread() so the executor's event
    loop is never blocked.

    Usage:
        mgr = WorktreeManager(base_dir="/workspaces/.agent_agent_tests/worktrees")
        rec = await mgr.create_coding_worktree(repo_path, dag_run_id, node_id, n=1)
        await mgr.remove_worktree(repo_path, rec.path)
    """

    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)

    async def create_coding_worktree(
        self,
        repo_path: str,
        dag_run_id: str,
        node_id: str,
        n: int,
    ) -> WorktreeRecord:
        """Create a new branch + worktree for a Coding composite."""
        short_id = dag_run_id[:8]
        branch = f"agent-{short_id}-code-{n}"
        worktree_name = f"agent-{short_id}-code-{n}"
        return await self._create_worktree(
            repo_path=repo_path,
            dag_run_id=dag_run_id,
            node_id=node_id,
            branch=branch,
            worktree_name=worktree_name,
            readonly=False,
            checkout_existing=False,
        )

    async def create_review_worktree(
        self,
        repo_path: str,
        dag_run_id: str,
        node_id: str,
        n: int,
        existing_branch: str,
    ) -> WorktreeRecord:
        """Checkout an existing branch into a new worktree for a Review composite.

        Read-only access is enforced by tool selection in Phase 4, not filesystem
        permissions. The worktree itself is created identically to a coding worktree.
        The readonly=True flag on WorktreeRecord signals Phase 4 to restrict tools.
        """
        short_id = dag_run_id[:8]
        worktree_name = f"review-{short_id}-{n}"
        return await self._create_worktree(
            repo_path=repo_path,
            dag_run_id=dag_run_id,
            node_id=node_id,
            branch=existing_branch,
            worktree_name=worktree_name,
            readonly=True,
            checkout_existing=True,
        )

    async def remove_worktree(self, repo_path: str, worktree_path: str) -> None:
        """Remove a git worktree (force-removes even if dirty)."""
        await asyncio.to_thread(
            self._run_git,
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=repo_path,
        )
        _logger.info(
            "worktree.removed",
            worktree_path=worktree_path,
            repo_path=repo_path,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _create_worktree(
        self,
        repo_path: str,
        dag_run_id: str,
        node_id: str,
        branch: str,
        worktree_name: str,
        readonly: bool,
        checkout_existing: bool = False,
    ) -> WorktreeRecord:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = self._base_dir / worktree_name

        if checkout_existing:
            cmd = ["git", "worktree", "add", str(worktree_path), branch]
        else:
            cmd = ["git", "worktree", "add", "-b", branch, str(worktree_path)]

        await asyncio.to_thread(self._run_git, cmd, cwd=repo_path)

        record = WorktreeRecord(
            path=str(worktree_path),
            branch=branch,
            dag_run_id=dag_run_id,
            node_id=node_id,
            readonly=readonly,
        )
        _logger.info(
            "worktree.created",
            path=record.path,
            branch=branch,
            readonly=readonly,
            dag_run_id=dag_run_id,
            node_id=node_id,
        )
        return record

    @staticmethod
    def _run_git(cmd: list[str], cwd: str) -> None:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if result.returncode != 0:
            raise WorktreeError(
                f"git command failed: {' '.join(cmd)}\n"
                f"stderr: {result.stderr.strip()}\n"
                f"stdout: {result.stdout.strip()}"
            )


def from_settings(base_dir: str | None) -> WorktreeManager:
    """Construct a WorktreeManager from settings.WORKTREE_BASE_DIR.

    Raises WorktreeMisconfiguredError if base_dir is None or empty.
    """
    if not base_dir:
        raise WorktreeMisconfiguredError(
            "AGENT_AGENT_WORKTREE_BASE_DIR is not set. "
            "Set it in your .env file or environment before running the executor."
        )
    return WorktreeManager(base_dir)
