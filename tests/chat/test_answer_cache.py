"""Chat caching: source safety and qualifier checks precede semantic reuse."""

import pytest

from app.chat.cache import CachedAnswer, RedisAnswerCache


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.index = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def zadd(self, key, members):
        self.index.setdefault(key, {}).update(members)

    def zrevrange(self, key, start, end):
        items = sorted(self.index.get(key, {}), key=self.index.get(key, {}).get, reverse=True)
        return items[start:] if end == -1 else items[start : end + 1]

    def zrem(self, key, *members):
        for member in members:
            self.index.get(key, {}).pop(member, None)

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    def expire(self, key, ttl):
        return True


def answer() -> CachedAnswer:
    return CachedAnswer(
        question="What is the P1 escalation procedure?",
        answer_markdown="Escalate using the documented procedure.",
        citations=[{"article_id": "KB0001-v1.0", "chunk_index": 0}],
        sources=[{"article_id": "KB0001-v1.0", "chunk_index": 0, "fingerprint": "source-v1"}],
    )


def cache(redis=None, validator=None, threshold=0.95):
    embeddings = []

    def embed(text):
        embeddings.append(text)
        return [1.0, 0.0]

    result = RedisAnswerCache(
        redis or MemoryRedis(),
        embed=embed,
        validate_sources=validator or (lambda sources: True),
        collection="kb",
        threshold=threshold,
        ttl_seconds=3600,
        max_entries=4,
    )
    return result, embeddings


def test_exact_lookup_does_not_embed_the_query_again():
    backend, embeddings = cache()
    revision = backend.revision()
    backend.save("restricted/model/v1", revision, answer())
    embeddings.clear()
    result, status = backend.lookup("restricted/model/v1", revision, answer().question)
    assert result.answer_markdown == answer().answer_markdown
    assert status == "exact" and embeddings == []


def test_semantic_paraphrase_can_reuse_valid_sources():
    backend, _ = cache()
    revision = backend.revision()
    backend.save("scope", revision, answer())
    result, status = backend.lookup("scope", revision, "Explain the P1 escalation procedure.")
    assert result is not None and status == "semantic"


@pytest.mark.parametrize(
    "question",
    [
        "What is the P2 escalation procedure?",
        "Must we not escalate P1?",
        "What about INC0010023?",
        "May we escalate P1?",
        "What is the P1 VPN escalation procedure?",
    ],
)
def test_incompatible_identifiers_and_negation_cannot_hit(question):
    backend, _ = cache()
    revision = backend.revision()
    backend.save("scope", revision, answer())
    assert backend.lookup("scope", revision, question) == (None, "miss")


def test_visibility_and_model_scope_are_isolated():
    backend, _ = cache()
    revision = backend.revision()
    backend.save("restricted/model/v1", revision, answer())
    assert backend.lookup("internal/model/v1", revision, answer().question) == (None, "miss")
    assert backend.lookup("restricted/other/v1", revision, answer().question) == (None, "miss")


def test_changed_or_retired_source_rejects_even_exact_hit():
    valid = [True]
    backend, _ = cache(validator=lambda sources: valid[0])
    revision = backend.revision()
    backend.save("scope", revision, answer())
    valid[0] = False
    assert backend.lookup("scope", revision, answer().question) == (None, "miss")


def test_revision_change_invalidates_answers_including_uncited_new_articles():
    backend, _ = cache()
    old = backend.revision()
    backend.save("scope", old, answer())
    backend.bump_revision()
    assert backend.lookup("scope", backend.revision(), answer().question) == (None, "miss")
    assert backend.lookup("scope", old, answer().question) == (None, "miss")


def test_ingestion_race_cannot_save_into_the_new_revision():
    backend, _ = cache()
    old = backend.revision()
    backend.bump_revision()
    backend.save("scope", old, answer())
    assert backend.lookup("scope", backend.revision(), answer().question) == (None, "miss")


def test_cache_outage_is_an_unavailable_result():
    class BrokenRedis:
        def get(self, key):
            raise ConnectionError("offline")

    backend, _ = cache(redis=BrokenRedis())
    assert backend.revision() is None
    assert backend.lookup("scope", "old", "question") == (None, "unavailable")


def test_entries_are_bounded_and_expired_values_are_misses():
    redis = MemoryRedis()
    backend, _ = cache(redis=redis)
    revision = backend.revision()
    for i in range(7):
        backend.save("scope", revision, answer().model_copy(update={"question": f"Question {i}"}))
    assert len(next(iter(redis.index.values()))) == 4
    redis.values = {key: value for key, value in redis.values.items() if "revision" in key}
    assert backend.lookup("scope", revision, "Question 6") == (None, "miss")


def test_similarity_below_threshold_is_a_miss():
    backend, _ = cache()
    revision = backend.revision()
    backend.save("scope", revision, answer())
    backend._embed = lambda text: [0.8, 0.6]
    assert backend.lookup("scope", revision, "Explain the P1 escalation procedure.") == (
        None,
        "miss",
    )


def test_partial_ingestion_disables_cache_until_a_successful_revision():
    from app.chat.cache import bump_revision

    redis = MemoryRedis()
    backend, _ = cache(redis=redis)
    old = backend.revision()
    backend.save("scope", old, answer())
    bump_revision(redis, "kb", pending=True)
    assert backend.revision() is None
    assert backend.lookup("scope", old, answer().question) == (None, "unavailable")
    backend.save("scope", old, answer())
    bump_revision(redis, "kb")
    assert backend.lookup("scope", backend.revision(), answer().question) == (None, "miss")
