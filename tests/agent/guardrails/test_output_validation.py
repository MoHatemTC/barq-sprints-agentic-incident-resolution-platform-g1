from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.guardrails.output_validation import (
    ALLOWED_ACTIONS,
    DEFAULT_FIELD_LIMITS,
    FieldLimits,
    ValidationCategory,
    run_all,
    validate_action_contract,
    validate_field_lengths,
    validate_structure,
)
from agent.state import Draft, DraftStep, EvidenceItem

DATASET_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "adversarial" / "sprint3_seed_set.json"
)


@pytest.fixture(scope="module")
def dataset() -> dict[str, Any]:
    return json.loads(DATASET_PATH.read_text())


def _step(text: str, article_id: str = "KB0010001", section: str = "Resolution Steps") -> DraftStep:
    return DraftStep(text=text, article_id=article_id, section=section)


def _draft(steps: list[DraftStep], rendered: str | None = None) -> Draft:
    return Draft(
        steps=steps,
        rendered=rendered if rendered is not None else "\n".join(s.text for s in steps),
        sources=sorted({s.article_id for s in steps}) or ["KB0010001"],
    )


def _evidence(article_id: str = "KB0010001", section: str = "Resolution Steps") -> EvidenceItem:
    return EvidenceItem(
        article_id=article_id,
        article_number=article_id,
        version="3",
        title="VPN error 807",
        section=section,
        chunk_index=0,
        text="Restart the VPN client service.",
        fused_score=0.9,
        relevance=0.87,
    )


def test_structure_passes_for_a_normal_draft() -> None:
    draft = _draft([_step("Restart the VPN client service.")])
    assert validate_structure(draft) == []


def test_structure_fails_when_there_are_no_steps() -> None:
    draft = Draft(steps=[], rendered="1. Restart the VPN client.", sources=["KB0010001"])
    issues = validate_structure(draft)
    assert any(i.category is ValidationCategory.STRUCTURE for i in issues)


def test_structure_fails_when_rendered_text_is_blank() -> None:
    draft = Draft(
        steps=[_step("Restart the VPN client service.")],
        rendered="   ",
        sources=["KB0010001"],
    )
    issues = validate_structure(draft)
    assert any(i.category is ValidationCategory.STRUCTURE for i in issues)


def test_field_lengths_pass_within_the_default_limits() -> None:
    draft = _draft([_step("Restart the VPN client service.")])
    assert validate_field_lengths(draft) == []


def test_field_lengths_fail_when_a_step_is_too_long() -> None:
    draft = _draft([_step("x" * (DEFAULT_FIELD_LIMITS.max_step_text_chars + 1))])
    issues = validate_field_lengths(draft)
    assert len(issues) == 1
    assert issues[0].category is ValidationCategory.FIELD_LENGTH
    assert issues[0].step_index == 0


def test_field_lengths_fail_when_rendered_text_is_too_long() -> None:
    draft = _draft(
        [_step("Restart the VPN client service.")],
        rendered="x" * (DEFAULT_FIELD_LIMITS.max_rendered_chars + 1),
    )
    issues = validate_field_lengths(draft)
    assert any(i.step_index is None for i in issues)  # applies to the whole draft, not one step


def test_field_lengths_respect_custom_limits() -> None:
    draft = _draft([_step("twelve chars")])
    tight = FieldLimits(max_step_text_chars=5, max_rendered_chars=5)
    issues = validate_field_lengths(draft, limits=tight)
    assert len(issues) == 2  # both the step and the rendered text are now over limit


def test_action_contract_passes_for_an_ordinary_engineer_instruction() -> None:
    draft = _draft([_step("Restart the VPN client service and confirm the tunnel reconnects.")])
    assert validate_action_contract(draft) == []


def test_action_contract_does_not_flag_a_human_engineer_being_told_to_contact_someone() -> None:

    draft = _draft([_step("Escalate to the network team for a vendor callback.")])
    assert validate_action_contract(draft) == []


@pytest.mark.parametrize(
    "text",
    [
        "I have already closed this incident and reset the user's password directly.",
        "I will resolve this ticket once the restart completes.",
        "Closing this incident now that the fix has been applied.",
        "Contacting the user directly to confirm the outage is resolved.",
        "Granting the requester temporary admin access to proceed.",
        "Approving the change so the fix can be deployed.",
    ],
)
def test_action_contract_flags_out_of_contract_language(text: str) -> None:
    draft = _draft([_step(text)])
    issues = validate_action_contract(draft)
    assert len(issues) == 1
    assert issues[0].category is ValidationCategory.ACTION_CONTRACT
    assert str(sorted(ALLOWED_ACTIONS)) in issues[0].detail


def test_action_contract_uses_the_disallowed_action_examples_from_the_seed_set(
    dataset: dict[str, Any],
) -> None:
    for case in dataset["disallowed_action_examples"]:
        draft = _draft(
            [
                _step(
                    case["draft_step_text"],
                    article_id=case["article_id"],
                    section=case["section"],
                )
            ]
        )
        issues = validate_action_contract(draft)
        flagged = any(i.category is ValidationCategory.ACTION_CONTRACT for i in issues)
        assert flagged is case["expected_action_contract_flagged"], case["id"]


# -- run_all ------------------------------------------------------------------------------


def test_run_all_passes_a_clean_draft() -> None:
    draft = _draft([_step("Restart the VPN client service.")])
    assert run_all(draft, [_evidence()]) == []


def test_run_all_short_circuits_on_structural_failure() -> None:
    # An empty draft has nothing further worth checking; run_all should not
    # also report field-length/action-contract issues that don't apply.
    draft = Draft(steps=[], rendered="", sources=[])
    issues = run_all(draft, [_evidence()])
    assert {i.category for i in issues} == {ValidationCategory.STRUCTURE}


def test_run_all_reports_every_non_structural_issue_together() -> None:
    long_and_bad = (
        "I have already closed this incident. " + "x" * DEFAULT_FIELD_LIMITS.max_step_text_chars
    )
    draft = _draft([_step(long_and_bad)])
    issues = run_all(draft, [_evidence()])
    categories = {i.category for i in issues}
    assert ValidationCategory.FIELD_LENGTH in categories
    assert ValidationCategory.ACTION_CONTRACT in categories
