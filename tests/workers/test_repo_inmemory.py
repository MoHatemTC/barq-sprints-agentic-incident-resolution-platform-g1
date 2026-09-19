"""In-memory backend contract tests (fast; real-Postgres twin in the
integration suite reuses the same contract body)."""

from __future__ import annotations

from uuid import uuid4

from app.workers.db import InMemoryRepo
from tests.workers.repo_contract import assert_repo_contract


def test_inmemory_backend_satisfies_contract() -> None:
    exec_id = uuid4()

    def make_repo() -> InMemoryRepo:
        repo = InMemoryRepo()
        repo.seed_execution(exec_id, status="queued")
        return repo

    assert_repo_contract(make_repo, seed_execution_id=exec_id)
