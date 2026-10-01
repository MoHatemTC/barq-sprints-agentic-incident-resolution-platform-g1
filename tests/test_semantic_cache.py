"""Unit & integration test suite for the Semantic Caching layer.

BARQ G1 - Sprint 4 (S4.2)
Verifies:
1. Similarity Clustering: Detects when incoming incidents are semantically similar.
2. Shared Solution Reuse: Full pipeline runs once per cluster; followers reuse cached solution.
3. Low-Confidence Fallback: Explicit threshold tau enforces independent fallback.
4. Visibility & Isolation: Preserves individual incident records and prevents cross-service joins.
5. Concurrent Burst: A burst of concurrent similar incidents reuses a single resolution run.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from agent.semantic_cache import (
    DEFAULT_SIMILARITY_THRESHOLD,
    SemanticCache,
    cosine_similarity,
)
from app.models.semantic_cluster import AdmissionMode, AdmissionResult, ClusterStatus
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


def _process_worker_fn(
    worker_id: str,
    svc: str,
    out_q: Any,
    host: str,
    port: int,
    pwd: str | None,
) -> None:
    import redis as rlib

    from agent.semantic_cache import SemanticCache

    rc = rlib.Redis(host=host, port=port, password=pwd)
    c = SemanticCache(redis_client=rc, embed_fn=dummy_embed_fn)

    p = {
        "event_id": f"evt-{worker_id}",
        "sys_id": f"sys-{worker_id}",
        "number": f"INC_{worker_id}",
        "service": svc,
        "category": "software",
        "short_description": "Multi-process Redis race test incident",
        "description": "Testing multi-process admission locking across live Redis",
    }
    res = c.admit(p, uuid4())
    out_q.put((worker_id, res.mode.value, str(res.cluster_id)))


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
        description=(
            "Cannot authenticate with GlobalProtect corporate VPN gateway, client times out."
        ),
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


def test_worker_task_leader_publishes_and_follower_reuses() -> None:
    """End-to-end integration test: Celery _run_incident worker with SemanticCache.

    1. First incident runs as LEADER, invokes graph, and publishes solution to the cluster.
    2. Second similar incident arrives as FOLLOWER, reuses the published solution
       without invoking graph (pipeline execution count = 1).
    """
    from app.workers.retry_policy import RetryConfig
    from app.workers.tasks import _run_incident

    class FakeTask:
        def __init__(self) -> None:
            self.request = SimpleNamespace(retries=0)

        def retry(self, exc=None, countdown=None):
            raise RuntimeError(f"Unexpected retry with countdown={countdown}")

    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)
    cfg = RetryConfig(max_retries=3, backoff_base=1.0, backoff_max=60.0, jitter=False)

    exec1 = uuid4()
    repo.seed_execution(exec1, status="queued")
    payload1 = {
        "event_id": "evt-001",
        "sys_id": "sys_pay_01",
        "number": "INC001",
        "service": "payment-gateway",
        "category": "software",
        "short_description": "Payment timeout 504 Gateway Timeout",
        "description": "Checkout service failing with 504 Gateway Timeout on payment API",
    }

    # 1. Leader incident execution
    res1 = _run_incident(
        FakeTask(),
        payload1,
        str(exec1),
        cfg,
        repo,
        semantic_cache=cache,
        graph_backend="stub",
    )

    assert res1["status"] == "succeeded"
    assert repo.get_status(exec1) == "succeeded"

    # Verify cluster was created and marked resolved with published solution
    active_clusters = list(cache._anchors.values())
    assert len(active_clusters) == 1
    leader_cluster = active_clusters[0]
    assert leader_cluster.status == ClusterStatus.RESOLVED
    assert leader_cluster.solution is not None

    # 2. Follower incident execution (similar issue)
    exec2 = uuid4()
    repo.seed_execution(exec2, status="queued")
    payload2 = {
        "event_id": "evt-002",
        "sys_id": "sys_pay_02",
        "number": "INC002",
        "service": "payment-gateway",
        "category": "software",
        "short_description": "Payment API 504 timeout",
        "description": "Customers reporting 504 timeout on checkout payment step",
    }

    res2 = _run_incident(
        FakeTask(),
        payload2,
        str(exec2),
        cfg,
        repo,
        semantic_cache=cache,
        graph_backend="stub",
    )

    # Follower immediately resolves from cached solution!
    assert res2["status"] == "succeeded"
    assert res2["cluster_role"] == "follower"
    assert res2["cluster_id"] == str(leader_cluster.cluster_id)
    assert res2["result"] == leader_cluster.solution
    assert repo.get_status(exec2) == "succeeded"


def test_worker_task_follower_yields_when_leader_running() -> None:
    """Follower yields non-blockingly via task.retry when cluster is still RUNNING."""
    from celery.exceptions import Retry

    from app.workers.retry_policy import RetryConfig
    from app.workers.tasks import _run_incident

    class YieldingTask:
        def __init__(self) -> None:
            self.request = SimpleNamespace(retries=0)
            self.retried_countdown = None

        def retry(self, exc=None, countdown=None):
            self.retried_countdown = countdown
            raise Retry(exc=exc, when=None)

    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)
    cfg = RetryConfig(max_retries=3, backoff_base=1.0, backoff_max=60.0, jitter=False)

    # 1. Admit leader to create active cluster in RUNNING state
    exec1 = uuid4()
    repo.seed_execution(exec1, status="queued")
    payload1 = {
        "event_id": "evt-001",
        "sys_id": "sys_db_01",
        "number": "INC_DB_001",
        "service": "postgres-cluster",
        "category": "database",
        "short_description": "Postgres connection pool exhausted",
        "description": (
            "FATAL: remaining connection slots are reserved for non-replication superuser"
        ),
    }
    adm1 = cache.admit(payload1, exec1)
    assert adm1.mode == AdmissionMode.LEADER
    assert cache.get_cluster_status(adm1.cluster_id) == ClusterStatus.RUNNING

    # 2. Follower arrives while leader is still RUNNING
    exec2 = uuid4()
    repo.seed_execution(exec2, status="queued")
    payload2 = {
        "event_id": "evt-002",
        "sys_id": "sys_db_02",
        "number": "INC_DB_002",
        "service": "postgres-cluster",
        "category": "database",
        "short_description": "Postgres connection pool full",
        "description": "Database connection pool saturated with FATAL connection slots error",
    }

    task2 = YieldingTask()
    with pytest.raises(Retry):
        _run_incident(
            task2,
            payload2,
            str(exec2),
            cfg,
            repo,
            semantic_cache=cache,
            graph_backend="stub",
        )

    assert task2.retried_countdown == 2.0


def test_worker_task_follower_falls_back_when_leader_failed() -> None:
    """Follower decouples and runs independently if cluster failed."""
    from app.workers.retry_policy import RetryConfig
    from app.workers.tasks import _run_incident

    class FakeTask:
        def __init__(self) -> None:
            self.request = SimpleNamespace(retries=0)

        def retry(self, exc=None, countdown=None):
            raise RuntimeError("Should not retry")

    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)
    cfg = RetryConfig(max_retries=3, backoff_base=1.0, backoff_max=60.0, jitter=False)

    # 1. Admit leader and mark cluster as FAILED
    exec1 = uuid4()
    repo.seed_execution(exec1, status="queued")
    payload1 = {
        "event_id": "evt-001",
        "sys_id": "sys_db_01",
        "number": "INC_DB_001",
        "service": "postgres-cluster",
        "category": "database",
        "short_description": "Postgres connection pool exhausted",
        "description": "FATAL connection slots error",
    }
    adm1 = cache.admit(payload1, exec1)
    assert adm1.mode == AdmissionMode.LEADER
    cache.mark_cluster_failed(adm1.cluster_id, "LangGraph execution exploded")
    assert cache.get_cluster_status(adm1.cluster_id) == ClusterStatus.FAILED

    # 2. Similar incident arrives. Because cluster failed, it falls back to independent execution!
    exec2 = uuid4()
    repo.seed_execution(exec2, status="queued")
    payload2 = {
        "event_id": "evt-002",
        "sys_id": "sys_db_02",
        "number": "INC_DB_002",
        "service": "postgres-cluster",
        "category": "database",
        "short_description": "Postgres connection pool exhausted",
        "description": "FATAL connection slots error",
    }

    res2 = _run_incident(
        FakeTask(),
        payload2,
        str(exec2),
        cfg,
        repo,
        semantic_cache=cache,
        graph_backend="stub",
    )

    # Follower ran independently and succeeded!
    assert res2["status"] == "succeeded"
    assert repo.get_status(exec2) == "succeeded"


def test_redis_double_search_prevents_concurrent_leader_race() -> None:
    """CRITICAL INVARIANT TEST: Double-Search Distributed Lock Race Immunity.

    Simulates two independent Celery worker processes concurrently picking up
    identical incidents arriving at the exact same millisecond.
    Verifies that the Double-Search Distributed Lock pattern guarantees:
    - Exactly ONE worker becomes LEADER.
    - The other worker becomes FOLLOWER via the inside-lock second search.
    - Both share the exact same cluster_id.
    - Zero duplicate clusters created.
    """
    import threading

    class ThreadSafeMockRedis:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._kv: dict[str, str] = {}
            self._sets: dict[str, set[str]] = {}

        def set(self, key: str, val: str, nx: bool = False, px: int | None = None) -> bool:
            with self._lock:
                if nx and key in self._kv:
                    return False
                self._kv[key] = val
                return True

        def get(self, key: str) -> str | None:
            with self._lock:
                return self._kv.get(key)

        def setex(self, key: str, ttl: int, val: str) -> bool:
            with self._lock:
                self._kv[key] = val
                return True

        def sadd(self, key: str, member: str) -> int:
            with self._lock:
                if key not in self._sets:
                    self._sets[key] = set()
                self._sets[key].add(member)
                return 1

        def smembers(self, key: str) -> set[str]:
            with self._lock:
                return set(self._sets.get(key, set()))

        def srem(self, key: str, member: str) -> int:
            with self._lock:
                if key in self._sets:
                    self._sets[key].discard(member)
                return 1

        def eval(self, script: str, numkeys: int, key: str, val: str) -> int:
            # Emulates Lua release: if get(key) == val then del(key)
            with self._lock:
                if self._kv.get(key) == val:
                    del self._kv[key]
                    return 1
                return 0

    shared_redis = ThreadSafeMockRedis()
    shared_repo = InMemoryRepo()

    # Two separate cache instances (simulating two isolated worker OS processes)
    cache_worker_a = SemanticCache(
        repo=shared_repo,
        redis_client=shared_redis,
        embed_fn=dummy_embed_fn,
    )
    cache_worker_b = SemanticCache(
        repo=shared_repo,
        redis_client=shared_redis,
        embed_fn=dummy_embed_fn,
    )

    inc_payload_a = {
        "event_id": "evt-burst-01",
        "sys_id": "sys_burst_01",
        "number": "INC_BURST_001",
        "service": "checkout-api",
        "category": "software",
        "short_description": "Checkout API 504 gateway timeout",
        "description": "Massive 504 timeouts on checkout payment gateway",
    }
    inc_payload_b = {
        "event_id": "evt-burst-02",
        "sys_id": "sys_burst_02",
        "number": "INC_BURST_002",
        "service": "checkout-api",
        "category": "software",
        "short_description": "Checkout API 504 gateway timeout",
        "description": "Massive 504 timeouts on checkout payment gateway",
    }

    results: list[AdmissionResult] = []
    barrier = threading.Barrier(2)

    def worker_admission(cache_instance: SemanticCache, payload: dict, exec_id: UUID) -> None:
        # Synchronize thread start to ensure exact concurrent arrival
        barrier.wait()
        res = cache_instance.admit(payload, exec_id)
        results.append(res)

    t1 = threading.Thread(target=worker_admission, args=(cache_worker_a, inc_payload_a, uuid4()))
    t2 = threading.Thread(target=worker_admission, args=(cache_worker_b, inc_payload_b, uuid4()))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    modes = [r.mode for r in results]

    # Non-negotiable invariant: Exactly one Leader, exactly one Follower!
    assert AdmissionMode.LEADER in modes, "Must elect exactly one leader"
    assert AdmissionMode.FOLLOWER in modes, "Second worker must join as follower"

    leader_res = next(r for r in results if r.mode == AdmissionMode.LEADER)
    follower_res = next(r for r in results if r.mode == AdmissionMode.FOLLOWER)

    # Both workers must reference the exact same cluster!
    assert follower_res.cluster_id == leader_res.cluster_id
    assert follower_res.similarity_score == 1.0

    # Exactly 1 cluster created in shared PostgreSQL / Repo!
    assert len(shared_repo.clusters) == 1


def test_admission_lock_timeout_yields_retry_without_duplicate_leader() -> None:
    """Verifies that if admission lock acquisition times out under contention,
    the worker task yields non-blockingly via task.retry rather than electing a duplicate leader.
    """
    from celery.exceptions import Retry

    from app.workers.retry_policy import RetryConfig
    from app.workers.tasks import _run_incident

    class ContendedRedis:
        def set(self, key: str, val: str, nx: bool = False, px: int | None = None) -> bool:
            # Always fail to acquire lock to simulate heavy contention timeout
            return False

    class YieldingTask:
        def __init__(self) -> None:
            self.request = SimpleNamespace(retries=0)
            self.retried_countdown = None

        def retry(self, exc=None, countdown=None):
            self.retried_countdown = countdown
            raise Retry(exc=exc, when=None)

    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, redis_client=ContendedRedis(), embed_fn=dummy_embed_fn)
    cfg = RetryConfig(max_retries=3, backoff_base=1.0, backoff_max=60.0, jitter=False)

    exec_id = uuid4()
    repo.seed_execution(exec_id, status="queued")
    payload = {
        "event_id": "evt-timeout-01",
        "sys_id": "sys_timeout_01",
        "number": "INC_TIMEOUT_001",
        "service": "billing-engine",
        "category": "software",
        "short_description": "Billing engine deadlock",
        "description": "Database deadlock on account ledger updates",
    }

    task = YieldingTask()
    with pytest.raises(Retry):
        _run_incident(
            task,
            payload,
            str(exec_id),
            cfg,
            repo,
            semantic_cache=cache,
            graph_backend="stub",
        )

    # Yielded for 1.0s non-blocking retry
    assert task.retried_countdown == 1.0
    # ZERO clusters or duplicate leaders created!
    assert len(repo.clusters) == 0


def test_redis_loss_recovery_from_postgresql_authoritative_source() -> None:
    """Verifies that PostgreSQL is the authoritative source of truth.
    If Redis active anchors are lost (e.g. Redis restart / wipe), the cache
    rehydrates active clusters from PostgreSQL on the next admission check.
    """
    shared_repo = InMemoryRepo()

    redis_store: dict[str, Any] = {}

    class FakeRedisStore:
        def set(self, key: str, val: str, nx: bool = False, px: int | None = None) -> bool:
            redis_store[key] = val
            return True

        def get(self, key: str) -> str | None:
            return redis_store.get(key)

        def setex(self, key: str, ttl: int, val: str) -> bool:
            redis_store[key] = val
            return True

        def sadd(self, key: str, member: str) -> int:
            redis_store.setdefault(key, set()).add(member)
            return 1

        def smembers(self, key: str) -> set[str]:
            return set(redis_store.get(key, set()))

        def srem(self, key: str, member: str) -> int:
            if key in redis_store and isinstance(redis_store[key], set):
                redis_store[key].discard(member)
            return 1

        def eval(self, script: str, numkeys: int, key: str, val: str) -> int:
            redis_store.pop(key, None)
            return 1

    shared_redis = FakeRedisStore()
    cache1 = SemanticCache(repo=shared_repo, redis_client=shared_redis, embed_fn=dummy_embed_fn)

    exec1 = uuid4()
    shared_repo.seed_execution(exec1, status="queued")
    payload1 = {
        "event_id": "evt-resilient-01",
        "sys_id": "sys_resilient_01",
        "number": "INC_RES_001",
        "service": "order-pipeline",
        "category": "software",
        "short_description": "Kafka consumer group rebalance storm",
        "description": "Kafka partitions constantly rebalancing across order-workers",
    }
    res1 = cache1.admit(payload1, exec1)
    assert res1.mode == AdmissionMode.LEADER
    assert len(shared_repo.clusters) == 1

    # 2. Redis restarts! (All Redis keys wiped completely)
    redis_store.clear()

    # 3. Fresh Worker 2 instance (empty in-process cache, empty Redis)
    cache2 = SemanticCache(repo=shared_repo, redis_client=shared_redis, embed_fn=dummy_embed_fn)

    payload2 = {
        "event_id": "evt-resilient-02",
        "sys_id": "sys_resilient_02",
        "number": "INC_RES_002",
        "service": "order-pipeline",
        "category": "software",
        "short_description": "Kafka consumer rebalance storm",
        "description": "Partitions rebalancing repeatedly across consumers",
    }
    exec2 = uuid4()
    shared_repo.seed_execution(exec2, status="queued")

    # Cache rehydrates from PostgreSQL and joins as FOLLOWER!
    res2 = cache2.admit(payload2, exec2)
    assert res2.mode == AdmissionMode.FOLLOWER
    assert res2.cluster_id == res1.cluster_id
    assert len(shared_repo.clusters) == 1
    # Redis active index was restored from PostgreSQL!
    assert "barq:cluster:active_set" in redis_store


def test_live_multiprocess_redis_race_prevents_duplicate_leaders() -> None:
    """CRITICAL MULTI-PROCESS INVARIANT TEST:
    Executes across 2 truly separate OS processes against the live Redis instance.
    Verifies that the Double-Search Distributed Lock pattern guarantees race immunity
    across separate operating system process heaps.
    """
    import multiprocessing
    import os
    import queue

    import redis as live_redis_lib

    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", 6379))
    pw = os.environ.get("REDIS_PASSWORD") or None

    # Verify live Redis is reachable
    try:
        r = live_redis_lib.Redis(host=host, port=port, password=pw)
        if not r.ping():
            pytest.skip("Live Redis not reachable")
    except Exception:
        pytest.skip("Live Redis not reachable or credentials unconfigured")

    test_service = f"mp-test-{uuid4().hex[:8]}"
    lock_key = f"barq:lock:admission:{test_service}"
    r.delete(lock_key)

    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()

    p1 = ctx.Process(target=_process_worker_fn, args=("W1", test_service, q, host, port, pw))
    p2 = ctx.Process(target=_process_worker_fn, args=("W2", test_service, q, host, port, pw))

    p1.start()
    p2.start()
    p1.join(timeout=10)
    p2.join(timeout=10)

    # Read exactly 2 results from queue with blocking timeout (avoids queue.empty race)
    res_list = []
    for _ in range(2):
        try:
            res_list.append(q.get(timeout=5.0))
        except queue.Empty:
            break

    assert len(res_list) == 2, f"Expected 2 results from OS processes, got {len(res_list)}"
    modes = [m for _, m, _ in res_list]
    cluster_ids = [cid for _, _, cid in res_list]

    assert "leader" in modes, "Exactly one OS process must become LEADER"
    assert "follower" in modes, "The other OS process must become FOLLOWER"
    assert cluster_ids[0] == cluster_ids[1], "Both processes must share the exact same cluster_id"

    # Cleanup Redis test keys
    for cid in cluster_ids:
        r.delete(f"barq:cluster:anchor:{cid}")
        r.srem("barq:cluster:active_set", cid)
    r.delete(lock_key)


def test_retrying_leader_resumes_as_leader() -> None:
    """Issue 6: A retry/redelivery of the original leader execution must match its own anchor

    and resume as LEADER rather than being misclassified as a FOLLOWER.
    """
    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)

    leader_exec_id = uuid4()
    repo.seed_execution(leader_exec_id, status="running")

    payload = {
        "event_id": "evt-leader-retry-01",
        "sys_id": "sys_leader_retry_01",
        "number": "INC_LDR_001",
        "service": "payment-api",
        "category": "software",
        "short_description": "Payment timeout gateway error",
        "description": "504 gateway timeout on payments",
    }

    # First admission: elected as LEADER
    res1 = cache.admit(payload, leader_exec_id)
    assert res1.mode == AdmissionMode.LEADER
    cluster_id = res1.cluster_id

    # Simulation: Leader task retries with the SAME execution ID
    res_retry = cache.admit(payload, leader_exec_id)
    assert res_retry.mode == AdmissionMode.LEADER
    assert res_retry.cluster_id == cluster_id
    assert res_retry.reason == "retrying_leader_execution"

    # A genuinely different execution still joins as FOLLOWER
    follower_exec_id = uuid4()
    repo.seed_execution(follower_exec_id, status="queued")
    res_follower = cache.admit(payload, follower_exec_id)
    assert res_follower.mode == AdmissionMode.FOLLOWER
    assert res_follower.cluster_id == cluster_id


def test_stale_redis_anchor_cannot_cause_reuse_of_failed_cluster() -> None:
    """Issue 3: If PostgreSQL says a cluster is FAILED, it must not remain usable

    merely because Redis still contains an anchor. Stale anchor must be evicted.
    """
    repo = InMemoryRepo()
    redis_store: dict[str, Any] = {}

    class TestRedisClient:
        def get(self, key: str) -> str | None:
            return redis_store.get(key)

        def setex(self, key: str, ttl: int, val: str) -> bool:
            redis_store[key] = val
            return True

        def sadd(self, key: str, member: str) -> int:
            redis_store.setdefault(key, set()).add(member)
            return 1

        def smembers(self, key: str) -> set[str]:
            return set(redis_store.get(key, set()))

        def srem(self, key: str, member: str) -> int:
            if key in redis_store and isinstance(redis_store[key], set):
                redis_store[key].discard(member)
            return 1

        def delete(self, *keys: str) -> int:
            for k in keys:
                redis_store.pop(k, None)
            return 1

        def set(self, key: str, val: str, **kwargs: Any) -> bool:
            return True

        def eval(self, *args: Any, **kwargs: Any) -> int:
            return 1

    fake_redis = TestRedisClient()
    cache = SemanticCache(repo=repo, redis_client=fake_redis, embed_fn=dummy_embed_fn)

    # 1. Create a cluster as leader
    exec1 = uuid4()
    repo.seed_execution(exec1, status="running")
    payload1 = {
        "event_id": "evt-failed-01",
        "sys_id": "sys_failed_01",
        "number": "INC_FAIL_001",
        "service": "billing",
        "category": "software",
        "short_description": "Payment gateway timeout",
        "description": "504 gateway timeout",
    }
    res1 = cache.admit(payload1, exec1)
    assert res1.mode == AdmissionMode.LEADER
    failed_cluster_id = res1.cluster_id

    # 2. In PostgreSQL, mark cluster as failed (e.g. fatal unhandled error in worker)
    repo.update_cluster_status(failed_cluster_id, status="failed", failure_reason="OOMKilled")

    # 3. Simulate stale state: Redis anchor was NOT cleared (or was out-of-sync)
    # The anchor key remains in redis_store and active_set
    assert f"barq:cluster:anchor:{failed_cluster_id}" in redis_store

    # 4. Inbound incident arrives from another execution
    exec2 = uuid4()
    repo.seed_execution(exec2, status="queued")
    payload2 = {
        "event_id": "evt-failed-02",
        "sys_id": "sys_failed_02",
        "number": "INC_FAIL_002",
        "service": "billing",
        "category": "software",
        "short_description": "Payment gateway timeout",
        "description": "504 gateway timeout",
    }

    # Must NOT join the failed cluster! Must evict stale anchor and elect new leader
    res2 = cache.admit(payload2, exec2)
    assert res2.mode == AdmissionMode.LEADER
    assert res2.cluster_id != failed_cluster_id
    # Stale anchor was evicted from Redis active_set
    assert str(failed_cluster_id) not in redis_store.get("barq:cluster:active_set", set())


def test_membership_registration_failure_propagates() -> None:
    """Issue 4: Actual repository failures during follower registration must not be swallowed."""
    repo = InMemoryRepo()
    cache = SemanticCache(repo=repo, embed_fn=dummy_embed_fn)

    # 1. Create leader cluster
    exec1 = uuid4()
    repo.seed_execution(exec1, status="running")
    payload1 = {
        "event_id": "evt-mem-01",
        "sys_id": "sys_mem_01",
        "number": "INC_MEM_001",
        "service": "auth",
        "category": "software",
        "short_description": "VPN authentication timeout",
        "description": "RADIUS timeout",
    }
    cache.admit(payload1, exec1)

    # 2. Poison add_cluster_member to raise a database operational error
    def _exploding_add_member(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("OperationalError: connection to PostgreSQL server lost")

    repo.add_cluster_member = _exploding_add_member  # type: ignore[method-assign]

    # 3. Inbound follower incident must raise RuntimeError, not silently return FOLLOWER
    exec2 = uuid4()
    repo.seed_execution(exec2, status="queued")
    payload2 = {
        "event_id": "evt-mem-02",
        "sys_id": "sys_mem_02",
        "number": "INC_MEM_002",
        "service": "auth",
        "category": "software",
        "short_description": "VPN authentication timeout",
        "description": "RADIUS timeout",
    }

    with pytest.raises(RuntimeError, match="connection to PostgreSQL server lost"):
        cache.admit(payload2, exec2)


def test_postgres_commit_before_redis_publication() -> None:
    """Issue 5: Ensure cluster is created/committed in PostgreSQL

    before Redis anchor is published.
    """
    repo = InMemoryRepo()
    call_log: list[str] = []

    class OrderTrackingRedis:
        def __init__(self) -> None:
            self.store: dict[str, Any] = {}

        def get(self, key: str) -> str | None:
            return self.store.get(key)

        def setex(self, key: str, ttl: int, val: str) -> bool:
            call_log.append("redis_setex")
            self.store[key] = val
            return True

        def sadd(self, key: str, member: str) -> int:
            call_log.append("redis_sadd")
            self.store.setdefault(key, set()).add(member)
            return 1

        def smembers(self, key: str) -> set[str]:
            return set(self.store.get(key, set()))

        def srem(self, key: str, member: str) -> int:
            return 1

        def set(self, *args: Any, **kwargs: Any) -> bool:
            return True

        def eval(self, *args: Any, **kwargs: Any) -> int:
            return 1

    orig_create = repo.create_cluster

    def _logged_create(*args: Any, **kwargs: Any) -> Any:
        call_log.append("postgres_create_cluster")
        return orig_create(*args, **kwargs)

    repo.create_cluster = _logged_create  # type: ignore[method-assign]
    fake_redis = OrderTrackingRedis()
    cache = SemanticCache(repo=repo, redis_client=fake_redis, embed_fn=dummy_embed_fn)

    exec1 = uuid4()
    repo.seed_execution(exec1, status="running")
    payload = {
        "event_id": "evt-order-01",
        "sys_id": "sys_order_01",
        "number": "INC_ORD_001",
        "service": "database",
        "category": "software",
        "short_description": "Postgres deadlock detected",
        "description": "Transaction aborted due to deadlock",
    }

    cache.admit(payload, exec1)

    assert "postgres_create_cluster" in call_log
    assert "redis_setex" in call_log
    # PostgreSQL must be called BEFORE Redis publication
    pg_idx = call_log.index("postgres_create_cluster")
    redis_idx = call_log.index("redis_setex")
    assert pg_idx < redis_idx, f"PostgreSQL ({pg_idx}) must precede Redis publication ({redis_idx})"
