"""Safe Markdown export for Runtime session timelines."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping


def build_session_markdown(
    session: Any,
    timeline: Iterable[Any],
) -> str:
    """Build a user-facing Markdown document from safe Memory projections.

    The exporter deliberately consumes only the session summary and timeline
    projections. It never loads the repository, runs another query, or emits
    timeline metadata and event payloads.
    """

    session_id = _text(_field(session, "session_id"), "unknown")
    title = _text(_field(session, "title"), "")
    status = _text(_field(session, "status"), "")
    created_at = _text(_field(session, "created_at"), "")
    last_activity_at = _text(_field(session, "last_activity_at"), "")

    lines = ["# Session", ""]
    lines.append(f"- Session ID: `{_inline(session_id)}`")
    if title:
        lines.append(f"- Title: {_inline(title)}")
    if status:
        lines.append(f"- Status: `{_inline(status)}`")
    if created_at:
        lines.append(f"- Created: `{_inline(created_at)}`")
    if last_activity_at:
        lines.append(f"- Last activity: `{_inline(last_activity_at)}`")

    messages: list[Any] = []
    events: list[Any] = []
    for item in timeline:
        if not _visible(item):
            continue
        if _field(item, "item_kind") == "message":
            messages.append(item)
        elif _field(item, "item_kind") == "execution_event":
            events.append(item)

    if messages:
        lines.extend(["", "## Conversation", ""])
        for item in messages:
            role = _text(_field(item, "role"), "message").capitalize()
            content = _text(_field(item, "content"), "")
            lines.extend([f"### {role}", content, ""])

    if events:
        lines.extend(["", "## Execution Events", ""])
        for item in events:
            event_type = _text(
                _field(_field(item, "metadata"), "event_type"),
                "event",
            )
            content = _text(_field(item, "content"), "")
            created_at = _text(_field(item, "created_at"), "")
            heading = f"### `{_inline(event_type)}`"
            if created_at:
                heading += f" `{_inline(created_at)}`"
            lines.extend([heading, content, ""])

    return "\n".join(lines).rstrip() + "\n"


def _field(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _visible(value: Any) -> bool:
    visible = _field(value, "visible_to_user", True)
    return visible is not False


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _inline(value: str) -> str:
    return value.replace("`", "'").replace("\r", " ").replace("\n", " ")


__all__ = ["build_session_markdown"]
