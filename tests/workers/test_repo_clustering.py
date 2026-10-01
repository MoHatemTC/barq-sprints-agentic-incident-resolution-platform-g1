"""Unit tests for the semantic clustering repository methods on InMemoryRepo."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.workers.db import InMemoryRepo


def test_inmemory_repo_cluster_lifecycle() -> None:
    repo = InMemoryRepo()

    cluster_id = uuid4()
    anchor_exec_id = uuid4()
    follower_exec_id = uuid4()

    # 1. Create cluster
    cluster = repo.create_cluster(
        cluster_id=cluster_id,
        anchor_incident_sys_id="sys_inc_001",
        anchor_incident_number="INC001",
        anchor_execution_id=anchor_exec_id,
        pipeline_execution_id=anchor_exec_id,
        similarity_threshold=0.88,
        embedding_model="BAAI/bge-small-en-v1.5",
        service="payment-gateway",
        category="network",
    )
    assert cluster.cluster_id == cluster_id
    assert cluster.status == "creating"
    assert cluster.anchor_incident_number == "INC001"

    # Verify anchor member automatically created
    members = repo.cluster_members[cluster_id]
    assert len(members) == 1
    assert members[0].role == "anchor"
    assert members[0].similarity_score == 1.0
    assert members[0].execution_id == anchor_exec_id

    # 2. Add follower member
    follower = repo.add_cluster_member(
        cluster_id=cluster_id,
        execution_id=follower_exec_id,
        incident_sys_id="sys_inc_002",
        incident_number="INC002",
        similarity_score=0.94,
        role="follower",
    )
    assert follower.role == "follower"
    assert follower.similarity_score == 0.94
    assert len(repo.cluster_members[cluster_id]) == 2

    # A later event for the same incident keeps a separate execution membership.
    later = repo.add_cluster_member(
        cluster_id=cluster_id,
        execution_id=uuid4(),
        incident_sys_id="sys_inc_002",
        incident_number="INC002",
        similarity_score=0.91,
    )
    assert later.execution_id != follower_exec_id
    # The same execution cannot join twice.
    with pytest.raises(ValueError, match="already in a cluster"):
        repo.add_cluster_member(
            cluster_id=cluster_id,
            execution_id=follower_exec_id,  # Duplicate execution_id
            incident_sys_id="sys_inc_003",
            incident_number="INC003",
            similarity_score=0.91,
        )

    # 4. Active cluster lookup for execution
    active_cluster = repo.get_active_cluster_for_execution(follower_exec_id)
    assert active_cluster is not None
    assert active_cluster.cluster_id == cluster_id

    # 5. Update status & solution
    solution_payload = {
        "outcome": "suggested",
        "summary": "Reset payment gateway connection pool",
        "work_note": "Automated resolution applied from cluster",
    }
    repo.update_cluster_status(
        cluster_id=cluster_id,
        status="resolved",
        solution=solution_payload,
    )
    fetched = repo.get_cluster(cluster_id)
    assert fetched is not None
    assert fetched.status == "resolved"
    assert fetched.solution == solution_payload
    assert fetched.completed_at is not None

    # 6. Mark member applied
    assert repo.cluster_members[cluster_id][1].applied_at is None
    repo.mark_member_applied(cluster_id, follower_exec_id)
    assert repo.cluster_members[cluster_id][1].applied_at is not None
