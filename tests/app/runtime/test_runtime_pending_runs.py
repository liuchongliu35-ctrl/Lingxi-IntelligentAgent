from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest

from src.app.runtime.errors import RuntimeErrorCode, RuntimeException
from src.app.runtime.pending_runs import PendingRunRegistry


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, 4, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _confirmation() -> dict[str, object]:
    return {
        "confirmation_id": "confirm_1",
        "preview_hash": "hash_1",
        "confirmation_type": "tool",
        "confirmation_message": "Run the selected tool?",
        "pending_action": {
            "action_type": "command",
            "action_target": "shell",
            "arguments": {"command": "echo secret"},
            "api_key": "must-not-leak",
        },
        "raw_tool_result": "private-result",
    }


def test_register_get_pop_and_remove_are_run_scoped() -> None:
    registry = PendingRunRegistry(ttl_seconds=60)
    context = {"private": "executor-context"}

    record = registry.register(
        "session_a",
        "run_a",
        context,
        _confirmation(),
        metadata={"trace_id": "trace_a"},
    )

    assert record.executor_context is context
    assert registry.get("run_a", session_id="session_a") is record
    assert registry.pop("run_a", session_id="session_a") is record
    assert registry.pop("run_a", session_id="session_a") is None
    assert registry.remove("run_a", session_id="session_a") is False


def test_session_mismatch_is_rejected_without_removing_the_record() -> None:
    registry = PendingRunRegistry()
    registry.register("session_a", "run_a", object(), _confirmation())

    with pytest.raises(RuntimeException) as error:
        registry.get("run_a", session_id="session_b")

    assert error.value.code == RuntimeErrorCode.SESSION_CONFLICT.value
    assert registry.get("run_a", session_id="session_a") is not None


def test_duplicate_run_id_is_rejected() -> None:
    registry = PendingRunRegistry()
    registry.register("session_a", "run_a", object(), _confirmation())

    with pytest.raises(RuntimeException) as error:
        registry.register("session_a", "run_a", object(), _confirmation())

    assert error.value.code == RuntimeErrorCode.SESSION_CONFLICT.value


def test_expire_removes_entries_and_get_drops_expired_entries() -> None:
    clock = FakeClock()
    registry = PendingRunRegistry(ttl_seconds=10, clock=clock)
    registry.register("session_a", "run_a", object(), _confirmation())
    registry.register("session_a", "run_b", object(), _confirmation())

    clock.advance(10)

    assert registry.expire() == ["run_a", "run_b"]
    assert registry.get("run_a") is None
    assert len(registry) == 0


def test_explicit_expiry_is_supported() -> None:
    clock = FakeClock()
    registry = PendingRunRegistry(ttl_seconds=60, clock=clock)
    expiry = clock.value + timedelta(seconds=5)
    registry.register(
        "session_a",
        "run_a",
        object(),
        _confirmation(),
        expires_at=expiry,
    )

    clock.advance(6)

    assert registry.get("run_a") is None


def test_public_snapshot_contains_only_safe_confirmation_and_metadata() -> None:
    registry = PendingRunRegistry()
    context = {"raw_prompt": "hidden", "api_key": "secret"}
    registry.register(
        "session_a",
        "run_a",
        context,
        _confirmation(),
        owner="connection_a",
        metadata={
            "trace_id": "trace_a",
            "raw_prompt": "do not return",
            "token": "do not return",
        },
    )

    public = registry.get_public("run_a", session_id="session_a")
    assert public is not None
    assert public["owner"] == "connection_a"
    assert public["pending_confirmation"] == {
        "confirmation_id": "confirm_1",
        "preview_hash": "hash_1",
        "confirmation_type": "tool",
        "action_name": "shell",
        "action_type": "command",
        "confirmation_message": "Run the selected tool?",
        "preview_summary": "Run the selected tool?",
        "expires_at": None,
    }
    assert public["metadata"] == {"trace_id": "trace_a"}
    assert "executor_context" not in public
    assert "secret" not in repr(public)
    assert "hidden" not in repr(public)

    record = registry.get("run_a")
    assert record is not None
    assert "executor_context" not in record.to_dict()
    assert record.to_dict() == public

    registry.remove("run_a", session_id="session_a")
    registry.register(
        "session_a",
        "run_b",
        context,
        _confirmation(),
        owner="token=secret-owner",
    )
    assert registry.get_public("run_b")["owner"] == "token=***REDACTED***"


def test_pop_is_atomic_for_concurrent_resume_attempts() -> None:
    registry = PendingRunRegistry()
    registry.register("session_a", "run_a", object(), _confirmation())
    barrier = Barrier(2)
    results: list[object] = []

    def pop_once() -> None:
        barrier.wait()
        results.append(registry.pop("run_a", session_id="session_a"))

    threads = [Thread(target=pop_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(result is not None for result in results) == 1
    assert len(registry) == 0


def test_clear_is_process_local_and_does_not_persist_context() -> None:
    registry = PendingRunRegistry()
    registry.register("session_a", "run_a", {"secret": "context"}, _confirmation())

    assert registry.clear() == 1
    assert registry.get("run_a") is None


@pytest.mark.parametrize("bad_ttl", [0, -1, False])
def test_ttl_must_be_positive(bad_ttl: object) -> None:
    with pytest.raises(ValueError):
        PendingRunRegistry(ttl_seconds=bad_ttl)  # type: ignore[arg-type]
