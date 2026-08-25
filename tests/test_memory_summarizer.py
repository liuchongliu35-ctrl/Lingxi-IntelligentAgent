from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.memory.config import MemoryConfig
from src.memory.ids import new_message_id
from src.memory.models import SummarySource
from src.memory.session_manager import SessionManager
from src.memory.storage import SQLiteSessionRepository
from src.models import (
    ModelErrorCode,
    ModelManager,
    ModelsConfig,
    ModelsRuntimeConfig,
    default_provider_specs,
    default_route_configs,
)
from src.models.protocol import ContextCompressionResult, ModelCallResult


class FakeModelManager:
    def __init__(self, result: ContextCompressionResult | None = None, *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    def compress_context(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("FakeModelManager requires a result or error")
        return self.result


def _make_manager(
    tmp_path: Path,
    *,
    max_recent_messages: int = 2,
    summary_trigger_messages: int = 4,
    summary_batch_messages: int = 2,
    model_manager: Any | None = None,
) -> SessionManager:
    config = MemoryConfig(
        database_path=tmp_path / "memory.db",
        log_path=tmp_path / "memory.log",
        max_recent_messages=max_recent_messages,
        summary_trigger_messages=summary_trigger_messages,
        summary_batch_messages=summary_batch_messages,
        summary_target_chars=2000,
        max_message_content_chars=12000,
        max_event_display_chars=1200,
        max_event_payload_chars=1000,
    )
    return SessionManager(config=config, model_manager=model_manager)


def _make_models_config(root: Path) -> ModelsConfig:
    return ModelsConfig(
        workspace_root=root,
        config_dir=root,
        runtime=ModelsRuntimeConfig(
            logs_path=root / "logs" / "models.log",
            retry_backoff_base_seconds=0.0,
            retry_backoff_max_seconds=0.0,
        ),
        provider_specs=default_provider_specs(),
        provider_confs={},
        routes=default_route_configs(),
    )


class SequenceCompressionModel:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, **kwargs: Any) -> ModelCallResult:
        self.calls.append({"prompt": prompt, "kwargs": dict(kwargs)})
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        response = self.responses[index]
        if isinstance(response, ModelCallResult):
            return response
        content = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        return ModelCallResult.ok(
            content,
            provider="mock-provider",
            model="mock-summary-model",
        )

    def stream_generate(self, prompt: str, **kwargs: Any):
        yield ""


def _make_real_model_manager(root: Path, *responses: Any) -> tuple[ModelManager, SequenceCompressionModel]:
    model = SequenceCompressionModel(*responses)
    manager = ModelManager(model_name="mock", models_config=_make_models_config(root))
    manager.model = model
    return manager, model


def _make_success_result(
    *,
    compressed_text: str,
    compression_method: str,
    model: str | None = "mock-summary",
) -> ContextCompressionResult:
    return ContextCompressionResult(
        success=True,
        short_summary=compressed_text[:80],
        compressed_text=compressed_text,
        source_refs=["summary:seed", "message:one"],
        metadata={"compression_method": compression_method},
        model_result=ModelCallResult.ok(
            compressed_text,
            model=model,
            provider="mock-provider" if model is not None else None,
        ),
    )


def test_auto_summarize_skips_until_batch_threshold(tmp_path: Path) -> None:
    model_manager = FakeModelManager(
        _make_success_result(
            compressed_text="rolled summary",
            compression_method="single_model_call",
        )
    )
    manager = _make_manager(
        tmp_path,
        summary_batch_messages=3,
        model_manager=model_manager,
    )
    session = manager.create_session("batch_session")
    manager.append_message(session.session_id, "user", "one")
    manager.append_message(session.session_id, "assistant", "two")
    manager.append_message(session.session_id, "user", "three")
    manager.append_message(session.session_id, "assistant", "four")

    result = manager.maybe_auto_summarize(session.session_id)

    assert result is None
    assert model_manager.calls == []
    assert manager.repo.load_current_summary(session.session_id) is None


def test_auto_summarize_creates_initial_summary_from_early_messages(tmp_path: Path) -> None:
    model_manager = FakeModelManager(
        _make_success_result(
            compressed_text="first summary",
            compression_method="single_model_call",
        )
    )
    manager = _make_manager(tmp_path, model_manager=model_manager)
    session = manager.create_session("initial_session")
    first = manager.append_message(session.session_id, "user", "one")
    second = manager.append_message(session.session_id, "assistant", "two")
    third = manager.append_message(session.session_id, "user", "three")
    fourth = manager.append_message(session.session_id, "assistant", "four")
    manager.append_message(session.session_id, "user", "five")
    manager.append_message(session.session_id, "assistant", "six")

    result = manager.maybe_auto_summarize(session.session_id)
    current_summary = manager.repo.load_current_summary(session.session_id)

    assert result is not None
    assert result.content == "first summary"
    assert result.covered_from_timeline_seq == 1
    assert result.covered_to_timeline_seq == 4
    assert current_summary is not None
    assert current_summary.summary_id == result.summary_id
    assert [chunk["source_ref"] for chunk in model_manager.calls[0]["chunks"]] == [
        f"message:{first.message_id}",
        f"message:{second.message_id}",
        f"message:{third.message_id}",
        f"message:{fourth.message_id}",
    ]


def test_auto_summarize_includes_existing_summary_chunk_and_updates_pointer(tmp_path: Path) -> None:
    model_manager = FakeModelManager(
        _make_success_result(
            compressed_text="rolled summary",
            compression_method="single_model_call",
        )
    )
    manager = _make_manager(tmp_path, model_manager=model_manager)
    session = manager.create_session("rolling_session")
    manager.append_message(session.session_id, "user", "one")
    second = manager.append_message(session.session_id, "assistant", "two")
    seed_summary = manager.update_summary(session.session_id, "seed summary", second.timeline_seq)
    third = manager.append_message(session.session_id, "user", "three")
    manager.append_message(session.session_id, "assistant", "four")
    manager.append_message(session.session_id, "user", "five")
    manager.append_message(session.session_id, "assistant", "six")

    result = manager.maybe_auto_summarize(session.session_id)
    current_summary = manager.repo.load_current_summary(session.session_id)
    call = model_manager.calls[0]
    current_session = manager.load_session(session.session_id)

    assert result is not None
    assert result.content == "rolled summary"
    assert result.source == SummarySource.MODEL.value
    assert result.covered_from_timeline_seq == 3
    assert result.covered_to_timeline_seq == current_session.messages[3].timeline_seq
    assert current_summary is not None
    assert current_summary.summary_id == result.summary_id
    assert call["source_type"] == "conversation_summary"
    assert call["trigger_reason"] == "memory_auto_summary"
    assert call["allow_rule_fallback"] is True
    assert call["preserve_keys"] == [
        "user_goal",
        "decisions",
        "constraints",
        "file_paths",
        "open_tasks",
        "preferences",
    ]
    assert [chunk["source_ref"] for chunk in call["chunks"]] == [
        f"summary:{seed_summary.summary_id}",
        f"message:{third.message_id}",
        f"message:{current_session.messages[3].message_id}",
    ]


def test_auto_summarize_rule_fallback_is_recorded_as_fallback_source(tmp_path: Path) -> None:
    model_manager = FakeModelManager(
        _make_success_result(
            compressed_text="fallback summary",
            compression_method="rule_fallback",
            model=None,
        )
    )
    manager = _make_manager(tmp_path, model_manager=model_manager)
    session = manager.create_session("fallback_session")
    manager.append_message(session.session_id, "user", "one")
    manager.append_message(session.session_id, "assistant", "two")
    manager.append_message(session.session_id, "user", "three")
    manager.append_message(session.session_id, "assistant", "four")
    manager.append_message(session.session_id, "user", "five")
    manager.append_message(session.session_id, "assistant", "six")
    manager.append_message(session.session_id, "assistant", "six")

    result = manager.maybe_auto_summarize(session.session_id)

    assert result is not None
    assert result.source == SummarySource.RULE_FALLBACK.value
    assert result.content == "fallback summary"


def test_auto_summarize_failure_keeps_previous_summary_and_logs_failure(tmp_path: Path) -> None:
    model_manager = FakeModelManager(error=RuntimeError("boom"))
    manager = _make_manager(tmp_path, model_manager=model_manager)
    session = manager.create_session("failure_session")
    manager.append_message(session.session_id, "user", "one")
    second = manager.append_message(session.session_id, "assistant", "two")
    seed_summary = manager.update_summary(session.session_id, "seed summary", second.timeline_seq)
    manager.append_message(session.session_id, "user", "three")
    manager.append_message(session.session_id, "assistant", "four")
    manager.append_message(session.session_id, "user", "five")
    manager.append_message(session.session_id, "assistant", "six")

    result = manager.maybe_auto_summarize(session.session_id)

    assert result is not None
    assert result.summary_id is not None
    assert manager.repo.load_current_summary(session.session_id).summary_id == seed_summary.summary_id
    assert manager.load_session(session.session_id).summary == "seed summary"
    log_text = Path(manager.config.log_path).read_text(encoding="utf-8")
    assert "summary_failed" in log_text
    assert "boom" in log_text
    assert model_manager.calls[0]["metadata"]["candidate_message_count"] == 2


def test_auto_summarize_uses_real_model_manager_compress_context(tmp_path: Path) -> None:
    model_manager, model = _make_real_model_manager(
        tmp_path,
        {
            "short_summary": "First chunk.",
            "compressed_text": "User asked for a project memory layer.",
            "key_points": ["memory layer"],
            "loss_risk": "low",
        },
        {
            "short_summary": "Second chunk.",
            "compressed_text": "Assistant confirmed SQLite and summaries.",
            "key_points": ["sqlite", "summaries"],
            "loss_risk": "low",
        },
        {
            "short_summary": "Merged memory summary.",
            "compressed_text": "User is building Memory MVP with SQLite sessions and automatic summaries.",
            "key_points": ["Memory MVP", "SQLite sessions", "automatic summaries"],
            "loss_risk": "low",
        },
    )
    manager = _make_manager(
        tmp_path,
        max_recent_messages=4,
        summary_trigger_messages=4,
        summary_batch_messages=2,
        model_manager=model_manager,
    )
    session = manager.create_session("models_integration_session")
    manager.append_message(session.session_id, "user", "Design Memory MVP.")
    manager.append_message(session.session_id, "assistant", "Use SQLite sessions.")
    manager.append_message(session.session_id, "user", "Keep summaries automatic.")
    manager.append_message(session.session_id, "assistant", "Use ModelManager.compress_context.")
    manager.append_message(session.session_id, "user", "Continue with Runtime later.")
    manager.append_message(session.session_id, "assistant", "Runtime remains a later step.")

    result = manager.maybe_auto_summarize(session.session_id)
    current_summary = manager.repo.load_current_summary(session.session_id)

    assert result is not None
    assert result.source == SummarySource.MODEL.value
    assert result.content == "User is building Memory MVP with SQLite sessions and automatic summaries."
    assert result.model_profile == "mock-summary-model"
    assert result.metadata["compression_method"] == "chunked_model_synthesis"
    assert result.metadata["source_type"] == "conversation_summary"
    assert result.metadata["trigger_reason"] == "memory_auto_summary"
    assert result.metadata["candidate_message_count"] == 2
    assert current_summary is not None
    assert current_summary.summary_id == result.summary_id
    assert len(model.calls) == 3
    assert all(call["kwargs"]["call_type"] == "context_compression" for call in model.calls)


def test_auto_summarize_uses_model_manager_rule_fallback_without_blocking(tmp_path: Path) -> None:
    model_manager, model = _make_real_model_manager(
        tmp_path,
        ModelCallResult.fail(ModelErrorCode.MODEL_CALL_FAILED, "provider unavailable"),
    )
    manager = _make_manager(
        tmp_path,
        max_recent_messages=4,
        summary_trigger_messages=4,
        summary_batch_messages=2,
        model_manager=model_manager,
    )
    session = manager.create_session("models_fallback_session")
    manager.append_message(session.session_id, "user", "Design Memory MVP.")
    manager.append_message(session.session_id, "assistant", "Use SQLite sessions.")
    manager.append_message(session.session_id, "user", "Keep summaries automatic.")
    manager.append_message(session.session_id, "assistant", "Use ModelManager.compress_context.")
    manager.append_message(session.session_id, "user", "Continue with Runtime later.")
    manager.append_message(session.session_id, "assistant", "Runtime remains a later step.")

    result = manager.maybe_auto_summarize(session.session_id)

    assert result is not None
    assert result.source == SummarySource.RULE_FALLBACK.value
    assert result.content
    assert "Design Memory MVP." in result.content
    assert result.metadata["compression_method"] == "rule_fallback"
    assert result.metadata["fallback_reason"] == ModelErrorCode.MODEL_CALL_FAILED.value
    assert len(model.calls) == 1
