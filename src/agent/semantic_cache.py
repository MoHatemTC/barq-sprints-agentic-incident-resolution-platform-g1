"""Semantic Caching & Single-Flight Clustering Engine for Concurrent Similar Incidents.

BARQ G1 - Sprint 4 (S4.2)
Detects semantic similarity among incidents arriving in close temporal proximity,
groups matching incidents into a cluster and shares a resolution candidate. Each
production follower runs its own diagnosis, evidence and safety gates, approval
when required, and incident write; cache reuse skips only draft generation.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy.exc import IntegrityError

from app.models.semantic_cluster import (
    AdmissionMode,
    AdmissionResult,
    ClusterStatus,
)
from app.services.clustering.signature import (
    _get_field,
    build_incident_signature,
    is_incident_cluster_eligible,
)
from app.workers.db import WorkerRepo

logger = structlog.getLogger(__name__)

# Calibrated similarity threshold – empirically validated by
# eval/calibrate_threshold.py (BAAI/bge-small-en-v1.5, 38 pairs):
#
#   Positive cluster (same root cause)    : n=21  min=0.7677  max=0.9646  mean=0.8772
#   Negative cluster (different incident) : n=17  min=0.4296  max=0.7398  mean=0.5959
#   Separation gap (min_pos - max_neg)    : +0.0280  (clean, no overlap)
#
#   Perfect accuracy band (TP=21, TN=17, FP=0, FN=0) : tau in [0.74, 0.76]
#   Chosen tau = 0.76 – midpoint of the perfect band, giving:
#     - 0.028 buffer above max_negative  (0.76 - 0.740 = 0.020 gap)
#     - 0.008 buffer below min_positive  (0.768 - 0.76 = 0.008 gap)
#   Re-run eval/calibrate_threshold.py to re-validate when pairs are added.
DEFAULT_SIMILARITY_THRESHOLD: float = 0.76
DEFAULT_TTL_SECONDS: int = 600  # 10 minutes cache window for outage bursts

# Redis Key Conventions
REDIS_ANCHOR_PREFIX = "barq:cluster:anchor:"
REDIS_ACTIVE_SET = "barq:cluster:active_set"
REDIS_LOCK_PREFIX = "barq:lock:admission:"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot_product = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot_product / (norm_a * norm_b))


class CachedClusterAnchor:
    """In-memory and Redis-serializable representation of an active cluster anchor."""

    def __init__(
        self,
        cluster_id: UUID,
        anchor_incident_sys_id: str,
        anchor_incident_number: str,
        anchor_execution_id: UUID,
        pipeline_execution_id: UUID,
        vector: list[float],
        service: str | None,
        category: str | None,
        status: ClusterStatus,
        expires_at: dt.datetime,
        solution: dict[str, Any] | None = None,
        created_at: dt.datetime | None = None,
    ) -> None:
        self.cluster_id = cluster_id
        self.anchor_incident_sys_id = anchor_incident_sys_id
        self.anchor_incident_number = anchor_incident_number
        self.anchor_execution_id = anchor_execution_id
        self.pipeline_execution_id = pipeline_execution_id
        self.vector = vector
        self.service = service.strip().lower() if service else None
        self.category = category.strip().lower() if category else None
        self.status = status
        self.expires_at = expires_at
        self.solution = solution
        self.created_at = created_at or _utcnow()
        self.failure_reason: str | None = None

    @property
    def is_expired(self) -> bool:
        return _utcnow() > self.expires_at

    @property
    def is_active(self) -> bool:
        return not self.is_expired and self.status in (
            ClusterStatus.CREATING,
            ClusterStatus.RUNNING,
            ClusterStatus.AWAITING_APPROVAL,
            ClusterStatus.RESOLVED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": str(self.cluster_id),
            "anchor_incident_sys_id": self.anchor_incident_sys_id,
            "anchor_incident_number": self.anchor_incident_number,
            "anchor_execution_id": str(self.anchor_execution_id),
            "pipeline_execution_id": str(self.pipeline_execution_id),
            "vector": self.vector,
            "service": self.service,
            "category": self.category,
            "status": self.status.value,
            "expires_at": self.expires_at.isoformat(),
            "solution": self.solution,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CachedClusterAnchor:
        anchor = cls(
            cluster_id=UUID(d["cluster_id"]),
            anchor_incident_sys_id=d["anchor_incident_sys_id"],
            anchor_incident_number=d["anchor_incident_number"],
            anchor_execution_id=UUID(d["anchor_execution_id"]),
            pipeline_execution_id=UUID(d["pipeline_execution_id"]),
            vector=d["vector"],
            service=d.get("service"),
            category=d.get("category"),
            status=ClusterStatus(d["status"]),
            expires_at=dt.datetime.fromisoformat(d["expires_at"]),
            solution=d.get("solution"),
            created_at=dt.datetime.fromisoformat(d["created_at"]) if "created_at" in d else None,
        )
        anchor.failure_reason = d.get("failure_reason")
        return anchor


class AdmissionLockTimeoutError(Exception):
    """Raised when distributed admission lock acquisition times out under high contention."""


_worker_embedding_engine: Any = None
_worker_engine_lock = threading.Lock()


def _get_worker_embedding_engine() -> Any:
    global _worker_embedding_engine
    if _worker_embedding_engine is None:
        with _worker_engine_lock:
            if _worker_embedding_engine is None:
                from app.retrieval.embedding import FastEmbedEngine

                _worker_embedding_engine = FastEmbedEngine()
    return _worker_embedding_engine


class SemanticCache:
    """Coordinates clustering, single-flight execution, and shared resolution reuse.

    Employs the Double-Search Distributed Lock Pattern across Redis and PostgreSQL to
    guarantee that concurrent Celery worker processes cannot elect duplicate leaders
    for the same semantic incident burst.
    """

    def __init__(
        self,
        repo: WorkerRepo | None = None,
        redis_client: Any = None,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self.repo = repo
        self.redis_client = redis_client
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self._anchors: dict[UUID, CachedClusterAnchor] = {}
        self._embed_fn = embed_fn
        self._local_lock = threading.Lock()

    def _embed(self, text: str) -> list[float]:
        """Generate embedding vector for text using FastEmbedEngine or custom callable."""
        if self._embed_fn is not None:
            return self._embed_fn(text)
        engine = _get_worker_embedding_engine()
        res = engine.embed_query(text)
        return res.dense

    @contextlib.contextmanager
    def _distributed_lock(
        self, service: str | None, ttl_seconds: float = 15.0, timeout: float = 5.0
    ):
        """Acquire a distributed Redis lock or local fallback lock with Lua token release."""
        if self.redis_client is None:
            with self._local_lock:
                yield
            return

        lock_key = f"{REDIS_LOCK_PREFIX}{service}" if service else f"{REDIS_LOCK_PREFIX}global"
        lock_token = str(uuid4())
        start_time = time.monotonic()
        acquired = False

        while time.monotonic() - start_time < timeout:
            try:
                # Redis SET lock_key token NX PX ttl_ms
                if self.redis_client.set(lock_key, lock_token, nx=True, px=int(ttl_seconds * 1000)):
                    acquired = True
                    break
            except Exception as exc:
                logger.warning("redis_lock_acquire_failed", error=str(exc))
                break
            time.sleep(0.02)

        if not acquired:
            logger.warning("redis_admission_lock_timeout", lock_key=lock_key, timeout=timeout)
            raise AdmissionLockTimeoutError(
                f"Timeout ({timeout}s) waiting for admission lock '{lock_key}'"
            )

        try:
            yield
        finally:
            # Lua script guarantees we only delete the lock if our token matches
            lua_release = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            try:
                self.redis_client.eval(lua_release, 1, lock_key, lock_token)
            except Exception as exc:
                logger.warning("redis_lock_release_failed", error=str(exc))

    def _evict_stale_anchor(self, cluster_id: UUID) -> None:
        """Evict a cluster anchor from Redis and local cache when invalidated by PostgreSQL."""
        cid_str = str(cluster_id)
        self._anchors.pop(cluster_id, None)
        if self.redis_client is not None:
            try:
                self.redis_client.srem(REDIS_ACTIVE_SET, cid_str)
                self.redis_client.delete(f"{REDIS_ANCHOR_PREFIX}{cid_str}")
            except Exception as exc:
                logger.warning("redis_stale_anchor_evict_failed", error=str(exc))

    def _is_cluster_authoritatively_active(self, cluster_id: UUID) -> bool:
        """Verify candidate cluster is currently active in PostgreSQL source of truth."""
        if self.repo is None:
            return True
        cluster = self.repo.get_cluster(cluster_id)
        if cluster is None:
            return False
        if cluster.status in ("failed", "expired"):
            return False
        if cluster.expires_at and cluster.expires_at <= _utcnow():
            return False
        return True

    def _get_active_anchors(
        self, service: str | None = None, refresh_from_db: bool = False
    ) -> list[CachedClusterAnchor]:
        """Fetch active cluster anchors from Redis, PostgreSQL, and memory, purging expired.

        Parameters
        ----------
        service:
            Normalized service name to filter by.
        refresh_from_db:
            If False (fast path), reads shared anchors from Redis (and local cache) without
            hitting PostgreSQL. If True (authoritative path, used inside double-search or on miss),
            reconciles Redis anchors against PostgreSQL and rehydrates any missing active clusters.
        """
        active: dict[UUID, CachedClusterAnchor] = {}

        # 1. Fetch shared anchors from Redis across all worker processes
        if self.redis_client is not None:
            try:
                raw_ids = self.redis_client.smembers(REDIS_ACTIVE_SET) or set()
                for raw_id in raw_ids:
                    cid_str = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
                    key = f"{REDIS_ANCHOR_PREFIX}{cid_str}"
                    data = self.redis_client.get(key)
                    if data:
                        raw_json = data.decode() if isinstance(data, bytes) else str(data)
                        anchor = CachedClusterAnchor.from_dict(json.loads(raw_json))
                        if anchor.is_active:
                            active[anchor.cluster_id] = anchor
                            self._anchors[anchor.cluster_id] = anchor
                        else:
                            self.redis_client.srem(REDIS_ACTIVE_SET, cid_str)
                    else:
                        # TTL expired in Redis
                        self.redis_client.srem(REDIS_ACTIVE_SET, cid_str)
            except Exception as exc:
                logger.warning("redis_active_anchors_fetch_failed", error=str(exc))

        # 2. Authoritative PostgreSQL reconciliation / rehydration
        # (on miss, fallback, or explicit refresh)
        should_query_db = self.repo is not None and (refresh_from_db or not active)
        if should_query_db and self.repo is not None:
            try:
                db_active_clusters = self.repo.list_active_clusters(service=service)
                db_active_ids = {c.cluster_id for c in db_active_clusters}

                # Prune stale Redis/local anchors that PostgreSQL says are no longer active
                if refresh_from_db:
                    stale_cids = [cid for cid in list(active.keys()) if cid not in db_active_ids]
                    for stale_cid in stale_cids:
                        active.pop(stale_cid, None)
                        self._evict_stale_anchor(stale_cid)

                for c in db_active_clusters:
                    vec = getattr(c, "anchor_vector", None)
                    if c.cluster_id not in active and vec is not None:
                        rehydrated = CachedClusterAnchor(
                            cluster_id=c.cluster_id,
                            anchor_incident_sys_id=c.anchor_incident_sys_id,
                            anchor_incident_number=c.anchor_incident_number,
                            anchor_execution_id=c.anchor_execution_id,
                            pipeline_execution_id=c.pipeline_execution_id,
                            vector=vec,
                            service=c.service,
                            category=c.category,
                            status=ClusterStatus(c.status),
                            expires_at=c.expires_at,
                            solution=c.solution,
                            created_at=c.created_at,
                        )
                        active[c.cluster_id] = rehydrated
                        self._anchors[c.cluster_id] = rehydrated
                        # Re-publish back to Redis to restore coordination index
                        if self.redis_client is not None:
                            try:
                                cid_str = str(c.cluster_id)
                                remaining_ttl = max(
                                    10, int((c.expires_at - _utcnow()).total_seconds())
                                )
                                self.redis_client.setex(
                                    f"{REDIS_ANCHOR_PREFIX}{cid_str}",
                                    remaining_ttl,
                                    json.dumps(rehydrated.to_dict()),
                                )
                                self.redis_client.sadd(REDIS_ACTIVE_SET, cid_str)
                            except Exception:
                                pass
            except Exception as exc:
                logger.warning("repo_active_clusters_fetch_failed", error=str(exc))

        # 3. Merge with in-process anchors
        for cid, anchor in list(self._anchors.items()):
            if anchor.is_active and cid not in active:
                active[cid] = anchor

        return list(active.values())

    def _find_matching_candidate(
        self,
        vector: list[float],
        norm_service: str | None,
        anchors: list[CachedClusterAnchor],
    ) -> tuple[CachedClusterAnchor | None, float]:
        """Find the best matching cluster anchor honoring operational service boundaries."""
        best_candidate: CachedClusterAnchor | None = None
        best_similarity: float = -1.0

        for anchor in anchors:
            if not anchor.is_active:
                continue

            # Strict operational compatibility: if both define service, they must match
            if norm_service and anchor.service and norm_service != anchor.service:
                continue

            sim = cosine_similarity(vector, anchor.vector)
            if sim > best_similarity:
                best_similarity = sim
                best_candidate = anchor

        return best_candidate, best_similarity

    def _join_as_follower(
        self,
        candidate: CachedClusterAnchor,
        execution_id: UUID,
        inc_sys_id: str,
        inc_number: str,
        similarity: float,
    ) -> AdmissionResult:
        """Register follower membership in PostgreSQL and return AdmissionResult."""
        cluster_id = candidate.cluster_id
        if self.repo is not None:
            try:
                self.repo.add_cluster_member(
                    cluster_id=cluster_id,
                    execution_id=execution_id,
                    incident_sys_id=inc_sys_id,
                    incident_number=inc_number,
                    similarity_score=similarity,
                    role="follower",
                )
            except IntegrityError:
                # Idempotent re-entry: member already registered in database
                logger.info(
                    "cluster_member_already_registered",
                    cluster_id=str(cluster_id),
                    execution_id=str(execution_id),
                )
            except ValueError as exc:
                # InMemoryRepo raises ValueError for duplicates
                if "already in a cluster" in str(exc):
                    logger.info(
                        "cluster_member_already_registered_in_memory",
                        cluster_id=str(cluster_id),
                        execution_id=str(execution_id),
                    )
                else:
                    raise
            except Exception as exc:
                logger.error(
                    "cluster_member_registration_failed",
                    cluster_id=str(cluster_id),
                    error=str(exc),
                )
                raise

        return AdmissionResult(
            mode=AdmissionMode.FOLLOWER,
            cluster_id=cluster_id,
            similarity_score=round(similarity, 4),
            reason=f"matched_active_cluster (similarity={similarity:.4f} >= {self.threshold})",
            anchor_incident_sys_id=candidate.anchor_incident_sys_id,
            anchor_incident_number=candidate.anchor_incident_number,
        )

    def _create_as_leader(
        self,
        execution_id: UUID,
        inc_sys_id: str,
        inc_number: str,
        vector: list[float],
        norm_service: str | None,
        inc_category: Any,
    ) -> AdmissionResult:
        """Create new cluster as LEADER:
        durably commit in PostgreSQL FIRST, then publish to Redis.
        """
        cluster_id = uuid4()
        now = _utcnow()
        expires_at = now + dt.timedelta(seconds=self.ttl_seconds)

        new_anchor = CachedClusterAnchor(
            cluster_id=cluster_id,
            anchor_incident_sys_id=inc_sys_id,
            anchor_incident_number=inc_number,
            anchor_execution_id=execution_id,
            pipeline_execution_id=execution_id,
            vector=vector,
            service=norm_service,
            category=str(inc_category).strip().lower() if inc_category else None,
            status=ClusterStatus.RUNNING,
            expires_at=expires_at,
        )

        # 1. Durably persist cluster in authoritative PostgreSQL FIRST
        if self.repo is not None:
            try:
                self.repo.create_cluster(
                    cluster_id=cluster_id,
                    anchor_incident_sys_id=inc_sys_id,
                    anchor_incident_number=inc_number,
                    anchor_execution_id=execution_id,
                    pipeline_execution_id=execution_id,
                    similarity_threshold=self.threshold,
                    embedding_model="BAAI/bge-small-en-v1.5",
                    service=norm_service,
                    category=new_anchor.category,
                    expires_at=expires_at,
                    anchor_vector=vector,
                )
            except Exception as exc:
                logger.error(
                    "cluster_creation_failed_in_repo",
                    cluster_id=str(cluster_id),
                    error=str(exc),
                )
                raise

        if self.repo is not None:
            self.repo.update_cluster_status(cluster_id, "running")

        # 2. Record locally and publish to Redis for instant cross-worker coordination
        self._anchors[cluster_id] = new_anchor
        if self.redis_client is not None:
            try:
                cid_str = str(cluster_id)
                self.redis_client.setex(
                    f"{REDIS_ANCHOR_PREFIX}{cid_str}",
                    self.ttl_seconds,
                    json.dumps(new_anchor.to_dict()),
                )
                self.redis_client.sadd(REDIS_ACTIVE_SET, cid_str)
            except Exception as exc:
                logger.warning("redis_anchor_registration_failed", error=str(exc))

        return AdmissionResult(
            mode=AdmissionMode.LEADER,
            cluster_id=cluster_id,
            similarity_score=1.0,
            reason="new_cluster_created_as_leader",
            anchor_incident_sys_id=inc_sys_id,
            anchor_incident_number=inc_number,
        )

    def admit(
        self,
        incident: Any,
        execution_id: UUID,
    ) -> AdmissionResult:
        """Evaluate an inbound incident for semantic cluster admission using Double-Search.

        Flow:
        1. Eligibility check (inactive, closed, locked, or empty text -> INDEPENDENT).
        2. Vector embedding via FastEmbed.
        3. FIRST SEARCH (Fast-path): Check active anchors without hitting PostgreSQL.
           If candidate matches:
             - If it is the original leader retrying -> resume as LEADER.
             - If authoritatively active in PostgreSQL -> join as FOLLOWER.
             - If stale in Redis -> evict and continue to lock.
        4. CRITICAL SECTION: Acquire Redis distributed lock (with 15s lease duration).
        5. DOUBLE SEARCH (Inside Lock): Authoritatively reconcile anchors against PostgreSQL.
        6. If match found inside lock -> FOLLOWER (or LEADER if retrying leader).
           If still none -> commit in PostgreSQL FIRST, publish to Redis, return LEADER.
        """
        # 1. Eligibility gate (closed, inactive, human-locked, or empty text)
        if not is_incident_cluster_eligible(incident):
            logger.info("incident_cluster_ineligible", execution_id=str(execution_id))
            return AdmissionResult(
                mode=AdmissionMode.INDEPENDENT,
                reason="incident_ineligible_for_clustering",
            )

        if self.repo is not None:
            existing = self.repo.get_cluster_for_execution(execution_id)
            if existing is not None:
                if existing.expires_at <= _utcnow() or existing.status in ("failed", "expired"):
                    return AdmissionResult(
                        mode=AdmissionMode.INDEPENDENT, reason="previous_cluster_unusable"
                    )
                return AdmissionResult(
                    mode=AdmissionMode.LEADER
                    if existing.anchor_execution_id == execution_id
                    else AdmissionMode.FOLLOWER,
                    cluster_id=existing.cluster_id,
                    similarity_score=1.0,
                    reason="retrying_leader_execution"
                    if existing.anchor_execution_id == execution_id
                    else "existing_follower_execution",
                    anchor_incident_sys_id=existing.anchor_incident_sys_id,
                    anchor_incident_number=existing.anchor_incident_number,
                )

        # 2. Build deterministic signature
        signature = build_incident_signature(incident)
        if not signature.strip():
            return AdmissionResult(
                mode=AdmissionMode.INDEPENDENT,
                reason="empty_incident_signature",
            )

        # 3. Generate embedding vector
        vector = self._embed(signature)

        inc_sys_id = str(_get_field(incident, "sys_id", "") or "")
        inc_number = str(_get_field(incident, "number", "") or "")
        inc_service = _get_field(incident, "service", None)
        inc_category = _get_field(incident, "category", None)
        norm_service = str(inc_service).strip().lower() if inc_service else None

        # 4. FIRST SEARCH: Uncontended check across active anchors (fast path, no N+1 DB query)
        anchors = self._get_active_anchors(service=norm_service, refresh_from_db=False)
        best_candidate, best_similarity = self._find_matching_candidate(
            vector, norm_service, anchors
        )

        logger.info(
            "semantic_cache_first_search",
            execution_id=str(execution_id),
            best_similarity=round(best_similarity, 4) if best_similarity >= 0 else None,
            threshold=self.threshold,
            matched=best_similarity >= self.threshold,
        )

        if best_candidate is not None and best_similarity >= self.threshold:
            # Check if this is the original leader retrying
            if best_candidate.anchor_execution_id == execution_id:
                logger.info(
                    "admit_leader_retry_detected",
                    execution_id=str(execution_id),
                    cluster_id=str(best_candidate.cluster_id),
                )
                return AdmissionResult(
                    mode=AdmissionMode.LEADER,
                    cluster_id=best_candidate.cluster_id,
                    similarity_score=1.0,
                    reason="retrying_leader_execution",
                    anchor_incident_sys_id=best_candidate.anchor_incident_sys_id,
                    anchor_incident_number=best_candidate.anchor_incident_number,
                )

            # Validate against PostgreSQL authoritative state
            if self._is_cluster_authoritatively_active(best_candidate.cluster_id):
                return self._join_as_follower(
                    best_candidate, execution_id, inc_sys_id, inc_number, best_similarity
                )
            else:
                # Stale anchor in Redis/cache - evict and fall through to distributed lock
                logger.warning(
                    "stale_redis_anchor_evicted",
                    cluster_id=str(best_candidate.cluster_id),
                    execution_id=str(execution_id),
                )
                self._evict_stale_anchor(best_candidate.cluster_id)

        # 5. CRITICAL SECTION: Acquire Distributed Admission Lock (15s safe lease)
        with self._distributed_lock(norm_service, ttl_seconds=15.0, timeout=5.0):
            # 6. DOUBLE SEARCH (Inside Lock):
            # Authoritatively reconcile with PostgreSQL and re-check active anchors
            anchors_in_lock = self._get_active_anchors(service=norm_service, refresh_from_db=True)
            second_candidate, second_similarity = self._find_matching_candidate(
                vector, norm_service, anchors_in_lock
            )

            if second_candidate is not None and second_similarity >= self.threshold:
                # Check if this is the original leader retrying
                if second_candidate.anchor_execution_id == execution_id:
                    logger.info(
                        "admit_leader_retry_detected_in_lock",
                        execution_id=str(execution_id),
                        cluster_id=str(second_candidate.cluster_id),
                    )
                    return AdmissionResult(
                        mode=AdmissionMode.LEADER,
                        cluster_id=second_candidate.cluster_id,
                        similarity_score=1.0,
                        reason="retrying_leader_execution",
                        anchor_incident_sys_id=second_candidate.anchor_incident_sys_id,
                        anchor_incident_number=second_candidate.anchor_incident_number,
                    )

                if self._is_cluster_authoritatively_active(second_candidate.cluster_id):
                    logger.info(
                        "semantic_cache_double_search_prevented_race",
                        execution_id=str(execution_id),
                        cluster_id=str(second_candidate.cluster_id),
                        similarity=round(second_similarity, 4),
                    )
                    return self._join_as_follower(
                        second_candidate, execution_id, inc_sys_id, inc_number, second_similarity
                    )
                else:
                    self._evict_stale_anchor(second_candidate.cluster_id)

            # Still no matching cluster exists -> safely elected as LEADER
            if second_candidate is not None and 0.0 <= second_similarity < self.threshold:
                logger.info(
                    "semantic_cache_low_confidence_fallback",
                    execution_id=str(execution_id),
                    similarity=round(second_similarity, 4),
                    threshold=self.threshold,
                    reason="similarity_below_confidence_threshold",
                )

            return self._create_as_leader(
                execution_id=execution_id,
                inc_sys_id=inc_sys_id,
                inc_number=inc_number,
                vector=vector,
                norm_service=norm_service,
                inc_category=inc_category,
            )

    def _sync_anchor_to_redis(self, anchor: CachedClusterAnchor) -> None:
        """Propagate updated anchor status and solution to Redis."""
        if self.redis_client is not None:
            try:
                cid_str = str(anchor.cluster_id)
                self.redis_client.setex(
                    f"{REDIS_ANCHOR_PREFIX}{cid_str}",
                    self.ttl_seconds,
                    json.dumps(anchor.to_dict()),
                )
            except Exception as exc:
                logger.warning("redis_anchor_sync_failed", error=str(exc))

    def publish_solution(self, cluster_id: UUID, solution: dict[str, Any]) -> None:
        """Commit authoritative state before publishing the disposable Redis copy."""
        if self.repo is not None:
            self.repo.update_cluster_status(cluster_id, "resolved", solution=solution)
        anchor = self._anchors.get(cluster_id)
        if anchor is not None:
            anchor.status = ClusterStatus.RESOLVED
            anchor.solution = solution
            self._sync_anchor_to_redis(anchor)

    def mark_cluster_failed(self, cluster_id: UUID, reason: str) -> None:
        if self.repo is not None:
            self.repo.update_cluster_status(cluster_id, "failed", failure_reason=reason)
        anchor = self._anchors.get(cluster_id)
        if anchor is not None:
            anchor.status = ClusterStatus.FAILED
            anchor.failure_reason = reason
            self._sync_anchor_to_redis(anchor)
        if self.redis_client is not None:
            with contextlib.suppress(Exception):
                self.redis_client.srem(REDIS_ACTIVE_SET, str(cluster_id))

    def mark_cluster_awaiting_approval(self, cluster_id: UUID) -> None:
        if self.repo is not None:
            self.repo.update_cluster_status(cluster_id, "awaiting_approval")
        anchor = self._anchors.get(cluster_id)
        if anchor is not None:
            anchor.status = ClusterStatus.AWAITING_APPROVAL
            self._sync_anchor_to_redis(anchor)

    def get_cluster_solution(self, cluster_id: UUID) -> dict[str, Any] | None:
        # Approval resumes in the API process. Its database transition must beat
        # any stale awaiting-approval anchor still resident in a worker process.
        if self.repo is not None:
            cluster = self.repo.get_cluster(cluster_id)
            return (
                cluster.solution if cluster is not None and cluster.status == "resolved" else None
            )
        anchor = self._anchors.get(cluster_id)
        return (
            anchor.solution
            if anchor is not None and anchor.status == ClusterStatus.RESOLVED
            else None
        )

    def get_cluster_status(self, cluster_id: UUID) -> ClusterStatus | None:
        if self.repo is not None:
            cluster = self.repo.get_cluster(cluster_id)
            if cluster is None:
                return None
            return (
                ClusterStatus.EXPIRED
                if cluster.expires_at <= _utcnow()
                else ClusterStatus(cluster.status)
            )
        anchor = self._anchors.get(cluster_id)
        return anchor.status if anchor is not None and not anchor.is_expired else None


# Module singleton instance
_cache_instance: SemanticCache | None = None


def get_semantic_cache(
    repo: WorkerRepo | None = None,
    redis_client: Any = None,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> SemanticCache:
    """Access or create the singleton semantic cache."""
    global _cache_instance
    if _cache_instance is None or (repo is not None and _cache_instance.repo is not repo):
        _cache_instance = SemanticCache(
            repo=repo,
            redis_client=redis_client,
            threshold=threshold,
            ttl_seconds=ttl_seconds,
        )
    else:
        if repo is not None and _cache_instance.repo is None:
            _cache_instance.repo = repo
        if redis_client is not None and _cache_instance.redis_client is None:
            _cache_instance.redis_client = redis_client
    return _cache_instance
