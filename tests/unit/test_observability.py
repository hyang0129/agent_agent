"""Unit tests for observability.py — emit_event, EventType, level registry."""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from agent_agent.observability import EventType, _LEVEL_REGISTRY, emit_event


# ---------------------------------------------------------------------------
# Level registry
# ---------------------------------------------------------------------------


def test_all_event_types_have_levels() -> None:
    for et in EventType:
        assert et in _LEVEL_REGISTRY, f"{et} missing from _LEVEL_REGISTRY"


def test_l1_events() -> None:
    l1 = [
        EventType.DAG_STARTED,
        EventType.DAG_COMPLETED,
        EventType.DAG_FAILED,
        EventType.DAG_PAUSED,
        EventType.NODE_STARTED,
        EventType.NODE_COMPLETED,
        EventType.NODE_FAILED,
        EventType.ESCALATION_TRIGGERED,
    ]
    for et in l1:
        assert _LEVEL_REGISTRY[et] == 1, f"{et} should be L1"


def test_l2_events() -> None:
    l2 = [
        EventType.NODE_RETRYING,
        EventType.BUDGET_USAGE,
        EventType.BUDGET_PAUSED,
        EventType.CONTEXT_TRUNCATED,
    ]
    for et in l2:
        assert _LEVEL_REGISTRY[et] == 2, f"{et} should be L2"


def test_l3_events() -> None:
    l3 = [
        EventType.TOOL_CALLED,
        EventType.TOOL_DENIED,
        EventType.LLM_REQUEST,
        EventType.LLM_RESPONSE,
    ]
    for et in l3:
        assert _LEVEL_REGISTRY[et] == 3, f"{et} should be L3"


# ---------------------------------------------------------------------------
# emit_event writes structlog record
# ---------------------------------------------------------------------------


def test_emit_event_calls_structlog() -> None:
    """emit_event must produce a structlog record with dag_run_id and node_id."""
    calls: list[dict] = []

    def capture(**kw: object) -> None:
        calls.append(dict(kw))

    mock_bound = MagicMock()
    mock_bound.bind.return_value = mock_bound
    mock_bound.info.side_effect = lambda msg: calls.append({"msg": msg})

    with patch("agent_agent.observability._logger") as mock_logger:
        mock_logger.bind.return_value = mock_bound
        emit_event(EventType.DAG_STARTED, "run-001", node_id=None)

    mock_logger.bind.assert_called_once()
    kwargs = mock_logger.bind.call_args.kwargs
    assert kwargs["dag_run_id"] == "run-001"
    assert kwargs["node_id"] is None
    assert kwargs["event_type"] == "dag.started"
    assert kwargs["observability_level"] == 1


def test_emit_event_includes_node_id() -> None:
    with patch("agent_agent.observability._logger") as mock_logger:
        mock_bound = MagicMock()
        mock_bound.bind.return_value = mock_bound
        mock_logger.bind.return_value = mock_bound

        emit_event(EventType.NODE_COMPLETED, "run-002", node_id="node-42")

    kwargs = mock_logger.bind.call_args.kwargs
    assert kwargs["node_id"] == "node-42"


def test_emit_event_l3_includes_trace_span() -> None:
    """L3 events should include trace_id and span_id when provided."""
    with patch("agent_agent.observability._logger") as mock_logger:
        mock_bound = MagicMock()
        mock_bound.bind.return_value = mock_bound
        mock_logger.bind.return_value = mock_bound

        emit_event(
            EventType.TOOL_CALLED,
            "run-003",
            node_id="node-1",
            trace_id="trace-abc",
            span_id="span-xyz",
            tool_name="read_file",
        )

    # After the first bind() call, subsequent bind() calls add trace/span/payload
    assert mock_bound.bind.call_count >= 2  # at least trace_id + span_id binds
    all_kwargs: dict = {}
    for call in mock_bound.bind.call_args_list:
        all_kwargs.update(call.kwargs)
    assert all_kwargs.get("trace_id") == "trace-abc"
    assert all_kwargs.get("span_id") == "span-xyz"
    assert all_kwargs.get("tool_name") == "read_file"


def test_emit_event_level_for_tool_called() -> None:
    with patch("agent_agent.observability._logger") as mock_logger:
        mock_bound = MagicMock()
        mock_bound.bind.return_value = mock_bound
        mock_logger.bind.return_value = mock_bound

        emit_event(EventType.TOOL_CALLED, "run-x", node_id="n")

    kwargs = mock_logger.bind.call_args.kwargs
    assert kwargs["observability_level"] == 3


# ---------------------------------------------------------------------------
# Non-fatal behaviour
# ---------------------------------------------------------------------------


def test_emit_event_does_not_raise_on_logger_error() -> None:
    """emit_event must never raise — structlog errors go to stderr."""
    with patch("agent_agent.observability._logger") as mock_logger:
        mock_logger.bind.side_effect = RuntimeError("structlog exploded")

        captured = StringIO()
        with patch("sys.stderr", captured):
            emit_event(EventType.DAG_STARTED, "run-x", node_id=None)

    assert "emit_event failed" in captured.getvalue()


def test_emit_event_extra_payload_forwarded() -> None:
    with patch("agent_agent.observability._logger") as mock_logger:
        mock_bound = MagicMock()
        mock_bound.bind.return_value = mock_bound
        mock_logger.bind.return_value = mock_bound

        emit_event(EventType.BUDGET_USAGE, "run-y", node_id="n", usd=0.05, tokens=100)

    all_kwargs: dict = {}
    for call in mock_bound.bind.call_args_list:
        all_kwargs.update(call.kwargs)
    assert all_kwargs.get("usd") == 0.05
    assert all_kwargs.get("tokens") == 100
