"""Admin chatbot: a separate LangGraph workflow behind the existing FastAPI app.

The chat reuses the shared retrieval, model, guardrail, auth and database
components. It never runs incident automation and never touches the incident
execution tables; see ``docs/admin_chatbot.md``.
"""
