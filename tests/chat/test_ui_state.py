"""Pending requests survive reruns and cannot be replaced by unrelated messages."""

import pytest

from app.chat_ui.state import begin_request, clear_request, pending_request


def test_retry_reuses_identity_only_for_the_same_message() -> None:
    state = {}
    first = begin_request(state, "chat-a", "First question")
    assert begin_request(state, "chat-a", "First question") is first
    with pytest.raises(ValueError, match="pending"):
        begin_request(state, "chat-a", "Different question")
    assert pending_request(state, "chat-a") is first


def test_switching_chats_preserves_both_pending_requests() -> None:
    state = {}
    first = begin_request(state, "chat-a", "First question")
    second = begin_request(state, "chat-b", "Second question")
    clear_request(state, "chat-b")
    assert pending_request(state, "chat-a") is first
    assert pending_request(state, "chat-b") is None
    assert first["request_id"] != second["request_id"]


def test_known_turn_cannot_be_resubmitted_as_a_new_request() -> None:
    state = {}
    first = begin_request(state, "chat-a", "First question")
    first["turn_id"] = "saved-turn"
    with pytest.raises(ValueError, match="pending"):
        begin_request(state, "chat-a", "First question")
