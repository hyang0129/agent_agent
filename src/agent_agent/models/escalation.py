"""Escalation models.  [P6]"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class EscalationSeverity(str, Enum):
    CRITICAL = "CRITICAL"   # safety violation — halt immediately, no retry
    HIGH = "HIGH"           # semantic anomaly, deterministic error, depth limit
    MEDIUM = "MEDIUM"       # retry exhaustion, budget exhaustion


class EscalationStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class EscalationRecord(BaseModel):
    id: str
    dag_run_id: str
    node_id: str | None
    severity: EscalationSeverity
    trigger: str
    message: str          # structured JSON string: attempt history, DAG impact, budget state
    status: EscalationStatus = EscalationStatus.OPEN
    resolution: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
