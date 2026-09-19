"""The Qdrant client specifier must track the server image in docker-compose.

Unit tests use the in-memory client, so a client/server version skew would not
otherwise fail CI.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.yml"
PYPROJECT = REPO / "pyproject.toml"

_IMAGE_RE = re.compile(r"^\s*image:\s*qdrant/qdrant:v(?P<major>\d+)\.(?P<minor>\d+)", re.M)
_SPEC_RE = re.compile(r"qdrant-client>=(?P<lo>\d+\.\d+),<(?P<hi_major>\d+)\.(?P<hi_minor>\d+)")


def _server_version() -> tuple[int, int]:
    match = _IMAGE_RE.search(COMPOSE.read_text(encoding="utf-8"))
    assert match, "No qdrant/qdrant:vX.Y image found in docker-compose.yml"
    return int(match.group("major")), int(match.group("minor"))


def _client_specifier() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    for dep in data["project"]["dependencies"]:
        if dep.startswith("qdrant-client"):
            return dep
    raise AssertionError("qdrant-client is not in [project].dependencies")


def test_client_upper_bound_matches_the_server_minor() -> None:
    """The client's exclusive upper bound is the server's next minor.

    Server v1.14.0 therefore pairs with `>=1.14,<1.15`. Bumping one without the other
    fails here rather than in production.
    """
    spec = _client_specifier()
    match = _SPEC_RE.search(spec)
    assert match, (
        f"qdrant-client specifier {spec!r} is not in the expected '>=X.Y,<X.Z' form. "
        "Keep it bounded: an open upper bound lets the client drift past the server."
    )

    server_major, server_minor = _server_version()
    assert (int(match.group("hi_major")), int(match.group("hi_minor"))) == (
        server_major,
        server_minor + 1,
    ), (
        f"docker-compose.yml runs Qdrant server v{server_major}.{server_minor}, but "
        f"pyproject.toml allows client {spec!r}. Bump both in the same PR — the unit "
        "tests use the in-memory client and will not catch this."
    )

    assert match.group("lo") == f"{server_major}.{server_minor}", (
        f"The client lower bound should be the server version {server_major}."
        f"{server_minor}; got {spec!r}."
    )


def test_the_coupling_is_documented_on_both_sides() -> None:
    """A future reader must be able to see why the pin is tight, from either file."""
    assert "docker-compose" in PYPROJECT.read_text(encoding="utf-8"), (
        "pyproject.toml should say the qdrant-client pin tracks the compose image"
    )
    assert "pyproject.toml" in COMPOSE.read_text(encoding="utf-8"), (
        "docker-compose.yml should say the qdrant image pin tracks the client"
    )
