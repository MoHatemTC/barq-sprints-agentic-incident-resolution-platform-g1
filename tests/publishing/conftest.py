"""Shared fixtures for app.publishing tests."""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import pytest

from app.publishing.payload import U_SOURCE_ID_FIELD
from app.publishing.servicenow_kb import KB_TABLE, ServiceNowKBClient
from tests.helpers import mock_settings

INSTANCE = "https://fake-pdi.service-now.com"
KB_SYS_ID = "kb-base-1111111111111111"


class FakeServiceNow:
    """In-memory kb_knowledge table speaking the Table API wire format."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.next_sys_id = 1
        self.query_returns_400 = False
        self.reject_auth = False
        self.dict_returns_error = False
        self.cat_returns_error = False
        self.kb_version_returns_error = False
        self.missing_schema_columns = False
        self.tamper_next_readback: tuple[str, str] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)

        if self.reject_auth:
            return httpx.Response(401, json={"error": "unauthorized"})

        if path == "/oauth_token.do":
            return httpx.Response(
                200,
                json={
                    "access_token": "fake_oauth_bearer_token",
                    "token_type": "Bearer",
                    "expires_in": 1800,
                    "refresh_token": "fake_refresh_token",
                },
            )

        if request.method == "GET" and path == f"/api/now/table/{KB_TABLE}":
            if self.query_returns_400:
                return httpx.Response(400, json={"error": "Invalid query"})
            query = params.get("sysparm_query", "")

            # Filter rows by query
            matches = self.rows
            if f"{U_SOURCE_ID_FIELD}=" in query:
                parts = query.split("^")
                source_id = None
                kb_id = None
                for part in parts:
                    if part.startswith(f"{U_SOURCE_ID_FIELD}="):
                        source_id = part.split("=", 1)[1]
                    elif part.startswith("kb_knowledge_base="):
                        kb_id = part.split("=", 1)[1]

                if source_id:
                    matches = [r for r in matches if r.get(U_SOURCE_ID_FIELD) == source_id]
                if kb_id:
                    matches = [r for r in matches if r.get("kb_knowledge_base") == kb_id]

            return httpx.Response(200, json={"result": matches})

        if request.method == "POST" and path == f"/api/now/table/{KB_TABLE}":
            body = _json.loads(request.read())
            row = {
                "sys_id": f"sys{self.next_sys_id:011d}",
                "version": {"value": f"ver{self.next_sys_id:011d}"},
                **body,
            }
            self.next_sys_id += 1
            self.rows.append(row)
            return httpx.Response(201, json={"result": row})

        if request.method == "PATCH" and path.startswith(f"/api/now/table/{KB_TABLE}/"):
            sys_id = path.rsplit("/", 1)[-1]
            body = _json.loads(request.read())
            for row in self.rows:
                if row["sys_id"] == sys_id:
                    row.update(body)
                    return httpx.Response(200, json={"result": row})
            return httpx.Response(404, json={"error": "not found"})

        if request.method == "GET" and path.startswith(f"/api/now/table/{KB_TABLE}/"):
            sys_id = path.rsplit("/", 1)[-1]
            for row in self.rows:
                if row["sys_id"] == sys_id:
                    served = dict(row)
                    if self.tamper_next_readback:
                        field, value = self.tamper_next_readback
                        served[field] = value
                        self.tamper_next_readback = None
                    return httpx.Response(200, json={"result": served})
            return httpx.Response(404, json={"error": "not found"})

        if path == "/api/now/table/sys_dictionary":
            if self.dict_returns_error:
                return httpx.Response(
                    500, json={"error": "dictionary table unavailable"}
                )
            if request.method == "GET":
                query = params.get("sysparm_query", "")
                if self.missing_schema_columns:
                    return httpx.Response(200, json={"result": []})
                # Pretend requested element exists
                element = query.split("element=")[-1] if "element=" in query else "elem"
                return httpx.Response(200, json={"result": [{"element": element}]})

        if path.startswith("/api/now/table/sys_properties"):
            if request.method == "GET":
                return httpx.Response(
                    200, json={"result": [{"sys_id": "prop1", "value": "true"}]}
                )
            if request.method in ("PATCH", "POST"):
                return httpx.Response(200, json={"result": {"sys_id": "prop1"}})

        if path == "/api/now/table/kb_category":
            if self.cat_returns_error:
                return httpx.Response(500, json={"error": "category table unavailable"})
            if request.method == "GET":
                return httpx.Response(200, json={"result": []})
            if request.method == "POST":
                body = _json.loads(request.read())
                return httpx.Response(
                    201, json={"result": {"sys_id": f"cat_{body.get('value', 'x')}"}}
                )

        if path.startswith("/api/now/table/kb_version"):
            if self.kb_version_returns_error:
                return httpx.Response(500, json={"error": "kb_version update failed"})
            return httpx.Response(200, json={"result": {"version": "1.0"}})

        return httpx.Response(405, json={"error": "method not allowed"})

    def build_client(self) -> ServiceNowKBClient:
        settings = mock_settings(
            servicenow_instance_url=INSTANCE,
            servicenow_kb_id=KB_SYS_ID,
            servicenow_username="svc_user",
            servicenow_password="svc_password",
            servicenow_client_id="barq_oauth_client",
            servicenow_client_secret="BarqOAuthSecret2026Token",
        )
        http_client = httpx.AsyncClient(
            base_url=INSTANCE,
            transport=httpx.MockTransport(self.handler),
        )
        return ServiceNowKBClient(settings, http_client=http_client)


@pytest.fixture
def fake() -> FakeServiceNow:
    return FakeServiceNow()
