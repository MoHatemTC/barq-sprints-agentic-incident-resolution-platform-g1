"""The team an incident goes to: what the agent found the problem to be, else the caller's
own category (an Outlook fault filed under Network still reaches Software)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.nodes.act import route

GROUPS = {"network": "n" * 32, "software": "s" * 32, "inquiry": "q" * 32}


@pytest.mark.parametrize(
    ("category", "label", "expected"),
    [
        ("network", "software", ("software", "s" * 32)),  # the agent's reading wins
        ("network", "access", ("network", "n" * 32)),  # no access team: caller's choice
        ("network", None, ("network", "n" * 32)),
        ("hardware", "other", ("hardware", None)),  # neither names a team
        ("inquiry", "security", ("inquiry", "q" * 32)),
    ],
)
def test_route(category: str, label: str | None, expected: tuple[str, str | None]) -> None:
    deps = SimpleNamespace(settings=SimpleNamespace(agent_assignment_groups=GROUPS))
    incident = SimpleNamespace(category=category)
    assert route(deps, incident, label) == expected  # type: ignore[arg-type]
