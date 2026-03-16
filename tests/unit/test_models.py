"""Tests for Pydantic model validation and AgentOutput union discrimination."""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_agent.models.agent import (
    AgentOutput,
    AgentTestOutput,
    AgentTestRole,
    ChildDAGSpec,
    CodeOutput,
    CompositeSpec,
    Constraint,
    DesignDecision,
    Discovery,
    FileMapping,
    NegativeFinding,
    PlanOutput,
    ReviewFinding,
    ReviewOutput,
    ReviewVerdict,
    RootCause,
    SequentialEdge,
)
from agent_agent.models.budget import BudgetEvent, BudgetEventType
from agent_agent.models.context import (
    IssueContext,
    NodeContext,
    SharedContext,
    SharedContextView,
    RepoMetadata,
)
from agent_agent.models.dag import DAGNode, DAGRun, ExecutionMeta, NodeResult, NodeType

_agent_output_adapter: TypeAdapter[AgentOutput] = TypeAdapter(AgentOutput)  # type: ignore[type-arg]
_discovery_adapter: TypeAdapter[Discovery] = TypeAdapter(Discovery)  # type: ignore[type-arg]


# ---------------------------------------------------------------------------
# Discovery types
# ---------------------------------------------------------------------------

class TestDiscoveryTypes:
    def test_file_mapping_round_trip(self):
        d = FileMapping(path="src/auth.py", description="token validation", confidence=0.9)
        out = _discovery_adapter.validate_python(d.model_dump())
        assert isinstance(out, FileMapping)
        assert out.path == "src/auth.py"

    def test_root_cause_round_trip(self):
        d = RootCause(description="null return", evidence="line 42", confidence=0.8)
        out = _discovery_adapter.validate_python(d.model_dump())
        assert isinstance(out, RootCause)

    def test_constraint_round_trip(self):
        d = Constraint(description="no test coverage", evidence="coverage report", confidence=0.95)
        out = _discovery_adapter.validate_python(d.model_dump())
        assert isinstance(out, Constraint)

    def test_design_decision_round_trip(self):
        d = DesignDecision(description="use null check", rationale="simpler", confidence=0.7)
        out = _discovery_adapter.validate_python(d.model_dump())
        assert isinstance(out, DesignDecision)

    def test_negative_finding_round_trip(self):
        d = NegativeFinding(description="utils.py not relevant", confidence=0.99)
        out = _discovery_adapter.validate_python(d.model_dump())
        assert isinstance(out, NegativeFinding)

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            FileMapping(path="x", description="y", confidence=1.5)

    def test_confidence_negative(self):
        with pytest.raises(ValidationError):
            RootCause(description="x", evidence="y", confidence=-0.1)


# ---------------------------------------------------------------------------
# AgentOutput union discrimination
# ---------------------------------------------------------------------------

class TestAgentOutputUnion:
    def test_plan_output_discriminated(self):
        raw = {"type": "plan", "investigation_summary": "looks good"}
        out = _agent_output_adapter.validate_python(raw)
        assert isinstance(out, PlanOutput)
        assert out.child_dag is None

    def test_code_output_discriminated(self):
        raw = {"type": "code", "summary": "added function", "branch_name": "agent/1/fix"}
        out = _agent_output_adapter.validate_python(raw)
        assert isinstance(out, CodeOutput)

    def test_test_output_plan_role(self):
        raw = {"type": "test", "role": "plan", "summary": "test plan here", "test_plan": "run pytest"}
        out = _agent_output_adapter.validate_python(raw)
        assert isinstance(out, AgentTestOutput)
        assert out.role == AgentTestRole.PLAN

    def test_review_output_discriminated(self):
        raw = {"type": "review", "verdict": "approved", "summary": "looks good"}
        out = _agent_output_adapter.validate_python(raw)
        assert isinstance(out, ReviewOutput)
        assert out.verdict == ReviewVerdict.APPROVED

    def test_unknown_type_raises(self):
        with pytest.raises(ValidationError):
            _agent_output_adapter.validate_python({"type": "unknown", "summary": "x"})

    def test_json_round_trip(self):
        original = PlanOutput(investigation_summary="summary", discoveries=[
            FileMapping(path="a.py", description="desc", confidence=0.5)
        ])
        json_str = original.model_dump_json()
        restored = _agent_output_adapter.validate_json(json_str)
        assert isinstance(restored, PlanOutput)
        assert len(restored.discoveries) == 1


# ---------------------------------------------------------------------------
# ChildDAGSpec
# ---------------------------------------------------------------------------

class TestChildDAGSpec:
    def test_valid_spec(self):
        spec = ChildDAGSpec(composites=[
            CompositeSpec(id="A", scope="add logging", branch_suffix="add-logging"),
            CompositeSpec(id="B", scope="add tests", branch_suffix="add-tests"),
        ])
        assert len(spec.composites) == 2
        assert spec.sequential_edges == []

    def test_sequential_edge(self):
        spec = ChildDAGSpec(
            composites=[
                CompositeSpec(id="A", scope="change signature", branch_suffix="sig-change"),
                CompositeSpec(id="B", scope="fix call sites", branch_suffix="fix-calls"),
            ],
            sequential_edges=[SequentialEdge(from_composite_id="A", to_composite_id="B")],
        )
        assert len(spec.sequential_edges) == 1

    def test_plan_output_terminal_when_no_child_dag(self):
        out = PlanOutput(investigation_summary="all done")
        assert out.child_dag is None


# ---------------------------------------------------------------------------
# ReviewFinding severity
# ---------------------------------------------------------------------------

class TestReviewOutput:
    def test_finding_severities(self):
        out = ReviewOutput(
            verdict=ReviewVerdict.NEEDS_REWORK,
            summary="issues found",
            findings=[
                ReviewFinding(severity="critical", description="SQL injection"),
                ReviewFinding(severity="minor", description="typo"),
            ],
        )
        assert out.findings[0].severity == "critical"

    def test_invalid_severity(self):
        with pytest.raises(ValidationError):
            ReviewFinding(severity="blocker", description="x")


# ---------------------------------------------------------------------------
# NodeContext
# ---------------------------------------------------------------------------

class TestNodeContext:
    def _issue(self) -> IssueContext:
        return IssueContext(url="https://github.com/x/y/issues/1", title="Fix bug", body="details")

    def _repo_metadata(self) -> RepoMetadata:
        return RepoMetadata(path="/tmp/repo", default_branch="main", claude_md="")

    def test_default_empty_context(self):
        ctx = NodeContext(issue=self._issue(), repo_metadata=self._repo_metadata())
        assert ctx.parent_outputs == {}
        assert ctx.context_bytes_used == 0

    def test_repo_metadata_required(self):
        # repo_metadata is mandatory — no default [P5.3]
        with pytest.raises(Exception):
            NodeContext(issue=self._issue())  # type: ignore[call-arg]

    def test_repo_metadata_is_repo_metadata_type(self):
        ctx = NodeContext(issue=self._issue(), repo_metadata=self._repo_metadata())
        assert isinstance(ctx.repo_metadata, RepoMetadata)
        assert ctx.repo_metadata.path == "/tmp/repo"

    def test_repo_metadata_claude_md_can_be_empty_stub(self):
        # Phase 2 stub: claude_md is empty string until Phase 3 populates it
        ctx = NodeContext(issue=self._issue(), repo_metadata=self._repo_metadata())
        assert ctx.repo_metadata.claude_md == ""

    def test_parent_outputs_keyed_by_node_id(self):
        plan = PlanOutput(investigation_summary="plan")
        ctx = NodeContext(
            issue=self._issue(),
            repo_metadata=self._repo_metadata(),
            parent_outputs={"node-plan-1": plan},
        )
        assert "node-plan-1" in ctx.parent_outputs
        assert isinstance(ctx.parent_outputs["node-plan-1"], PlanOutput)


class TestSharedContextView:
    def test_usd_budget_used_is_float(self):
        view = SharedContextView(usd_budget_used=0.042)
        assert isinstance(view.usd_budget_used, float)
        assert view.usd_budget_used == pytest.approx(0.042)

    def test_default_usd_budget_used_is_zero(self):
        view = SharedContextView()
        assert view.usd_budget_used == 0.0

    def test_no_token_budget_used_field(self):
        view = SharedContextView()
        assert not hasattr(view, "token_budget_used")


class TestBudgetEventModel:
    def test_usd_fields_are_float(self):
        from datetime import datetime, timezone
        import uuid
        event = BudgetEvent(
            id=str(uuid.uuid4()),
            dag_run_id="run-1",
            node_id="node-1",
            event_type=BudgetEventType.USAGE,
            usd_before=1.0,
            usd_after=1.05,
            reason="recorded",
            timestamp=datetime.now(timezone.utc),
        )
        assert isinstance(event.usd_before, float)
        assert isinstance(event.usd_after, float)

    def test_no_token_fields(self):
        assert not hasattr(BudgetEvent.model_fields, "tokens_before")
        assert not hasattr(BudgetEvent.model_fields, "tokens_after")
