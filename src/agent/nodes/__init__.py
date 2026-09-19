"""The eleven graph nodes, in execution order.

Each node has the same stable signature — ``(state, deps) -> partial state`` — and
only reads the state sections written before it.
"""

from agent.nodes.act import act
from agent.nodes.classify import classify
from agent.nodes.confidence_check import confidence_check
from agent.nodes.determine_risk import determine_risk
from agent.nodes.diagnose import diagnose
from agent.nodes.generate import generate
from agent.nodes.load import load
from agent.nodes.retrieve import retrieve
from agent.nodes.safety_check import safety_check
from agent.nodes.validate import validate
from agent.nodes.verify_evidence import verify_evidence

NODE_ORDER = (
    "load",
    "validate",
    "classify",
    "determine_risk",
    "retrieve",
    "diagnose",
    "generate",
    "verify_evidence",
    "safety_check",
    "confidence_check",
    "act",
)

NODES = {
    "load": load,
    "validate": validate,
    "classify": classify,
    "determine_risk": determine_risk,
    "retrieve": retrieve,
    "diagnose": diagnose,
    "generate": generate,
    "verify_evidence": verify_evidence,
    "safety_check": safety_check,
    "confidence_check": confidence_check,
    "act": act,
}

__all__ = ["NODES", "NODE_ORDER", *NODE_ORDER]
