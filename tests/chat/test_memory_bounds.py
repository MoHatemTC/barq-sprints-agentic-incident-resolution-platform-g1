"""Memory prompts respect their configured bounds even with oversized messages."""

from app.chat.prompts import _context_lines


def test_oversized_latest_message_and_summary_fit_the_memory_bound() -> None:
    result = _context_lines(
        [{"role": "user", "content": "x" * 20000 + "Refer to P2"}], "s" * 5000, 1000
    )
    assert len(result) <= 1000
    assert "Refer to P2" in result


def test_tiny_memory_budget_is_still_respected() -> None:
    assert len(_context_lines([{"role": "user", "content": "hello"}], "old", 10)) <= 10
