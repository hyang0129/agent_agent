"""Agent output models and discovery types.

All inter-node data uses these canonical models. See data-models.md for field specs.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Discovery types  [P5.7]
# ---------------------------------------------------------------------------


class FileMapping(BaseModel):
    type: Literal["file_mapping"] = "file_mapping"
    path: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0)


class RootCause(BaseModel):
    type: Literal["root_cause"] = "root_cause"
    description: str
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)


class Constraint(BaseModel):
    type: Literal["constraint"] = "constraint"
    description: str
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)


class DesignDecision(BaseModel):
    type: Literal["design_decision"] = "design_decision"
    description: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class NegativeFinding(BaseModel):
    type: Literal["negative_finding"] = "negative_finding"
    description: str
    confidence: float = Field(ge=0.0, le=1.0)


Discovery = Annotated[
    FileMapping | RootCause | Constraint | DesignDecision | NegativeFinding,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Child DAG specification  (produced inside PlanOutput)
# ---------------------------------------------------------------------------


class CompositeSpec(BaseModel):
    id: str  # short label: "A", "B", "C" — used in branch names + logs
    scope: str  # what this composite is responsible for
    branch_suffix: str  # used in agent/<issue-number>/<branch_suffix>


class SequentialEdge(BaseModel):
    from_composite_id: str  # Review of this composite must complete first
    to_composite_id: str  # before this Coding composite may start


class ChildDAGSpec(BaseModel):
    composites: list[CompositeSpec]
    sequential_edges: list[SequentialEdge] = []
    justification: str | None = None  # required when len(composites) >= 6


# ---------------------------------------------------------------------------
# Agent output models
# ---------------------------------------------------------------------------


class PlanOutput(BaseModel):
    type: Literal["plan"] = "plan"
    investigation_summary: str
    child_dag: ChildDAGSpec | None = None  # None = work complete
    discoveries: list[Discovery] = []


class CodeOutput(BaseModel):
    type: Literal["code"] = "code"
    summary: str
    files_changed: list[str] = []
    branch_name: str
    commit_sha: str | None = None
    tests_passed: bool | None = None
    discoveries: list[Discovery] = []


class AgentTestRole(str, Enum):
    PLAN = "plan"
    RESULTS = "results"
    TESTER = "tester"


class AgentTestOutput(BaseModel):
    type: Literal["test"] = "test"
    role: AgentTestRole
    summary: str
    # role == plan
    test_plan: str | None = None
    # role == results
    passed: bool | None = None
    total_tests: int | None = None
    failed_tests: int | None = None
    failure_details: str | None = None  # raw output, truncated to 2000 chars
    discoveries: list[Discovery] = []


class ReviewVerdict(str, Enum):
    APPROVED = "approved"
    NEEDS_REWORK = "needs_rework"
    REJECTED = "rejected"


class ReviewFinding(BaseModel):
    severity: Literal["critical", "major", "minor"]
    location: str | None = None
    description: str
    suggested_fix: str | None = None


class ReviewOutput(BaseModel):
    type: Literal["review"] = "review"
    verdict: ReviewVerdict
    summary: str
    findings: list[ReviewFinding] = []
    downstream_impacts: list[str] = []
    discoveries: list[Discovery] = []
    policy_review: "PolicyReviewOutput | None" = None


class PolicyCitation(BaseModel):
    policy_id: str       # identifier matching the policy document (e.g. "P1", "POLICY-001")
    policy_text: str     # the exact clause cited, quoted from the policy document
    location: str        # file:line where the violation occurs in the diff
    finding: str         # description of the specific violation
    is_violation: bool   # True if this citation is a violation; False if cited as compliant confirmation


class PolicyReviewOutput(BaseModel):
    type: Literal["policy_review"] = "policy_review"
    approved: bool                           # False if any citation has is_violation=True
    policy_citations: list[PolicyCitation]   # one entry per evaluated policy; empty if no policies
    policies_evaluated: list[str]            # policy_ids the reviewer determined applicable to this diff
    skipped: bool                            # True if no CLAUDE.md or policy docs exist in repo


# Rebuild ReviewOutput after PolicyReviewOutput is defined (forward reference resolution)
ReviewOutput.model_rebuild()


AgentOutput = Annotated[
    PlanOutput | CodeOutput | AgentTestOutput | ReviewOutput | PolicyReviewOutput,
    Field(discriminator="type"),
]
