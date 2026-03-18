"""Context models: NodeContext, SharedContext, SharedContextView, and related types.

See data-models.md and P05 for field specs and assembly rules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .agent import AgentOutput, Discovery


# ---------------------------------------------------------------------------
# Immutable issue + repo context
# ---------------------------------------------------------------------------


class IssueContext(BaseModel):
    url: str
    title: str
    body: str  # verbatim — never summarized or truncated [P5.3]


class RepoMetadata(BaseModel):
    path: str
    default_branch: str
    language: str | None = None
    framework: str | None = None
    claude_md: str  # verbatim content of the target repo's CLAUDE.md


# ---------------------------------------------------------------------------
# Discovery record (orchestrator adds provenance before writing)
# ---------------------------------------------------------------------------


class DiscoveryRecord(BaseModel):
    discovery: Discovery
    source_node_id: str
    timestamp: datetime
    superseded_by: str | None = None


# ---------------------------------------------------------------------------
# Shared context  [P5.7]
# Append-only. Orchestrator is the sole writer.
# ---------------------------------------------------------------------------


class SharedContext(BaseModel):
    issue: IssueContext
    repo_metadata: RepoMetadata
    file_mappings: list[DiscoveryRecord] = []
    root_causes: list[DiscoveryRecord] = []
    constraints: list[DiscoveryRecord] = []
    design_decisions: list[DiscoveryRecord] = []
    negative_findings: list[DiscoveryRecord] = []
    summary: str = ""  # derived; recomputed by orchestrator [P5.15]
    active_plan: str = ""  # derived


class SharedContextView(BaseModel):
    """Read-only snapshot passed to each node at dispatch time.

    Evidence fields may be masked per P5.8 pruning rules.
    Capped at 25% of the node's USD budget [P5, P7].
    """

    file_mappings: list[DiscoveryRecord] = []
    root_causes: list[DiscoveryRecord] = []
    constraints: list[DiscoveryRecord] = []
    design_decisions: list[DiscoveryRecord] = []
    negative_findings: list[DiscoveryRecord] = []
    summary: str = ""
    active_plan: str = ""
    usd_budget_used: float = 0.0


# ---------------------------------------------------------------------------
# Ancestor context  (grandparent+ outputs, possibly summarized)
# ---------------------------------------------------------------------------


class AncestorEntry(BaseModel):
    node_id: str
    depth: int  # 1 = parent, 2 = grandparent, etc.
    # AgentOutput when within pass-through depth threshold; str when summarized [P5.12]
    output: Any
    summarized: bool = False


class AncestorContext(BaseModel):
    entries: list[AncestorEntry] = []


# ---------------------------------------------------------------------------
# NodeContext  — assembled by ContextProvider at dispatch time  [P5.11]
# ---------------------------------------------------------------------------


class NodeContext(BaseModel):
    issue: IssueContext  # always verbatim [P5.3]; never capped or pruned
    repo_metadata: RepoMetadata  # always verbatim [P5.3]; never capped or pruned;
    # populated unconditionally on every dispatch
    # All immediate DAG predecessors, keyed by node_id [P5.11]
    parent_outputs: dict[str, AgentOutput] = {}
    ancestor_context: AncestorContext = AncestorContext()
    shared_context_view: SharedContextView = SharedContextView()
    context_bytes_used: int = 0  # byte sum of DiscoveryRecords in shared_context_view
