"""S2.1 contract tests for every documented Sprint 2-4 endpoint.

Route existence, HTTP methods, authentication, request validation (422), schema-valid
responses, malformed path parameters, the DLQ Operator RBAC matrix, config secret
redaction, and the global error taxonomy envelope. Endpoint behaviour beyond the
HTTP boundary (worker retries, DLQ engine, eval runner, HITL frontend) is out of
scope per the S2.1 boundary: stubs must be schema-valid and must not crash.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

import tests.helpers as h
from api.schemas.approvals import ApprovalResponse
from api.schemas.config import REDACTED_SENTINEL, RedactedConfigResponse
from api.schemas.dlq import DLQEventResponse, DLQReplayResponse
from api.schemas.eval import EvalResultResponse, EvalRunResponse
from api.schemas.executions import ExecutionResponse, IncidentExecutionsResponse, TraceResponse
from app.core.constants import ERROR_STATUS_MAP
from app.db.models import Execution, ExecutionNodeState
from app.exceptions.app_errors import NotImplementedStubError
from app.main import create_app
from tests.helpers import mock_settings

AUTH = h.AUTH_HEADERS


def _execution_row(**overrides) -> Execution:
    now = datetime.now(UTC)
    defaults: dict = dict(
        execution_id=uuid4(),
        event_record_id=uuid4(),
        incident_sys_id=h.VALID_SYS_ID,
        status="succeeded",
        started_at=now,
        ended_at=now,
        termination_cause="completed",
        updated_at=now,
    )
    defaults.update(overrides)
    return Execution(**defaults)


def _node_row(execution_id) -> ExecutionNodeState:
    now = datetime.now(UTC)
    return ExecutionNodeState(
        id=uuid4(),
        execution_id=execution_id,
        sequence_number=1,
        node_name="triage",
        attempt=1,
        status="succeeded",
        started_at=now,
        ended_at=now,
        evidence=[{"source": "manual"}],
    )


def _db_session_mock(get_values: dict) -> MagicMock:
    """AsyncSession mock where db.get(Model, id) returns get_values.get(Model)."""
    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, pk: get_values.get(model))
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


def _session_factory(session: MagicMock) -> MagicMock:
    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return None

    return MagicMock(return_value=_Ctx())


def _execute_result(rows: list):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


@pytest.fixture
def app():
    settings = mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN)
    application = create_app(settings=settings)
    application.state.engine = MagicMock()
    application.state.session_factory = MagicMock()
    application.state.redis = MagicMock()
    application.state.redis.ping = AsyncMock(return_value=True)
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _with_db(app, session: MagicMock) -> None:
    app.state.session_factory = _session_factory(session)


# ---------------------------------------------------------------------------
# Route surface & authentication
# ---------------------------------------------------------------------------
DOCUMENTED_ENDPOINTS = {
    "GET /api/v1/executions/{execution_id}",
    "GET /api/v1/executions/{execution_id}/trace",
    "GET /api/v1/incidents/{sys_id}/executions",
    "GET /api/v1/approvals",
    "POST /api/v1/approvals/{id}/decide",
    "GET /api/v1/dlq",
    "POST /api/v1/dlq/{event_id}/replay",
    "GET /api/v1/eval/results",
    "POST /api/v1/eval/run",
    "GET /api/v1/config",
    "GET /health",
    "GET /ready",
    "POST /api/v1/webhook/incident",
}


def test_all_documented_routes_exist_with_correct_methods(app) -> None:
    paths = app.openapi()["paths"]
    found = {f"{method.upper()} {path}" for path, ops in paths.items() for method in ops}
    missing = DOCUMENTED_ENDPOINTS - found
    assert not missing, f"documented endpoints missing from the app: {sorted(missing)}"


@pytest.mark.asyncio
async def test_every_protected_endpoint_rejects_missing_auth_with_401(client) -> None:
    protected = [
        ("POST", "/api/v1/webhook/incident"),
        ("GET", "/api/v1/executions/" + str(uuid4())),
        ("GET", f"/api/v1/executions/{uuid4()}/trace"),
        ("GET", f"/api/v1/incidents/{h.VALID_SYS_ID}/executions"),
        ("GET", "/api/v1/approvals"),
        ("POST", f"/api/v1/approvals/{uuid4()}/decide"),
        ("GET", "/api/v1/dlq"),
        ("POST", "/api/v1/dlq/evt-1/replay"),
        ("GET", "/api/v1/eval/results"),
        ("POST", "/api/v1/eval/run"),
        ("GET", "/api/v1/config"),
    ]
    for method, url in protected:
        resp = await client.request(method, url)
        assert resp.status_code == 401, f"{method} {url} must require authentication"
        assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    # Probes stay unauthenticated by design.
    for url in ("/health", "/ready"):
        resp = await client.get(url)
        assert resp.status_code != 401, f"{url} is a probe and must not require auth"


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execution_endpoints_schema_valid_404_and_422(app, client) -> None:
    execution = _execution_row()
    session = _db_session_mock({Execution: execution})
    session.execute = AsyncMock(return_value=_execute_result([_node_row(execution.execution_id)]))
    _with_db(app, session)

    got = await client.get(f"/api/v1/executions/{execution.execution_id}", headers=AUTH)
    assert got.status_code == 200
    ExecutionResponse.model_validate(got.json())

    trace = await client.get(f"/api/v1/executions/{execution.execution_id}/trace", headers=AUTH)
    assert trace.status_code == 200
    trace_body = TraceResponse.model_validate(trace.json())
    assert len(trace_body.node_states) == 1

    # Not found -> documented 404 envelope.
    session.get = AsyncMock(return_value=None)
    missing = await client.get(f"/api/v1/executions/{uuid4()}", headers=AUTH)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    # Malformed path parameter -> 422, no crash.
    for url in ("/api/v1/executions/not-a-uuid", "/api/v1/executions/not-a-uuid/trace"):
        resp = await client.get(url, headers=AUTH)
        assert resp.status_code == 422, f"{url} must reject malformed UUID with 422"
        assert resp.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_incident_executions_returns_schema_valid_list(app, client) -> None:
    rows = [_execution_row(), _execution_row()]
    session = _db_session_mock({})
    session.execute = AsyncMock(return_value=_execute_result(rows))
    _with_db(app, session)

    resp = await client.get(f"/api/v1/incidents/{h.VALID_SYS_ID}/executions", headers=AUTH)
    assert resp.status_code == 200
    body = IncidentExecutionsResponse.model_validate(resp.json())
    assert body.incident_sys_id == h.VALID_SYS_ID
    assert len(body.executions) == 2

    # Empty result is a valid contract response.
    session.execute = AsyncMock(return_value=_execute_result([]))
    empty = await client.get(f"/api/v1/incidents/{h.VALID_SYS_ID}/executions", headers=AUTH)
    assert empty.status_code == 200
    assert empty.json()["executions"] == []


@pytest.mark.asyncio
async def test_incident_executions_rejects_over_length_sys_id(app, client) -> None:
    session = _db_session_mock({})
    session.execute = AsyncMock(return_value=_execute_result([]))
    _with_db(app, session)
    resp = await client.get(f"/api/v1/incidents/{'a' * 40}/executions", headers=AUTH)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_approvals_list_and_decide_stub_contract(app, client) -> None:
    session = _db_session_mock({})  # no existing approval/execution -> stub path
    session.execute = AsyncMock(return_value=_execute_result([]))
    _with_db(app, session)

    listed = await client.get("/api/v1/approvals", headers=AUTH)
    assert listed.status_code == 200
    assert listed.json() == []

    filtered = await client.get(f"/api/v1/approvals?execution_id={uuid4()}", headers=AUTH)
    assert filtered.status_code == 200

    approval_id = uuid4()
    decision = {"decision": "approved", "decided_by": "lead_ops", "reason": "verified"}
    decided = await client.post(
        f"/api/v1/approvals/{approval_id}/decide", json=decision, headers=AUTH
    )
    assert decided.status_code == 200, decided.text
    body = ApprovalResponse.model_validate(decided.json())
    assert body.decision == "approved"
    assert body.decided_by == "lead_ops"

    # Invalid decision values / extra fields / malformed UUID -> 422.
    invalid_bodies = [
        {"decision": "maybe", "decided_by": "x"},
        {"decision": "approved"},  # missing decided_by
        {"decision": "approved", "decided_by": "x", "extra": 1},
    ]
    for body_ in invalid_bodies:
        resp = await client.post(f"/api/v1/approvals/{uuid4()}/decide", json=body_, headers=AUTH)
        assert resp.status_code == 422, f"{body_} must return 422"

    bad_uuid = await client.post("/api/v1/approvals/not-a-uuid/decide", json=decision, headers=AUTH)
    assert bad_uuid.status_code == 422


# ---------------------------------------------------------------------------
# DLQ + Operator RBAC
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dlq_list_contract_and_replay_rbac_matrix(client) -> None:
    listed = await client.get("/api/v1/dlq", headers=AUTH)
    assert listed.status_code == 200
    assert isinstance(listed.json(), list)
    if listed.json():
        DLQEventResponse.model_validate(listed.json()[0])

    event_id = "evt-dlq-42"
    # 1. Missing authentication -> 401.
    no_auth = await client.post(f"/api/v1/dlq/{event_id}/replay")
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    # 2/3. Authenticated normal user or invalid role -> 403 FORBIDDEN.
    for role_header in ({}, {"X-User-Role": "user"}, {"X-User-Role": "viewer"}):
        resp = await client.post(f"/api/v1/dlq/{event_id}/replay", headers={**AUTH, **role_header})
        assert resp.status_code == 403, f"role={role_header or 'none'} must be 403"
        body = resp.json()
        assert body["error"]["code"] == "PERMISSION_DENIED"
        assert "role" in body["error"]["message"].lower()

    # 4. Operator -> documented accepted stub response (202).
    replayed = await client.post(f"/api/v1/dlq/{event_id}/replay", headers=h.OPERATOR_HEADERS)
    assert replayed.status_code == 202
    validated = DLQReplayResponse.model_validate(replayed.json())
    assert validated.event_id == event_id
    assert validated.status == "accepted"


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_eval_run_and_results_stub_contract(client) -> None:
    run = await client.post(
        "/api/v1/eval/run",
        json={"dataset_name": "servicenow-incident-benchmarks-v1", "sample_size": 10},
        headers=AUTH,
    )
    assert run.status_code == 202
    run_body = EvalRunResponse.model_validate(run.json())
    assert run_body.status == "started"

    results = await client.get("/api/v1/eval/results", headers=AUTH)
    assert results.status_code == 200
    entries = results.json()
    assert isinstance(entries, list) and entries
    EvalResultResponse.model_validate(entries[0])

    filtered = await client.get("/api/v1/eval/results?run_id=eval-x", headers=AUTH)
    assert filtered.status_code == 200
    assert filtered.json()[0]["run_id"] == "eval-x"

    # Request schema rejects invalid input.
    invalid = [
        {"sample_size": 10},  # missing dataset_name
        {"dataset_name": "d", "sample_size": 0},  # below ge=1
        {"dataset_name": "d", "unexpected": True},  # extra field
    ]
    for body_ in invalid:
        resp = await client.post("/api/v1/eval/run", json=body_, headers=AUTH)
        assert resp.status_code == 422, f"{body_} must return 422"


# ---------------------------------------------------------------------------
# Config secret redaction
# ---------------------------------------------------------------------------
RAW_SECRETS = {
    "servicenow_password": "raw-sn-pw-ZX9",
    "client_secret": "raw-client-secret-QW7",
    "postgres_password": "raw-pg-pw-PL4",
    "redis_password": "raw-redis-pw-KV2",
}


@pytest.mark.asyncio
async def test_config_redacts_every_secret_recursively(app, client) -> None:
    from pydantic import SecretStr

    raw_settings = mock_settings(
        webhook_auth_token=h.WEBHOOK_TOKEN,
        servicenow_password=SecretStr(RAW_SECRETS["servicenow_password"]),
        servicenow_client_secret=SecretStr(RAW_SECRETS["client_secret"]),
        postgres_password=SecretStr(RAW_SECRETS["postgres_password"]),
        redis_password=SecretStr(RAW_SECRETS["redis_password"]),
    )
    app.state.settings = raw_settings

    resp = await client.get("/api/v1/config", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    RedactedConfigResponse.model_validate(body)

    for field in (
        "servicenow_password",
        "client_secret",
        "postgres_password",
        "redis_password",
        "webhook_auth_token",
    ):
        assert body.get(field) == REDACTED_SENTINEL, f"{field} must be redacted"

    # Recursive scan: no configured secret value may appear anywhere in the payload.
    def _walk(node) -> list[str]:
        if isinstance(node, dict):
            return [x for v in node.values() for x in _walk(v)]
        if isinstance(node, list):
            return [x for v in node for x in _walk(v)]
        return [str(node)]

    rendered = " ".join(_walk(body))
    for secret in [*RAW_SECRETS.values(), h.WEBHOOK_TOKEN]:
        assert secret not in rendered, f"secret {secret!r} leaked in /config response"


# ---------------------------------------------------------------------------
# Global error taxonomy
# ---------------------------------------------------------------------------
def test_error_taxonomy_status_mapping() -> None:
    assert ERROR_STATUS_MAP == {
        **ERROR_STATUS_MAP,
        NotImplementedStubError: 501,
    }
    from app.exceptions.app_errors import (
        AuthenticationError,
        ContractValidationError,
        PermissionDeniedError,
        ResourceNotFoundError,
        ServiceUnavailableError,
    )

    assert {
        AuthenticationError: 401,
        PermissionDeniedError: 403,
        ResourceNotFoundError: 404,
        ContractValidationError: 422,
        ServiceUnavailableError: 503,
        NotImplementedStubError: 501,
    } == {
        k: v
        for k, v in ERROR_STATUS_MAP.items()
        if k
        in (
            AuthenticationError,
            PermissionDeniedError,
            ResourceNotFoundError,
            ContractValidationError,
            ServiceUnavailableError,
            NotImplementedStubError,
        )
    }


@pytest.mark.asyncio
async def test_error_envelope_shape_for_each_taxonomy_error() -> None:
    from fastapi import FastAPI

    from app.exceptions.app_errors import (
        AuthenticationError,
        ContractValidationError,
        PermissionDeniedError,
        ResourceNotFoundError,
        ServiceUnavailableError,
    )
    from app.exceptions.handlers import register_exception_handlers

    errors = [
        (AuthenticationError(), 401),
        (PermissionDeniedError(), 403),
        (ResourceNotFoundError(), 404),
        (ContractValidationError(), 422),
        (ServiceUnavailableError(), 503),
        (NotImplementedStubError(), 501),
    ]

    tax_app = FastAPI()
    register_exception_handlers(tax_app)

    @tax_app.get("/boom/{code}")
    async def boom(code: str):
        for err, _ in errors:
            if type(err).__name__.lower().startswith(code.lower()[:4]):
                raise err
        raise errors[0][0]

    async with AsyncClient(transport=ASGITransport(app=tax_app), base_url="http://t") as ac:
        for err, expected_status in errors:
            name = type(err).__name__
            resp = await ac.get(f"/boom/{name}")
            assert resp.status_code == expected_status, f"{name} must map to {expected_status}"
            envelope = resp.json()["error"]
            for field in ("code", "message", "details", "correlation_id", "timestamp"):
                assert field in envelope, f"{name} envelope missing '{field}'"
            assert envelope["correlation_id"]
            assert envelope["timestamp"]
