"""Structured observability — emit_event(), EventType enum, structlog wrapper.

Three levels per P11:
  L1 — status:  DAG and node execution state
  L2 — metrics: tokens, cost, budget events, retries
  L3 — traces:  full LLM I/O and tool calls (OTel-compatible field names)

emit_event() is NON-FATAL — failures are written to stderr and never block execution.
All records include dag_run_id + node_id on every line [P11].
"""
from __future__ import annotations

import sys
from enum import Enum
from typing import Any

import structlog

_logger = structlog.get_logger(__name__)


class EventType(str, Enum):
    # L1 — status
    DAG_STARTED = "dag.started"
    DAG_COMPLETED = "dag.completed"
    DAG_FAILED = "dag.failed"
    DAG_PAUSED = "dag.paused"
    NODE_STARTED = "node.started"
    NODE_COMPLETED = "node.completed"
    NODE_FAILED = "node.failed"
    ESCALATION_TRIGGERED = "escalation.triggered"

    # L2 — metrics
    NODE_RETRYING = "node.retrying"
    BUDGET_USAGE = "budget.usage"
    BUDGET_PAUSED = "budget.paused"
    CONTEXT_TRUNCATED = "context.truncated"

    # L3 — traces (OTel-compatible field names; exporter added post-MVP)
    TOOL_CALLED = "tool.called"
    TOOL_DENIED = "tool.denied"
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"


# Maps each EventType to its observability level (1/2/3)
_LEVEL_REGISTRY: dict[EventType, int] = {
    # L1
    EventType.DAG_STARTED: 1,
    EventType.DAG_COMPLETED: 1,
    EventType.DAG_FAILED: 1,
    EventType.DAG_PAUSED: 1,
    EventType.NODE_STARTED: 1,
    EventType.NODE_COMPLETED: 1,
    EventType.NODE_FAILED: 1,
    EventType.ESCALATION_TRIGGERED: 1,
    # L2
    EventType.NODE_RETRYING: 2,
    EventType.BUDGET_USAGE: 2,
    EventType.BUDGET_PAUSED: 2,
    EventType.CONTEXT_TRUNCATED: 2,
    # L3
    EventType.TOOL_CALLED: 3,
    EventType.TOOL_DENIED: 3,
    EventType.LLM_REQUEST: 3,
    EventType.LLM_RESPONSE: 3,
}


def emit_event(
    event_type: EventType,
    dag_run_id: str,
    *,
    node_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    **payload: Any,
) -> None:
    """Emit a structured observability event.

    Non-fatal: any exception is written to stderr and execution continues.
    dag_run_id and node_id are included on every record [P11].
    trace_id / span_id are first-class fields for OTel compatibility (Phase 4).
    """
    try:
        level = _LEVEL_REGISTRY.get(event_type, 1)

        bound = _logger.bind(
            dag_run_id=dag_run_id,
            node_id=node_id,
            event_type=event_type.value,
            observability_level=level,
        )

        if trace_id is not None:
            bound = bound.bind(trace_id=trace_id)
        if span_id is not None:
            bound = bound.bind(span_id=span_id)
        if payload:
            bound = bound.bind(**payload)

        bound.info(event_type.value)

    except Exception as exc:  # noqa: BLE001
        print(f"emit_event failed ({event_type}): {exc}", file=sys.stderr)
