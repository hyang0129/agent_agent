"""DAG run and node models, plus NodeResult / ExecutionMeta."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from .agent import AgentOutput


class DAGRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"
    PAUSED = "paused"     # budget threshold reached; resumes on human increase [P7]


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"   # not dispatched because DAG was paused [P7]


class NodeType(str, Enum):
    PLAN = "plan"
    CODING = "coding"
    REVIEW = "review"


class DAGRun(BaseModel):
    id: str
    issue_url: str
    repo_path: str
    status: DAGRunStatus = DAGRunStatus.PENDING
    budget_usd: float
    usd_used: float = 0.0
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class DAGNode(BaseModel):
    id: str
    dag_run_id: str
    type: NodeType
    status: NodeStatus = NodeStatus.PENDING
    level: int
    composite_id: str          # matches CompositeSpec.id (e.g. "A", "B")
    parent_node_ids: list[str] = []
    child_node_ids: list[str] = []
    worktree_path: str | None = None
    branch_name: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ExecutionMeta(BaseModel):
    attempt_number: int = 1
    started_at: datetime
    completed_at: datetime
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    failure_category: str | None = None


class NodeResult(BaseModel):
    node_id: str
    dag_run_id: str
    output: AgentOutput
    meta: ExecutionMeta
