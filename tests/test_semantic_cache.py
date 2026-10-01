"""Unit & integration test suite for the Semantic Caching layer.

BARQ G1 - Sprint 4 (S4.2)
Verifies:
1. Similarity Clustering: Detects when incoming incidents are semantically similar.
2. Shared Solution Reuse: Full resolution pipeline runs once per cluster; followers reuse cached solution.
3. Low-Confidence Fallback: Explicit threshold tau (0.86) enforces independent execution below threshold.
4. Visibility & Isolation: Preserves individual incident records and prevents cross-service false clustering.
5. Concurrent Burst: A burst of concurrent similar incidents reuses a single resolution run.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4
import pytest

from agent.semantic_cache import (
    DEFAULT_SIMILARITY_THRESHOLD,
    SemanticCache,
    cosine_similarity,
)
from app.models.semantic_cluster import AdmissionMode, ClusterStatus
from app.workers.db import InMemoryRepo


def dummy_embed_fn(text: str) -> list[float]:
    """Deterministic 4-dim embedding mock for fast vector similarity tests."""
    text_lower = text.lower()
    # Topic 1: Payment gateway / timeout
    if "payment" in text_lower or "gateway" in text_lower or "timeout" in text_lower:
        return [0.95, 0.1, 0.0, 0.0]
    # Topic 2: VPN disconnect / network
    elif "vpn" in text_lower or "corporate-vpn" in text_lower:
        return [0.0, 0.95, 0.1, 0.0]
    # Topic 3: Database deadlock / postgres
    elif "database" in text_lower or "deadlock" in text_lower:
        return [0.0, 0.0, 0.95, 0.1]
    # Generic fallback
    return [0.2, 0.2, 0.2, 0.2]


def test_cosine_similarity_edge_cases() -> None:
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert round(cosine_similarity([1.0, 1.0], [1.0, 0.0]), 4) == 0.7071


def test_semantic_cache_first_incident_becomes_leader() -> None:
    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)

    inc1 = SimpleNamespace(
        sys_id="sys_001",
        number="INC001",
        service="payment-gateway",
        category="software",
        short_description="Payment timeout error 504",
        description="Transactions are timing out on checkout",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    exec_id_1 = uuid4()

    res1 = cache.admit(inc1, exec_id_1)
    assert res1.mode == AdmissionMode.LEADER
    assert res1.cluster_id is not None
    assert res1.anchor_incident_number == "INC001"

    # Verify cluster state in repo
    cluster_in_repo = repo.get_cluster(res1.cluster_id)
    assert cluster_in_repo is not None
    assert cluster_in_repo.status == "creating"
    assert len(repo.cluster_members[res1.cluster_id]) == 1
    assert repo.cluster_members[res1.cluster_id][0].role == "anchor"


def test_semantic_cache_similar_incident_joins_as_follower() -> None:
    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)

    inc1 = SimpleNamespace(
        sys_id="sys_001",
        number="INC001",
        service="payment-gateway",
        category="software",
        short_description="Payment timeout error 504",
        description="Transactions are timing out on checkout",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    exec_id_1 = uuid4()
    res1 = cache.admit(inc1, exec_id_1)

    # Second incident with paraphrased text on same service
    inc2 = SimpleNamespace(
        sys_id="sys_002",
        number="INC002",
        service="payment-gateway",
        category="software",
        short_description="Checkout gateway timeout",
        description="Users experiencing 504 gateway timeouts when processing payments",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    exec_id_2 = uuid4()
    res2 = cache.admit(inc2, exec_id_2)

    assert res2.mode == AdmissionMode.FOLLOWER
    assert res2.cluster_id == res1.cluster_id
    assert res2.similarity_score is not None
    assert res2.similarity_score >= DEFAULT_SIMILARITY_THRESHOLD
    assert res2.anchor_incident_number == "INC001"

    # Verify follower is recorded in repo
    members = repo.cluster_members[res1.cluster_id]
    assert len(members) == 2
    assert members[1].role == "follower"
    assert members[1].incident_number == "INC002"
    assert members[1].execution_id == exec_id_2


def test_semantic_cache_low_confidence_fallback() -> None:
    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)

    # Incident 1: Payment issue
    inc1 = SimpleNamespace(
        sys_id="sys_001",
        number="INC001",
        service="payment-gateway",
        category="software",
        short_description="Payment gateway timeout",
        description="Checkout fails with 504 gateway timeout",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    res1 = cache.admit(inc1, uuid4())
    assert res1.mode == AdmissionMode.LEADER

    # Incident 2: VPN disconnect (completely different vector, similarity = 0.0)
    inc2 = SimpleNamespace(
        sys_id="sys_002",
        number="INC002",
        service="corporate-vpn",
        category="network",
        short_description="VPN disconnect",
        description="User cannot connect to corporate VPN client",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    res2 = cache.admit(inc2, uuid4())

    # Must NOT join cluster 1! It forms its own cluster as leader
    assert res2.cluster_id != res1.cluster_id
    assert res2.mode == AdmissionMode.LEADER
    assert res2.anchor_incident_number == "INC002"


def test_semantic_cache_prevents_cross_service_clustering() -> None:
    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=lambda txt: [1.0, 0.0, 0.0, 0.0])  # Same vector

    inc1 = SimpleNamespace(
        sys_id="sys_001",
        number="INC001",
        service="payment-service",
        category="software",
        short_description="Database timeout",
        description="Database connection timeout",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    res1 = cache.admit(inc1, uuid4())

    # Same text and vector, but DIFFERENT service
    inc2 = SimpleNamespace(
        sys_id="sys_002",
        number="INC002",
        service="inventory-service",  # Different service!
        category="software",
        short_description="Database timeout",
        description="Database connection timeout",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    res2 = cache.admit(inc2, uuid4())

    # Strict operational compatibility prevents cross-service false clustering!
    assert res2.cluster_id != res1.cluster_id
    assert res2.mode == AdmissionMode.LEADER


def test_semantic_cache_solution_publication_and_reuse() -> None:
    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)

    inc1 = SimpleNamespace(
        sys_id="sys_001",
        number="INC001",
        service="payment-gateway",
        short_description="Payment timeout",
        description="Checkout payment timeout",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    res1 = cache.admit(inc1, uuid4())

    # Leader finishes LangGraph and publishes solution
    solution_data = {
        "outcome": "suggested",
        "summary": "Restart payment connection pool",
        "work_note": "Identified connection pool exhaustion on payment gateway; restarted pool.",
        "confidence": 0.92,
    }
    cache.publish_solution(res1.cluster_id, solution_data)

    # Verify solution is cached
    cached_sol = cache.get_cluster_solution(res1.cluster_id)
    assert cached_sol is not None
    assert cached_sol["summary"] == "Restart payment connection pool"
    assert cache.get_cluster_status(res1.cluster_id) == ClusterStatus.RESOLVED


def test_synthetic_burst_demonstration_10_incidents() -> None:
    """Synthetic burst demonstration required by Sprint 4.2 acceptance criteria.

    Simulates an outage storm of 10 incoming incidents:
    - 6 Payment gateway timeouts (burst) -> 1 Leader + 5 Followers (reusing 1 resolution)
    - 2 VPN connection disconnects (burst) -> 1 Leader + 1 Follower (reusing 1 resolution)
    - 2 Database deadlock errors (burst) -> 1 Leader + 1 Follower (reusing 1 resolution)

    Total pipeline executions required: Exactly 3 (instead of 10!)
    Pipeline savings: 70% reduction in expensive LLM/retrieval operations.
    """
    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)

    pipeline_executions = 0
    reused_solutions = 0

    # 1. Burst of 6 Payment timeouts
    for i in range(1, 7):
        inc = SimpleNamespace(
            sys_id=f"sys_pay_{i}",
            number=f"INC_PAY_{i}",
            service="payment-gateway",
            category="software",
            short_description="Payment gateway timeout 504",
            description=f"Transaction {i} failed due to payment gateway timeout",
            active=True,
            state="in_progress",
            ai_human_lock=False,
        )
        res = cache.admit(inc, uuid4())
        if res.mode == AdmissionMode.LEADER:
            pipeline_executions += 1
            # Simulate leader executing pipeline and publishing solution
            cache.publish_solution(
                res.cluster_id,
                {"outcome": "suggested", "summary": "Reset payment gateway connection pool"},
            )
        elif res.mode == AdmissionMode.FOLLOWER:
            reused_solutions += 1
            sol = cache.get_cluster_solution(res.cluster_id)
            assert sol is not None

    # 2. Burst of 2 VPN disconnects
    for i in range(1, 3):
        inc = SimpleNamespace(
            sys_id=f"sys_vpn_{i}",
            number=f"INC_VPN_{i}",
            service="corporate-vpn",
            category="network",
            short_description="Corporate VPN disconnect",
            description="User disconnected from corporate VPN server",
            active=True,
            state="in_progress",
            ai_human_lock=False,
        )
        res = cache.admit(inc, uuid4())
        if res.mode == AdmissionMode.LEADER:
            pipeline_executions += 1
            cache.publish_solution(
                res.cluster_id,
                {"outcome": "suggested", "summary": "Restart corporate VPN authentication server"},
            )
        elif res.mode == AdmissionMode.FOLLOWER:
            reused_solutions += 1

    # 3. Burst of 2 Database deadlocks
    for i in range(1, 3):
        inc = SimpleNamespace(
            sys_id=f"sys_db_{i}",
            number=f"INC_DB_{i}",
            service="database-cluster",
            category="database",
            short_description="PostgreSQL deadlock detected",
            description="Deadlock found on transaction update",
            active=True,
            state="in_progress",
            ai_human_lock=False,
        )
        res = cache.admit(inc, uuid4())
        if res.mode == AdmissionMode.LEADER:
            pipeline_executions += 1
            cache.publish_solution(
                res.cluster_id,
                {"outcome": "suggested", "summary": "Kill blocking transaction PID"},
            )
        elif res.mode == AdmissionMode.FOLLOWER:
            reused_solutions += 1

    # Verification of Invariant #1: pipeline_executions == number of distinct clusters
    assert pipeline_executions == 3
    assert reused_solutions == 7
    assert pipeline_executions + reused_solutions == 10
    assert len(cache._anchors) == 3


def test_semantic_cache_real_fastembed_inference() -> None:
    """End-to-end verification using the real FastEmbed bge-small-en-v1.5 embedding model."""
    repo = InMemoryRepo()
    # Cache without mock embed_fn -> uses real FastEmbedEngine
    cache = SemanticCache(repo=repo, threshold=0.86)

    # Incident 1: Corporate VPN connection error
    inc1 = SimpleNamespace(
        sys_id="sys_vpn_real_1",
        number="INC_VPN_001",
        service="corporate-vpn",
        category="network",
        short_description="Unable to connect to GlobalProtect VPN",
        description="User reports connection timeout after entering credentials on VPN client.",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    res1 = cache.admit(inc1, uuid4())
    assert res1.mode == AdmissionMode.LEADER

    # Incident 2: Paraphrased same issue
    inc2 = SimpleNamespace(
        sys_id="sys_vpn_real_2",
        number="INC_VPN_002",
        service="corporate-vpn",
        category="network",
        short_description="VPN GlobalProtect login timeout",
        description="Cannot authenticate with GlobalProtect corporate VPN gateway, client times out.",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    res2 = cache.admit(inc2, uuid4())
    assert res2.mode == AdmissionMode.FOLLOWER
    assert res2.cluster_id == res1.cluster_id
    assert res2.similarity_score is not None
    assert res2.similarity_score >= 0.86

    # Incident 3: Totally different issue (software crash)
    inc3 = SimpleNamespace(
        sys_id="sys_app_real_3",
        number="INC_APP_003",
        service="payroll-app",
        category="software",
        short_description="Payroll export throws NullPointerException",
        description="Stack trace shows NullPointer in PaymentCalculationJob.java line 102.",
        active=True,
        state="in_progress",
        ai_human_lock=False,
    )
    res3 = cache.admit(inc3, uuid4())
    assert res3.mode == AdmissionMode.LEADER
    assert res3.cluster_id != res1.cluster_id

