"""ContextProvider — assembles NodeContext at dispatch time.

Sources per implementation plan:
  issue:              SharedContext.issue      — always verbatim, never capped [P5.3/P5.18]
  repo_metadata:      SharedContext.repo_metadata — always verbatim, never pruned [P5.3]
  parent_outputs:     StateStore (immediate DAG predecessors, keyed by node_id)
  ancestor_context:   empty for two-level MVP DAG (logic present for Phase 4)
  shared_context_view: capped at 25% of node's USD budget via BudgetManager [P5/P7]
  context_bytes_used: byte sum of included DiscoveryRecords

SharedContextView cap behaviour:
  - usd_per_byte == 0.0 → cap unenforced (placeholder until profiled)
  - otherwise → truncation-only stub (Tiers 2+3 masking/summarization deferred to Phase 6)
    Sort DiscoveryRecords newest-first, accumulate byte sizes, drop records that exceed limit.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from ..budget import BudgetManager
from ..config import Settings
from ..models.context import (
    AncestorContext,
    DiscoveryRecord,
    NodeContext,
    SharedContext,
    SharedContextView,
)
from ..models.dag import DAGNode
from ..observability import EventType, emit_event
from ..state import StateStore

_logger = structlog.get_logger(__name__)


class ContextProvider:
    """Assembles NodeContext at dispatch time.

    Constructed once per DAG run and reused for every node dispatch.
    """

    def __init__(
        self,
        shared_context: SharedContext,
        budget: BudgetManager,
        state: StateStore,
        settings: Settings,
    ) -> None:
        self._shared = shared_context
        self._budget = budget
        self._state = state
        self._settings = settings

    @property
    def shared_context(self) -> SharedContext:
        return self._shared

    async def build_context(self, node: DAGNode, all_nodes: list[DAGNode]) -> NodeContext:
        """Assemble NodeContext for a node at dispatch time."""
        # 1. Parent outputs — from all immediate predecessors in the state store
        parent_outputs = {}
        for parent_id in node.parent_node_ids:
            result = await self._state.get_node_result(parent_id)
            if result is not None:
                parent_outputs[parent_id] = result.output

        # 2. SharedContextView — capped at 25% of node's USD budget
        shared_view, bytes_used = self._build_shared_view(node.id, node.dag_run_id)

        # 3. AncestorContext — empty for two-level MVP DAG
        #    (grandparent+ summarization logic present here for Phase 4)
        ancestor_context = AncestorContext()

        return NodeContext(
            issue=self._shared.issue,
            repo_metadata=self._shared.repo_metadata,
            parent_outputs=parent_outputs,
            ancestor_context=ancestor_context,
            shared_context_view=shared_view,
            context_bytes_used=bytes_used,
        )

    def _build_shared_view(self, node_id: str, dag_run_id: str) -> tuple[SharedContextView, int]:
        """Build SharedContextView with USD cap enforcement.

        Returns (SharedContextView, context_bytes_used).
        """
        usd_per_byte = self._settings.usd_per_byte

        all_records: list[tuple[str, DiscoveryRecord]] = [
            (attr, r)
            for attr, lst in [
                ("file_mappings", self._shared.file_mappings),
                ("root_causes", self._shared.root_causes),
                ("constraints", self._shared.constraints),
                ("design_decisions", self._shared.design_decisions),
                ("negative_findings", self._shared.negative_findings),
            ]
            for r in lst
        ]

        if usd_per_byte == 0.0 or not all_records:
            # Cap unenforced — include everything
            bytes_used = sum(
                len(json.dumps(r.model_dump(mode="json")).encode()) for _, r in all_records
            )
            return (
                SharedContextView(
                    file_mappings=list(self._shared.file_mappings),
                    root_causes=list(self._shared.root_causes),
                    constraints=list(self._shared.constraints),
                    design_decisions=list(self._shared.design_decisions),
                    negative_findings=list(self._shared.negative_findings),
                    summary=self._shared.summary,
                    active_plan=self._shared.active_plan,
                    usd_budget_used=0.0,
                ),
                bytes_used,
            )

        # Cap enforced — truncation-only (Tiers 2+3 deferred to Phase 6 per P5.8)
        cap_usd = self._budget.shared_context_cap(node_id)
        byte_budget = cap_usd / usd_per_byte

        # Sort newest-first, accumulate, drop records exceeding budget
        all_records.sort(key=lambda x: x[1].timestamp, reverse=True)

        included: dict[str, list[DiscoveryRecord]] = {
            "file_mappings": [],
            "root_causes": [],
            "constraints": [],
            "design_decisions": [],
            "negative_findings": [],
        }
        bytes_used = 0

        for attr, record in all_records:
            record_bytes = len(json.dumps(record.model_dump(mode="json")).encode())
            if bytes_used + record_bytes <= byte_budget:
                included[attr].append(record)
                bytes_used += record_bytes
            else:
                emit_event(
                    EventType.CONTEXT_TRUNCATED,
                    dag_run_id,
                    node_id=node_id,
                    bytes_budget=int(byte_budget),
                    bytes_used=bytes_used,
                )
                _logger.warning(
                    "context.truncated",
                    node_id=node_id,
                    bytes_budget=byte_budget,
                    bytes_used=bytes_used,
                )

        usd_used = bytes_used * usd_per_byte

        return (
            SharedContextView(
                file_mappings=included["file_mappings"],
                root_causes=included["root_causes"],
                constraints=included["constraints"],
                design_decisions=included["design_decisions"],
                negative_findings=included["negative_findings"],
                summary=self._shared.summary,
                active_plan=self._shared.active_plan,
                usd_budget_used=usd_used,
            ),
            bytes_used,
        )
