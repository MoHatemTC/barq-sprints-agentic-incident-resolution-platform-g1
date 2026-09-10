"""Shared pytest configuration.

`app.core.config.Settings` declares the ServiceNow connection fields as
required on purpose: the service must fail fast at startup rather than boot
with a half-configured integration. That means importing anything under
`app` needs those variables present.

Tests must not depend on a developer's local `.env`, and CI runners have none,
so the required variables are injected here with values that are obviously not
real credentials. `get_settings` is cached, so this runs before the first
import that would build a Settings instance.
"""

import os

import pytest

_REQUIRED_TEST_ENV = {
    "SERVICENOW_INSTANCE_URL": "https://dev00000.service-now.com",
    "SERVICENOW_CLIENT_ID": "test-client-id",
    "SERVICENOW_CLIENT_SECRET": "test-client-secret",
    "SERVICENOW_USERNAME": "test_service_account",
    "SERVICENOW_PASSWORD": "test-password",
}

for _key, _value in _REQUIRED_TEST_ENV.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture
def settings():
    """Settings built from the test environment above."""
    from app.core.config import get_settings

    return get_settings()
"""Shared pytest fixtures for the test suite.

Provides sample articles and temporary corpus files used across tests.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fixtures.articles import POSTGRES_V1, POSTGRES_V2, REDIS_DRAFT

from app.models.knowledge import Article


def make_article(data: dict) -> Article:
    return Article.model_validate(data)


@pytest.fixture
def article_dicts() -> list[dict]:
    return [deepcopy(POSTGRES_V2), deepcopy(POSTGRES_V1), deepcopy(REDIS_DRAFT)]


@pytest.fixture
def sample_articles(article_dicts: list[dict]) -> list[Article]:
    return [make_article(data) for data in article_dicts]


@pytest.fixture
def corpus_file(tmp_path: Path, article_dicts: list[dict]) -> Path:
    path = tmp_path / "articles.json"
    path.write_text(json.dumps(article_dicts), encoding="utf-8")
    return path
