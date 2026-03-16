"""DAG executor — dispatch loop, NodeContext assembly, failure classification.

Failure classification [P10.7]:
  Transient           retry with backoff, up to MAX_TRANSIENT_ATTEMPTS total attempts;
                      retries do NOT consume a rerun slot; no escalation if eventually success
  AgentError/Unknown  re-invoke once with failure context; max 1 rerun per node [P10.9]
  ResourceExhaustion  escalate immediately; no retry
  Deterministic       escalate immediately; no retry
  SafetyViolation     escalate immediately; CRITICAL severity; no retry

Budget/pause rules [P7]:
  After each node: drain BudgetManager events → flush to StateStore → increment_usd_used
  Then: check should_pause() → if True, set DAGRunStatus.PAUSED; all pending nodes
  remain NodeStatus.PENDING (never SKIPPED); no further nodes are dispatched.

Review dispatch gate:
  Before dispatching a Review node, the executor checks that the paired Coding
  node has branch_name set in the state store.  The stub CodeOutput always
  sets a fake branch name; the check is against the persisted state.

Child DAG recursion (Phase 4):
  If the terminal Plan output has a non-null child_dag, raise NotImplementedError.

P11 Source-of-Truth rule:
  SQLite writes are fatal (surface + halt on failure).
  emit_event failures are non-fatal (logged to stderr, never block execution).
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from enum import Enum

import structlog

from ..budget import BudgetManager
from ..config import Settings
from ..context.provider import ContextProvider
from ..context.shared import append_discoveries
from ..models.agent import AgentOutput, CodeOutput, PlanOutput
from ..models.budget import BudgetEventType
from ..models.context import NodeContext
from ..models.dag import (
    DAGNode,
    DAGRun,
    DAGRunStatus,
    ExecutionMeta,
    NodeResult,
    NodeStatus,
    NodeType,
)
from ..models.escalation import EscalationRecord, EscalationSeverity, EscalationStatus
from ..observability import EventType, emit_event
from ..state import StateStore
from .engine import topological_sort

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Retry constants
# ---------------------------------------------------------------------------
MAX_TRANSIENT_ATTEMPTS = 3   # 3 total attempts (2 retries) for transient errors
MAX_RERUNS = 1               # max re-invocations for AgentError / Unknown [P10.9]


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

class FailureCategory(str, Enum):
    TRANSIENT = "transient"
    AGENT_ERROR = "agent_error"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    DETERMINISTIC = "deterministic"
    SAFETY_VIOLATION = "safety_violation"
    UNKNOWN = "unknown"


class TransientError(Exception):
    """Retry with backoff; up to MAX_TRANSIENT_ATTEMPTS total; no rerun slot consumed."""


class AgentError(Exception):
    """Re-invoke once with full failure context; max MAX_RERUNS per node [P10.9]."""


class ResourceExhaustionError(Exception):
    """Escalate immediately; no retry [P10.9]."""


class DeterministicError(Exception):
    """Escalate immediately; no retry [P10.9]."""


class SafetyViolationError(Exception):
    """Escalate immediately; CRITICAL severity; no retry [P10.9]."""


def classify_failure(exc: Exception) -> FailureCategory:
    if isinstance(exc, TransientError):
        return FailureCategory.TRANSIENT
    if isinstance(exc, AgentError):
        return FailureCategory.AGENT_ERROR
    if isinstance(exc, ResourceExhaustionError):
        return FailureCategory.RESOURCE_EXHAUSTION
    if isinstance(exc, DeterministicError):
        return FailureCategory.DETERMINISTIC
    if isinstance(exc, SafetyViolationError):
        return FailureCategory.SAFETY_VIOLATION
    return FailureCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Agent function type
# ---------------------------------------------------------------------------

# In Phase 2: stubs. In Phase 4: wraps invoke_agent() from agents/base.py.
# Returns (AgentOutput, cost_usd). cost_usd drives BudgetManager.record_usage().
AgentFn = Callable[[DAGNode, NodeContext], Awaitable[tuple[AgentOutput, float]]]


# ---------------------------------------------------------------------------
# DAGExecutor
# ---------------------------------------------------------------------------

class DAGExecutor:
    """Orchestrates node dispatch for a single DAG run.

    Constructed once per run.  Call execute() to run to completion (or pause/fail).

    The caller is responsible for:
      - Constructing a StateStore (initialised)
      - Constructing a BudgetManager (not yet allocated)
      - Constructing a ContextProvider (with SharedContext pre-populated with
        stub issue / repo_metadata in Phase 2; real data from Phase 3 onwards)
      - Providing an agent_fn (stub in Phase 2; real composites in Phase 4)
    """

    def __init__(
        self,
        state: StateStore,
        budget: BudgetManager,
        context_provider: ContextProvider,
        agent_fn: AgentFn,
        settings: Settings,
    ) -> None:
        self._state = state
        self._budget = budget
        self._ctx = context_provider
        self._agent_fn = agent_fn
        self._settings = settings

    async def execute(self, dag_run: DAGRun, nodes: list[DAGNode]) -> None:
        """Execute a DAG run to completion, pause, or failure.

        All nodes must already be persisted to dag_nodes before this is called [P1.8].
        """
        # Allocate budget equally across all nodes (MVP equal-split)
        self._budget.allocate([n.id for n in nodes])

        ordered = topological_sort(nodes)

        emit_event(EventType.DAG_STARTED, dag_run.id, node_id=None)
        await self._state.update_dag_run_status(dag_run.id, DAGRunStatus.RUNNING.value)

        for node in ordered:
            # Re-read run status — may have been set to PAUSED / FAILED by a prior iteration
            current_run = await self._state.get_dag_run(dag_run.id)
            if current_run and current_run.status in (
                DAGRunStatus.PAUSED,
                DAGRunStatus.FAILED,
                DAGRunStatus.ESCALATED,
            ):
                break

            # Review gate: the paired Coding node must have branch_name set
            if not await self._can_dispatch(node, nodes):
                await self._state.update_dag_node_status(node.id, NodeStatus.FAILED.value)
                await self._state.update_dag_run_status(
                    dag_run.id,
                    DAGRunStatus.FAILED.value,
                    error=f"Review gate blocked: coding branch not ready for node {node.id}",
                )
                emit_event(
                    EventType.DAG_FAILED,
                    dag_run.id,
                    node_id=node.id,
                    reason="review_gate_blocked",
                )
                return

            success = await self._dispatch_node(dag_run, node, nodes)
            if not success:
                return  # DAG failed/escalated; status already updated

            # Drain budget events → flush to StateStore → increment usd_used
            await self._drain_and_flush(dag_run.id)

            # Pause check — runs immediately after drain [P7]
            if self._budget.should_pause():
                self._budget.record_pause()
                await self._drain_and_flush(dag_run.id)
                await self._state.update_dag_run_status(dag_run.id, DAGRunStatus.PAUSED.value)
                emit_event(
                    EventType.DAG_PAUSED,
                    dag_run.id,
                    node_id=node.id,
                    remaining_usd=self._budget.remaining_dag(),
                )
                return

        # If we exited the loop normally, mark completed (unless already paused/failed)
        current_run = await self._state.get_dag_run(dag_run.id)
        if current_run and current_run.status == DAGRunStatus.RUNNING:
            await self._state.update_dag_run_status(dag_run.id, DAGRunStatus.COMPLETED.value)
            emit_event(EventType.DAG_COMPLETED, dag_run.id, node_id=None)

    # ------------------------------------------------------------------
    # Review gate
    # ------------------------------------------------------------------

    async def _can_dispatch(self, node: DAGNode, all_nodes: list[DAGNode]) -> bool:
        """Return False if a Review node's Coding dependency has no branch_name."""
        if node.type != NodeType.REVIEW:
            return True

        node_map = {n.id: n for n in all_nodes}
        for parent_id in node.parent_node_ids:
            parent = node_map.get(parent_id)
            if parent is not None and parent.type == NodeType.CODING:
                db_node = await self._state.get_dag_node(parent_id)
                if db_node is None or db_node.branch_name is None:
                    _logger.warning(
                        "executor.review_gate_blocked",
                        node_id=node.id,
                        coding_node_id=parent_id,
                    )
                    return False
        return True

    # ------------------------------------------------------------------
    # Node dispatch with retry / rerun logic
    # ------------------------------------------------------------------

    async def _dispatch_node(
        self, dag_run: DAGRun, node: DAGNode, all_nodes: list[DAGNode]
    ) -> bool:
        """Dispatch a single node with transient-retry and rerun logic.

        Returns True on success, False if the node failed and the DAG should stop.
        """
        emit_event(EventType.NODE_STARTED, dag_run.id, node_id=node.id)
        await self._state.update_dag_node_status(node.id, NodeStatus.RUNNING.value)

        reruns_used = 0

        while True:
            result, exc, category = await self._run_with_transient_retry(
                dag_run, node, all_nodes, reruns_used
            )

            if result is not None:
                # --- Success path ---
                await self._state.save_node_result(result)

                # Persist branch_name to dag_node if CodeOutput
                branch_name: str | None = None
                if isinstance(result.output, CodeOutput) and result.output.branch_name:
                    branch_name = result.output.branch_name

                await self._state.update_dag_node_status(
                    node.id,
                    NodeStatus.COMPLETED.value,
                    branch_name=branch_name,
                )

                # Record cost against budget
                if result.meta.cost_usd > 0.0:
                    self._budget.record_usage(node.id, result.meta.cost_usd)

                emit_event(
                    EventType.NODE_COMPLETED,
                    dag_run.id,
                    node_id=node.id,
                    cost_usd=result.meta.cost_usd,
                    attempt=result.meta.attempt_number,
                )

                # Write discoveries to SharedContext
                discoveries = getattr(result.output, "discoveries", [])
                if discoveries:
                    await append_discoveries(
                        discoveries,
                        source_node_id=node.id,
                        dag_run_id=dag_run.id,
                        shared_context=self._ctx.shared_context,
                        state=self._state,
                    )

                # Phase 4 wiring point: if terminal Plan has child_dag → recurse
                if isinstance(result.output, PlanOutput) and result.output.child_dag is not None:
                    raise NotImplementedError(
                        "Child DAG spawning is not implemented in Phase 2. "
                        "Wire recursive dispatch in Phase 4."
                    )

                return True

            # --- Failure path ---
            assert exc is not None and category is not None

            # No-retry categories → escalate immediately
            if category in (
                FailureCategory.SAFETY_VIOLATION,
                FailureCategory.RESOURCE_EXHAUSTION,
                FailureCategory.DETERMINISTIC,
            ):
                await self._escalate(dag_run, node, exc, category)
                return False

            # AgentError / Unknown: check rerun budget [P10.9]
            if reruns_used >= MAX_RERUNS:
                # Rerun cap exhausted — escalate; do not attempt again
                await self._escalate(dag_run, node, exc, category)
                return False

            # Rerun is available — loop back for second invocation
            reruns_used += 1
            _logger.info(
                "executor.node_rerun",
                dag_run_id=dag_run.id,
                node_id=node.id,
                rerun=reruns_used,
                category=category.value,
            )

    async def _run_with_transient_retry(
        self,
        dag_run: DAGRun,
        node: DAGNode,
        all_nodes: list[DAGNode],
        reruns_used: int,
    ) -> tuple[NodeResult | None, Exception | None, FailureCategory | None]:
        """Run the agent with transient-retry loop.

        Returns (NodeResult, None, None) on success.
        Returns (None, exc, category) on failure.

        Transient retries are transparent — they do not consume a rerun slot.
        MAX_TRANSIENT_ATTEMPTS total attempts (MAX_TRANSIENT_ATTEMPTS-1 retries).
        """
        context = await self._ctx.build_context(node, all_nodes)
        started_at = datetime.now(timezone.utc)
        transient_tries = 0

        for attempt in range(MAX_TRANSIENT_ATTEMPTS):
            try:
                output, cost_usd = await self._agent_fn(node, context)
                completed_at = datetime.now(timezone.utc)

                meta = ExecutionMeta(
                    attempt_number=reruns_used + 1,
                    started_at=started_at,
                    completed_at=completed_at,
                    cost_usd=cost_usd,
                )
                return (
                    NodeResult(
                        node_id=node.id,
                        dag_run_id=dag_run.id,
                        output=output,
                        meta=meta,
                    ),
                    None,
                    None,
                )

            except TransientError as exc:
                transient_tries += 1
                if attempt + 1 < MAX_TRANSIENT_ATTEMPTS:
                    emit_event(
                        EventType.NODE_RETRYING,
                        dag_run.id,
                        node_id=node.id,
                        transient_retry=transient_tries,
                        error=str(exc),
                    )
                    _logger.warning(
                        "executor.transient_retry",
                        dag_run_id=dag_run.id,
                        node_id=node.id,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                    await asyncio.sleep(0)  # yield; real backoff in Phase 6
                    continue
                # Transient budget exhausted → treat as AgentError
                _logger.error(
                    "executor.transient_exhausted",
                    dag_run_id=dag_run.id,
                    node_id=node.id,
                    retries=transient_tries,
                )
                return None, exc, FailureCategory.AGENT_ERROR

            except (
                SafetyViolationError,
                ResourceExhaustionError,
                DeterministicError,
                AgentError,
            ) as exc:
                category = classify_failure(exc)
                emit_event(
                    EventType.NODE_FAILED,
                    dag_run.id,
                    node_id=node.id,
                    category=category.value,
                    error=str(exc),
                )
                return None, exc, category

            except Exception as exc:
                category = FailureCategory.UNKNOWN
                emit_event(
                    EventType.NODE_FAILED,
                    dag_run.id,
                    node_id=node.id,
                    category=category.value,
                    error=str(exc),
                )
                return None, exc, category

        # Should be unreachable
        return None, RuntimeError("unreachable"), FailureCategory.UNKNOWN

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    async def _escalate(
        self,
        dag_run: DAGRun,
        node: DAGNode,
        exc: Exception,
        category: FailureCategory,
    ) -> None:
        """Create escalation record, mark node/DAG failed, emit event."""
        severity = {
            FailureCategory.SAFETY_VIOLATION: EscalationSeverity.CRITICAL,
            FailureCategory.DETERMINISTIC: EscalationSeverity.HIGH,
            FailureCategory.RESOURCE_EXHAUSTION: EscalationSeverity.HIGH,
        }.get(category, EscalationSeverity.MEDIUM)

        escalation = EscalationRecord(
            id=str(uuid.uuid4()),
            dag_run_id=dag_run.id,
            node_id=node.id,
            severity=severity,
            trigger=category.value,
            message=str(exc),
            status=EscalationStatus.OPEN,
            created_at=datetime.now(timezone.utc),
        )

        await self._state.create_escalation(escalation)
        await self._state.update_dag_node_status(node.id, NodeStatus.FAILED.value)
        await self._state.update_dag_run_status(
            dag_run.id,
            DAGRunStatus.ESCALATED.value,
            error=f"{category.value}: {exc}",
        )

        emit_event(
            EventType.ESCALATION_TRIGGERED,
            dag_run.id,
            node_id=node.id,
            severity=severity.value,
            category=category.value,
            error=str(exc),
        )
        _logger.error(
            "executor.escalated",
            dag_run_id=dag_run.id,
            node_id=node.id,
            severity=severity.value,
            category=category.value,
            error=str(exc),
        )

    # ------------------------------------------------------------------
    # Budget drain
    # ------------------------------------------------------------------

    async def _drain_and_flush(self, dag_run_id: str) -> None:
        """Drain BudgetManager events, persist to StateStore, update usd_used.

        The executor is the sole flusher — called after each node and after
        a pause event is recorded [P7 / Phase 2 spec].
        """
        events = self._budget.drain_events()
        usage_delta = 0.0

        for event in events:
            await self._state.append_budget_event(event)
            if event.event_type == BudgetEventType.USAGE:
                usage_delta += event.usd_after - event.usd_before

        if usage_delta > 0.0:
            await self._state.increment_usd_used(dag_run_id, usage_delta)
