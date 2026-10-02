"""BARQ admin chatbot UI (Streamlit), ChatGPT-style.

Thin client over the existing FastAPI app: it never calls models, Qdrant or
ServiceNow directly. Tokens and the chat-session secret live in Streamlit
session memory only and are cleared on logout. Request ids are generated per
submission so a Streamlit rerun cannot double-charge a turn.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import streamlit as st

#: Compose sets CHAT_UI_API_BASE so the containerized UI reaches barq-api
#: over the internal network; local runs default to the localhost API.
DEFAULT_API_BASE = os.environ.get("CHAT_UI_API_BASE", "http://localhost:8000")
MAX_MESSAGE_CHARS = 6000
NEW_CONVERSATION = "New conversation"

st.set_page_config(page_title="BARQ Admin Chat", page_icon="💬", layout="wide")

# ChatGPT-ish chrome: narrow centered chat column, quiet sidebar, compact rows.
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.2rem; max-width: 820px; margin: auto;}
      [data-testid="stSidebar"] {min-width: 260px; max-width: 320px;}
      [data-testid="stSidebar"] button {text-align: left; padding: 0.2rem 0.5rem;
        font-size: 0.88rem; border: none; background: transparent;}
      [data-testid="stSidebar"] button:hover {background: rgba(128,128,128,0.15);}
      [data-testid="stSidebar"] button[kind="primary"] {background: rgba(128,128,128,0.25);}
      [data-testid="stChatMessage"] {padding: 0.4rem 0;}
    </style>
    """,
    unsafe_allow_html=True,
)


# -- HTTP helpers ----------------------------------------------------------------


def _api() -> str:
    return st.session_state.get("api_base", DEFAULT_API_BASE).rstrip("/")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {st.session_state['token']}"}


def _session_headers() -> dict[str, str]:
    return {
        **_auth_headers(),
        "X-Chat-Session-Id": st.session_state["chat_session_id"],
        "X-Chat-Session-Secret": st.session_state["chat_session_secret"],
    }


def _request(method: str, path: str, *, headers: dict[str, str], **kwargs: Any) -> httpx.Response:
    try:
        return httpx.request(method, f"{_api()}{path}", headers=headers, timeout=180, **kwargs)
    except httpx.HTTPError as exc:
        st.error(f"Cannot reach the API at {_api()} ({type(exc).__name__}).")
        st.stop()


def _api_error(response: httpx.Response) -> str:
    """One-line user-facing message; never a stack trace."""
    try:
        return str(response.json()["error"].get("message") or f"HTTP {response.status_code}")
    except Exception:  # noqa: BLE001 - the UI must render any error shape
        return f"Request failed with HTTP {response.status_code}"


def _handle_auth_failure(status_code: int) -> bool:
    if status_code != 401:
        return False
    for key in ("token", "chat_session_id", "chat_session_secret"):
        st.session_state.pop(key, None)
    st.error("Your session expired. Sign in again.")
    st.rerun()
    return True


# -- login -----------------------------------------------------------------------


def _login() -> None:
    st.markdown("## 💬 BARQ Admin Chat")
    st.caption("Knowledge-base assistant for trusted admins.")
    with st.form("login"):
        base = st.text_input(
            "API base URL", value=st.session_state.get("api_base", DEFAULT_API_BASE)
        )
        client_id = st.text_input("Operator client ID", value="barq-operator")
        client_secret = st.text_input("Operator secret", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if not submitted:
        return
    if not (base and client_id and client_secret):
        st.warning("Fill in all three fields.")
        return

    st.session_state["api_base"] = base.strip()
    with st.spinner("Signing in…"):
        token_response = _request(
            "POST",
            "/api/v1/oauth/token",
            headers={},
            data={
                "grant_type": "client_credentials",
                "client_id": client_id.strip(),
                "client_secret": client_secret,
            },
        )
    if token_response.status_code != 200:
        st.error(_api_error(token_response))
        return
    token = token_response.json()["access_token"]

    with st.spinner("Preparing chat session…"):
        chat_response = _request(
            "POST", "/api/v1/chat/sessions", headers={"Authorization": f"Bearer {token}"}
        )
    if chat_response.status_code != 201:
        _handle_auth_failure(chat_response.status_code)
        st.error(_api_error(chat_response))
        return
    body = chat_response.json()
    st.session_state["token"] = token
    st.session_state["chat_session_id"] = body["session_id"]
    st.session_state["chat_session_secret"] = body["chat_secret"]
    st.rerun()


# -- conversation state ----------------------------------------------------------


def _conversations() -> list[dict[str, Any]]:
    if "conversations" not in st.session_state:
        listed = _request("GET", "/api/v1/chat/conversations", headers=_session_headers())
        if _handle_auth_failure(listed.status_code):
            st.stop()
        if listed.status_code != 200:
            st.error(_api_error(listed))
            st.stop()
        st.session_state["conversations"] = listed.json()
    return st.session_state["conversations"]


def _invalidate_conversations() -> None:
    st.session_state.pop("conversations", None)


def _new_chat() -> None:
    response = _request("POST", "/api/v1/chat/conversations", headers=_session_headers(), json={})
    if response.status_code != 201:
        _handle_auth_failure(response.status_code)
        st.error(_api_error(response))
        return
    _invalidate_conversations()
    st.session_state["conversation_id"] = response.json()["id"]
    st.rerun()


def _select_conversation(conversation_id: str) -> None:
    st.session_state["conversation_id"] = conversation_id
    st.rerun()


def _rename_conversation(conversation_id: str, title: str) -> None:
    response = _request(
        "PATCH",
        f"/api/v1/chat/conversations/{conversation_id}",
        headers=_session_headers(),
        json={"title": title.strip()},
    )
    if response.status_code != 200:
        _handle_auth_failure(response.status_code)
        st.error(_api_error(response))
        return
    _invalidate_conversations()
    st.rerun()


def _delete_conversation(conversation_id: str) -> None:
    response = _request(
        "DELETE", f"/api/v1/chat/conversations/{conversation_id}", headers=_session_headers()
    )
    if response.status_code != 204:
        _handle_auth_failure(response.status_code)
        st.error(_api_error(response))
        return
    _invalidate_conversations()
    if st.session_state.get("conversation_id") == conversation_id:
        st.session_state.pop("conversation_id", None)
    st.rerun()


# -- sidebar ---------------------------------------------------------------------


def _sidebar() -> None:
    with st.sidebar:
        st.markdown("### 💬 BARQ Chat")
        if st.button("＋  New chat", type="primary", use_container_width=True):
            _new_chat()

        current = st.session_state.get("conversation_id")
        pending_delete = st.session_state.get("pending_delete")
        for conversation in _conversations():
            conversation_id = conversation["id"]
            title = conversation["title"]
            active = conversation_id == current
            row = st.columns([0.66, 0.16, 0.18])
            label = f"**{title}**" if active else title
            if row[0].button(
                label,
                key=f"sel-{conversation_id}",
                use_container_width=True,
                help="Open conversation",
            ):
                _select_conversation(conversation_id)

            with row[1].popover("✏️", use_container_width=True, help="Rename"):
                new_title = st.text_input("Title", value=title, key=f"rn-{conversation_id}")
                if st.button("Save", key=f"rn-save-{conversation_id}", type="primary"):
                    if new_title.strip():
                        _rename_conversation(conversation_id, new_title)
                    else:
                        st.warning("Title cannot be empty.")

            if pending_delete == conversation_id:
                if row[2].button(
                    "❗", key=f"del-confirm-{conversation_id}", help="Click again to delete"
                ):
                    st.session_state.pop("pending_delete", None)
                    _delete_conversation(conversation_id)
            elif row[2].button("🗑", key=f"del-{conversation_id}", help="Delete"):
                st.session_state["pending_delete"] = conversation_id
                st.rerun()

        st.divider()
        if st.button("⎋  Log out", use_container_width=True):
            for key in (
                "token",
                "chat_session_id",
                "chat_session_secret",
                "conversation_id",
                "conversations",
                "pending_delete",
            ):
                st.session_state.pop(key, None)
            st.rerun()


# -- chat panel ------------------------------------------------------------------


def _citation_label(citation: dict[str, Any]) -> str:
    number = citation["article_number"]
    title = citation["title"]
    if citation.get("manual_section"):
        return f"{number} — Manual §{citation['manual_section']} — {title}"
    return f"{number} v{citation['version']} — {title} — §{citation['section']}"


def _sources(citations: list[dict[str, Any]]) -> None:
    if not citations:
        return
    with st.expander(f"📎 Sources ({len(citations)})", expanded=False):
        for citation in citations:
            st.markdown(f"**{_citation_label(citation)}**")
            st.text(citation.get("excerpt") or "")
            st.divider()


def _welcome() -> None:
    st.markdown("### 👋 How can I help?")
    st.caption("Ask anything about the BARQ knowledge base — procedures, policies, the manual.")
    examples = [
        "Explain the known error register and when to use it.",
        "Who is responsible for escalating a P1 incident?",
        "What belongs in a work note?",
    ]
    columns = st.columns(3)
    for column, example in zip(columns, examples, strict=True):
        if column.button(example, use_container_width=True):
            _send(st.session_state["conversation_id"], example)


def _send(conversation_id: str, prompt: str) -> None:
    request_id = uuid.uuid4().hex
    st.session_state["last_request_id"] = request_id
    with st.spinner("Thinking…"):
        response = _request(
            "POST",
            f"/api/v1/chat/conversations/{conversation_id}/messages",
            headers=_session_headers(),
            json={"content": prompt[:MAX_MESSAGE_CHARS], "request_id": request_id},
        )
    if _handle_auth_failure(response.status_code):
        return
    if response.status_code == 409:
        st.warning("Another answer is still being prepared for this conversation.")
        return
    if response.status_code != 200:
        st.error(_api_error(response))
        return
    turn = response.json()
    if turn["status"] == "failed":
        st.error("The answer could not be produced. It is recorded as a failed turn; try again.")
    elif turn["status"] == "blocked":
        st.warning("The answer was withheld; see the assistant message for the reason.")
    _invalidate_conversations()  # a first message may have auto-titled the chat
    st.rerun()


def _chat_panel(conversation_id: str) -> None:
    conversation = next((c for c in _conversations() if c["id"] == conversation_id), None)
    st.markdown(f"#### {conversation['title'] if conversation else 'Conversation'}")

    history = _request(
        "GET",
        f"/api/v1/chat/conversations/{conversation_id}/messages?limit=200",
        headers=_session_headers(),
    )
    if _handle_auth_failure(history.status_code):
        return
    if history.status_code != 200:
        st.error(_api_error(history))
        return

    messages = history.json()
    if not messages:
        _welcome()
    for message in messages:
        avatar = "🧑‍💼" if message["role"] == "user" else "🤖"
        with st.chat_message("user" if message["role"] == "user" else "assistant", avatar=avatar):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                _sources(message.get("citations") or [])

    if prompt := st.chat_input("Ask about the knowledge base…"):
        _send(conversation_id, prompt)


def main() -> None:
    if not st.session_state.get("token"):
        _login()
        return
    _sidebar()
    conversation_id = st.session_state.get("conversation_id")
    if not conversation_id:
        st.markdown("### 👋 Select or create a conversation")
        st.caption("Use **＋ New chat** in the sidebar to start asking.")
        return
    _chat_panel(conversation_id)


main()
