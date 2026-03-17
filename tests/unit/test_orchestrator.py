"""Orchestrator unit tests — mock GitHub client, :memory: SQLite.

Tests:
  - DAGRun record created in SQLite BEFORE emit_event(DAG_STARTED) fires
  - orchestrator.run() returns (branch_name, summary) from stub agent outputs
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agent_agent.config import Settings, get_settings
from agent_agent.orchestrator import Orchestrator
from agent_agent.state import StateStore


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_AGENT_ENV", "test")
    get_settings.cache_clear()


@pytest.fixture()
async def state_store() -> StateStore:
    store = StateStore(":memory:")
    await store.init()
    yield store  # type: ignore[misc]
    await store.close()


@pytest.fixture()
def settings() -> Settings:
    return Settings(env="test", port=0)  # port=0 → won't actually bind


class TestOrchestrator:
    async def test_run_returns_branch_and_summary(
        self, settings: Settings, state_store: StateStore
    ) -> None:
        """orchestrator.run() should return stub values from Phase 2 agents."""
        # Patch the server start to avoid binding a real port
        with patch("agent_agent.orchestrator.Orchestrator._start_server") as mock_server:
            mock_server.return_value = type("FakeServer", (), {"should_exit": False})()

            orchestrator = Orchestrator(
                settings=settings,
                repo_path="/tmp/fake-repo",
                claude_md_content="# Test CLAUDE.md",
                issue_url="https://github.com/test/repo/issues/1",
                state_store=state_store,
            )
            branch_name, summary = await orchestrator.run()

        assert branch_name  # non-empty string from stub CodeOutput
        assert "agent/" in branch_name or "stub" in branch_name
        assert summary  # non-empty string from stub ReviewOutput

    async def test_dag_run_created_before_dag_started_event(
        self, settings: Settings, state_store: StateStore
    ) -> None:
        """DAGRun must exist in SQLite before emit_event(DAG_STARTED) fires [P11].

        Strategy: record the order of state store writes vs emit_event calls
        by patching both. The create_dag_run call must precede the DAG_STARTED event.
        """
        call_log: list[str] = []

        original_create = state_store.create_dag_run

        async def tracking_create(run):  # type: ignore[no-untyped-def]
            call_log.append("create_dag_run")
            return await original_create(run)

        def tracking_emit(event_type, dag_run_id, **kwargs):  # type: ignore[no-untyped-def]
            from agent_agent.observability import EventType
            if event_type == EventType.DAG_STARTED:
                call_log.append("emit_dag_started")

        with (
            patch("agent_agent.orchestrator.Orchestrator._start_server") as mock_server,
            patch("agent_agent.orchestrator.emit_event", side_effect=tracking_emit),
            patch.object(state_store, "create_dag_run", side_effect=tracking_create),
        ):
            mock_server.return_value = type("FakeServer", (), {"should_exit": False})()

            orchestrator = Orchestrator(
                settings=settings,
                repo_path="/tmp/fake-repo",
                claude_md_content="# Test",
                issue_url="https://github.com/test/repo/issues/1",
                state_store=state_store,
            )
            await orchestrator.run()

        assert "create_dag_run" in call_log
        assert "emit_dag_started" in call_log
        # create_dag_run must come before emit_dag_started
        assert call_log.index("create_dag_run") < call_log.index("emit_dag_started")

    async def test_dag_nodes_persisted_before_execution(
        self, settings: Settings, state_store: StateStore
    ) -> None:
        """All nodes must be persisted to dag_nodes before execution begins [P1.8]."""
        nodes_at_execute: list[int] = []

        original_execute = None

        async def capturing_execute(dag_run, nodes):  # type: ignore[no-untyped-def]
            # Check that nodes are already in the DB
            db_nodes = await state_store.list_dag_nodes(dag_run.id)
            nodes_at_execute.append(len(db_nodes))

        with (
            patch("agent_agent.orchestrator.Orchestrator._start_server") as mock_server,
            patch("agent_agent.dag.executor.DAGExecutor.execute", side_effect=capturing_execute),
        ):
            mock_server.return_value = type("FakeServer", (), {"should_exit": False})()

            orchestrator = Orchestrator(
                settings=settings,
                repo_path="/tmp/fake-repo",
                claude_md_content="# Test",
                issue_url="https://github.com/test/repo/issues/1",
                state_store=state_store,
            )
            # run() won't complete normally since execute is mocked, but branch/summary
            # will be empty - that's fine for this test
            await orchestrator.run()

        assert nodes_at_execute[0] == 4  # L0-plan + L1-coding + L1-review + L1-plan
