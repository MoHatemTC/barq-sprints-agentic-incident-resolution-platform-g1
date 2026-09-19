"""``verify_evidence`` — Sprint 4 gate, pass-through in Sprint 2.

Stable signature: ``(state, deps) -> {"verification": GateResult}``. Sprint 4
matches every drafted step back to a retrieved article here (manual §11.6
"after" check 2); the edge condition already routes a failed gate to ``act``.
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.state import AgentState, GateResult


def verify_evidence(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    result = GateResult(gate="verify_evidence", passed=True, implemented=False)
    return {"verification": result.model_dump(mode="json")}
