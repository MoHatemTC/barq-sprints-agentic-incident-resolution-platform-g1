set shell := ["bash", "-cu"]

default:
    @just --list

install:
    uv sync

run:
    uv run uvicorn app.main:app --reload

test:
    uv run pytest

test-cov:
    uv run pytest --cov=app

lint:
    uv run ruff check .

format:
    uv run ruff format .

fix:
    uv run ruff check . --fix
    uv run ruff format .

typecheck:
    uv run mypy src

precommit:
    uv run pre-commit run --all-files  

check:
    just lint
    just typecheck
    just test

