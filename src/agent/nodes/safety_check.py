"""``safety_check`` — Sprint 4 gate, pass-through in Sprint 2.

Stable signature: ``(state, deps) -> {"safety": GateResult}``. Sprint 4 adds the
output guardrails here (schema, permitted-action list, secret scan of the draft —
manual §11.6 "after" checks 1, 3 and 4); the edge condition already routes a failed
gate to ``act``.
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.state import AgentState, GateResult


def safety_check(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    result = GateResult(gate="safety_check", passed=True, implemented=False)
    return {"safety": result.model_dump(mode="json")}
