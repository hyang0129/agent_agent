"""SharedContext write protocol — validation, append, and persistence.

The orchestrator is the sole writer to SharedContext [P5 invariant].
Agents propose discoveries via their output; the executor validates and commits them here.

Conflict detection is deferred to Phase 6. MVP: last-write-wins + structlog warning.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog

from ..models.agent import Discovery
from ..models.context import DiscoveryRecord, SharedContext
from ..state import StateStore

_logger = structlog.get_logger(__name__)

# Maps Discovery.type literal → SharedContext list attribute name
_CATEGORY_ATTR: dict[str, str] = {
    "file_mapping": "file_mappings",
    "root_cause": "root_causes",
    "constraint": "constraints",
    "design_decision": "design_decisions",
    "negative_finding": "negative_findings",
}


async def append_discoveries(
    discoveries: list[Discovery],
    source_node_id: str,
    dag_run_id: str,
    shared_context: SharedContext,
    state: StateStore,
) -> None:
    """Validate and append agent discoveries to SharedContext (in-memory + SQLite).

    Validates each discovery via Pydantic (already guaranteed by AgentOutput model).
    Appends a DiscoveryRecord with provenance.
    Logs a warning when a potential conflict is detected (Phase 6 adds full resolution).
    Persists each record to the state store.
    """
    now = datetime.now(timezone.utc)

    for discovery in discoveries:
        category: str = getattr(discovery, "type")
        attr = _CATEGORY_ATTR.get(category)
        if attr is None:
            _logger.warning(
                "shared_context.unknown_category",
                dag_run_id=dag_run_id,
                node_id=source_node_id,
                category=category,
            )
            continue

        existing: list[DiscoveryRecord] = getattr(shared_context, attr)

        # MVP conflict detection: log warn if same category has overlapping identity
        if _has_potential_conflict(discovery, existing):
            _logger.warning(
                "shared_context.potential_conflict",
                dag_run_id=dag_run_id,
                node_id=source_node_id,
                category=category,
                detail="last-write-wins; full resolution deferred to Phase 6",
            )

        record = DiscoveryRecord(
            discovery=discovery,
            source_node_id=source_node_id,
            timestamp=now,
        )
        existing.append(record)

        await state.append_shared_context(
            entry_id=str(uuid.uuid4()),
            dag_run_id=dag_run_id,
            source_node_id=source_node_id,
            category=category,
            data=record.model_dump(mode="json"),
        )


def _has_potential_conflict(incoming: Discovery, existing: list[DiscoveryRecord]) -> bool:
    """Heuristic conflict check. MVP: detects duplicate paths for file_mappings."""
    if not existing:
        return False

    incoming_path: str | None = getattr(incoming, "path", None)
    if incoming_path is not None:
        return any(getattr(r.discovery, "path", None) == incoming_path for r in existing)

    return False
