"""Bounded Redis cache for context-free, screened KB answers.

Cache errors are misses. Budget errors remain fail-closed in the turn service.
Every hit revalidates its sources; the cache never trusts similarity alone.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)
PREFIX = "barq:chat:answers:"
SOURCE_FIELDS = (
    "article_id",
    "article_number",
    "version",
    "chunk_index",
    "title",
    "section",
    "chunk_text",
    "workflow_state",
    "security_level",
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def source_fingerprint(payload: Mapping[str, Any]) -> str:
    return digest({field: payload.get(field) for field in SOURCE_FIELDS})


def revision_key(collection: str) -> str:
    return PREFIX + "revision:" + digest(collection)


def bump_revision(redis: Any, collection: str, *, pending: bool = False) -> str:
    revision = ("pending:" if pending else "") + uuid4().hex
    redis.set(revision_key(collection), revision)
    return revision


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _normalize(question: str) -> str:
    return " ".join(question.casefold().split())


def compatible(left: str, right: str) -> bool:
    """Identifiers, quantities, actions and negation must agree before cosine."""

    def qualifiers(text: str) -> tuple[Any, ...]:
        text = _normalize(text).replace("’", "'")
        identifiers = frozenset(re.findall(r"\b[\w.-]*\d[\w.-]*\b", text))
        words = set(re.findall(r"[\w']+", text))
        quantities = words & {"one", "two", "three", "four", "five", "all", "none", "only"}
        negative = bool(
            words & {"no", "not", "never", "without", "except", "forbidden", "prohibited"}
        )
        negative |= any(word.endswith("n't") for word in words)
        negative |= "cannot" in words
        actions = words & {
            "create",
            "delete",
            "close",
            "reopen",
            "publish",
            "unpublish",
            "approve",
            "reject",
        }
        subjects = words & {"vpn", "mfa", "sso", "dns", "database", "firewall"}
        mandatory = bool(words & {"must", "mandatory", "required"})
        optional = bool(words & {"may", "optional"})
        return identifiers, quantities, negative, actions, subjects, mandatory, optional

    return qualifiers(left) == qualifiers(right)


class CachedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=6000)
    answer_markdown: str = Field(min_length=1, max_length=32000)
    citations: list[dict[str, Any]] = Field(min_length=1, max_length=64)
    sources: list[dict[str, Any]] = Field(min_length=1, max_length=64)
    vector: list[float] = Field(default_factory=list, max_length=4096)


class RedisAnswerCache:
    def __init__(
        self,
        redis: Any,
        *,
        embed: Callable[[str], list[float]],
        validate_sources: Callable[[list[dict[str, Any]]], bool],
        collection: str,
        threshold: float = 0.95,
        ttl_seconds: int = 3600,
        max_entries: int = 64,
    ) -> None:
        self._redis = redis
        self._embed = embed
        self._validate_sources = validate_sources
        self.collection = collection
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries

    def revision(self) -> str | None:
        try:
            key = revision_key(self.collection)
            value = self._redis.get(key)
            if value is None:
                self._redis.set(key, uuid4().hex, nx=True)
                value = self._redis.get(key)
            revision = _text(value) if value is not None else None
            # Do not admit or serve snapshots from a partial ingestion.
            return None if revision and revision.startswith("pending:") else revision
        except Exception as exc:
            logger.warning("chat_cache_revision_unavailable", error=type(exc).__name__)
            return None

    def bump_revision(self) -> str:
        return bump_revision(self._redis, self.collection)

    def _namespace(self, scope: str, revision: str) -> str:
        return PREFIX + digest([self.collection, scope, revision])

    def lookup(
        self, scope: str, revision: str | None, question: str
    ) -> tuple[CachedAnswer | None, str]:
        try:
            current = self.revision()
            if current is None:
                return None, "unavailable"
            if revision is None or revision != current:
                return None, "miss"
            namespace = self._namespace(scope, revision)
            exact = namespace + ":" + digest(_normalize(question))
            raw = self._redis.get(exact)
            if raw:
                entry = CachedAnswer.model_validate_json(raw)
                if self._validate_sources(entry.sources) and self.revision() == revision:
                    return entry, "exact"
                self._redis.delete(exact)
                self._redis.zrem(namespace + ":index", exact)
            candidates = self._redis.zrevrange(namespace + ":index", 0, self.max_entries - 1)
            query_vector = None
            best = None
            best_score = self.threshold
            for key in candidates:
                raw = self._redis.get(key)
                if not raw:
                    self._redis.zrem(namespace + ":index", key)
                    continue
                entry = CachedAnswer.model_validate_json(raw)
                if not compatible(question, entry.question):
                    continue
                if query_vector is None:
                    query_vector = self._embed(question)
                score = _cosine(query_vector, entry.vector)
                if score >= best_score and self._validate_sources(entry.sources):
                    best, best_score = entry, score
            if best is not None and self.revision() == revision:
                return best, "semantic"
            return None, "miss"
        except Exception as exc:
            logger.warning("chat_answer_cache_unavailable", error=type(exc).__name__)
            return None, "unavailable"

    def save(self, scope: str, revision: str | None, answer: CachedAnswer) -> None:
        try:
            if revision is None or self.revision() != revision:
                return
            if not self._validate_sources(answer.sources):
                return
            entry = answer.model_copy(update={"vector": self._embed(answer.question)})
            if self.revision() != revision:
                return
            namespace = self._namespace(scope, revision)
            key = namespace + ":" + digest(_normalize(answer.question))
            self._redis.set(key, entry.model_dump_json(), ex=self.ttl_seconds)
            index = namespace + ":index"
            self._redis.zadd(index, {key: time.time()})
            self._redis.expire(index, self.ttl_seconds)
            extra = self._redis.zrevrange(index, self.max_entries, -1)
            if extra:
                self._redis.delete(*extra)
                self._redis.zrem(index, *extra)
        except Exception as exc:
            logger.warning("chat_answer_cache_save_failed", error=type(exc).__name__)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / denominator if denominator else -1.0
