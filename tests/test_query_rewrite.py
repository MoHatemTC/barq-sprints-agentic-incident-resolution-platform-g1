"""Query rewrite failure isolation and the actual retrieve-node seam."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from agent.llm import LiteLLMClient
from agent.nodes.retrieve import build_query, retrieve
from agent.query_rewrite import QueryRewriteOutput, rewrite_query
from agent.state import AgentState, IncidentSnapshot
from tests.agent_support import FakeLLM, FakeOpenAISDK, make_deps


def test_disabled_rewrite_calls_no_model() -> None:
    deps = make_deps()
    assert rewrite_query("VPN error 691", deps) == "VPN error 691"
    assert deps.llm.calls == []


def test_augmented_query_retains_identifiers_negation_and_original_text() -> None:
    original = "Outlook 16.0 error 0x800CCC0E; not a password problem"
    llm = FakeLLM({"query_rewrite": QueryRewriteOutput(query="Outlook disconnected")})
    deps = make_deps(llm=llm, agent_query_rewrite_enabled=True)
    query = rewrite_query(original, deps)
    assert query == f"Outlook disconnected\nOriginal incident:\n{original}"
    assert original in llm.calls[0]["prompt"]
    assert llm.purposes() == ["query_rewrite"]


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("secret provider message")])
def test_model_failure_falls_back_without_retrying(error: Exception) -> None:
    llm = FakeLLM({"query_rewrite": error})
    deps = make_deps(llm=llm, agent_query_rewrite_enabled=True)
    assert rewrite_query("VPN fails", deps) == "VPN fails"
    assert llm.purposes() == ["query_rewrite"]


@pytest.mark.parametrize("query", ["", "  \n ", "x" * 601])
def test_invalid_model_output_uses_original(query: str) -> None:
    # model_construct simulates a client that violates its structured-output promise.
    llm = FakeLLM({"query_rewrite": QueryRewriteOutput.model_construct(query=query)})
    deps = make_deps(llm=llm, agent_query_rewrite_enabled=True)
    assert rewrite_query("VPN fails", deps) == "VPN fails"


def test_output_is_redacted_and_unchanged_query_is_not_duplicated() -> None:
    llm = FakeLLM({"query_rewrite": QueryRewriteOutput(query="Call joe@example.com about VPN")})
    deps = make_deps(llm=llm, agent_query_rewrite_enabled=True)
    assert "joe@example.com" not in rewrite_query("VPN fails", deps)
    llm.answers["query_rewrite"] = QueryRewriteOutput(query="VPN fails")
    assert rewrite_query("VPN fails", deps) == "VPN fails"


def test_retrieve_node_sends_only_bounded_redacted_source_and_keeps_filters() -> None:
    incident = IncidentSnapshot(
        sys_id="a" * 32,
        number="INC0010023",
        short_description="VPN fails; email joe@example.com",
        description="Error 691. " + "long detail " * 200,
        category="network",
    )
    llm = FakeLLM({"query_rewrite": QueryRewriteOutput(query="VPN authentication error 691")})
    deps = make_deps(llm=llm, agent_query_rewrite_enabled=True)
    state: AgentState = {
        "incident": incident.model_dump(),
        "classification": {"label": "network", "rationale": "VPN", "model_confidence": 0.9},
    }
    update = retrieve(state, deps)
    sent = llm.calls[0]["prompt"]
    assert "joe@example.com" not in sent
    assert len(sent) < 1200
    call = deps.retriever.calls[0]
    assert call["query"].startswith("VPN authentication error 691\n")
    assert build_query(incident) in call["query"]
    assert call["incident_category"] == "network"
    assert call["top_k"] == deps.settings.agent_retrieval_top_k
    assert update["retrieval"]["query"] == call["query"]


def test_production_rewrite_uses_short_transport_timeout_and_no_sdk_retries() -> None:
    sdk = FakeOpenAISDK({"query_rewrite": QueryRewriteOutput(query="VPN fails")})
    sdk.SCHEMA_PURPOSE = sdk.SCHEMA_PURPOSE | {"QueryRewriteOutput": "query_rewrite"}
    client = MagicMock()
    client.with_options.return_value = sdk
    deps = make_deps(agent_query_rewrite_timeout_seconds=2.0)
    llm = LiteLLMClient(deps.settings, deps.tracer, client=client)
    answer = llm.structured(
        purpose="query_rewrite", system="s", prompt="p", schema=QueryRewriteOutput
    )
    assert answer.query == "VPN fails"
    client.with_options.assert_called_once_with(timeout=2.0, max_retries=0)


def test_rewrite_timeout_is_validated() -> None:
    with pytest.raises(ValidationError):
        make_deps(agent_query_rewrite_timeout_seconds=0)


def test_enabled_rewrite_runs_through_the_whole_graph_and_failure_still_completes() -> None:
    from tests.agent_support import VPN, vpn_answers
    from tests.test_graph import FULL_PATH, run

    for response in [QueryRewriteOutput(query="VPN authentication failure"), TimeoutError()]:
        llm = FakeLLM(vpn_answers() | {"query_rewrite": response})
        deps = make_deps(llm=llm, agent_query_rewrite_enabled=True)
        result = run(VPN, deps)
        assert result["path"] == FULL_PATH
        assert result["outcome"] == "suggested"
        assert llm.purposes().count("query_rewrite") == 1
