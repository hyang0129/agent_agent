"""DAG engine — construction and topological traversal.

MVP stub DAG shape (P1.2):
  L0 DAG: single NodeType.PLAN node (initial decomposition)
  L1 DAG: NodeType.CODING → NodeType.REVIEW → NodeType.PLAN (terminal)

The terminal Plan at L1 is the last node in the L1 child DAG.  It is NOT a
separate level — it is the final node within L1.

The full structure is pre-built by build_stub_dag() and persisted to dag_nodes
before any execution begins [P1.7/P1.8].  All nodes share the same dag_run_id.
The L0→L1 relationship is expressed through parent_node_ids edges.

Child DAG spawning (Phase 4):  when the terminal Plan outputs a non-null
child_dag, the executor constructs a new child DAGRun.  Phase 2 stub raises
NotImplementedError in that case.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone

from ..models.dag import DAGNode, DAGRun, NodeStatus, NodeType


def build_stub_dag(dag_run: DAGRun) -> list[DAGNode]:
    """Build the complete MVP stub DAG (L0 + L1 nodes) for a DAGRun.

    Returns all four DAGNodes in definition order:
        L0-PLAN → L1-CODING → L1-REVIEW → L1-PLAN (terminal)

    All nodes must be persisted to dag_nodes before execution begins [P1.8].
    """
    now = datetime.now(timezone.utc)
    run_id = dag_run.id

    l0_plan_id = f"{run_id}-l0-plan"
    l1_coding_id = f"{run_id}-l1-coding"
    l1_review_id = f"{run_id}-l1-review"
    l1_plan_id = f"{run_id}-l1-plan"

    # L0: single Plan node — initial decomposition; spawns L1 child DAG
    l0_plan = DAGNode(
        id=l0_plan_id,
        dag_run_id=run_id,
        type=NodeType.PLAN,
        status=NodeStatus.PENDING,
        level=0,
        composite_id="L0",
        parent_node_ids=[],
        child_node_ids=[l1_coding_id],
        created_at=now,
        updated_at=now,
    )

    # L1: Coding node — depends on L0 Plan
    l1_coding = DAGNode(
        id=l1_coding_id,
        dag_run_id=run_id,
        type=NodeType.CODING,
        status=NodeStatus.PENDING,
        level=1,
        composite_id="A",
        parent_node_ids=[l0_plan_id],
        child_node_ids=[l1_review_id],
        created_at=now,
        updated_at=now,
    )

    # L1: Review node — depends on Coding completing and pushing branch
    l1_review = DAGNode(
        id=l1_review_id,
        dag_run_id=run_id,
        type=NodeType.REVIEW,
        status=NodeStatus.PENDING,
        level=1,
        composite_id="A-review",
        parent_node_ids=[l1_coding_id],
        child_node_ids=[l1_plan_id],
        created_at=now,
        updated_at=now,
    )

    # L1: Terminal Plan — stub always returns PlanOutput(child_dag=None)
    l1_plan = DAGNode(
        id=l1_plan_id,
        dag_run_id=run_id,
        type=NodeType.PLAN,
        status=NodeStatus.PENDING,
        level=1,
        composite_id="L1-terminal",
        parent_node_ids=[l1_review_id],
        child_node_ids=[],  # no successors — terminal node
        created_at=now,
        updated_at=now,
    )

    return [l0_plan, l1_coding, l1_review, l1_plan]


def build_l0_dag(dag_run: DAGRun) -> list[DAGNode]:
    """Build a single-node L0 DAG for composite execution mode.

    When use_composites=True, the L0 Plan composite's PlanOutput.child_dag
    drives _spawn_child_dag(), which builds L1 nodes dynamically at runtime.
    Pre-building L1 stub nodes causes duplicate dispatch when _spawn_child_dag
    returns, so only the L0 Plan node is pre-built.
    """
    now = datetime.now(timezone.utc)
    run_id = dag_run.id

    l0_plan = DAGNode(
        id=f"{run_id}-l0-plan",
        dag_run_id=run_id,
        type=NodeType.PLAN,
        status=NodeStatus.PENDING,
        level=0,
        composite_id="L0",
        parent_node_ids=[],
        # child_node_ids is intentionally empty: in composite mode, L1 nodes are built
        # dynamically by _spawn_child_dag() from the Plan composite's ChildDAGSpec.
        # Pre-building child edges would cause duplicate dispatch.
        child_node_ids=[],
        created_at=now,
        updated_at=now,
    )
    return [l0_plan]


def topological_sort(nodes: list[DAGNode]) -> list[DAGNode]:
    """Return nodes in topological order (all dependencies before dependents).

    Uses Kahn's algorithm seeded from parent_node_ids.  Only edges between
    nodes present in the input list are considered — external parent references
    are ignored (supports sub-DAG sorting).

    Raises ValueError if the graph has cycles or disconnected nodes.
    """
    node_ids = {n.id for n in nodes}
    id_to_node = {n.id: n for n in nodes}

    # in-degree: count of known-parent edges
    in_degree: dict[str, int] = {
        n.id: sum(1 for p in n.parent_node_ids if p in node_ids) for n in nodes
    }

    # successor map: parent_id → [child_id, ...]
    successors: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        for parent_id in n.parent_node_ids:
            if parent_id in node_ids:
                successors[parent_id].append(n.id)

    # Kahn's algorithm
    queue: deque[str] = deque(sorted(nid for nid, deg in in_degree.items() if deg == 0))
    result: list[DAGNode] = []

    while queue:
        node_id = queue.popleft()
        result.append(id_to_node[node_id])
        for succ_id in sorted(successors[node_id]):
            in_degree[succ_id] -= 1
            if in_degree[succ_id] == 0:
                queue.append(succ_id)

    if len(result) != len(nodes):
        raise ValueError(
            f"topological_sort: cycle or disconnected nodes detected "
            f"(sorted {len(result)}/{len(nodes)} nodes)"
        )

    return result
