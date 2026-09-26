from __future__ import annotations

import base64
import json
from dataclasses import replace
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.webhook_oauth import (
    TOKEN_PATH,
    InvalidClientError,
    InvalidTokenError,
    WebhookOAuthConfig,
    bearer_dependency,
    create_token_router,
    issue_access_token,
    verify_access_token,
)

CONFIG = WebhookOAuthConfig(
    client_id="barq-servicenow",
    client_secret="client-secret-for-tests",  # noqa: S106 - test value
    signing_key="k" * 40,
    operator_client_id="barq-operator",
    operator_client_secret="operator-api-token-for-tests",  # noqa: S106 - test value
    operator_roles=("operator", "approver"),
    token_ttl_seconds=300,
)
NOW = 1_800_000_000.0
Claims = Annotated[dict[str, Any], Depends(bearer_dependency(lambda: CONFIG))]


def _token(**overrides: Any) -> str:
    return str(
        issue_access_token(CONFIG, CONFIG.client_id, CONFIG.client_secret, now=NOW, **overrides)[
            "access_token"
        ]
    )


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(create_token_router(lambda: CONFIG))

    @app.post("/api/v1/webhook/incident", status_code=202)
    def webhook(claims: Claims) -> dict[str, str]:
        return {"client": claims["sub"]}

    return TestClient(app)


class TestIssueAndVerify:
    def test_round_trip(self) -> None:
        claims = verify_access_token(CONFIG, _token(), now=NOW + 10)
        assert claims["sub"] == "barq-servicenow"
        assert claims["exp"] == int(NOW) + 300

    def test_wrong_secret_is_refused(self) -> None:
        with pytest.raises(InvalidClientError):
            issue_access_token(CONFIG, CONFIG.client_id, "wrong", now=NOW)

    def test_wrong_client_id_is_refused(self) -> None:
        with pytest.raises(InvalidClientError):
            issue_access_token(CONFIG, "someone-else", CONFIG.client_secret, now=NOW)

    def test_expired_token_is_refused(self) -> None:
        with pytest.raises(InvalidTokenError, match="expired"):
            verify_access_token(CONFIG, _token(), now=NOW + 300 + 31)

    def test_token_from_the_future_is_refused(self) -> None:
        with pytest.raises(InvalidTokenError, match="future"):
            verify_access_token(CONFIG, _token(), now=NOW - 31)

    def test_other_signing_key_is_refused(self) -> None:
        other = replace(CONFIG, signing_key="x" * 40)
        with pytest.raises(InvalidTokenError, match="signature"):
            verify_access_token(other, _token(), now=NOW)

    def test_tampered_claims_are_refused(self) -> None:
        header, payload, signature = _token().split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        claims["exp"] += 10_000
        forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        with pytest.raises(InvalidTokenError, match="signature"):
            verify_access_token(CONFIG, f"{header}.{forged}.{signature}", now=NOW)

    def test_alg_none_is_refused(self) -> None:
        payload = _token().split(".")[1]
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        with pytest.raises(InvalidTokenError):
            verify_access_token(CONFIG, f"{header}.{payload}.", now=NOW)

    @pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c.d"])
    def test_malformed_tokens_are_refused(self, token: str) -> None:
        with pytest.raises(InvalidTokenError):
            verify_access_token(CONFIG, token, now=NOW)

    def test_short_signing_key_is_rejected_at_startup(self) -> None:
        with pytest.raises(ValueError, match="signing_key"):
            WebhookOAuthConfig("id", "secret", "short")


class TestEndpoints:
    def test_client_credentials_in_body_then_webhook_accepts_the_token(self) -> None:
        client = _app()
        resp = client.post(
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": CONFIG.client_id,
                "client_secret": CONFIG.client_secret,
            },
        )
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"
        body = resp.json()
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 300

        hook = client.post(
            "/api/v1/webhook/incident",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert hook.status_code == 202
        assert hook.json() == {"client": "barq-servicenow"}

    def test_client_credentials_as_basic_header(self) -> None:
        basic = base64.b64encode(f"{CONFIG.client_id}:{CONFIG.client_secret}".encode()).decode()
        resp = _app().post(
            TOKEN_PATH,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
        )
        assert resp.status_code == 200

    def test_bad_client_secret_gets_401(self) -> None:
        resp = _app().post(
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": CONFIG.client_id,
                "client_secret": "wrong",
            },
        )
        assert resp.status_code == 401
        assert resp.json() == {"error": "invalid_client"}
        assert "access_token" not in resp.text

    def test_other_grant_types_get_400(self) -> None:
        resp = _app().post(TOKEN_PATH, data={"grant_type": "password"})
        assert resp.status_code == 400
        assert resp.json() == {"error": "unsupported_grant_type"}

    @pytest.mark.parametrize(
        "headers",
        [{}, {"Authorization": "Bearer nope"}, {"Authorization": f"Basic {CONFIG.client_secret}"}],
    )
    def test_webhook_without_a_valid_token_gets_401(self, headers: dict[str, str]) -> None:
        resp = _app().post("/api/v1/webhook/incident", headers=headers)
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"].startswith("Bearer")

    def test_the_static_client_secret_is_not_a_valid_bearer_token(self) -> None:
        resp = _app().post(
            "/api/v1/webhook/incident",
            headers={"Authorization": f"Bearer {CONFIG.client_secret}"},
        )
        assert resp.status_code == 401
