"""Agent runtime initialisation (S2.5): settings, providers, clients, worker wiring.

Covers the brief's "agent runtime initialization" deliverable: the state schema is
importable and JSON-only, the LLM/embedding providers are process singletons, the
checkpointer and graph are built once per process, and the Celery task runs the
graph with the correlation id taken from the message headers.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import openai
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny

from agent import dependencies as dependencies_module
from agent import llm as llm_module
from agent import runtime as runtime_module
from agent.checkpointer import WorkflowStateSaver, build_checkpointer
from agent.config import AGENT_VERSION, AgentSettings
from agent.llm import (
    LiteLLMClient,
    ModelRefusalError,
    bounded,
    cost_details,
    usage_details,
)
from agent.prompts import (
    ClassifyOutput,
    CriticOutput,
    DiagnoseOutput,
    GenerateOutput,
)
from agent.retrieval import (
    CLASSIFICATION_TO_CORPUS_CATEGORY,
    OUT_OF_CATEGORY_EVIDENCE_MARGIN,
    QdrantRetriever,
    _MemoEngine,
    _run_search,
    search_categories,
)
from agent.runtime import build_runtime, invoke_incident_graph
from agent.state import AgentState, EventPayload, initial_state
from app.models.knowledge import Classification, SecurityLevel
from app.retrieval.embedding import EmbeddedText
from app.retrieval.ingest import ingest_articles
from app.retrieval.sources import LocalJSONSource
from app.workers import producer as producer_module
from app.workers import tasks as tasks_module
from app.workers.db import InMemoryRepo
from app.workers.retry_policy import RetryableError, TerminalError
from observability.tracing import Tracer
from tests.agent_support import (
    EXECUTION_ID,
    VPN,
    FakeOpenAISDK,
    event_for,
    make_deps,
    vpn_answers,
)

# -- settings -------------------------------------------------------------------------------


class TestSettings:
    def test_defaults_are_the_manual_11_7_values(self) -> None:
        settings = AgentSettings(_env_file=None)
        assert settings.agent_retrieval_top_k == 5
        assert settings.agent_retrieval_threshold == 0.55
        assert settings.agent_confidence_floor == 0.45
        assert settings.agent_risk_priorities == [1]
        assert settings.agent_supported_categories == ["network", "software", "hardware", "inquiry"]
        assert settings.agent_max_security_level == "internal"
        assert settings.agent_llm_model == "gemini/gemini-3.5-flash"
        assert settings.litellm_base_url == "https://management.sprints.ai/litellm"
        assert settings.agent_graph_backend == "langgraph"
        assert settings.agent_version == AGENT_VERSION

    def test_environment_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_RETRIEVAL_THRESHOLD", "0.6")
        monkeypatch.setenv("AGENT_RISK_PRIORITIES", "[1, 2]")
        monkeypatch.setenv("AGENT_GRAPH_BACKEND", "stub")
        settings = AgentSettings(_env_file=None)
        assert settings.agent_retrieval_threshold == 0.6
        assert settings.agent_risk_priorities == [1, 2]
        assert settings.agent_graph_backend == "stub"

    def test_out_of_range_values_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            AgentSettings(_env_file=None, agent_confidence_floor=1.5)


def test_state_schema_is_json_only() -> None:
    state = initial_state(
        EventPayload.model_validate(event_for(VPN)),
        execution_id=EXECUTION_ID,
        correlation_id="c",
        started_at="2026-09-08T06:14:00+00:00",
    )
    assert json.loads(json.dumps(state)) == state
    assert set(AgentState.__annotations__) >= {
        "event",
        "incident",
        "retrieval",
        "classification",
        "risk",
        "confidence",
        "output",
    }


# -- providers ------------------------------------------------------------------------------


class TestProviders:
    def test_llm_is_a_process_singleton(self) -> None:
        llm_module.get_llm.cache_clear()
        with (
            patch("openai.OpenAI") as sdk,
            patch.object(
                llm_module,
                "get_agent_settings",
                return_value=AgentSettings(_env_file=None, litellm_api_key="k"),
            ),
        ):
            first = llm_module.get_llm()
            second = llm_module.get_llm()
        llm_module.get_llm.cache_clear()
        assert first is second
        sdk.assert_called_once()
        assert sdk.call_args.kwargs["max_retries"] == 2
        assert sdk.call_args.kwargs["base_url"] == "https://management.sprints.ai/litellm"

    def test_embedding_engine_is_a_process_singleton(self) -> None:
        llm_module.get_embedding_engine.cache_clear()
        with patch("app.retrieval.embedding.FastEmbedEngine") as engine:
            assert llm_module.get_embedding_engine() is llm_module.get_embedding_engine()
        llm_module.get_embedding_engine.cache_clear()
        engine.assert_called_once_with()

    def test_dependencies_are_wired_lazily(self) -> None:
        dependencies_module.get_agent_dependencies.cache_clear()
        backend_factory = MagicMock()
        tools = MagicMock()
        with (
            patch.object(dependencies_module, "get_llm") as get_llm,
            patch.object(dependencies_module, "build_default_retriever") as retriever,
            patch.object(dependencies_module, "build_servicenow_backend", backend_factory),
            patch.object(
                dependencies_module,
                "build_production_tool_registry",
                return_value=tools,
            ) as build_tools,
        ):
            deps = dependencies_module.get_agent_dependencies()
            assert dependencies_module.get_agent_dependencies() is deps
        dependencies_module.get_agent_dependencies.cache_clear()
        get_llm.assert_called_once()
        retriever.assert_called_once()
        assert deps.tools is tools
        assert build_tools.call_args.args[1] is get_llm.return_value
        backend_factory.assert_not_called()  # no ServiceNow client before first use

    def test_production_registry_uses_sync_approval_storage_and_llm_explainer(self) -> None:
        gateway = MagicMock()
        llm = MagicMock()
        engine = MagicMock()
        session_factory = MagicMock()
        registry = MagicMock()
        with (
            patch.object(dependencies_module, "get_settings") as settings,
            patch.object(dependencies_module, "build_sync_database_url", return_value="db") as url,
            patch.object(
                dependencies_module, "create_sync_engine", return_value=engine
            ) as make_engine,
            patch.object(
                dependencies_module,
                "create_sync_session_factory",
                return_value=session_factory,
            ) as make_sessions,
            patch.object(
                dependencies_module,
                "build_servicenow_tool_registry",
                return_value=registry,
            ) as build_registry,
        ):
            result = dependencies_module.build_production_tool_registry(gateway, llm)

        assert result is registry
        url.assert_called_once_with(settings.return_value)
        make_engine.assert_called_once_with("db")
        make_sessions.assert_called_once_with(engine)
        assert build_registry.call_args.args == (gateway,)
        checker = build_registry.call_args.kwargs["approval_checker"]
        explainer = build_registry.call_args.kwargs["refusal_explainer"]
        assert checker._session_factory is session_factory
        assert explainer._llm is llm

    def test_runtime_is_built_once_per_process(self) -> None:
        runtime_module.get_runtime.cache_clear()
        with (
            patch.object(runtime_module, "get_agent_dependencies", return_value=make_deps()),
            patch.object(runtime_module, "build_checkpointer", return_value=InMemorySaver()) as bc,
        ):
            assert runtime_module.get_runtime() is runtime_module.get_runtime()
        runtime_module.get_runtime.cache_clear()
        bc.assert_called_once_with("memory")

    def test_checkpointer_backends(self) -> None:
        assert isinstance(build_checkpointer("memory"), InMemorySaver)
        saver = build_checkpointer("postgres")  # lazy engine: no connection is opened
        assert isinstance(saver, WorkflowStateSaver)


# -- the Gemini client (LiteLLM proxy) -----------------------------------------------------


def _status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://management.sprints.ai/litellm/chat/completions")
    return cls("err", response=httpx.Response(status, request=request), body=None)


class TestLiteLLMClient:
    def _llm(self, sdk: Any) -> LiteLLMClient:
        return LiteLLMClient(AgentSettings(_env_file=None), Tracer(None), client=sdk)

    def test_request_shape(self) -> None:
        sdk = FakeOpenAISDK(vpn_answers())
        answer = self._llm(sdk).structured(
            purpose="classify", system="sys", prompt="the incident", schema=ClassifyOutput
        )
        assert answer.label == "network"
        request = sdk.requests[0]
        assert request["model"] == "gemini/gemini-3.5-flash"
        assert request["model"].startswith("gemini/")  # the only models the key allows
        assert request["response_format"] is ClassifyOutput
        assert request["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "the incident"},
        ]
        assert request["max_completion_tokens"] == 16000
        assert "reasoning_effort" not in request

    def test_reasoning_effort_is_passed_when_set(self) -> None:
        sdk = FakeOpenAISDK(vpn_answers())
        llm = LiteLLMClient(
            AgentSettings(_env_file=None, agent_llm_reasoning_effort="low"),
            Tracer(None),
            client=sdk,
        )
        llm.structured(purpose="classify", system="s", prompt="p", schema=ClassifyOutput)
        assert sdk.requests[0]["reasoning_effort"] == "low"

    def test_diagnostic_model_override(self) -> None:
        sdk = FakeOpenAISDK(vpn_answers())
        llm = LiteLLMClient(
            AgentSettings(_env_file=None, agent_diagnostic_model="gemini/gemini-diagnostic"),
            Tracer(None),
            client=sdk,
        )
        llm.structured(purpose="diagnose", system="s", prompt="p", schema=DiagnoseOutput)
        assert sdk.requests[0]["model"] == "gemini/gemini-diagnostic"
        assert llm.model_name == "gemini/gemini-diagnostic"
        assert llm.model_name == sdk.requests[0]["model"]

    def test_resolution_model_override(self) -> None:
        sdk = FakeOpenAISDK(vpn_answers())
        llm = LiteLLMClient(
            AgentSettings(_env_file=None, agent_resolution_model="gemini/gemini-resolution"),
            Tracer(None),
            client=sdk,
        )
        llm.structured(purpose="generate", system="s", prompt="p", schema=GenerateOutput)
        assert sdk.requests[0]["model"] == "gemini/gemini-resolution"
        assert llm.model_name == "gemini/gemini-resolution"
        assert llm.model_name == sdk.requests[0]["model"]

    def test_critic_model_override(self) -> None:
        sdk = FakeOpenAISDK(vpn_answers())
        llm = LiteLLMClient(
            AgentSettings(_env_file=None, agent_critic_model="gemini/gemini-critic"),
            Tracer(None),
            client=sdk,
        )
        llm.structured(purpose="verify_evidence", system="s", prompt="p", schema=CriticOutput)
        assert sdk.requests[0]["model"] == "gemini/gemini-critic"
        assert llm.model_name == "gemini/gemini-critic"
        assert llm.model_name == sdk.requests[0]["model"]

    def test_fallback_to_agent_llm_model(self) -> None:
        sdk = FakeOpenAISDK(vpn_answers())
        llm = LiteLLMClient(
            AgentSettings(_env_file=None, agent_llm_model="gemini/gemini-fallback"),
            Tracer(None),
            client=sdk,
        )
        # Before structured call
        assert llm.model_name == "gemini/gemini-fallback"
        # After structured call without purpose override
        llm.structured(purpose="classify", system="s", prompt="p", schema=ClassifyOutput)
        assert sdk.requests[0]["model"] == "gemini/gemini-fallback"
        assert llm.model_name == "gemini/gemini-fallback"
        assert llm.model_name == sdk.requests[0]["model"]

    def test_explicit_override(self) -> None:
        sdk = FakeOpenAISDK(vpn_answers())
        llm = LiteLLMClient(
            AgentSettings(
                _env_file=None,
                agent_llm_model="gemini/gemini-fallback",
                agent_diagnostic_model="gemini/gemini-diagnostic",
            ),
            Tracer(None),
            client=sdk,
        )
        llm.structured(
            purpose="diagnose",
            system="s",
            prompt="p",
            schema=DiagnoseOutput,
            model="gemini/gemini-explicit-override",
        )
        assert sdk.requests[0]["model"] == "gemini/gemini-explicit-override"
        assert llm.model_name == "gemini/gemini-explicit-override"
        assert llm.model_name == sdk.requests[0]["model"]

    def test_recorded_audit_model_matches_request_in_multi_agent_execution(self) -> None:
        sdk = FakeOpenAISDK(vpn_answers())
        llm = LiteLLMClient(
            AgentSettings(
                _env_file=None,
                agent_llm_model="gemini/gemini-fallback",
                agent_diagnostic_model="gemini/gemini-diag-model",
                agent_resolution_model="gemini/gemini-res-model",
                agent_critic_model="gemini/gemini-critic-model",
            ),
            Tracer(None),
            client=sdk,
        )
        # 1. Diagnose executes with diagnostic model
        llm.structured(purpose="diagnose", system="s", prompt="p", schema=DiagnoseOutput)
        assert sdk.requests[0]["model"] == "gemini/gemini-diag-model"

        # 2. Generate executes with resolution model
        llm.structured(purpose="generate", system="s", prompt="p", schema=GenerateOutput)
        assert sdk.requests[1]["model"] == "gemini/gemini-res-model"

        # 3. Critic executes with critic model
        llm.structured(purpose="verify_evidence", system="s", prompt="p", schema=CriticOutput)
        assert sdk.requests[2]["model"] == "gemini/gemini-critic-model"

        # 4. Audit model for act() reflects the resolution model that drafted the remediation
        assert llm.model_name == "gemini/gemini-res-model"
        assert llm.model_name == sdk.requests[1]["model"]
        assert llm.get_used_model("diagnose") == "gemini/gemini-diag-model"
        assert llm.get_used_model("generate") == "gemini/gemini-res-model"
        assert llm.get_used_model("verify_evidence") == "gemini/gemini-critic-model"

    @pytest.mark.parametrize(
        ("kwargs", "error"),
        [
            ({"finish_reason": "content_filter"}, ModelRefusalError),
            ({"refusal": "I can't help with that"}, ModelRefusalError),
            ({"finish_reason": "length"}, TerminalError),
        ],
    )
    def test_unusable_answers_are_terminal(
        self, kwargs: dict[str, Any], error: type[Exception]
    ) -> None:
        sdk = FakeOpenAISDK(vpn_answers(), **kwargs)
        with pytest.raises(error):
            self._llm(sdk).structured(purpose="x", system="s", prompt="p", schema=ClassifyOutput)

    def test_missing_structured_output_is_terminal(self) -> None:
        sdk = FakeOpenAISDK({"classify": None})  # type: ignore[dict-item]
        sdk.answers["classify"] = None
        with pytest.raises(TerminalError):
            self._llm(sdk).structured(purpose="x", system="s", prompt="p", schema=ClassifyOutput)

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (_status_error(openai.RateLimitError, 429), RetryableError),
            (_status_error(openai.InternalServerError, 500), RetryableError),
            (_status_error(openai.APIStatusError, 503), RetryableError),
            (_status_error(openai.APIStatusError, 408), RetryableError),
            (_status_error(openai.BadRequestError, 400), TerminalError),
            (_status_error(openai.AuthenticationError, 401), TerminalError),
            (_status_error(openai.PermissionDeniedError, 403), TerminalError),
            (
                openai.APIConnectionError(
                    request=httpx.Request("POST", "https://management.sprints.ai/litellm")
                ),
                RetryableError,
            ),
            (ValueError("schema mismatch"), TerminalError),
        ],
    )
    def test_error_mapping(self, exc: Exception, expected: type[Exception]) -> None:
        sdk = FakeOpenAISDK({"classify": exc})
        with pytest.raises(expected):
            self._llm(sdk).structured(purpose="x", system="s", prompt="p", schema=ClassifyOutput)

    def test_missing_key_fails_fast(self) -> None:
        with pytest.raises(TerminalError, match="LITELLM_API_KEY"):
            LiteLLMClient(AgentSettings(_env_file=None, litellm_api_key=None), Tracer(None))

    def test_usage_and_cost(self) -> None:
        usage = usage_details(
            MagicMock(
                prompt_tokens=17,
                completion_tokens=180,
                completion_tokens_details=MagicMock(reasoning_tokens=120),
                prompt_tokens_details=MagicMock(cached_tokens=0),
            )
        )
        assert usage == {"input": 17, "output": 180, "reasoning_tokens": 120}
        assert usage_details(None) == {}
        assert cost_details("0.0016455") == {"total": 0.0016455}
        assert cost_details(None) is None
        assert cost_details("n/a") is None

    def test_bounded_redacts_and_truncates(self) -> None:
        text = bounded("password=abc12345 " + "y" * 50, 20)
        assert "abc12345" not in text
        assert text.endswith("…[truncated]")


# -- retrieval against a real (in-memory) Qdrant ---------------------------------------------


class HashingEngine:
    """Deterministic stand-in for FastEmbed: hashed bag-of-words, unit length."""

    dense_vector_size = 384

    def _tokens(self, text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _embed(self, text: str) -> EmbeddedText:
        dense = [0.0] * self.dense_vector_size
        sparse: dict[int, float] = {}
        for token in self._tokens(text):
            h = int(hashlib.sha256(token.encode()).hexdigest(), 16)
            dense[h % self.dense_vector_size] += 1.0
            sparse[h % 100_000] = sparse.get(h % 100_000, 0.0) + 1.0
        norm = math.sqrt(sum(v * v for v in dense)) or 1.0
        return EmbeddedText(
            dense=[v / norm for v in dense],
            sparse_indices=list(sparse),
            sparse_values=list(sparse.values()),
        )

    def embed_documents(self, texts: list[str]) -> list[EmbeddedText]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> EmbeddedText:
        return self._embed(text)


@pytest.fixture(scope="module")
def corpus_qdrant() -> QdrantClient:
    from pathlib import Path

    client = QdrantClient(":memory:")
    articles = LocalJSONSource(Path("data/corpus/barq_articles.json")).load_articles()
    ingest_articles(
        articles, client, collection_name="agent_test", embedding_engine=HashingEngine()
    )
    return client


class TestQdrantRetriever:
    def _retriever(self, client: QdrantClient, **kwargs: Any) -> QdrantRetriever:
        return QdrantRetriever(
            lambda: client, HashingEngine, collection_name="agent_test", **kwargs
        )

    def test_relevant_query_is_sufficient_and_category_filtered(
        self, corpus_qdrant: QdrantClient
    ) -> None:
        result = self._retriever(corpus_qdrant).search(
            "VPN client authentication fails after a password change, invalid credentials",
            classification=Classification.NETWORK,
            top_k=5,
            threshold=0.3,
        )
        assert result.category_filter == "network"
        assert result.hits and result.hits[0].article_number == "KB0001"
        # Not `all(relevance > 0)`: a weak chunk can legitimately score a negative
        # cosine, which the clamp floors to 0.0. S2.4's reranker admits exactly such
        # a chunk (KB0003 at a raw logit of -10). What matters is that the match the
        # answer rests on is scored, and that the gate is decided from it.
        assert result.hits[0].relevance > 0
        assert result.best_relevance == max(h.relevance for h in result.hits)
        assert result.sufficient is True

    def test_every_returned_chunk_is_scored(self, corpus_qdrant: QdrantClient) -> None:
        """No hit may fall back to a defaulted relevance.

        A fused hit outside the dense top-N used to get 0.0 because it was absent
        from the score lookup, which is indistinguishable from a genuinely poor
        match and can escalate "no evidence" for an incident that had evidence.
        """
        retriever = self._retriever(corpus_qdrant)
        query = "VPN client authentication fails after a password change"
        engine = _MemoEngine(HashingEngine())
        extra = Filter(must=[FieldCondition(key="category", match=MatchAny(any=["network"]))])
        hits = _run_search(
            corpus_qdrant,
            query,
            collection_name="agent_test",
            limit=5,
            extra_filter=extra,
            max_security_level=SecurityLevel.INTERNAL,
            engine=engine,
        )
        assert hits, "fixture should return hits"

        scores = retriever._dense_scores(corpus_qdrant, engine.embed_query(query), extra, hits)
        missing = [
            (h.article_id, h.chunk_index)
            for h in hits
            if (h.article_id, h.chunk_index) not in scores
        ]
        assert not missing, f"unscored chunks fall back to 0.0: {missing}"

    def test_incident_category_is_searched_with_the_label(
        self, corpus_qdrant: QdrantClient
    ) -> None:
        """Gemini labelled a VPN failure ``access`` (→ inquiry); KB0001 is ``network``.

        The label alone used to hide KB0001 entirely. It no longer does — the
        corpus-wide pass surfaces it — but the incident's own category is still
        searched alongside the label, which is what puts KB0001 first.
        """
        query = "VPN client authentication fails after a password change, invalid credentials"
        label_only = self._retriever(corpus_qdrant).search(
            query, classification=Classification.ACCESS, top_k=5, threshold=0.3
        )
        assert "KB0001" in {h.article_number for h in label_only.hits}
        both = self._retriever(corpus_qdrant).search(
            query,
            classification=Classification.ACCESS,
            top_k=5,
            threshold=0.3,
            incident_category="network",
        )
        assert both.category_filter == "inquiry,network"
        assert both.hits[0].article_number == "KB0001"

    @pytest.mark.parametrize(
        ("label", "incident_category", "expected"),
        [
            (Classification.NETWORK, "network", ["network"]),
            (Classification.ACCESS, "network", ["inquiry", "network"]),
            (Classification.SOFTWARE, "database", ["software"]),
            (Classification.SOFTWARE, None, ["software"]),
            (Classification.OTHER, "inquiry", []),
            (Classification.SECURITY, "network", []),
        ],
    )
    def test_search_categories(
        self, label: Classification, incident_category: str | None, expected: list[str]
    ) -> None:
        assert search_categories(label, incident_category) == expected

    @pytest.mark.parametrize("label", [Classification.OTHER, Classification.SECURITY])
    def test_labels_without_corpus_coverage_have_no_evidence(
        self, corpus_qdrant: QdrantClient, label: Classification
    ) -> None:
        client = MagicMock(wraps=corpus_qdrant)
        result = self._retriever(client).search(
            "request annual leave approval next week holiday",
            classification=label,
            top_k=5,
            threshold=0.0,
        )
        assert result.category_filter is None
        assert result.hits == []
        assert result.sufficient is False
        client.query_points.assert_not_called()

    def test_unrelated_query_scores_low(self, corpus_qdrant: QdrantClient) -> None:
        result = self._retriever(corpus_qdrant).search(
            "quarterly budget spreadsheet pivot table formatting",
            classification=Classification.SOFTWARE,
            top_k=5,
            threshold=0.3,
        )
        # The corpus-wide pass means an unrelated query now has the whole corpus
        # to be nearest to, so its best score rises. What must not move is the
        # verdict: nothing here is evidence, and the out-of-category margin is
        # what keeps that true.
        assert result.category_filter == "software"
        assert result.sufficient is False

    @staticmethod
    def _category_values(query_filter: object) -> list[str]:
        """Every ``category`` value the filter tree matches on."""
        found: list[str] = []
        stack = [query_filter]
        while stack:
            node = stack.pop()
            for attr in ("must", "should", "must_not"):
                stack.extend(getattr(node, attr, None) or [])
            if getattr(node, "key", None) == "category":
                match = getattr(node, "match", None)
                found.extend(getattr(match, "any", None) or [])
                value = getattr(match, "value", None)
                if value is not None:
                    found.append(value)
        return found

    def test_wrong_label_does_not_hide_the_right_article(self, corpus_qdrant: QdrantClient) -> None:
        """The case seen live on dev407364, 2026-09-20.

        Gemini labels an Outlook mail fault ``network``. KB0002 is filed under
        ``software``, so the category pass cannot return it — and it does not
        come up empty either: it returns KB0003 at 0.67, over the 0.55 threshold,
        so nothing keyed on "found nothing" would ever notice. Both searches run,
        so KB0002 reaches the model regardless of the label.
        """
        client = MagicMock(wraps=corpus_qdrant)
        result = self._retriever(client).search(
            "Outlook shows Disconnected and no mail is delivered",
            classification=Classification.NETWORK,
            top_k=5,
            threshold=0.3,
        )
        assert "KB0002" in {hit.article_number for hit in result.hits}

        filters = [
            call.kwargs["query_filter"]
            for call in client.query_points.call_args_list
            if "query_filter" in call.kwargs
        ]
        assert [f for f in filters if self._category_values(f)], "category pass must run"
        assert [f for f in filters if not self._category_values(f)], "corpus pass must run"

    def test_out_of_category_hit_needs_a_higher_score_to_be_evidence(
        self, corpus_qdrant: QdrantClient
    ) -> None:
        """Widening the search must not lower the bar for "no evidence"."""
        retriever = self._retriever(corpus_qdrant)
        query = "Outlook shows Disconnected and no mail is delivered"
        probe = retriever.search(
            query, classification=Classification.NETWORK, top_k=5, threshold=0.0
        )
        best = probe.best_relevance
        # A threshold the best hit clears, but not by the out-of-category margin.
        just_under = best - OUT_OF_CATEGORY_EVIDENCE_MARGIN / 2
        result = retriever.search(
            query, classification=Classification.NETWORK, top_k=5, threshold=just_under
        )
        assert result.best_relevance >= just_under
        assert result.sufficient is False

    def test_restricted_and_retired_articles_are_never_returned(
        self, corpus_qdrant: QdrantClient
    ) -> None:
        result = self._retriever(corpus_qdrant).search(
            "order service returning 500 errors connection pool exhaustion",
            classification=Classification.SOFTWARE,
            top_k=5,
            threshold=0.0,
        )
        assert "KB0010" not in {h.article_number for h in result.hits}

        cleared = self._retriever(corpus_qdrant, max_security_level=SecurityLevel.RESTRICTED)
        result = cleared.search(
            "order service returning 500 errors connection pool exhaustion",
            classification=Classification.SOFTWARE,
            top_k=5,
            threshold=0.0,
        )
        versions = {(h.article_number, h.version) for h in result.hits}
        assert ("KB0010", "2.0") in versions
        assert ("KB0010", "1.0") not in versions  # retired

    def test_unreachable_store_is_retryable(self) -> None:
        broken = MagicMock()
        broken.query_points.side_effect = ConnectionError("refused")
        retriever = QdrantRetriever(lambda: broken, HashingEngine, collection_name="x")
        with pytest.raises(RetryableError):
            retriever.search("q", classification=Classification.NETWORK, top_k=5, threshold=0.5)

    def test_classification_mapping_covers_the_corpus(self) -> None:
        assert set(CLASSIFICATION_TO_CORPUS_CATEGORY.values()) == {
            "hardware",
            "software",
            "network",
            "inquiry",
        }


# -- runtime and worker wiring ---------------------------------------------------------------


class TestRuntime:
    def test_invoke_runs_the_graph(self) -> None:
        runtime = build_runtime(make_deps(), checkpointer=InMemorySaver())
        result = invoke_incident_graph(
            event_for(VPN),
            execution_id=EXECUTION_ID,
            correlation_id="c",
            attempt=1,
            runtime=runtime,
        )
        assert result["outcome"] == "suggested"

    def test_invalid_event_is_terminal(self) -> None:
        runtime = build_runtime(make_deps(), checkpointer=InMemorySaver())
        with pytest.raises(TerminalError):
            invoke_incident_graph(
                {"event_id": "x"},
                execution_id=EXECUTION_ID,
                correlation_id="c",
                attempt=1,
                runtime=runtime,
            )


class TestWorkerWiring:
    def test_stub_stays_the_default_for_bare_task_logic(self) -> None:
        with patch("agent.runtime.invoke_incident_graph") as run:
            assert tasks_module.invoke_graph({"number": "INC0000001"})["draft"].startswith("stub")
        run.assert_not_called()

    def test_langgraph_backend_passes_the_execution_context(self) -> None:
        with patch("agent.runtime.invoke_incident_graph", return_value={"outcome": "x"}) as run:
            tasks_module.invoke_graph(
                {"number": "INC1"},
                backend="langgraph",
                execution_id="e-1",
                attempt=3,
                correlation_id="c-1",
            )
        run.assert_called_once_with(
            {"number": "INC1"}, execution_id="e-1", correlation_id="c-1", attempt=3
        )

    def test_missing_correlation_falls_back_to_execution_id(self) -> None:
        with patch("agent.runtime.invoke_incident_graph", return_value={}) as run:
            tasks_module.invoke_graph({}, backend="langgraph", execution_id="e-2")
        assert run.call_args.kwargs["correlation_id"] == "e-2"

    def test_stub_backend_setting_keeps_the_s23_stub(self, settings: Any) -> None:
        from celery import Celery

        app = Celery("stub-test")
        repo = InMemoryRepo()
        execution_id = uuid4()
        repo.seed_execution(execution_id, status="queued")
        with (
            patch.object(
                tasks_module,
                "get_agent_settings",
                return_value=AgentSettings(_env_file=None, agent_graph_backend="stub"),
            ),
            patch("agent.runtime.invoke_incident_graph") as run,
        ):
            task = tasks_module.build_incident_task(app, settings, repo=repo, dlq_redis=MagicMock())
            outcome = task.apply(args=[event_for(VPN), str(execution_id)]).get()
        run.assert_not_called()
        assert outcome["result"]["draft"].startswith("stub")

    def test_celery_task_runs_the_graph_with_the_header_correlation_id(self, settings: Any) -> None:
        """The real task body under Celery's eager ``apply``: headers → graph → success."""
        from celery import Celery

        app = Celery("eager-test")
        app.conf.task_always_eager = True
        repo = InMemoryRepo()
        execution_id = uuid4()
        repo.seed_execution(execution_id, status="queued")
        runtime = build_runtime(make_deps(), checkpointer=InMemorySaver())
        seen: dict[str, Any] = {}

        def spy(payload: dict[str, Any], **context: Any) -> dict[str, Any]:
            seen.update(context)
            return invoke_incident_graph(payload, runtime=runtime, **context)

        with (
            patch.object(
                tasks_module, "get_agent_settings", return_value=AgentSettings(_env_file=None)
            ),
            patch("agent.runtime.invoke_incident_graph", side_effect=spy),
        ):
            task = tasks_module.build_incident_task(app, settings, repo=repo, dlq_redis=MagicMock())
            outcome = task.apply(
                args=[event_for(VPN), str(execution_id)],
                headers={producer_module.CORRELATION_HEADER: "corr-from-api"},
            ).get()

        assert outcome["status"] == "succeeded"
        assert outcome["result"]["outcome"] == "suggested"
        assert seen == {
            "execution_id": str(execution_id),
            "correlation_id": "corr-from-api",
            "attempt": 1,
        }
        assert repo.get_status(execution_id) == "succeeded"
        # The outcome, not a bare "completed", so the executions row records what
        # the graph decided (§FR-13 / FR-15 audit).
        assert repo.get_termination_cause(execution_id) == "suggested"
        assert repo.executions[execution_id]["node_reached"] == "act"
        assert repo.executions[execution_id]["agent_version"] == AGENT_VERSION

    def test_a_high_risk_escalation_is_recorded_on_the_executions_row(self, settings: Any) -> None:
        """A P1 stops at determine_risk, and the execution row has to say so.

        It used to read termination_cause="completed" for this, indistinguishable
        from a drafted suggestion. Since S3.4 the graph pauses instead of writing,
        so the row reads awaiting_approval with no termination cause — the one
        state an auditor must not confuse with a finished run.
        """
        from celery import Celery

        from tests.agent_support import ORDER_P1

        app = Celery("eager-risk-test")
        app.conf.task_always_eager = True
        repo = InMemoryRepo()
        execution_id = uuid4()
        repo.seed_execution(execution_id, status="queued")
        runtime = build_runtime(make_deps(), checkpointer=InMemorySaver())

        with (
            patch.object(
                tasks_module, "get_agent_settings", return_value=AgentSettings(_env_file=None)
            ),
            patch(
                "agent.runtime.invoke_incident_graph",
                side_effect=lambda payload, **ctx: invoke_incident_graph(
                    payload, runtime=runtime, **ctx
                ),
            ),
        ):
            task = tasks_module.build_incident_task(app, settings, repo=repo, dlq_redis=MagicMock())
            outcome = task.apply(args=[event_for(ORDER_P1), str(execution_id)]).get()

        assert outcome["result"]["outcome"] == "escalated_high_risk"
        # S3.4: the graph pauses at act instead of writing, so the row is parked
        # non-terminal — awaiting_approval, with no termination cause yet. The
        # cause is written when the human decision resumes the execution.
        assert outcome["result"]["paused"] is True
        assert repo.get_status(execution_id) == "awaiting_approval"
        assert repo.get_termination_cause(execution_id) is None
        assert "retrieve" not in outcome["result"]["path"]


class TestProducer:
    def test_correlation_header_is_added_when_known(self) -> None:
        sink = MagicMock()
        with patch.object(producer_module.celery_app, "send_task", sink):
            producer_module.send_incident_event({"event_id": "e"}, "x", "corr-9")
        assert sink.call_args.kwargs["headers"] == {"x_correlation_id": "corr-9"}

    def test_no_header_without_a_correlation_id(self) -> None:
        sink = MagicMock()
        with patch.object(producer_module.celery_app, "send_task", sink):
            producer_module.send_incident_event({"event_id": "e"}, "x")
        assert "headers" not in sink.call_args.kwargs
