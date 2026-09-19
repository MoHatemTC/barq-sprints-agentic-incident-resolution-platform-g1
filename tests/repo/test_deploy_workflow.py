"""Regression checks for the production deployment safety contract."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


def test_deploy_workflow_is_valid_yaml_with_expected_trigger() -> None:
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    assert config[True]["push"]["branches"] == ["main"]
    assert config["jobs"]["deploy"]["needs"] == "quality"
    assert config["jobs"]["deploy"]["environment"] == "production"


def test_deploy_script_fails_closed_and_deploys_the_triggering_commit() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "ssh-keyscan" not in workflow
    assert "EC2_KNOWN_HOSTS" in workflow
    assert "${{ github.sha }}" in workflow
    assert 'git checkout --detach "$deploy_sha"' in workflow
    assert "git pull origin main" not in workflow
    assert "alembic upgrade head || true" not in workflow
    # `docker compose run` claims stdin, which is the pipe the remote script is
    # being read from. Without -T and /dev/null it eats the rest of the script and
    # the deploy exits 0 having never restarted the app.
    assert "docker compose run --rm --no-deps -T api alembic upgrade head < /dev/null" in workflow
    # The image installs dependencies system-wide, so the remote steps must not
    # re-resolve the project with `uv run` (it rebuilds the package on the box).
    assert "uv run alembic" not in workflow
    assert "uv run celery" not in workflow
    assert "http://127.0.0.1:8000/ready" in workflow
    assert "celery -A app.workers.celery_app inspect ping" in workflow
