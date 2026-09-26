"""S3.5 commit 2: the Article Composer.

The composer restructures a human engineer's terse solution into a structured
KB article — the platform's only self-improving write. Faithfulness is the
contract: the LLM restructures, deterministic code normalizes metadata, and
``check_faithfulness`` flags any content token the human never stated.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.article_composer import check_faithfulness, compose_article
from agent.prompts import (
    ARTICLE_COMPOSER_SYSTEM,
    ComposedArticle,
)
from agent.state import IncidentSnapshot
from app.models.knowledge import SecurityLevel, WorkflowState
from app.workers.retry_policy import RetryableError
from tests.agent_support import FakeLLM, make_deps

INCIDENT = IncidentSnapshot(
    sys_id="sys-1",
    number="INC0010099",
    short_description="VPN drops every few minutes",
    description="Corporate VPN drops intermittently on the requester laptop.",
    category="software",
    service="corporate-vpn",
)

FAITHFUL_SOLUTION = (
    "was the MTU mismatch; fixed by setting MTU 1400 and restarting the tunnel interface"
)
FAITHFUL_ANSWER = ComposedArticle(
    title="Resolving Intermittent Corporate VPN Drops",
    short_description="Set MTU 1400 and restart the tunnel interface.",
    category="network",
    body=("1. Set MTU 1400 on the tunnel.\n2. Fixed by restarting the interface."),
)


def _deps(answer: object) -> object:
    return make_deps(llm=FakeLLM(answers={"article_composer": answer}))


class TestComposeArticle:
    def test_composes_structured_article_with_human_resolved_marker(self) -> None:
        article = compose_article(
            INCIDENT, FAITHFUL_SOLUTION, deps=_deps(FAITHFUL_ANSWER), article_number="KB1001"
        )
        assert article.article_number == "KB1001"
        assert article.version == "1.0"
        assert article.workflow_state is WorkflowState.HUMAN_RESOLVED
        assert article.security_level is SecurityLevel.INTERNAL
        assert article.service == "corporate-vpn"

    def test_numbered_steps_rendered_from_procedural_solution(self) -> None:
        article = compose_article(
            INCIDENT, FAITHFUL_SOLUTION, deps=_deps(FAITHFUL_ANSWER), article_number="KB1001"
        )
        assert "1." in article.body
        assert "2." in article.body
        assert "1400" in article.body

    def test_faithful_output_passes_check_faithfulness(self) -> None:
        article = compose_article(
            INCIDENT, FAITHFUL_SOLUTION, deps=_deps(FAITHFUL_ANSWER), article_number="KB1001"
        )
        assert check_faithfulness(article, FAITHFUL_SOLUTION) == []

    def test_empty_or_garbage_solution_rejected_before_llm(self) -> None:
        deps = _deps(FAITHFUL_ANSWER)
        for bad in ("", "   ", "ok"):
            with pytest.raises(ValueError):
                compose_article(INCIDENT, bad, deps=deps, article_number="KB1001")
        assert deps.llm.calls == []  # no model call was ever made

    def test_short_title_falls_back_to_deterministic_title(self) -> None:
        answer = FAITHFUL_ANSWER.model_copy(update={"title": "MTU"})
        article = compose_article(
            INCIDENT, FAITHFUL_SOLUTION, deps=_deps(answer), article_number="KB1001"
        )
        assert len(article.title) >= 5
        assert article.title.startswith("Resolution for INC0010099")

    def test_body_too_short_is_a_compose_failure(self) -> None:
        answer = FAITHFUL_ANSWER.model_copy(update={"body": "hi"})
        with pytest.raises(ValueError):
            compose_article(
                INCIDENT, FAITHFUL_SOLUTION, deps=_deps(answer), article_number="KB1001"
            )

    def test_category_normalized_to_slug(self) -> None:
        answer = FAITHFUL_ANSWER.model_copy(update={"category": "Corporate VPN"})
        article = compose_article(
            INCIDENT, FAITHFUL_SOLUTION, deps=_deps(answer), article_number="KB1001"
        )
        assert article.category == "corporate-vpn"

    def test_missing_service_falls_back_to_valid_slug(self) -> None:
        incident = INCIDENT.model_copy(update={"service": None})
        article = compose_article(
            incident, FAITHFUL_SOLUTION, deps=_deps(FAITHFUL_ANSWER), article_number="KB1001"
        )
        assert article.service  # non-empty valid slug
        from app.models.knowledge import SLUG_PATTERN

        assert SLUG_PATTERN.match(article.service)

    def test_llm_errors_propagate_to_caller(self) -> None:
        with pytest.raises(RetryableError):
            compose_article(
                INCIDENT,
                FAITHFUL_SOLUTION,
                deps=_deps(RetryableError("proxy 503")),
                article_number="KB1001",
            )


class TestModelContract:
    def test_uses_article_composer_purpose_and_system(self) -> None:
        deps = _deps(FAITHFUL_ANSWER)
        compose_article(INCIDENT, FAITHFUL_SOLUTION, deps=deps, article_number="KB1001")
        assert deps.llm.calls[0]["purpose"] == "article_composer"
        assert deps.llm.calls[0]["system"] == ARTICLE_COMPOSER_SYSTEM

    def test_solution_fenced_as_data_in_prompt(self) -> None:
        deps = _deps(FAITHFUL_ANSWER)
        compose_article(INCIDENT, FAITHFUL_SOLUTION, deps=deps, article_number="KB1001")
        prompt = deps.llm.calls[0]["prompt"]
        assert FAITHFUL_SOLUTION in prompt
        assert "<solution>" in prompt and "</solution>" in prompt
        assert "INC0010099" in prompt  # incident context present

    def test_system_prompt_demands_restructure_only(self) -> None:
        lowered = ARTICLE_COMPOSER_SYSTEM.lower()
        assert "only" in lowered and "never" in lowered


class TestCheckFaithfulness:
    def test_fabricated_token_is_flagged(self) -> None:
        answer = FAITHFUL_ANSWER.model_copy(
            update={"body": FAITHFUL_ANSWER.body + "\n3. Also replace the core router."}
        )
        article = compose_article(
            INCIDENT, FAITHFUL_SOLUTION, deps=_deps(answer), article_number="KB1001"
        )
        issues = check_faithfulness(article, FAITHFUL_SOLUTION)
        assert "router" in issues

    def test_stopwords_and_short_tokens_ignored(self) -> None:
        article = compose_article(
            INCIDENT, FAITHFUL_SOLUTION, deps=_deps(FAITHFUL_ANSWER), article_number="KB1001"
        )
        # "the"/"on" are stopwords or sub-threshold; body is extractive.
        assert check_faithfulness(article, FAITHFUL_SOLUTION) == []

    def test_numbers_are_treated_as_facts(self) -> None:
        answer = FAITHFUL_ANSWER.model_copy(update={"body": "Set MTU 2048 on the tunnel."})
        article = compose_article(
            INCIDENT,
            "was the MTU mismatch, set MTU 1400",
            deps=_deps(answer),
            article_number="KB1001",
        )
        assert "2048" in check_faithfulness(article, "was the MTU mismatch, set MTU 1400")

    def test_sparse_input_composes_without_invention(self) -> None:
        """Rubric: sparse input ('restarted the service') must not grow roots.

        The scripted composer invents a root cause the human never stated;
        the faithfulness backstop must flag it — sparse input stays sparse.
        """
        solution = "restarted the service"
        answer = ComposedArticle(
            title="Service Restart",
            short_description="Restart the service.",
            category="software",
            body=("1. Restart the service.\n2. Root cause was a memory leak in the worker pool."),
        )
        article = compose_article(INCIDENT, solution, deps=_deps(answer), article_number="KB1002")
        issues = check_faithfulness(article, solution)
        assert {"memory", "leak", "worker", "pool"} & set(issues), (
            "invented diagnostic details were not flagged"
        )
        # the extractive part of the sparse solution survives untouched
        assert "service" in article.body.lower()

    def test_incident_context_counts_as_allowed_source(self) -> None:
        answer = FAITHFUL_ANSWER.model_copy(update={"body": "VPN drops every few minutes."})
        article = compose_article(
            INCIDENT, "flaky wifi", deps=_deps(answer), article_number="KB1001"
        )
        issues = check_faithfulness(
            article, "flaky wifi", incident_context="vpn drops every few minutes"
        )
        assert issues == []


def test_composed_article_schema_is_strict() -> None:
    with pytest.raises(ValidationError):
        ComposedArticle(title="t", short_description="s", category="network")  # body missing
