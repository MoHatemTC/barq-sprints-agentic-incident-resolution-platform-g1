"""A ticket that waits for an engineer reaches a team's queue and is never silent for the
caller."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.workers import cluster_runtime

SYS_ID = "a" * 32
GROUPS = {"network": "n" * 32, "software": "s" * 32, "hardware": "h" * 32}


class Tools:
    def __init__(self, caller: str) -> None:
        self.caller = caller
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(self, name: str, *, context: Any, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if name == "read_incident":
            return {"sys_id": SYS_ID, "number": "INC1", "caller_id": self.caller}
        return "applied"


def notify(
    caller: str,
    summary: str,
    *,
    category: str = "hardware",
    label: str | None = None,
    state: str = "1",
) -> tuple[str, Tools]:
    tools = Tools(caller)
    deps = SimpleNamespace(
        tools=tools,
        settings=SimpleNamespace(agent_service_account_ids={"svc"}, agent_assignment_groups=GROUPS),
    )
    with (
        patch.object(cluster_runtime, "get_agent_dependencies", return_value=deps),
        patch.object(
            cluster_runtime,
            "snapshot_incident",
            side_effect=lambda raw: SimpleNamespace(
                caller_id=raw["caller_id"], category=category, state=state
            ),
        ),
    ):
        outcome = cluster_runtime.notify_caller_waiting(
            {"sys_id": SYS_ID},
            {"interrupt_payload": {"summary": summary, "classification": label}},
            "e",
            "c",
        )
    return outcome, tools


def test_a_repeat_waiting_for_approval_tells_the_caller_why() -> None:
    outcome, tools = notify("c" * 32, "Approval required: repeat from the same caller (INC1)")
    assert outcome == "assign_incident:applied;update_caller:applied"
    message = tools.calls[-1][1]["message"]
    assert "similar problem recently" in message
    assert "an engineer of the hardware team will check" in message


def test_other_pauses_name_the_team_that_checks_first() -> None:
    _, tools = notify("c" * 32, "risk assessed as high")
    assert "An engineer of the hardware team needs to check it" in tools.calls[-1][1]["message"]


def test_a_paused_ticket_reaches_the_team_the_agent_found() -> None:
    """An Outlook fault the caller filed under Network waits in the Software queue."""
    _, tools = notify("c" * 32, "x", category="network", label="software")
    assign = dict(tools.calls)["assign_incident"]
    assert assign["assignment_group"] == GROUPS["software"]
    assert "software group" in assign["work_note"]


def test_a_ticket_already_started_is_not_routed_again() -> None:
    outcome, tools = notify("c" * 32, "x", state="2")
    assert "assign_incident" not in dict(tools.calls)
    assert outcome == "update_caller:applied"


def test_routing_still_happens_without_a_real_caller() -> None:
    for caller in ("", "svc"):
        outcome, tools = notify(caller, "x")
        assert outcome == "assign_incident:applied;update_caller:skipped_no_caller"
        assert [name for name, _ in tools.calls] == ["read_incident", "assign_incident"]
