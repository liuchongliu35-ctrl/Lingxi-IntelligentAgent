from __future__ import annotations

import pytest

from src.memory.models import (
    AgentRun,
    AgentRunStatus,
    ContextBuildResult,
    ExecutionEventRecord,
    ExecutionEventStatus,
    Message,
    MessageRole,
    MessageStatus,
    Role,
    SessionState,
    SessionSummary,
    SummarySource,
    TimelineItem,
    TimelineItemKind,
)


def test_message_normalizes_enums_and_serializes_nested_values() -> None:
    message = Message(
        message_id="msg_1",
        session_id="session_1",
        timeline_seq="1",
        role=MessageRole.USER,
        content="hello",
        status=MessageStatus.COMPLETED,
        metadata={"source": MessageRole.USER},
    )

    assert message.role == "user"
    assert message.timeline_seq == 1
    assert message.to_dict()["role"] == "user"
    assert message.to_dict()["metadata"]["source"] == "user"
    assert Role.USER is MessageRole.USER


def test_session_state_converts_message_dicts_and_exposes_count() -> None:
    state = SessionState(
        session_id="session_1",
        messages=[
            {
                "message_id": "msg_1",
                "session_id": "session_1",
                "timeline_seq": 1,
                "role": "user",
                "content": "hello",
            }
        ],
    )

    assert isinstance(state.messages[0], Message)
    assert state.message_count == 1
    assert state.to_dict()["messages"][0]["status"] == "completed"


def test_agent_run_and_event_record_validate_state_values() -> None:
    run = AgentRun(
        run_id="run_1",
        session_id="session_1",
        user_message_id="msg_1",
        status=AgentRunStatus.RUNNING,
        started_at="2026-08-20T00:00:00Z",
    )
    event = ExecutionEventRecord(
        event_id="event_1",
        session_id="session_1",
        run_id=run.run_id,
        timeline_seq=2,
        event_type="tool_started",
        display_type="tool_progress",
        display_content="running",
        visible_to_user=True,
        status=ExecutionEventStatus.STARTED,
        created_at="2026-08-20T00:00:01Z",
    )

    assert run.status == "running"
    assert event.status == "started"
    with pytest.raises(ValueError):
        AgentRun(
            run_id="run_2",
            session_id="session_1",
            user_message_id="msg_1",
            status="unknown",
            started_at="2026-08-20T00:00:00Z",
        )


def test_timeline_item_and_summary_keep_timeline_bounds_consistent() -> None:
    item = TimelineItem(
        item_id="event_1",
        item_kind=TimelineItemKind.EXECUTION_EVENT,
        session_id="session_1",
        timeline_seq=2,
        display_type="tool_progress",
        content="running",
        status="recorded",
        created_at="2026-08-20T00:00:01Z",
    )
    summary = SessionSummary(
        summary_id="summary_1",
        session_id="session_1",
        content="summary",
        covered_from_timeline_seq=1,
        covered_to_timeline_seq=2,
        created_at="2026-08-20T00:00:02Z",
        source=SummarySource.MODEL,
    )

    assert item.item_kind == "execution_event"
    assert summary.source == "model"
    with pytest.raises(ValueError):
        SessionSummary(
            summary_id="summary_2",
            session_id="session_1",
            content="summary",
            covered_from_timeline_seq=3,
            covered_to_timeline_seq=2,
            created_at="2026-08-20T00:00:02Z",
            source="manual",
        )


def test_context_build_result_normalizes_recent_messages() -> None:
    result = ContextBuildResult(
        session_id="session_1",
        context_text="user: hello",
        recent_messages=[
            {
                "message_id": "msg_1",
                "session_id": "session_1",
                "timeline_seq": 1,
                "role": "user",
                "content": "hello",
            }
        ],
        included_message_ids=["msg_1"],
        char_count=11,
    )

    assert isinstance(result.recent_messages[0], Message)
    assert result.to_dict()["recent_messages"][0]["role"] == "user"


def test_invalid_message_role_is_rejected() -> None:
    with pytest.raises(ValueError):
        Message(
            message_id="msg_1",
            session_id="session_1",
            timeline_seq=1,
            role="developer",
            content="hello",
        )
