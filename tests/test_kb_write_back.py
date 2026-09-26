"""S3.5 commit 3: the registry-guarded KB write-back gate.

Covers the three moving parts the write-back depends on:
- the retrieval filter fallback actually referencing DEFAULT_WORKFLOW_STATES
  (the constant was dead code — the hardcoded [published] fallback governed
  every real search), so human_resolved articles are discoverable;
- ``publish_kb_article`` registered HIGH_RISK through ``extra_registrations``
  and refused exactly like any high-risk tool without a well-formed approval;
- the publish handler mapping HUMAN_RESOLVED to a *published copy* for
  ServiceNow (its choice list cannot hold the value and _verify_stored is
  fail-closed) while the original article keeps the human_resolved marker.
"""

from __future__ import annotations

import pytest
from qdrant_client import models as qm

from agent.servicenow import IncidentGateway
from agent.tools import build_servicenow_tool_registry
from agent.tools.permissions import PermissionClass
from agent.tools.registry import (
    ApprovalCheckResult,
    RefusalReason,
    RegistryRefusalError,
    ToolCallContext,
    ToolRegistration,
)
from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.publishing.exceptions import ServiceNowWriteRejectedError
from app.publishing.payload import build_kb_payload
from app.retrieval.filters import DEFAULT_WORKFLOW_STATES, build_metadata_filter
from app.publishing.servicenow_kb import _verify_stored
from tests.agent_support import FakeServiceNow, Tracer, shared_runner

EXECUTION_ID = "11111111-1111-4111-8111-111111111111"


def _article(**overrides: object) -> Article:
    fields: dict[str, object] = {
        "article_number": "KB1001",
        "version": "1.0",
        "title": "Resolving Intermittent Corporate VPN Drops",
        "short_description": "Set MTU 1400 and restart the tunnel interface.",
        "body": "1. Set MTU 1400 on the tunnel.\n2. Restart the interface.",
        "category": "network",
        "service": "corporate-vpn",
        "workflow_state": WorkflowState.HUMAN_RESOLVED,
        "security_level": SecurityLevel.INTERNAL,
    }
    fields.update(overrides)
    return Article(**fields)  # type: ignore[arg-type]


def _gateway() -> IncidentGateway:
    tracer = Tracer(None)
    return IncidentGateway(lambda: FakeServiceNow(), tracer, runner=shared_runner())


class StubApprovalChecker:
    """Permissive/scripted stand-in for PostgreSQLApprovalChecker."""

    def __init__(self, result: ApprovalCheckResult) -> None:
        self.result = result

    async def check(self, *, execution_id: str, tool_name: str) -> ApprovalCheckResult:
        return self.result


PERMITTED = StubApprovalChecker(ApprovalCheckResult(True))
REFUSED_MISSING = StubApprovalChecker(ApprovalCheckResult(False, RefusalReason.APPROVAL_MISSING))
REFUSED_SCOPE = StubApprovalChecker(
    ApprovalCheckResult(False, RefusalReason.APPROVAL_SCOPE_INVALID)
)


class FakeKBClient:
    """Stands in for ServiceNowKBClient in registry-level tests."""

    def __init__(self) -> None:
        self.record: dict[str, object] = {"sys_id": "sn-sys-1"}

    async def find_by_source_id(
        self, article_id: str, *, kb_sys_id: str | None = None
    ) -> dict[str, object] | None:
        self.record["u_source_id"] = article_id
        return self.record


def _registration() -> ToolRegistration:
    from app.publishing.servicenow_kb import make_kb_publish_handler

    return ToolRegistration(
        "publish_kb_article",
        PermissionClass.HIGH_RISK,
        make_kb_publish_handler(FakeKBClient(), "kb-sys-1"),
    )


def _match_any_for(build_filter: qm.Filter, key: str) -> list[str] | None:
    for condition in build_filter.must or []:
        if getattr(condition, "key", None) == key:
            return list(condition.match.any or [])  # type: ignore[union-attr]
    return None


class TestFilterFallback:
    def test_human_resolved_in_default_workflow_states(self) -> None:
        assert WorkflowState.HUMAN_RESOLVED in DEFAULT_WORKFLOW_STATES

    def test_default_fallback_filter_discovers_human_resolved(self) -> None:
        # No explicit workflow_state — the exact path the agent retriever takes.
        matched = _match_any_for(build_metadata_filter(), "workflow_state")
        assert matched is not None
        assert set(matched) == {
            WorkflowState.PUBLISHED.value,
            WorkflowState.HUMAN_RESOLVED.value,
        }


class TestRegistration:
    @staticmethod
    def _patch_publish(monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_publish(client: object, article: Article, kb_sys_id: str) -> str:
            return "created"

        monkeypatch.setattr("app.publishing.servicenow_kb.publish_article", fake_publish)

    async def test_publish_kb_article_invokable_and_returns_sys_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_publish(monkeypatch)
        registry = build_servicenow_tool_registry(
            _gateway(), approval_checker=PERMITTED, extra_registrations=[_registration()]
        )
        sys_id = await registry.invoke(
            "publish_kb_article",
            context=ToolCallContext(execution_id=EXECUTION_ID),
            arguments={"article": _article()},
        )
        assert sys_id == "sn-sys-1"

    async def test_refused_without_approval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_publish(monkeypatch)
        registry = build_servicenow_tool_registry(
            _gateway(),
            approval_checker=REFUSED_MISSING,
            extra_registrations=[_registration()],
        )
        with pytest.raises(RegistryRefusalError) as exc:
            await registry.invoke(
                "publish_kb_article",
                context=ToolCallContext(execution_id=EXECUTION_ID),
                arguments={"article": _article()},
            )
        assert exc.value.reason is RefusalReason.APPROVAL_MISSING

    async def test_refused_on_malformed_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_publish(monkeypatch)
        registry = build_servicenow_tool_registry(
            _gateway(),
            approval_checker=REFUSED_SCOPE,
            extra_registrations=[_registration()],
        )
        with pytest.raises(RegistryRefusalError) as exc:
            await registry.invoke(
                "publish_kb_article",
                context=ToolCallContext(execution_id=EXECUTION_ID),
                arguments={"article": _article()},
            )
        assert exc.value.reason is RefusalReason.APPROVAL_SCOPE_INVALID

    def test_duplicate_extra_registration_name_raises(self) -> None:
        duplicate = ToolRegistration("read_incident", PermissionClass.READ, lambda: None)
        with pytest.raises(ValueError):
            build_servicenow_tool_registry(
                _gateway(), approval_checker=PERMITTED, extra_registrations=[duplicate]
            )


class TestReadBackVerification:
    """Mentor polish: the verify-stored step must fail closed on its own."""

    def test_verify_stored_rejects_state_mismatch(self) -> None:
        article = _article(workflow_state=WorkflowState.PUBLISHED)
        sent = build_kb_payload(article, "kb-sys-1")
        stored = {**sent, "workflow_state": "draft"}
        with pytest.raises(ServiceNowWriteRejectedError):
            _verify_stored(stored, sent, article.article_id)

    def test_verify_stored_rejects_body_tampering(self) -> None:
        article = _article(workflow_state=WorkflowState.PUBLISHED)
        sent = build_kb_payload(article, "kb-sys-1")
        stored = {**sent, "text": "<p>tampered — provenance marker removed</p>"}
        with pytest.raises(ServiceNowWriteRejectedError):
            _verify_stored(stored, sent, article.article_id)

    def test_verify_stored_accepts_faithful_readback(self) -> None:
        article = _article(workflow_state=WorkflowState.PUBLISHED)
        sent = build_kb_payload(article, "kb-sys-1")
        _verify_stored(dict(sent), sent, article.article_id)  # must not raise


class TestPublishHandler:
    async def test_maps_human_resolved_to_published_copy_for_servicenow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        async def fake_publish(client: object, article: Article, kb_sys_id: str) -> str:
            captured["article"] = article
            captured["kb_sys_id"] = kb_sys_id
            return "created"

        monkeypatch.setattr("app.publishing.servicenow_kb.publish_article", fake_publish)
        from app.publishing.servicenow_kb import make_kb_publish_handler

        handler = make_kb_publish_handler(FakeKBClient(), "kb-sys-1")
        original = _article()
        sys_id = await handler(original)

        assert sys_id == "sn-sys-1"
        assert captured["kb_sys_id"] == "kb-sys-1"
        # ServiceNow sees a published copy...
        assert captured["article"].workflow_state is WorkflowState.PUBLISHED  # type: ignore[union-attr]
        # ...while the original keeps the human_resolved marker for Qdrant.
        assert original.workflow_state is WorkflowState.HUMAN_RESOLVED

    async def test_non_human_states_pass_through_unmapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Article] = {}

        async def fake_publish(client: object, article: Article, kb_sys_id: str) -> str:
            captured["article"] = article
            return "unchanged"

        monkeypatch.setattr("app.publishing.servicenow_kb.publish_article", fake_publish)
        from app.publishing.servicenow_kb import make_kb_publish_handler

        handler = make_kb_publish_handler(FakeKBClient(), "kb-sys-1")
        curated = _article(workflow_state=WorkflowState.PUBLISHED)
        await handler(curated)
        assert captured["article"] is curated
