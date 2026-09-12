"""Tests for ServiceNowProvisioner preflight and bootstrap logic."""

from __future__ import annotations

from typing import Any

import pytest

from app.publishing.exceptions import (
    ServiceNowAuthError,
    ServiceNowKBSchemaError,
    ServiceNowRequestError,
)
from app.publishing.provisioning import ServiceNowProvisioner

KB_SYS_ID = "kb-base-1111111111111111"


def test_provisioner_ensure_schema(fake: Any) -> None:
    client = fake.build_client()
    provisioner = ServiceNowProvisioner(client)
    provisioner.ensure_schema()
    client.close()


def test_provisioner_ensure_schema_fails_loud_on_error(fake: Any) -> None:
    fake.dict_returns_error = True
    client = fake.build_client()
    provisioner = ServiceNowProvisioner(client)
    with pytest.raises(ServiceNowKBSchemaError, match="Failed to inspect or provision"):
        provisioner.ensure_schema()
    client.close()


def test_provisioner_ensure_categories(fake: Any) -> None:
    client = fake.build_client()
    provisioner = ServiceNowProvisioner(client)
    cats = ["network", "software", "hardware"]
    mapping = provisioner.ensure_categories(KB_SYS_ID, cats)
    client.close()

    assert len(mapping) == 3
    assert mapping["network"] == "cat_network"
    assert mapping["software"] == "cat_software"
    assert mapping["hardware"] == "cat_hardware"


def test_provisioner_ensure_categories_fails_loud_on_error(fake: Any) -> None:
    fake.cat_returns_error = True
    client = fake.build_client()
    provisioner = ServiceNowProvisioner(client)
    with pytest.raises(ServiceNowRequestError, match="Failed to query kb_category"):
        provisioner.ensure_categories(KB_SYS_ID, ["network"])
    client.close()


def test_provisioner_fails_loud_on_auth_error(fake: Any) -> None:
    fake.reject_auth = True
    client = fake.build_client()
    provisioner = ServiceNowProvisioner(client)
    with pytest.raises(ServiceNowAuthError):
        provisioner.run_preflight(KB_SYS_ID, ["network"])
    client.close()


def test_provisioner_run_preflight_orchestrates_all(fake: Any) -> None:
    client = fake.build_client()
    provisioner = ServiceNowProvisioner(client)
    mapping = provisioner.run_preflight(KB_SYS_ID, ["network", "inquiry"])
    client.close()

    assert "network" in mapping
    assert "inquiry" in mapping
