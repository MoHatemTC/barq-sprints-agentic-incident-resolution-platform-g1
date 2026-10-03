"""A ticket that waits for an engineer's approval is never silent for the caller."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.workers import cluster_runtime

SYS_ID = "a" * 32


class Tools:
    def __init__(self, caller: str) -> None:
        self.caller = caller
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(self, name: str, *, context: Any, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if name == "read_incident":
            return {"sys_id": SYS_ID, "number": "INC1", "caller_id": self.caller}
        return "applied"


def notify(caller: str, summary: str) -> tuple[str, Tools]:
    tools = Tools(caller)
    deps = SimpleNamespace(tools=tools, settings=SimpleNamespace(agent_service_account_ids={"svc"}))
    with (
        patch.object(cluster_runtime, "get_agent_dependencies", return_value=deps),
        patch.object(
            cluster_runtime,
            "snapshot_incident",
            side_effect=lambda raw: SimpleNamespace(caller_id=raw["caller_id"]),
        ),
    ):
        outcome = cluster_runtime.notify_caller_waiting(
            {"sys_id": SYS_ID}, {"interrupt_payload": {"summary": summary}}, "e", "c"
        )
    return outcome, tools


def test_a_repeat_waiting_for_approval_tells_the_caller_why() -> None:
    outcome, tools = notify("c" * 32, "Approval required: repeat from the same caller (INC1)")
    assert outcome == "update_caller:applied"
    message = tools.calls[-1][1]["message"]
    assert "similar problem recently" in message and "engineer will check" in message


def test_other_pauses_tell_the_caller_an_engineer_checks_first() -> None:
    _, tools = notify("c" * 32, "risk assessed as high")
    assert "An engineer needs to check it" in tools.calls[-1][1]["message"]


def test_no_message_without_a_real_caller() -> None:
    assert notify("", "x")[0] == "skipped:no_caller"
    assert notify("svc", "x")[0] == "skipped:no_caller"
