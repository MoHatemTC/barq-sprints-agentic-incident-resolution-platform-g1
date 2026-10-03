"""Learning from outcomes: reopened articles stop resolving alone (scenario K3)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.nodes import act
from app.feedback import WEAK_SCORE, cited_articles, weak_articles
from app.models.incident import AIProcessingState
from app.workers.incident_state import record_outcome_best_effort
from tests.agent_support import VPN, FakeServiceNow, make_deps
from tests.helpers import mock_settings
from tests.test_nodes import reasoned_state, snapshot

CALLER = "c" * 32


def test_articles_are_read_from_the_fix_once_each() -> None:
    fix = "1. Sign out [KB0001 v2.0 §Resolution]\n2. Sign in [KB0001 v2.0]\nSources: KB0010060"
    assert cited_articles(fix) == ["KB0001", "KB0010060"]


def test_weak_articles_follow_the_score_and_fail_open() -> None:
    fix = "Do it [KB0001] and [KB0002]"
    assert weak_articles(lambda a: {"KB0001": WEAK_SCORE, "KB0002": 3}, fix) == ["KB0001"]
    assert weak_articles(None, fix) == []

    def broken(articles: list[str]) -> dict[str, int]:
        raise RuntimeError("db down")

    assert weak_articles(broken, fix) == []


@pytest.fixture(autouse=True)
def _approve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys.modules["agent.nodes.act"],
        "_request_human_decision",
        lambda payload: {"decision": "approved", "decided_by": "t", "source": "no_graph"},
    )


def run(trust: Any) -> tuple[FakeServiceNow, dict[str, Any]]:
    record = {**VPN, "caller_id": CALLER}
    backend = FakeServiceNow({"vpn": record})
    deps = make_deps(
        servicenow=backend,
        agent_autonomy_level="autonomous",
        agent_assignment_groups={"network": "e" * 32},
    )
    deps.article_trust = trust
    return backend, act(reasoned_state(incident=snapshot(record)), deps)["output"]


def test_a_failing_article_no_longer_resolves_alone() -> None:
    backend, output = run(lambda articles: dict.fromkeys(articles, WEAK_SCORE))
    assert output["fulfilment"][0] == "assign_incident:applied"
    assert output["fulfilment"][1] == "update_caller:applied"
    assert output["fulfilment"][2] == "flag_human_review:applied"
    assert output["fulfilment"][3].startswith("resolve_incident:skipped_weak_article:KB0001")
    assert backend.records[VPN["sys_id"]]["state"] == "2"


def test_a_trusted_article_still_resolves() -> None:
    _, output = run(lambda articles: dict.fromkeys(articles, 2))
    assert output["fulfilment"] == ["assign_incident:applied", "resolve_incident:applied"]


def _incident(**fields: Any) -> SimpleNamespace:
    values = {
        "ai_resolution": "Fix [KB0010060 v1]",
        "ai_human_review_required": False,
        "ai_processing_state": AIProcessingState.COMPLETE,
    }
    values.update(fields)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "event_type,incident,outcome",
    [
        ("incident.reopened", _incident(ai_human_review_required=True), "reopened"),
        ("incident.closed", _incident(), "confirmed"),
        ("incident.reopened", _incident(), None),  # not an AI resolution coming back
        ("incident.closed", _incident(ai_human_review_required=True), None),
        ("incident.closed", _incident(ai_resolution=""), None),
        ("incident.engineer_replied", _incident(), None),
    ],
)
def test_outcomes_are_recorded_only_for_agent_resolutions(event_type, incident, outcome) -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_incident = AsyncMock(return_value=incident)
    store = MagicMock()
    store.record.return_value = 1
    with (
        patch("app.workers.incident_state.ServiceNowClient", return_value=client),
        patch("app.workers.incident_state.ArticleFeedbackStore", return_value=store),
        patch("app.workers.sync_engine.create_sync_engine"),
    ):
        recorded = record_outcome_best_effort(
            mock_settings(), {"sys_id": "a" * 32, "event_id": "evt-1"}, event_type
        )
    if outcome is None:
        assert recorded == 0
        store.record.assert_not_called()
    else:
        assert recorded == 1
        assert store.record.call_args.kwargs["outcome"] == outcome
        assert store.record.call_args.kwargs["articles"] == ["KB0010060"]


def test_learning_never_breaks_event_handling() -> None:
    with patch("app.workers.incident_state.ServiceNowClient", side_effect=RuntimeError("down")):
        assert (
            record_outcome_best_effort(mock_settings(), {"sys_id": "a" * 32}, "incident.closed")
            == 0
        )
