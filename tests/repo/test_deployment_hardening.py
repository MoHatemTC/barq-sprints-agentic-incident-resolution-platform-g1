"""Invariants of the deployment files that a quiet edit could undo.

Each one is a defect found on the shared host during the 2026-10-02 audit: datastores
published on every interface (protected only by a cloud firewall), unauthenticated admin
UIs started by a plain ``docker compose up``, unbounded container logs on a 19 GB disk,
unpinned images, and a deploy that builds on the host and never reclaims the space.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((REPO / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]
DATASTORES = ("postgres", "redis", "qdrant")
ADMIN_TOOLS = ("pgweb", "redis-commander")


def test_datastores_are_published_on_loopback_only() -> None:
    for name in DATASTORES:
        for port in SERVICES[name]["ports"]:
            assert str(port).startswith("127.0.0.1:"), (
                f"{name} publishes {port}: BIND_IP must never widen a datastore"
            )


def test_admin_tools_are_opt_in_and_loopback_only() -> None:
    for name in ADMIN_TOOLS:
        assert SERVICES[name].get("profiles") == ["tools"], f"{name} must sit behind a profile"
        for port in SERVICES[name]["ports"]:
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port}"


def test_every_service_has_bounded_logs() -> None:
    for name, service in SERVICES.items():
        options = service.get("logging", {}).get("options", {})
        assert options.get("max-size") and options.get("max-file"), f"{name} logs are unbounded"


def test_database_admin_image_is_pinned() -> None:
    assert not SERVICES["pgweb"]["image"].endswith(":latest")


def test_dockerfile_pins_its_base_images_and_trusts_only_loopback_proxies() -> None:
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "astral-sh/uv:latest" not in dockerfile
    assert "FROM python:3.12-slim AS" not in dockerfile  # unqualified rolling tag
    assert "--forwarded-allow-ips=*" not in dockerfile


def test_deploy_reclaims_build_leftovers_without_ever_failing_a_good_deploy() -> None:
    workflow = (REPO / ".github" / "workflows" / "deploy.yml").read_text()
    assert "docker image prune -f > /dev/null || true" in workflow
    assert 'docker builder prune -f --filter "until=72h" > /dev/null || true' in workflow
    assert "--volumes" not in workflow, "a prune must never be able to touch the data volumes"
