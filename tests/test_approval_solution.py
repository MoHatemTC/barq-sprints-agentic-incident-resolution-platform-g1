"""S3.5 commit 1: the human-solution field on ``ApprovalDecisionRequest``.

The solution is knowledge a reviewer contributes when resolving an escalated
incident — distinct from ``reason`` (decision metadata). On the decide path it
is folded into the immutable ``evidence`` JSONB together with the registry tool
name, so the S3.2 high-risk approval checker later finds a well-formed
``evidence.tool_name`` for ``publish_kb_article`` (S3.5 design decision D3).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.approvals import ApprovalDecisionRequest, fold_solution_into_evidence
from observability.redaction import REDACTED


def _request(**overrides: object) -> ApprovalDecisionRequest:
    payload: dict[str, object] = {"decision": "approved"}
    payload.update(overrides)
    return ApprovalDecisionRequest(**payload)


class TestSolutionField:
    def test_accepts_solution(self) -> None:
        request = _request(solution="bumped max_connections and restarted the pooler")
        assert request.solution == "bumped max_connections and restarted the pooler"

    def test_solution_defaults_to_none(self) -> None:
        assert _request().solution is None

    def test_max_length_8000_enforced(self) -> None:
        assert _request(solution="x" * 8000).solution == "x" * 8000
        with pytest.raises(ValidationError):
            _request(solution="x" * 8001)

    def test_coexists_with_reason_independently(self) -> None:
        both = _request(reason="looked fine", solution="reinstalled the client")
        assert both.reason == "looked fine"
        assert both.solution == "reinstalled the client"
        only_solution = _request(solution="reinstalled the client")
        assert only_solution.reason is None

    def test_extra_forbid_still_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            _request(solutions="typo-plural")

    def test_human_solution_alias_accepted(self) -> None:
        """The mentor's spec names the field 'human_solution'; both spellings work."""
        request = _request(human_solution="flushed the vpn routes")
        assert request.solution == "flushed the vpn routes"


class TestEvidenceFold:
    """The decide endpoint persists evidence through this fold (S3.5 D3)."""

    def test_solution_folds_into_evidence_with_tool_name(self) -> None:
        folded = fold_solution_into_evidence(None, "used the service account")
        assert folded["tool_name"] == "publish_kb_article"

    def test_merge_preserves_caller_evidence(self) -> None:
        folded = fold_solution_into_evidence({"incident": "INC0010099"}, "flushed routes")
        assert folded["incident"] == "INC0010099"
        assert folded["tool_name"] == "publish_kb_article"
        assert "flushed routes" in folded["solution"]

    def test_solution_is_redacted_in_fold(self) -> None:
        folded = fold_solution_into_evidence(None, "token was Bearer abcdefgh12345678 ok")
        assert "abcdefgh12345678" not in folded["solution"]
        assert REDACTED in folded["solution"]

    def test_tool_name_conflict_is_overwritten(self) -> None:
        folded = fold_solution_into_evidence({"tool_name": "something_else"}, "fix")
        assert folded["tool_name"] == "publish_kb_article"

    def test_no_solution_returns_evidence_verbatim(self) -> None:
        assert fold_solution_into_evidence({"a": 1}, None) == {"a": 1}
        assert fold_solution_into_evidence(None, None) is None
