"""Semantic Caching & Single-Flight Clustering Engine for Concurrent Similar Incidents.

BARQ G1 - Sprint 4 (S4.2)
Detects semantic similarity among incidents arriving in close temporal proximity,
groups matching incidents into a cluster, coordinates running the diagnosis and
resolution pipeline once per cluster, and distributes the resulting solution across
every incident in the cluster while preserving individual incident records and audit states.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable
from uuid import UUID, uuid4

import structlog

from app.models.semantic_cluster import (
    AdmissionMode,
    AdmissionResult,
    ClusterRole,
    ClusterSolution,
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
    """In-memory cache entry for an active cluster anchor."""

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
        self.created_at = _utcnow()
        self.failure_reason: str | None = None

    @property
    def is_expired(self) -> bool:
        return _utcnow() > self.expires_at

    @property
    def is_active(self) -> bool:
        return (
            not self.is_expired
            and self.status
            in (
                ClusterStatus.CREATING,
                ClusterStatus.RUNNING,
                ClusterStatus.AWAITING_APPROVAL,
                ClusterStatus.RESOLVED,
            )
        )


class SemanticCache:
    """Coordinates semantic clustering, single-flight pipeline execution, and shared resolution reuse."""

    def __init__(
        self,
        repo: WorkerRepo | None = None,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self.repo = repo
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self._anchors: dict[UUID, CachedClusterAnchor] = {}
        self._embed_fn = embed_fn

    def _embed(self, text: str) -> list[float]:
        """Generate embedding vector for text using FastEmbedEngine or custom callable."""
        if self._embed_fn is not None:
            return self._embed_fn(text)
        from app.retrieval.embedding import FastEmbedEngine

        engine = FastEmbedEngine()
        res = engine.embed_query(text)
        return res.dense

    def admit(
        self,
        incident: Any,
        execution_id: UUID,
    ) -> AdmissionResult:
        """Evaluate an inbound incident for semantic cluster admission.

        Returns AdmissionResult:
        - LEADER: First incident of its kind. Creates new cluster and runs the pipeline.
        - FOLLOWER: Semantically similar (similarity >= tau) to an active cluster.
                    Reuses the leader's resolution.
        - INDEPENDENT: Below threshold (< tau) or ineligible. Runs independently.
        """
        # 1. Eligibility gate (closed, inactive, human-locked, or empty text)
        if not is_incident_cluster_eligible(incident):
            logger.info("incident_cluster_ineligible", execution_id=str(execution_id))
            return AdmissionResult(
                mode=AdmissionMode.INDEPENDENT,
                reason="incident_ineligible_for_clustering",
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

        # 4. Search active anchors in cache
        best_candidate: CachedClusterAnchor | None = None
        best_similarity: float = -1.0

        now = _utcnow()
        for anchor in list(self._anchors.values()):
            # Skip expired or failed clusters
            if not anchor.is_active:
                continue

            # Operational compatibility check: if both have explicit service, must match
            if norm_service and anchor.service and norm_service != anchor.service:
                continue

            sim = cosine_similarity(vector, anchor.vector)
            if sim > best_similarity:
                best_similarity = sim
                best_candidate = anchor

        logger.info(
            "semantic_cache_search",
            execution_id=str(execution_id),
            best_similarity=round(best_similarity, 4) if best_similarity >= 0 else None,
            threshold=self.threshold,
            matched=best_similarity >= self.threshold,
        )

        # 5. Threshold evaluation
        if best_candidate is not None and best_similarity >= self.threshold:
            # Join as FOLLOWER
            cluster_id = best_candidate.cluster_id
            if self.repo is not None:
                try:
                    self.repo.add_cluster_member(
                        cluster_id=cluster_id,
                        execution_id=execution_id,
                        incident_sys_id=inc_sys_id,
                        incident_number=inc_number,
                        similarity_score=best_similarity,
                        role="follower",
                    )
                except Exception as exc:
                    logger.warning(
                        "cluster_member_registration_failed",
                        cluster_id=str(cluster_id),
                        error=str(exc),
                    )

            return AdmissionResult(
                mode=AdmissionMode.FOLLOWER,
                cluster_id=cluster_id,
                similarity_score=round(best_similarity, 4),
                reason=f"matched_active_cluster (similarity={best_similarity:.4f} >= {self.threshold})",
                anchor_incident_sys_id=best_candidate.anchor_incident_sys_id,
                anchor_incident_number=best_candidate.anchor_incident_number,
            )

        # 6. Fallback or New Leader
        # If there were candidate clusters but similarity was below threshold,
        # visibly log low-confidence fallback!
        if best_candidate is not None and 0.0 <= best_similarity < self.threshold:
            logger.info(
                "semantic_cache_low_confidence_fallback",
                execution_id=str(execution_id),
                similarity=round(best_similarity, 4),
                threshold=self.threshold,
                reason="similarity_below_confidence_threshold",
            )
            # Create a separate new cluster as leader so this distinct issue can anchor its own group
            # or proceed independently

        cluster_id = uuid4()
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
        self._anchors[cluster_id] = new_anchor

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
                )
            except Exception as exc:
                logger.warning(
                    "cluster_creation_failed_in_repo",
                    cluster_id=str(cluster_id),
                    error=str(exc),
                )

        return AdmissionResult(
            mode=AdmissionMode.LEADER,
            cluster_id=cluster_id,
            similarity_score=1.0,
            reason="new_cluster_created_as_leader",
            anchor_incident_sys_id=inc_sys_id,
            anchor_incident_number=inc_number,
        )

    def publish_solution(
        self,
        cluster_id: UUID,
        solution: dict[str, Any],
    ) -> None:
        """Store the durable resolution produced by the leader LangGraph execution."""
        anchor = self._anchors.get(cluster_id)
        if anchor is not None:
            anchor.status = ClusterStatus.RESOLVED
            anchor.solution = solution

        if self.repo is not None:
            try:
                self.repo.update_cluster_status(
                    cluster_id=cluster_id,
                    status="resolved",
                    solution=solution,
                )
            except Exception as exc:
                logger.error(
                    "failed_to_publish_cluster_solution_to_repo",
                    cluster_id=str(cluster_id),
                    error=str(exc),
                )

    def mark_cluster_failed(
        self,
        cluster_id: UUID,
        reason: str,
    ) -> None:
        """Mark cluster as failed when the leader encounters a fatal error."""
        anchor = self._anchors.get(cluster_id)
        if anchor is not None:
            anchor.status = ClusterStatus.FAILED
            anchor.failure_reason = reason

        if self.repo is not None:
            try:
                self.repo.update_cluster_status(
                    cluster_id=cluster_id,
                    status="failed",
                    failure_reason=reason,
                )
            except Exception as exc:
                logger.error(
                    "failed_to_mark_cluster_failed_in_repo",
                    cluster_id=str(cluster_id),
                    error=str(exc),
                )

    def mark_cluster_awaiting_approval(
        self,
        cluster_id: UUID,
    ) -> None:
        """Park cluster when leader execution reaches a human approval interrupt."""
        anchor = self._anchors.get(cluster_id)
        if anchor is not None:
            anchor.status = ClusterStatus.AWAITING_APPROVAL

        if self.repo is not None:
            try:
                self.repo.update_cluster_status(
                    cluster_id=cluster_id,
                    status="awaiting_approval",
                )
            except Exception as exc:
                logger.error(
                    "failed_to_mark_cluster_awaiting_approval_in_repo",
                    cluster_id=str(cluster_id),
                    error=str(exc),
                )

    def get_cluster_solution(self, cluster_id: UUID) -> dict[str, Any] | None:
        """Fetch cached resolution for a cluster."""
        anchor = self._anchors.get(cluster_id)
        if anchor is not None and anchor.solution is not None:
            return anchor.solution

        if self.repo is not None:
            cluster = self.repo.get_cluster(cluster_id)
            if cluster is not None and cluster.solution is not None:
                return cluster.solution
        return None

    def get_cluster_status(self, cluster_id: UUID) -> ClusterStatus | None:
        """Fetch current status of a cluster."""
        anchor = self._anchors.get(cluster_id)
        if anchor is not None:
            return anchor.status

        if self.repo is not None:
            cluster = self.repo.get_cluster(cluster_id)
            if cluster is not None:
                return ClusterStatus(cluster.status)
        return None


# Module singleton instance
_cache_instance: SemanticCache | None = None


def get_semantic_cache(
    repo: WorkerRepo | None = None,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> SemanticCache:
    """Access or create the singleton semantic cache."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SemanticCache(repo=repo, threshold=threshold, ttl_seconds=ttl_seconds)
    elif repo is not None and _cache_instance.repo is None:
        _cache_instance.repo = repo
    return _cache_instance
