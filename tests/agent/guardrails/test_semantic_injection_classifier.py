from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.guardrails.semantic_injection_classifier import (
    CLASSIFIER_PURPOSE,
    classify_injection,
)
from agent.prompts import InjectionClassification

DATASET_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "adversarial" / "sprint3_seed_set.json"
)


@pytest.fixture(scope="module")
def dataset() -> dict[str, Any]:
    return json.loads(DATASET_PATH.read_text())


class _ScriptedLLM:
    """Mirrors the real LLMClient.structured(*, purpose, system, prompt, schema,
    model=None) contract exactly, so a regression like passing `system_prompt=`
    instead of `system=` fails this fake the same way it fails the real client.
    """

    def __init__(self, verdict: InjectionClassification | Exception) -> None:
        self._verdict = verdict
        self.calls: list[dict[str, Any]] = []

    def structured(
        self,
        *,
        purpose: str,
        system: str,
        prompt: str,
        schema: type,
        model: str | None = None,
    ):
        self.calls.append(
            {"purpose": purpose, "system": system, "prompt": prompt, "schema": schema}
        )
        if isinstance(self._verdict, Exception):
            raise self._verdict
        return self._verdict


def test_benign_incident_is_not_flagged(dataset: dict[str, Any]) -> None:
    case = dataset["benign_controls"][0]
    llm = _ScriptedLLM(InjectionClassification(is_injection=False, reason="ordinary IT incident"))
    outcome = classify_injection(llm, case["description"])
    assert outcome.available
    assert outcome.is_injection is False


def test_paraphrased_injection_is_flagged(dataset: dict[str, Any]) -> None:
    case = dataset["paraphrased_semantic_injections"][0]
    llm = _ScriptedLLM(
        InjectionClassification(is_injection=True, reason="asks the model to drop prior rules")
    )
    outcome = classify_injection(llm, case["description"])
    assert outcome.available
    assert outcome.is_injection is True


def test_data_exfiltration_style_injection_is_flagged(dataset: dict[str, Any]) -> None:
    case = dataset["paraphrased_semantic_injections"][1]
    llm = _ScriptedLLM(
        InjectionClassification(is_injection=True, reason="requests secrets be disclosed")
    )
    outcome = classify_injection(llm, case["description"])
    assert outcome.is_injection is True


def test_technical_text_with_suspicious_words_but_benign_intent(
    dataset: dict[str, Any],
) -> None:
    case = dataset["benign_controls"][0]
    llm = _ScriptedLLM(
        InjectionClassification(is_injection=False, reason="ordinary technical language")
    )
    outcome = classify_injection(llm, case["description"])
    assert outcome.is_injection is False


def test_structured_call_uses_the_project_llm_contract() -> None:
    """Regression test for the system_prompt= vs system= bug: asserts the
    classifier calls llm.structured with the exact keyword-only signature
    every other node in the project uses (purpose, system, prompt, schema)."""
    llm = _ScriptedLLM(InjectionClassification(is_injection=False, reason="fine"))
    classify_injection(llm, "some sanitized text")
    call = llm.calls[0]
    assert call["schema"] is InjectionClassification
    assert call["purpose"] == CLASSIFIER_PURPOSE
    assert call["system"]  # the system prompt was actually passed as `system=`


def test_timeout_degrades_safely_not_as_injection() -> None:
    llm = _ScriptedLLM(TimeoutError("timed out"))
    outcome = classify_injection(llm, "some sanitized text")
    assert outcome.available is False
    assert outcome.is_injection is False  # never interpreted as True


def test_exception_degrades_safely_not_as_injection() -> None:
    llm = _ScriptedLLM(RuntimeError("boom"))
    outcome = classify_injection(llm, "some sanitized text")
    assert outcome.available is False
    assert outcome.is_injection is False


def test_a_signature_mismatch_degrades_safely_instead_of_raising() -> None:
    """This is exactly the bug that broke test_benign_incident_passes_and_
    reaches_sanitized_state: a caller (or a future refactor) passing a keyword
    llm.structured() doesn't recognise must degrade to "unavailable", not
    propagate a TypeError out of classify_injection."""

    class _StrictLLM:
        def structured(
            self,
            *,
            purpose: str,
            system: str,
            prompt: str,
            schema: type,
            model: str | None = None,
        ):
            raise AssertionError("should not be reached with a bad kwarg")

    class _WrongKwargLLM:
        def structured(
            self,
            *,
            purpose: str,
            system_prompt: str,
            prompt: str,
            schema: type,
            model: str | None = None,
        ):
            raise AssertionError("this signature should never be called by classify_injection")

    outcome = classify_injection(_WrongKwargLLM(), "some sanitized text")
    assert outcome.available is False
    assert outcome.failure_category == "TypeError"
    assert outcome.is_injection is False


def test_malformed_output_degrades_safely_not_as_injection() -> None:
    class _BadLLM:
        def structured(self, **kwargs: Any):
            return {"is_injection": True}  # not an InjectionClassification instance

    outcome = classify_injection(_BadLLM(), "some sanitized text")
    assert outcome.available is False
    assert outcome.failure_category == "malformed_output"
    assert outcome.is_injection is False


def test_empty_text_short_circuits_without_a_model_call() -> None:
    llm = _ScriptedLLM(InjectionClassification(is_injection=True, reason="should never be called"))
    outcome = classify_injection(llm, "   ")
    assert outcome.available is True
    assert outcome.is_injection is False
    assert llm.calls == []
