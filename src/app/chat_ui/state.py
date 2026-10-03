"""Pure state transitions shared by the UI and its mocked tests."""

from collections.abc import MutableMapping
from typing import Any
from uuid import uuid4


def pending_request(state: MutableMapping[str, Any], conversation_id: str) -> dict[str, Any] | None:
    return state.get("pending_requests", {}).get(conversation_id)


def clear_request(state: MutableMapping[str, Any], conversation_id: str) -> None:
    state.get("pending_requests", {}).pop(conversation_id, None)


def begin_request(
    state: MutableMapping[str, Any], conversation_id: str, content: str
) -> dict[str, Any]:
    pending = pending_request(state, conversation_id)
    if pending:
        if pending["content"] != content or pending.get("turn_id"):
            raise ValueError("Recover the pending message before sending another question.")
        return pending
    pending = {
        "conversation_id": conversation_id,
        "request_id": uuid4().hex,
        "turn_id": None,
        "content": content,
    }
    state.setdefault("pending_requests", {})[conversation_id] = pending
    return pending
