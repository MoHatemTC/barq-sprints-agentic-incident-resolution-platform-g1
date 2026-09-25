"""Incident-resolution agent: LangGraph state machine, runtime and providers (S2.5).

Layout (paths as named in the S2.5 brief):

- ``state.py`` — graph state schema
- ``llm.py`` — LLM and embedding clients with DI providers
- ``checkpointer.py`` — LangGraph checkpointer on the ``workflow_state`` table
- ``graph.py`` — the compiled eleven-node graph; ``nodes/`` — one module per node
- ``edges.py`` — deterministic edge conditions; ``policy.py`` — eligibility and risk
- ``runtime.py`` — per-process bootstrap called from the Celery task
"""

from agent.config import AGENT_VERSION

__all__ = ["AGENT_VERSION"]
