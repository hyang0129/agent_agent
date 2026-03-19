"""Orchestrator — owns the full run lifecycle after CLI validation.

Reusable by any caller (CLI, tests, future API). Not CLI-specific.

Lifecycle:
  1. Stores claude_md in SharedContext.repo_metadata.claude_md
  2. Creates DAGRun record in state store
  3. emit_event(DAG_STARTED, dag_run_id, node_id=None) — node_id=None per P11
  4. Binds in-process FastAPI server on settings.PORT (default 8100)
  5. Hands off to DAGExecutor (real composites or stub depending on use_composites)
  6. Opens a PR on GitHub if branch was pushed and dry_run_github=False
  7. Returns (CodeOutput.branch_name, ReviewOutput.summary)
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from datetime import datetime, timezone

import structlog
import uvicorn

from .budget import BudgetManager
from .config import Settings
from .context.provider import ContextProvider
from .context.shared import SharedContext
from .dag.engine import build_stub_dag, build_l0_dag
from .dag.executor import AgentFn, DAGExecutor
from .github.client import GitHubClient, parse_issue_url
from .models.agent import CodeOutput, PlanOutput, ReviewOutput
from .models.context import IssueContext, RepoMetadata
from .models.dag import DAGNode, DAGRun, DAGRunStatus, NodeType
from .observability import EventType, emit_event
from .server import app as fastapi_app
from .server import set_state_store
from .state import StateStore

_logger = structlog.get_logger(__name__)


def _stub_agent_fn() -> AgentFn:
    """Return the Phase 2/3 stub agent function.

    Returns hardcoded outputs for each node type so the executor can run
    end-to-end without a Claude API key.
    """
    from .models.agent import AgentOutput, ReviewVerdict

    async def _stub(node: DAGNode, context: object) -> tuple[AgentOutput, float]:
        if node.type == NodeType.PLAN:
            return PlanOutput(
                investigation_summary="Stub plan: no real analysis performed.",
                child_dag=None,
                discoveries=[],
            ), 0.0
        elif node.type == NodeType.CODING:
            return CodeOutput(
                summary="Stub coding: no real code changes.",
                files_changed=[],
                branch_name=f"agent/0/stub-{node.id[:8]}",
                commit_sha=None,
                tests_passed=True,
                discoveries=[],
            ), 0.0
        elif node.type == NodeType.REVIEW:
            return ReviewOutput(
                verdict=ReviewVerdict.APPROVED,
                summary="Stub review: auto-approved.",
                findings=[],
                downstream_impacts=[],
                discoveries=[],
            ), 0.0
        else:
            raise ValueError(f"Unknown node type: {node.type}")

    return _stub  # type: ignore[return-value]


class Orchestrator:
    """Owns the full run lifecycle: DAGRun → executor → result.

    Constructed with validated config, repo path, CLAUDE.md content, and issue URL.
    """

    def __init__(
        self,
        settings: Settings,
        repo_path: str,
        claude_md_content: str,
        issue_url: str,
        state_store: StateStore,
        *,
        agent_fn: AgentFn | None = None,
        use_composites: bool = False,
        issue_context: IssueContext | None = None,
    ) -> None:
        self._settings = settings
        self._repo_path = repo_path
        self._claude_md = claude_md_content
        self._issue_url = issue_url
        self._state = state_store
        self._issue_context = issue_context
        # use_composites=True: executor uses real SDK composite dispatch (Phase 4).
        # use_composites=False (default): uses agent_fn or stub for backward compat.
        self._use_composites = use_composites
        if use_composites:
            self._agent_fn: AgentFn | None = None
        else:
            self._agent_fn = agent_fn or _stub_agent_fn()

    async def run(self) -> tuple[str, str]:
        """Execute the full run lifecycle.

        Returns (branch_name, summary) from the stub/real agent outputs.
        """
        # 0. Prune orphaned worktrees from prior crashed runs (non-fatal)
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["git", "worktree", "prune"],
                cwd=self._repo_path,
                capture_output=True,
                check=False,
            )
        except Exception:
            _logger.debug("orchestrator.worktree_prune_skipped", repo_path=self._repo_path)

        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # 1. Build SharedContext with repo metadata
        issue_ctx = self._issue_context or IssueContext(url=self._issue_url, title="", body="")
        repo_meta = RepoMetadata(
            path=self._repo_path,
            default_branch="main",
            claude_md=self._claude_md,
        )
        shared_context = SharedContext(issue=issue_ctx, repo_metadata=repo_meta)
        self._shared_context = shared_context

        # 2. Create DAGRun record in state store
        dag_run = DAGRun(
            id=run_id,
            issue_url=self._issue_url,
            repo_path=self._repo_path,
            status=DAGRunStatus.PENDING,
            budget_usd=self._settings.max_budget_usd,
            usd_used=0.0,
            created_at=now,
            updated_at=now,
        )
        await self._state.create_dag_run(dag_run)

        # 3. emit DAG_STARTED — node_id=None per P11
        emit_event(EventType.DAG_STARTED, run_id, node_id=None)

        # 4. Build DAG nodes and persist before execution [P1.7/P1.8]
        if self._use_composites:
            nodes = build_l0_dag(dag_run)
        else:
            nodes = build_stub_dag(dag_run)
        for node in nodes:
            await self._state.create_dag_node(node)

        # 5. Set up budget, context provider, executor
        budget = BudgetManager(dag_run_id=run_id, total_budget_usd=self._settings.max_budget_usd)
        ctx_provider = ContextProvider(
            shared_context=shared_context,
            budget=budget,
            state=self._state,
            settings=self._settings,
        )

        # When using real composites (Phase 4), create WorktreeManager
        # for Coding/Review composite worktree lifecycle.
        worktree_mgr = None
        if self._use_composites:
            from .worktree import WorktreeManager

            if self._settings.worktree_base_dir is None:
                raise ValueError(
                    "worktree_base_dir must be set in settings when use_composites=True"
                )
            worktree_mgr = WorktreeManager(
                base_dir=self._settings.worktree_base_dir,
            )

        executor = DAGExecutor(
            state=self._state,
            budget=budget,
            context_provider=ctx_provider,
            agent_fn=self._agent_fn,
            settings=self._settings,
            worktree_manager=worktree_mgr,
            repo_path=self._repo_path,
            issue_number=self._extract_issue_number(),
        )

        # 6. Inject state store into the FastAPI app for the server endpoint
        set_state_store(self._state)

        # 7. Start in-process FastAPI server
        server = await self._start_server()

        try:
            # 8. Execute the DAG
            await executor.execute(dag_run, nodes)
        finally:
            # 9. Shutdown server
            server.should_exit = True

        # 10. Extract results from completed nodes
        branch_name, summary = await self._extract_results(run_id)

        # 11. Open a PR on GitHub if a branch was pushed and GitHub writes are enabled
        if branch_name and not self._settings.dry_run_github:
            await self._create_pr(branch_name, summary)

        _logger.info(
            "orchestrator.run_complete",
            dag_run_id=run_id,
            branch_name=branch_name,
            summary=summary,
        )

        return branch_name, summary

    async def _start_server(self) -> uvicorn.Server:
        """Start the FastAPI server in-process on a background task."""
        config = uvicorn.Config(
            fastapi_app,
            host="127.0.0.1",
            port=self._settings.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())
        return server

    def _extract_issue_number(self) -> str:
        """Extract issue number from the issue URL."""
        # https://github.com/owner/repo/issues/123 -> "123"
        parts = self._issue_url.rstrip("/").split("/")
        if parts and parts[-1].isdigit():
            return parts[-1]
        return "0"

    async def _create_pr(self, branch_name: str, summary: str) -> str:
        """Open a PR on the GitHub remote for branch_name → default branch.

        Returns the PR URL, or empty string if PR creation fails (non-fatal).
        Guarded by dry_run_github — only called when dry_run_github=False.
        """
        try:
            owner, repo, issue_number = parse_issue_url(self._issue_url)
        except ValueError as exc:
            _logger.warning("orchestrator.pr_skipped_bad_url", error=str(exc))
            return ""

        title = f"Fix #{issue_number}: agent-generated patch"
        body = summary or f"Automated fix for issue #{issue_number}."

        try:
            async with GitHubClient(
                dry_run=self._settings.dry_run_github,
                token=None,  # reads GITHUB_TOKEN from env
            ) as gh:
                pr_url = await gh.create_pull_request(
                    owner=owner,
                    repo=repo,
                    title=title,
                    body=body,
                    head=branch_name,
                    base=self._shared_context.repo_metadata.default_branch,
                )
            _logger.info(
                "orchestrator.pr_created",
                pr_url=pr_url,
                branch=branch_name,
                owner=owner,
                repo=repo,
            )
            return pr_url
        except Exception as exc:
            _logger.error(
                "orchestrator.pr_creation_failed",
                error=str(exc),
                branch=branch_name,
                owner=owner,
                repo=repo,
            )
            return ""

    async def _extract_results(self, run_id: str) -> tuple[str, str]:
        """Extract branch_name from CodeOutput and summary from ReviewOutput.

        Queries ALL nodes from the state store (not just pre-built ones) so that
        dynamically-spawned child DAG nodes (built by _spawn_child_dag) are included.

        branch_name: taken from the last CodeOutput with a non-empty branch_name.
        summary: taken from the last ReviewOutput — always, regardless of whether
            summary is empty (so callers see the actual agent output, not a stale "").
        """
        branch_name = ""
        summary = ""

        all_nodes = await self._state.list_dag_nodes(run_id)
        for node in all_nodes:
            result = await self._state.get_node_result(node.id)
            if result is None:
                continue
            if isinstance(result.output, CodeOutput) and result.output.branch_name:
                branch_name = result.output.branch_name
            if isinstance(result.output, ReviewOutput):
                # Always update summary from the last ReviewOutput seen so callers
                # receive the actual agent output rather than a stale empty string.
                # A genuinely empty summary will surface as "" (agent quality issue),
                # not silently masked by the old truthiness guard.
                summary = result.output.summary

        return branch_name, summary
