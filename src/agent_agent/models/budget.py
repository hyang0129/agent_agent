"""Budget event models.  [P7]"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class BudgetEventType(str, Enum):
    INITIAL_ALLOCATION = "initial_allocation"
    USAGE = "usage"
    RECLAIM = "reclaim"
    TOP_UP = "top_up"
    PAUSE = "pause"  # DAG paused after node completed over budget
    HUMAN_INCREASE = "human_increase"


class BudgetEvent(BaseModel):
    id: str
    dag_run_id: str
    node_id: str | None  # None for DAG-level events
    event_type: BudgetEventType
    usd_before: float
    usd_after: float
    reason: str
    timestamp: datetime
