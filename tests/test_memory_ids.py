from __future__ import annotations

import pytest

from src.memory.ids import (
    new_event_id,
    new_message_id,
    new_run_id,
    new_session_id,
    new_summary_id,
    validate_generated_id,
    validate_session_id,
)


def test_memory_ids_follow_documented_shapes() -> None:
    assert validate_generated_id(new_session_id(), prefix="session").startswith("session_")
    assert validate_generated_id(new_message_id(), prefix="msg").startswith("msg_")
    assert validate_generated_id(new_run_id(), prefix="run").startswith("run_")
    assert validate_generated_id(new_event_id(), prefix="event").startswith("event_")
    assert validate_generated_id(new_summary_id(), prefix="summary").startswith("summary_")


@pytest.mark.parametrize("session_id", ["", " ", "..", "abc/def", r"abc\def", "C:\\memory", "/tmp/session", "a.b"])
def test_session_id_rejects_unsafe_values(session_id: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_session_id(session_id)


def test_session_id_accepts_safe_values_and_strips_outer_whitespace() -> None:
    assert validate_session_id(" session-01_test ") == "session-01_test"


def test_generated_id_prefix_validation_rejects_mismatch() -> None:
    with pytest.raises(ValueError):
        validate_generated_id(new_message_id(), prefix="event")
