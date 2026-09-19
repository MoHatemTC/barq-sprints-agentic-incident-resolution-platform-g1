#!/usr/bin/env python3
"""Render docs/graph_state_diagram.png from the compiled graph (S2.5).

The nodes and edges come from the compiled LangGraph, and the edge labels from
``agent.edges.EDGE_TABLE``; a test asserts those two agree, so the picture cannot
drift from the code. Needs Graphviz (``dot``) on PATH.

    uv run python scripts/render_graph_diagram.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

for key, value in {
    "SERVICENOW_INSTANCE_URL": "https://dev00000.service-now.com",
    "SERVICENOW_CLIENT_ID": "diagram",
    "SERVICENOW_CLIENT_SECRET": "diagram",
    "SERVICENOW_USERNAME": "diagram",
    "SERVICENOW_PASSWORD": "diagram",
    "WEBHOOK_AUTH_TOKEN": "diagram",
}.items():
    os.environ.setdefault(key, value)

from agent.config import AgentSettings  # noqa: E402
from agent.dependencies import AgentDependencies  # noqa: E402
from agent.edges import EDGE_TABLE  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from observability.tracing import Tracer  # noqa: E402

STYLE = {
    "__start__": 'shape=circle label="" width=0.25 style=filled fillcolor="#222222"',
    "__end__": 'shape=doublecircle label="" width=0.2 style=filled fillcolor="#222222"',
    "determine_risk": 'fillcolor="#fde2cf" color="#c2410c" penwidth=2',
    "act": 'fillcolor="#dbeafe" color="#1d4ed8" penwidth=2',
    "verify_evidence": 'fillcolor="#eeeeee" style="rounded,filled,dashed"',
    "safety_check": 'fillcolor="#eeeeee" style="rounded,filled,dashed"',
    "confidence_check": 'fillcolor="#fef9c3" color="#a16207"',
}

ESCALATION_LABELS = {
    ("validate", "act"): "ineligible\\n(skip, no write)",
    ("determine_risk", "act"): "risk HIGH\\n(no retrieval,\\nno generation)",
    ("retrieve", "act"): "best cosine < 0.55\\nor no corpus category",
    ("diagnose", "act"): "no retrieved article\\nmatches the fault",
    ("verify_evidence", "act"): "gate failed",
    ("safety_check", "act"): "gate failed",
}


def dot_source() -> str:
    deps = AgentDependencies(
        settings=AgentSettings(),
        llm=None,  # type: ignore[arg-type]
        retriever=None,  # type: ignore[arg-type]
        servicenow=None,  # type: ignore[arg-type]
        tracer=Tracer(None),
    )
    compiled = build_graph(deps).get_graph()
    labels = {(src, dst): cond for src, cond, dst in EDGE_TABLE}
    lines = [
        "digraph incident_resolution {",
        '  graph [rankdir=TB fontname="Helvetica" nodesep=0.5 ranksep=0.45 '
        'label="BARQ incident-resolution graph (S2.5) — 11 nodes, deterministic edges\\n'
        "orange: risk is decided before any retrieval · dashed: Sprint 4 gates (pass-through) · "
        "yellow: confidence floor 0.45 · blue: the only node that writes to ServiceNow"
        '" labelloc=t fontsize=16];',
        '  node [shape=box style="rounded,filled" fillcolor="#f5f5ff" fontname="Helvetica" '
        "fontsize=12];",
        '  edge [fontname="Helvetica" fontsize=9 color="#555555"];',
    ]
    for node in compiled.nodes:
        lines.append(f'  "{node}" [{STYLE.get(node, "")}];')
    for edge in compiled.edges:
        key = (edge.source, edge.target)
        text = ESCALATION_LABELS.get(key) or (
            "" if labels.get(key, "always").startswith("always") else labels[key]
        )
        attrs = [f'label="{text}"'] if text else []
        if edge.target == "act" and key in ESCALATION_LABELS:
            attrs += ['color="#c2410c"', 'fontcolor="#c2410c"', "style=dashed", "constraint=false"]
        lines.append(f'  "{edge.source}" -> "{edge.target}" [{" ".join(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("docs/graph_state_diagram.png"))
    args = parser.parse_args()
    dot = shutil.which("dot")
    if dot is None:
        raise SystemExit("Graphviz 'dot' is not installed")
    source = dot_source()
    args.out.with_suffix(".dot").write_text(source + "\n", encoding="utf-8")
    subprocess.run(
        [dot, "-Tpng", "-Gdpi=160", "-o", str(args.out)], input=source.encode(), check=True
    )
    print(f"wrote {args.out} and {args.out.with_suffix('.dot')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
