from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from src.app.runtime import CancelRequest, ResumeRequest, Runtime, RuntimeRequest
from src.app.runtime.core import (
    MAX_RUNTIME_INPUT_CHARS,
    MAX_RUNTIME_METADATA_ITEMS,
)


class _FakeSessionManager:
    def recover_interrupted_runs(self) -> int:
        return 0


def _runtime(tmp_path: Path) -> Runtime:
    dependency = object()
    return Runtime(
        config=SimpleNamespace(workspace_root=tmp_path),
        model_manager=dependency,
        tool_manager=dependency,
        tool_registry=dependency,
        session_manager=_FakeSessionManager(),
        context_builder=dependency,
        memory_adapter=dependency,
        analyzer=dependency,
        planner=dependency,
        react_executor=dependency,
        react_agent=dependency,
        output_feedback_processor=dependency,
        pending_run_registry=dependency,
        recover_on_startup=False,
    )


def test_run_returns_validation_error_for_empty_input_without_side_effects(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    request = RuntimeRequest(input="valid")
    request.input = "   "

    result = runtime.run(request)

    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "validation_error"
    assert runtime.__dict__.get("session_id") is None
    assert runtime.__dict__.get("run_id") is None


def test_run_returns_validation_error_for_invalid_session_id(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    request = RuntimeRequest(input="valid")
    request.session_id = "../unsafe"

    result = runtime.run(request)

    assert result.error_code == "validation_error"
    assert result.session_id is None


def test_run_returns_validation_error_for_non_dict_metadata(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    request = RuntimeRequest(input="valid")
    request.metadata = ["not", "a", "dict"]  # type: ignore[assignment]

    assert runtime.run(request).error_code == "validation_error"


def test_run_rejects_oversized_metadata_and_restricted_fields(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    too_many_items = RuntimeRequest(input="valid")
    too_many_items.metadata = {
        f"key_{index}": index for index in range(MAX_RUNTIME_METADATA_ITEMS + 1)
    }
    restricted = RuntimeRequest(input="valid")
    restricted.metadata = {"api_key": "must-not-enter-runtime"}

    assert runtime.run(too_many_items).error_code == "validation_error"
    assert runtime.run(restricted).error_code == "validation_error"


def test_run_validates_runtime_request_field_types_and_input_limit(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    non_boolean = RuntimeRequest(input="valid")
    non_boolean.debug = 1  # type: ignore[assignment]
    non_text_profile = RuntimeRequest(input="valid")
    non_text_profile.model_profile = 1  # type: ignore[assignment]
    oversized_input = RuntimeRequest(input="valid")
    oversized_input.input = "x" * (MAX_RUNTIME_INPUT_CHARS + 1)

    assert runtime.run(non_boolean).error_code == "validation_error"
    assert runtime.run(non_text_profile).error_code == "validation_error"
    assert runtime.run(oversized_input).error_code == "validation_error"


def test_public_facade_methods_and_stable_parameter_names_exist(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    expected_parameters = {
        "run": ("self", "request"),
        "run_stream": ("self", "request", "event_sink"),
        "resume": ("self", "request"),
        "cancel": ("self", "request"),
        "get_session": ("self", "session_id"),
        "list_sessions": ("self",),
        "get_timeline": ("self", "session_id"),
        "delete_session": ("self", "session_id"),
        "export_session": ("self", "session_id", "output_path"),
        "health": ("self",),
        "close": ("self",),
    }

    for method_name, parameters in expected_parameters.items():
        assert tuple(inspect.signature(getattr(Runtime, method_name)).parameters) == (
            parameters
        )

    assert runtime.run_stream(RuntimeRequest(input="valid"), event_sink=object()).error_code == (
        "validation_error"
    )
    assert runtime.resume(
        ResumeRequest(
            session_id="session_20260824_120000_demo001",
            run_id="run_20260824_120000_demo001",
            approved=True,
        )
    ).error_code == "internal_error"
    assert runtime.cancel(
        CancelRequest(
            session_id="session_20260824_120000_demo001",
            run_id="run_20260824_120000_demo001",
        )
    ).error_code == "internal_error"
