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


@pytest.mark.parametrize(
    ("article_category", "label", "category", "expected"),
    [
        ("hardware", "software", "hardware", "hardware"),  # printing belongs to hardware
        ("network", "software", "software", "network"),  # file shares belong to network
        ("database", "software", "network", "software"),  # no such team: next choice
        (None, "software", "network", "software"),
    ],
)
def test_the_team_owning_the_article_comes_first(article_category, label, category, expected):
    deps = SimpleNamespace(
        settings=SimpleNamespace(agent_assignment_groups={**GROUPS, "hardware": "h" * 32})
    )
    incident = SimpleNamespace(category=category)
    assert route(deps, incident, label, article_category)[0] == expected  # type: ignore[arg-type]


def test_fix_owner_reads_the_first_cited_article_that_was_retrieved() -> None:
    from agent.nodes.act import fix_owner

    state = {
        "retrieval": {
            "hits": [
                {"article_number": "KB0005", "category": "inquiry"},
                {"article_number": "KB0004", "category": "hardware"},
            ]
        }
    }
    fix = "1. Restart the spooler [KB0004 v1.0 §Resolution]\n2. Unlock [KB0005 v4.0 §R]"
    assert fix_owner(state, fix) == "hardware"  # type: ignore[arg-type]
    assert fix_owner(state, "no citation") is None  # type: ignore[arg-type]
    assert fix_owner({}, fix) is None  # type: ignore[arg-type]
