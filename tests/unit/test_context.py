"""Unit tests for context/provider.py and context/shared.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent_agent.budget import BudgetManager
from agent_agent.config import Settings
from agent_agent.context.provider import ContextProvider
from agent_agent.context.shared import append_discoveries
from agent_agent.models.agent import FileMapping, RootCause
from agent_agent.models.context import (
    DiscoveryRecord,
    IssueContext,
    RepoMetadata,
    SharedContext,
)
from agent_agent.models.dag import DAGNode, DAGRun, DAGRunStatus, NodeStatus, NodeType
from agent_agent.state import StateStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_shared_context() -> SharedContext:
    return SharedContext(
        issue=IssueContext(
            url="https://github.com/org/repo/issues/7", title="Fix bug", body="body"
        ),
        repo_metadata=RepoMetadata(path="/repo", default_branch="main", claude_md=""),
    )


def _make_settings(usd_per_byte: float = 0.0) -> Settings:
    # Override the cached singleton for test isolation
    from unittest.mock import MagicMock

    s = MagicMock(spec=Settings)
    s.usd_per_byte = usd_per_byte
    return s  # type: ignore[return-value]


def _make_budget(dag_run_id: str = "run-1", total: float = 1.0) -> BudgetManager:
    return BudgetManager(dag_run_id=dag_run_id, total_budget_usd=total)


def _make_node(node_id: str, parent_ids: list[str] | None = None) -> DAGNode:
    now = datetime.now(timezone.utc)
    return DAGNode(
        id=node_id,
        dag_run_id="run-1",
        type=NodeType.PLAN,
        status=NodeStatus.PENDING,
        level=0,
        composite_id="X",
        parent_node_ids=parent_ids or [],
        created_at=now,
        updated_at=now,
    )


async def _make_state() -> StateStore:
    store = StateStore(":memory:")
    await store.init()
    return store


# ---------------------------------------------------------------------------
# issue field: always present, never summarized or omitted [P5.3]
# ---------------------------------------------------------------------------


async def test_issue_always_present() -> None:
    state = await _make_state()
    shared = _make_shared_context()
    budget = _make_budget()
    budget.allocate(["node-a"])
    provider = ContextProvider(shared, budget, state, _make_settings())
    node = _make_node("node-a")

    ctx = await provider.build_context(node, [node])

    assert ctx.issue.url == "https://github.com/org/repo/issues/7"
    assert ctx.issue.title == "Fix bug"


async def test_issue_present_even_when_shared_view_truncated() -> None:
    """issue must be present even when SharedContextView is fully truncated."""
    state = await _make_state()
    shared = _make_shared_context()
    # Add a large discovery
    now = datetime.now(timezone.utc)
    big_content = "x" * 10_000
    shared.file_mappings.append(
        DiscoveryRecord(
            discovery=FileMapping(path="/big.py", description=big_content, confidence=0.9),
            source_node_id="prev",
            timestamp=now,
        )
    )
    # Very tight cap so everything gets truncated
    budget = _make_budget(total=0.0001)
    budget.allocate(["node-a"])
    provider = ContextProvider(shared, budget, state, _make_settings(usd_per_byte=1.0))
    node = _make_node("node-a")

    ctx = await provider.build_context(node, [node])

    # Issue must still be present
    assert ctx.issue is not None
    assert ctx.issue.url != ""
    # repo_metadata must also be present
    assert ctx.repo_metadata is not None
    assert ctx.repo_metadata.path == "/repo"


# ---------------------------------------------------------------------------
# repo_metadata: always present, never capped or pruned [P5.3]
# ---------------------------------------------------------------------------


async def test_repo_metadata_always_present() -> None:
    state = await _make_state()
    shared = _make_shared_context()
    budget = _make_budget()
    budget.allocate(["node-a"])
    provider = ContextProvider(shared, budget, state, _make_settings())
    node = _make_node("node-a")

    ctx = await provider.build_context(node, [node])

    assert ctx.repo_metadata.path == "/repo"
    assert ctx.repo_metadata.default_branch == "main"


async def test_repo_metadata_is_verbatim_not_pruned() -> None:
    """repo_metadata must be the same object as in SharedContext (never pruned)."""
    state = await _make_state()
    shared = _make_shared_context()
    shared.repo_metadata.claude_md = "some instructions"
    budget = _make_budget()
    budget.allocate(["node-a"])
    provider = ContextProvider(shared, budget, state, _make_settings())
    node = _make_node("node-a")

    ctx = await provider.build_context(node, [node])

    assert ctx.repo_metadata.claude_md == "some instructions"


# ---------------------------------------------------------------------------
# parent_outputs keyed by node_id
# ---------------------------------------------------------------------------


async def test_parent_outputs_keyed_by_node_id() -> None:
    from agent_agent.models.dag import ExecutionMeta, NodeResult
    from agent_agent.models.agent import PlanOutput

    state = await _make_state()
    now = datetime.now(timezone.utc)
    run = DAGRun(
        id="run-1",
        issue_url="https://x",
        repo_path="/r",
        budget_usd=1.0,
        created_at=now,
        updated_at=now,
    )
    await state.create_dag_run(run)

    parent_node = _make_node("parent-1")
    child_node = _make_node("child-1", parent_ids=["parent-1"])

    await state.create_dag_node(parent_node)
    await state.create_dag_node(child_node)

    output = PlanOutput(investigation_summary="done", child_dag=None)
    result = NodeResult(
        node_id="parent-1",
        dag_run_id="run-1",
        output=output,
        meta=ExecutionMeta(
            attempt_number=1,
            started_at=now,
            completed_at=now,
        ),
    )
    await state.save_node_result(result)

    shared = _make_shared_context()
    budget = _make_budget()
    budget.allocate(["child-1"])
    provider = ContextProvider(shared, budget, state, _make_settings())

    ctx = await provider.build_context(child_node, [parent_node, child_node])

    assert "parent-1" in ctx.parent_outputs
    assert ctx.parent_outputs["parent-1"].type == "plan"  # type: ignore[union-attr]


async def test_parent_outputs_empty_when_no_parents() -> None:
    state = await _make_state()
    shared = _make_shared_context()
    budget = _make_budget()
    budget.allocate(["node-root"])
    provider = ContextProvider(shared, budget, state, _make_settings())
    node = _make_node("node-root")

    ctx = await provider.build_context(node, [node])

    assert ctx.parent_outputs == {}


# ---------------------------------------------------------------------------
# AncestorContext: empty for two-level MVP DAG
# ---------------------------------------------------------------------------


async def test_ancestor_context_empty_for_two_level_dag() -> None:
    state = await _make_state()
    shared = _make_shared_context()
    budget = _make_budget()
    budget.allocate(["node-a"])
    provider = ContextProvider(shared, budget, state, _make_settings())
    node = _make_node("node-a")

    ctx = await provider.build_context(node, [node])

    assert ctx.ancestor_context.entries == []


# ---------------------------------------------------------------------------
# SharedContextView cap
# ---------------------------------------------------------------------------


async def test_cap_unenforced_when_usd_per_byte_zero() -> None:
    """All records included when usd_per_byte == 0."""
    state = await _make_state()
    shared = _make_shared_context()
    now = datetime.now(timezone.utc)
    shared.file_mappings.append(
        DiscoveryRecord(
            discovery=FileMapping(path="/a.py", description="desc", confidence=1.0),
            source_node_id="prev",
            timestamp=now,
        )
    )
    shared.root_causes.append(
        DiscoveryRecord(
            discovery=RootCause(description="rc", evidence="ev", confidence=0.8),
            source_node_id="prev",
            timestamp=now,
        )
    )

    budget = _make_budget()
    budget.allocate(["node-a"])
    provider = ContextProvider(shared, budget, state, _make_settings(usd_per_byte=0.0))
    node = _make_node("node-a")

    ctx = await provider.build_context(node, [node])

    assert len(ctx.shared_context_view.file_mappings) == 1
    assert len(ctx.shared_context_view.root_causes) == 1


async def test_cap_enforced_truncation_drops_large_records() -> None:
    """When usd_per_byte > 0 and budget is tight, large records are dropped."""
    state = await _make_state()
    shared = _make_shared_context()
    now = datetime.now(timezone.utc)

    # Add two records; one small, one huge
    shared.file_mappings.append(
        DiscoveryRecord(
            discovery=FileMapping(path="/small.py", description="tiny", confidence=1.0),
            source_node_id="n1",
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),  # older
        )
    )
    big_desc = "x" * 100_000
    shared.root_causes.append(
        DiscoveryRecord(
            discovery=RootCause(description=big_desc, evidence="ev", confidence=0.9),
            source_node_id="n2",
            timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc),  # newer → sorted first
        )
    )

    # Budget so tight only the small record fits
    budget = _make_budget(total=0.01)
    budget.allocate(["node-a"])
    # usd_per_byte such that 100_000 bytes >> cap
    provider = ContextProvider(shared, budget, state, _make_settings(usd_per_byte=1e-4))
    node = _make_node("node-a")

    ctx = await provider.build_context(node, [node])

    # Huge record (root_cause) should be dropped; small file_mapping included
    # Note: newest-first sort means the huge root_cause tries first and gets dropped,
    # then the small file_mapping fits.
    total_included = len(ctx.shared_context_view.file_mappings) + len(
        ctx.shared_context_view.root_causes
    )
    assert total_included < 2  # at least one was dropped


async def test_context_bytes_used_equals_byte_sum_of_included() -> None:
    """context_bytes_used should equal the byte sum of included DiscoveryRecords."""
    state = await _make_state()
    shared = _make_shared_context()
    now = datetime.now(timezone.utc)
    rec = DiscoveryRecord(
        discovery=FileMapping(path="/f.py", description="hello", confidence=1.0),
        source_node_id="n1",
        timestamp=now,
    )
    shared.file_mappings.append(rec)

    budget = _make_budget()
    budget.allocate(["node-a"])
    provider = ContextProvider(shared, budget, state, _make_settings(usd_per_byte=0.0))
    node = _make_node("node-a")

    ctx = await provider.build_context(node, [node])

    expected_bytes = len(json.dumps(rec.model_dump(mode="json")).encode())
    assert ctx.context_bytes_used == expected_bytes


# ---------------------------------------------------------------------------
# context/shared.py — append_discoveries
# ---------------------------------------------------------------------------


async def test_append_discoveries_updates_in_memory_and_state() -> None:
    state = await _make_state()
    now = datetime.now(timezone.utc)
    run = DAGRun(
        id="run-2",
        issue_url="https://x",
        repo_path="/r",
        budget_usd=1.0,
        created_at=now,
        updated_at=now,
    )
    await state.create_dag_run(run)

    shared = _make_shared_context()
    discoveries = [FileMapping(path="/x.py", description="x", confidence=1.0)]

    await append_discoveries(
        discoveries=discoveries,  # type: ignore[arg-type]
        source_node_id="node-src",
        dag_run_id="run-2",
        shared_context=shared,
        state=state,
    )

    assert len(shared.file_mappings) == 1
    rows = await state.list_shared_context("run-2")
    assert len(rows) == 1
    assert rows[0]["category"] == "file_mapping"


async def test_append_discoveries_conflict_detection_logs_warn() -> None:
    """Duplicate file_mapping path should trigger a conflict warning via structlog."""
    from unittest.mock import patch, MagicMock

    state = await _make_state()
    now = datetime.now(timezone.utc)
    run = DAGRun(
        id="run-3",
        issue_url="https://x",
        repo_path="/r",
        budget_usd=1.0,
        created_at=now,
        updated_at=now,
    )
    await state.create_dag_run(run)

    shared = _make_shared_context()
    disc = FileMapping(path="/dup.py", description="first", confidence=1.0)
    await append_discoveries(
        [disc],  # type: ignore[list-item]
        source_node_id="n1",
        dag_run_id="run-3",
        shared_context=shared,
        state=state,
    )

    # Second write to same path → structlog warning
    warning_calls: list[str] = []
    disc2 = FileMapping(path="/dup.py", description="second", confidence=1.0)

    with patch("agent_agent.context.shared._logger") as mock_log:
        mock_bound = MagicMock()
        mock_log.warning.side_effect = lambda msg, **kw: warning_calls.append(msg)

        await append_discoveries(
            [disc2],  # type: ignore[list-item]
            source_node_id="n2",
            dag_run_id="run-3",
            shared_context=shared,
            state=state,
        )

    assert any("conflict" in w.lower() or "potential_conflict" in w for w in warning_calls), (
        f"Expected conflict warning, got: {warning_calls}"
    )
