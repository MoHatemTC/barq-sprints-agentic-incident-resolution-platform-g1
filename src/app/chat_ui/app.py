"""BARQ admin chatbot UI (Streamlit).

Thin client over the existing FastAPI app: it never calls models, Qdrant or
ServiceNow directly. Tokens and the chat-session secret live in Streamlit
session memory only and are cleared on logout; request ids are generated
before submission so a Streamlit rerun cannot double-charge a turn.
"""

from __future__ import annotations

import uuid

import httpx
import streamlit as st

DEFAULT_API_BASE = "http://localhost:8000"
MAX_MESSAGE_CHARS = 6000

st.set_page_config(page_title="BARQ Admin Chat", page_icon=":speech_balloon:", layout="centered")


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


def _api_error(response: httpx.Response) -> str:
    """One-line user-facing message; never a stack trace."""
    try:
        return str(response.json()["error"].get("message") or f"HTTP {response.status_code}")
    except Exception:  # noqa: BLE001 - the UI must render any error shape
        return f"Request failed with HTTP {response.status_code}"


def _request(
    method: str, path: str, *, headers: dict[str, str], **kwargs: object
) -> httpx.Response:
    try:
        return httpx.request(method, f"{_api()}{path}", headers=headers, timeout=180, **kwargs)  # type: ignore[arg-type]
    except httpx.HTTPError as exc:
        st.error(f"Cannot reach the API at {_api()} ({type(exc).__name__}).")
        st.stop()


def _handle_auth_failure(status_code: int) -> bool:
    if status_code != 401:
        return False
    for key in ("token", "chat_session_id", "chat_session_secret"):
        st.session_state.pop(key, None)
    st.error("Your session expired. Sign in again.")
    st.rerun()
    return True


# -- login ----------------------------------------------------------------------


def _login() -> None:
    st.title("BARQ Admin Chat")
    st.caption("Knowledge-base assistant for trusted admins. Sign in with operator credentials.")
    base = st.text_input("API base URL", value=st.session_state.get("api_base", DEFAULT_API_BASE))
    client_id = st.text_input("Operator client ID", value="barq-operator")
    client_secret = st.text_input("Operator secret (webhook_auth_token)", type="password")
    if st.button("Sign in", type="primary", disabled=not (base and client_id and client_secret)):
        st.session_state["api_base"] = base.strip()
        with st.spinner("Signing in..."):
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


# -- conversations ---------------------------------------------------------------


def _sidebar() -> str | None:
    with st.sidebar:
        st.header("Conversations")
        if st.button("New conversation"):
            response = _request(
                "POST", "/api/v1/chat/conversations", headers=_session_headers(), json={}
            )
            if response.status_code == 201:
                st.session_state["conversation_id"] = response.json()["id"]
                st.rerun()
            else:
                _handle_auth_failure(response.status_code)
                st.error(_api_error(response))

        listed = _request("GET", "/api/v1/chat/conversations", headers=_session_headers())
        if _handle_auth_failure(listed.status_code):
            st.stop()
        if listed.status_code != 200:
            st.error(_api_error(listed))
            st.stop()

        conversations = listed.json()
        labels = {c["id"]: c["title"] for c in conversations}
        current = st.session_state.get("conversation_id")
        selected = st.selectbox(
            "Open conversation",
            options=list(labels) or [None],
            index=(list(labels).index(current) if current in labels else 0) if labels else 0,
            format_func=lambda value: labels.get(value, "(none)"),
        )
        if selected and selected != current:
            st.session_state["conversation_id"] = selected
            st.rerun()

        if labels and st.button("Delete open conversation"):
            response = _request(
                "DELETE",
                f"/api/v1/chat/conversations/{st.session_state.get('conversation_id')}",
                headers=_session_headers(),
            )
            if response.status_code == 204:
                st.session_state.pop("conversation_id", None)
                st.rerun()
            else:
                _handle_auth_failure(response.status_code)
                st.error(_api_error(response))

        st.divider()
        if st.button("Log out"):
            for key in ("token", "chat_session_id", "chat_session_secret", "conversation_id"):
                st.session_state.pop(key, None)
            st.rerun()
    return st.session_state.get("conversation_id")


# -- chat -------------------------------------------------------------------------


def _citation_label(citation: dict) -> str:
    number = citation["article_number"]
    title = citation["title"]
    if citation.get("manual_section"):
        return f"{number} — Manual §{citation['manual_section']} — {title}"
    return f"{number} v{citation['version']} — {title} — §{citation['section']}"


def _sources(citations: list[dict]) -> None:
    if not citations:
        return
    with st.expander("Sources"):
        for citation in citations:
            st.markdown(f"**{_citation_label(citation)}**")
            st.text(citation.get("excerpt") or "")


def _send(conversation_id: str, prompt: str) -> None:
    request_id = uuid.uuid4().hex
    st.session_state["last_request_id"] = request_id
    with st.spinner("Thinking..."):
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
    st.rerun()


def _chat_panel(conversation_id: str) -> None:
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

    for message in history.json():
        with st.chat_message("user" if message["role"] == "user" else "assistant"):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                _sources(message.get("citations") or [])

    if prompt := st.chat_input("Ask about the knowledge base"):
        _send(conversation_id, prompt)


def main() -> None:
    if not st.session_state.get("token"):
        _login()
        return
    conversation_id = _sidebar()
    if conversation_id is None:
        st.info("Create or open a conversation to start asking.")
        return
    _chat_panel(conversation_id)


main()
