"""Runtime application object and dependency lifecycle boundary."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from src.memory.ids import validate_session_id

from .contracts import (
    CancelRequest,
    ResumeRequest,
    RuntimeEvent,
    RuntimeRequest,
    RuntimeResult,
)
from .errors import (
    RuntimeErrorCode,
    RuntimeException,
    map_exception,
    runtime_result_from_exception,
    sanitize_error_message,
)
from .serialization import (
    SENSITIVE_FIELD_NAMES,
    safe_serialize,
    serialize_execution_result,
    serialize_memory_result,
    serialize_output_feedback,
    serialize_pending_confirmation,
    serialize_runtime_event,
)
from .events import RuntimeEventCoordinator
from .export import build_session_markdown
from .health import HealthChecker


MAX_RUNTIME_INPUT_CHARS = 32_000
MAX_RUNTIME_METADATA_ITEMS = 50
MAX_RUNTIME_METADATA_BYTES = 16_384


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


@dataclass(frozen=True)
class _RuntimeRequestContext:
    """Per-call Runtime state reserved for the later execution chain."""

    request: RuntimeRequest
    event_sink: Callable[[Any], None] | None = None
    memory_turn: Any | None = None
    react_agent_kwargs: Mapping[str, Any] | None = None
    event_coordinator: RuntimeEventCoordinator = field(
        default_factory=RuntimeEventCoordinator
    )


@dataclass(frozen=True)
class _RuntimePendingContext:
    """Process-local state required to resume one waiting Runtime turn."""

    executor_context: Any
    runtime_context: _RuntimeRequestContext


class Runtime:
    """Process-level application facade for the formal Runtime mode.

    Runtime coordinates application dependencies while leaving Agent,
    Memory, Models, and Tools responsibilities in their owning layers.
    """

    def __init__(
        self,
        *,
        config: Any,
        model_manager: Any,
        tool_manager: Any,
        tool_registry: Any,
        session_manager: Any,
        context_builder: Any,
        memory_adapter: Any,
        analyzer: Any,
        planner: Any,
        react_executor: Any,
        react_agent: Any,
        output_feedback_processor: Any,
        pending_run_registry: Any,
        health_checker: Any | None = None,
        recover_on_startup: bool = True,
    ) -> None:
        self.config = config
        self.workspace_root = Path(config.workspace_root)
        self.model_manager = model_manager
        self.tool_manager = tool_manager
        self.tool_registry = tool_registry
        self.session_manager = session_manager
        self.context_builder = context_builder
        self.memory_adapter = memory_adapter
        self.analyzer = analyzer
        self.planner = planner
        self.react_executor = react_executor
        self.react_agent = react_agent
        self.output_feedback_processor = output_feedback_processor
        self.pending_run_registry = pending_run_registry
        self.health_checker = health_checker
        self.recovery_count: int | None = None
        self.close_errors: list[RuntimeException] = []
        self._closed = False

        if recover_on_startup:
            self.recovery_count = self._recover_interrupted_runs()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def dependencies(self) -> dict[str, Any]:
        """Return the assembled dependency objects for diagnostics/tests."""

        return {
            "model_manager": self.model_manager,
            "tool_manager": self.tool_manager,
            "tool_registry": self.tool_registry,
            "session_manager": self.session_manager,
            "context_builder": self.context_builder,
            "memory_adapter": self.memory_adapter,
            "analyzer": self.analyzer,
            "planner": self.planner,
            "react_executor": self.react_executor,
            "react_agent": self.react_agent,
            "output_feedback_processor": self.output_feedback_processor,
            "pending_run_registry": self.pending_run_registry,
            "health_checker": self.health_checker,
        }

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        """Execute one normal Runtime turn through the formal Agent mode."""

        try:
            context = self._prepare_request_context(request)
        except RuntimeException as exc:
            return runtime_result_from_exception(exc)
        try:
            context = self._begin_memory_turn(context)
        except RuntimeException as exc:
            return runtime_result_from_exception(exc)
        try:
            execution_result, output_feedback = self._run_agent(context)
        except RuntimeException as exc:
            return self._runtime_result_from_error(
                context,
                exc,
            )
        except Exception as exc:
            runtime_error = map_exception(
                exc,
                default_code=RuntimeErrorCode.AGENT_EXECUTION_FAILED,
                metadata={"stage": "agent_execution"},
            )
            return self._runtime_result_from_error(
                context,
                runtime_error,
            )
        return self._runtime_result_from_agent(
            context,
            execution_result,
            output_feedback,
        )

    def run_stream(
        self,
        request: RuntimeRequest,
        event_sink: Callable[[Any], None] | None = None,
    ) -> RuntimeResult:
        """Validate the future streaming entrypoint without starting a run."""

        try:
            context = self._prepare_request_context(request, event_sink=event_sink)
        except RuntimeException as exc:
            return runtime_result_from_exception(exc)
        return self._execution_not_available_result(context)

    def resume(self, request: ResumeRequest) -> RuntimeResult:
        """Resume a same-process run that is waiting for confirmation."""

        try:
            self._validate_resume_request(request)
        except RuntimeException as exc:
            return runtime_result_from_exception(exc)

        pop = getattr(self.pending_run_registry, "pop", None)
        if not callable(pop):
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                    "Runtime pending-run registry does not provide pop.",
                    metadata={"dependency": "pending_run_registry"},
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        try:
            pending_record = pop(
                request.run_id,
                session_id=request.session_id,
            )
        except RuntimeException as exc:
            return runtime_result_from_exception(
                exc,
                session_id=request.session_id,
                run_id=request.run_id,
            )
        except Exception as exc:
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.INTERNAL_ERROR,
                    "Runtime could not access the pending run registry.",
                    metadata={"stage": "resume_registry"},
                    cause=exc,
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        if pending_record is None:
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.RUN_NOT_FOUND,
                    "The pending Runtime run was not found or has expired.",
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        pending_context = getattr(pending_record, "executor_context", None)
        if not isinstance(pending_context, _RuntimePendingContext):
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.INTERRUPTED,
                    "The pending execution context is no longer available for resume.",
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        executor_context = pending_context.executor_context
        context = pending_context.runtime_context
        if executor_context is None:
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.INTERRUPTED,
                    "The pending execution context is no longer available for resume.",
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        if (
            self._context_session_id(context) != request.session_id
            or self._context_run_id(context) != request.run_id
        ):
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.SESSION_CONFLICT,
                    "The pending execution context does not match the requested run.",
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        # A resume request may choose a different debug view, but it keeps the
        # original Memory turn, event sink, and coordinator instance intact.
        if context.request.debug != request.debug:
            context = replace(
                context,
                request=replace(context.request, debug=request.debug),
            )

        # Runtime performs the safe boundary check first. The underlying
        # executor receives the same values as well and performs its own
        # authoritative confirmation/preview validation before any tool call.
        confirmation_mismatch = self._pending_confirmation_mismatch(
            pending_record,
            request,
        )

        event_stream = getattr(executor_context, "event_stream", None)
        unsubscribe = None
        if event_stream is not None:
            subscribe = getattr(event_stream, "subscribe", None)
            if callable(subscribe):
                try:
                    unsubscribe = subscribe(
                        self._build_run_event_callback(context),
                        visible_only=True,
                    )
                except Exception as exc:
                    return self._runtime_result_from_error(
                        context,
                        RuntimeException(
                            RuntimeErrorCode.AGENT_EXECUTION_FAILED,
                            "Runtime could not subscribe to resume events.",
                            metadata={"stage": "resume_event_subscription"},
                            cause=exc,
                        ),
                    )

        try:
            resume_after_confirmation = getattr(
                self.react_executor,
                "resume_after_confirmation",
                None,
            )
            if not callable(resume_after_confirmation):
                raise RuntimeException(
                    RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                    "Runtime ReActExecutor does not provide resume_after_confirmation.",
                    metadata={"dependency": "react_executor"},
                )

            execution_result = resume_after_confirmation(
                executor_context,
                approved=request.approved,
                reason=(
                    confirmation_mismatch
                    if confirmation_mismatch
                    else request.reason
                ),
                confirmation_id=request.confirmation_id,
                preview_hash=request.preview_hash,
            )
            if self._runtime_status_from_execution(execution_result) == "waiting_user":
                # Re-registering a second confirmation needs the same live
                # executor context, while keeping it out of public results.
                execution_result.executor_context = executor_context
            self._replay_unprocessed_result_events(context, execution_result)

            build_feedback = getattr(self.output_feedback_processor, "build", None)
            if not callable(build_feedback):
                raise RuntimeException(
                    RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                    "Runtime OutputFeedbackProcessor does not provide build.",
                    metadata={"dependency": "output_feedback_processor"},
                )
            output_feedback = build_feedback(
                execution_result,
                include_internal=False,
                group_related=True,
            )
        except RuntimeException as exc:
            return self._runtime_result_from_error(context, exc)
        except Exception as exc:
            return self._runtime_result_from_error(
                context,
                RuntimeException(
                    RuntimeErrorCode.AGENT_EXECUTION_FAILED,
                    "Runtime confirmation recovery failed.",
                    metadata={"stage": "resume_execution"},
                    cause=exc,
                ),
            )
        finally:
            if callable(unsubscribe):
                try:
                    unsubscribe()
                except Exception:
                    pass

        result = self._runtime_result_from_agent(
            context,
            execution_result,
            output_feedback,
        )
        result.metadata = {
            **result.metadata,
            "resumed": True,
        }
        return result

    def cancel(self, request: CancelRequest) -> RuntimeResult:
        """Cancel a same-process run that is waiting for confirmation."""

        try:
            self._validate_cancel_request(request)
        except RuntimeException as exc:
            return runtime_result_from_exception(exc)

        pop = getattr(self.pending_run_registry, "pop", None)
        if not callable(pop):
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                    "Runtime pending-run registry does not provide pop.",
                    metadata={"dependency": "pending_run_registry"},
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        try:
            pending_record = pop(
                request.run_id,
                session_id=request.session_id,
            )
        except RuntimeException as exc:
            return runtime_result_from_exception(
                exc,
                session_id=request.session_id,
                run_id=request.run_id,
            )
        except Exception as exc:
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.INTERNAL_ERROR,
                    "Runtime could not access the pending run registry.",
                    metadata={"stage": "cancel_registry"},
                    cause=exc,
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        if pending_record is None:
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.RUN_NOT_FOUND,
                    "Only a waiting_user pending run can be cancelled; no pending run was found.",
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        pending_context = getattr(pending_record, "executor_context", None)
        if not isinstance(pending_context, _RuntimePendingContext):
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.INTERRUPTED,
                    "The pending execution context is no longer available for cancellation.",
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        executor_context = pending_context.executor_context
        context = pending_context.runtime_context
        if executor_context is None:
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.INTERRUPTED,
                    "The pending execution context is no longer available for cancellation.",
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        if (
            self._context_session_id(context) != request.session_id
            or self._context_run_id(context) != request.run_id
        ):
            return runtime_result_from_exception(
                RuntimeException(
                    RuntimeErrorCode.SESSION_CONFLICT,
                    "The pending execution context does not match the requested run.",
                ),
                session_id=request.session_id,
                run_id=request.run_id,
            )

        event_stream = getattr(executor_context, "event_stream", None)
        unsubscribe = None
        if event_stream is not None:
            subscribe = getattr(event_stream, "subscribe", None)
            if callable(subscribe):
                try:
                    unsubscribe = subscribe(
                        self._build_run_event_callback(context),
                        visible_only=True,
                    )
                except Exception as exc:
                    return self._runtime_result_from_error(
                        context,
                        RuntimeException(
                            RuntimeErrorCode.AGENT_EXECUTION_FAILED,
                            "Runtime could not subscribe to cancellation events.",
                            metadata={"stage": "cancel_event_subscription"},
                            cause=exc,
                        ),
                    )

        cancel_reason = sanitize_error_message(
            request.reason or "User cancelled the pending confirmation."
        )
        pending_confirmation = getattr(pending_record, "pending_confirmation", None)
        confirmation_id = None
        preview_hash = None
        if isinstance(pending_confirmation, Mapping):
            confirmation_id = pending_confirmation.get("confirmation_id")
            preview_hash = pending_confirmation.get("preview_hash")

        try:
            emit_event = getattr(event_stream, "emit_event", None)
            if callable(emit_event):
                emit_event(
                    "system_notice",
                    cancel_reason,
                    payload={"status": "cancelled", "reason": cancel_reason},
                    visible_to_user=True,
                )

            cancel_confirmation = getattr(
                self.react_executor,
                "resume_after_confirmation",
                None,
            )
            if not callable(cancel_confirmation):
                raise RuntimeException(
                    RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                    "Runtime ReActExecutor does not provide confirmation cancellation.",
                    metadata={"dependency": "react_executor"},
                )

            execution_result = cancel_confirmation(
                executor_context,
                approved=False,
                reason=cancel_reason,
                confirmation_id=confirmation_id,
                preview_hash=preview_hash,
            )
            self._replay_unprocessed_result_events(context, execution_result)

            build_feedback = getattr(self.output_feedback_processor, "build", None)
            if not callable(build_feedback):
                raise RuntimeException(
                    RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                    "Runtime OutputFeedbackProcessor does not provide build.",
                    metadata={"dependency": "output_feedback_processor"},
                )
            output_feedback = build_feedback(
                execution_result,
                include_internal=False,
                group_related=True,
            )
        except RuntimeException as exc:
            return self._runtime_result_from_error(context, exc)
        except Exception as exc:
            return self._runtime_result_from_error(
                context,
                RuntimeException(
                    RuntimeErrorCode.AGENT_EXECUTION_FAILED,
                    "Runtime confirmation cancellation failed.",
                    metadata={"stage": "cancel_execution"},
                    cause=exc,
                ),
            )
        finally:
            if callable(unsubscribe):
                try:
                    unsubscribe()
                except Exception:
                    pass

        result = self._runtime_result_from_agent(
            context,
            execution_result,
            output_feedback,
            terminal_error=RuntimeException(
                RuntimeErrorCode.CANCELLED,
                cancel_reason,
                status="cancelled",
                metadata={"stage": "runtime_cancel"},
            ),
        )
        result.metadata = {
            **result.metadata,
            "cancelled": True,
        }
        return result

    def get_session(self, session_id: str) -> Any:
        """Return a safe serialized Memory session projection."""

        normalized_session_id = self._validate_session_id(session_id)
        get_session = getattr(self.memory_adapter, "get_session", None)
        if not callable(get_session):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime Memory adapter does not provide get_session.",
                metadata={"dependency": "memory_adapter"},
            )
        try:
            session = get_session(normalized_session_id)
        except KeyError as exc:
            raise RuntimeException(
                RuntimeErrorCode.SESSION_NOT_FOUND,
                "The requested session was not found.",
                metadata={"operation": "get_session"},
                cause=exc,
            ) from exc
        except RuntimeException:
            raise
        except Exception as exc:
            raise self._session_facade_error(
                exc,
                operation="get_session",
            ) from exc
        return self._safe_session_projection(session)

    def list_sessions(self) -> list[Any]:
        """Return safe serialized session summaries from SessionManager."""

        list_sessions = getattr(self.session_manager, "list_sessions", None)
        if not callable(list_sessions):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime SessionManager does not provide list_sessions.",
                metadata={"dependency": "session_manager"},
            )
        try:
            sessions = list_sessions()
        except RuntimeException:
            raise
        except Exception as exc:
            raise self._session_facade_error(
                exc,
                operation="list_sessions",
            ) from exc
        serialized = safe_serialize(sessions or [], debug=False)
        return serialized if isinstance(serialized, list) else []

    def get_timeline(self, session_id: str) -> list[Any]:
        """Return the Memory-owned, visible-only timeline projection."""

        normalized_session_id = self._validate_session_id(session_id)
        get_timeline = getattr(self.memory_adapter, "get_timeline", None)
        if not callable(get_timeline):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime Memory adapter does not provide get_timeline.",
                metadata={"dependency": "memory_adapter"},
            )
        try:
            timeline = get_timeline(normalized_session_id)
        except KeyError as exc:
            raise RuntimeException(
                RuntimeErrorCode.SESSION_NOT_FOUND,
                "The requested session was not found.",
                metadata={"operation": "get_timeline"},
                cause=exc,
            ) from exc
        except RuntimeException:
            raise
        except Exception as exc:
            raise self._session_facade_error(
                exc,
                operation="get_timeline",
            ) from exc
        serialized = safe_serialize(timeline or [], debug=False)
        if not isinstance(serialized, list):
            return []
        return [
            item
            for item in serialized
            if isinstance(item, dict) and item.get("visible_to_user", True) is not False
        ]

    def delete_session(self, session_id: str) -> bool:
        """Hard-delete a session through SessionManager's public interface."""

        normalized_session_id = self._validate_session_id(session_id)
        delete_session = getattr(self.session_manager, "delete_session", None)
        if not callable(delete_session):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime SessionManager does not provide delete_session.",
                metadata={"dependency": "session_manager"},
            )
        try:
            deleted = delete_session(normalized_session_id)
        except RuntimeException:
            raise
        except Exception as exc:
            raise self._session_facade_error(
                exc,
                operation="delete_session",
            ) from exc
        if not isinstance(deleted, bool):
            raise RuntimeException(
                RuntimeErrorCode.INTERNAL_ERROR,
                "Runtime session deletion returned an invalid result.",
                metadata={"operation": "delete_session"},
            )
        if not deleted:
            raise RuntimeException(
                RuntimeErrorCode.SESSION_NOT_FOUND,
                "The requested session was not found.",
                metadata={"operation": "delete_session"},
            )
        clear_session = getattr(self.pending_run_registry, "clear_session", None)
        if callable(clear_session):
            try:
                clear_session(normalized_session_id)
            except Exception:
                # Session deletion is already complete; stale process-local
                # confirmation state must not turn a successful delete into a
                # different public result.
                pass
        return True

    def export_session(
        self,
        session_id: str,
        output_path: str | Path | None = None,
    ) -> Any:
        """Return Markdown and optionally write it to a safe new file."""

        normalized_session_id = self._validate_session_id(session_id)
        if output_path is not None and not isinstance(output_path, (str, Path)):
            raise self._validation_error("output_path must be a path string or Path")
        session = self.get_session(normalized_session_id)
        timeline = self.get_timeline(normalized_session_id)
        content = build_session_markdown(session, timeline)

        if output_path is None:
            return content

        destination = self._safe_export_path(output_path)
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        except FileExistsError as exc:
            raise RuntimeException(
                RuntimeErrorCode.EXPORT_FAILED,
                "The export destination already exists.",
                metadata={"operation": "export_session", "reason": "file_exists"},
                cause=exc,
            ) from exc
        except (OSError, ValueError) as exc:
            raise RuntimeException(
                RuntimeErrorCode.EXPORT_FAILED,
                "Runtime could not write the session export.",
                metadata={"operation": "export_session"},
                cause=exc,
            ) from exc
        return content

    def _session_facade_error(
        self,
        error: Exception,
        *,
        operation: str,
    ) -> RuntimeException:
        mapped = map_exception(
            error,
            default_code=RuntimeErrorCode.MEMORY_UNAVAILABLE,
            metadata={"operation": operation},
        )
        if mapped.code in {
            RuntimeErrorCode.SESSION_NOT_FOUND.value,
            RuntimeErrorCode.SESSION_CONFLICT.value,
        }:
            return mapped
        return RuntimeException(
            RuntimeErrorCode.MEMORY_UNAVAILABLE,
            "Runtime could not access session data.",
            metadata={"operation": operation},
            cause=error,
        )

    def _safe_session_projection(self, session: Any) -> dict[str, Any]:
        serialized = safe_serialize(session, debug=False)
        if not isinstance(serialized, dict):
            return {}
        messages = serialized.get("messages")
        if isinstance(messages, list):
            serialized["messages"] = [
                message
                for message in messages
                if isinstance(message, dict)
                and message.get("visible_to_user", True) is not False
            ]
        return serialized

    def _safe_export_path(self, output_path: str | Path) -> Path:
        raw_path = Path(output_path).expanduser()
        if not raw_path.is_absolute():
            destination = self.workspace_root / raw_path
        else:
            destination = raw_path
        try:
            resolved = destination.resolve()
            workspace = self.workspace_root.resolve()
            resolved.relative_to(workspace)
        except (OSError, ValueError) as exc:
            raise RuntimeException(
                RuntimeErrorCode.EXPORT_FAILED,
                "Export destination must be inside the Runtime workspace.",
                metadata={"operation": "export_session", "reason": "path_policy"},
                cause=exc,
            ) from exc
        if resolved == workspace:
            raise RuntimeException(
                RuntimeErrorCode.EXPORT_FAILED,
                "Export destination must be a file inside the Runtime workspace.",
                metadata={"operation": "export_session", "reason": "path_policy"},
            )
        return resolved

    def health(self) -> dict[str, Any]:
        """Return an aggregated, sanitized health report for Runtime."""

        checker = self.health_checker or HealthChecker()
        check = getattr(checker, "check", None)
        if not callable(check):
            return HealthChecker().failure(
                self,
                RuntimeError("health checker does not provide check()"),
            ).to_dict()
        try:
            report = check(self)
            if hasattr(report, "to_dict"):
                report = report.to_dict()
            if not isinstance(report, dict):
                raise TypeError("health checker returned a non-mapping result")
            serialized = safe_serialize(
                report,
                debug=False,
                max_depth=8,
                max_items=100,
                max_text_chars=500,
            )
            return serialized if isinstance(serialized, dict) else HealthChecker().failure(
                self,
                RuntimeError("health report serialization failed"),
            ).to_dict()
        except Exception as exc:
            return HealthChecker().failure(self, exc).to_dict()

    def close(self) -> None:
        """Close releasable dependencies once, without deleting Memory data."""

        if self._closed:
            return
        self._closed = True
        seen: set[int] = set()
        for dependency in self._close_candidates():
            if dependency is None or id(dependency) in seen:
                continue
            seen.add(id(dependency))
            self._close_dependency(dependency)

    def _prepare_request_context(
        self,
        request: RuntimeRequest,
        *,
        event_sink: Callable[[Any], None] | None = None,
    ) -> _RuntimeRequestContext:
        self._validate_runtime_request(request)
        if event_sink is not None and not callable(event_sink):
            raise self._validation_error("event_sink must be callable")
        return _RuntimeRequestContext(request=request, event_sink=event_sink)

    def _begin_memory_turn(
        self,
        context: _RuntimeRequestContext,
    ) -> _RuntimeRequestContext:
        """Create the Memory-owned turn and cache its agent inputs per call."""

        begin_turn = getattr(self.memory_adapter, "begin_turn", None)
        if not callable(begin_turn):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime Memory adapter does not provide begin_turn.",
                metadata={"dependency": "memory_adapter"},
            )

        request = context.request
        try:
            turn = begin_turn(
                request.session_id,
                request.input,
                user_metadata=dict(request.metadata),
                agent_version=request.agent_version,
                model_profile=request.model_profile,
            )
            react_agent_kwargs = self._react_agent_kwargs_from_turn(turn)
        except RuntimeException:
            raise
        except Exception as exc:
            raise RuntimeException(
                RuntimeErrorCode.MEMORY_UNAVAILABLE,
                "Runtime could not start the Memory turn.",
                metadata={"stage": "begin_turn"},
                cause=exc,
            ) from exc

        return _RuntimeRequestContext(
            request=request,
            event_sink=context.event_sink,
            memory_turn=turn,
            react_agent_kwargs=react_agent_kwargs,
            event_coordinator=context.event_coordinator,
        )

    def _react_agent_kwargs_from_turn(self, turn: Any) -> Mapping[str, Any]:
        build_kwargs = getattr(turn, "react_agent_kwargs", None)
        if not callable(build_kwargs):
            raise RuntimeException(
                RuntimeErrorCode.MEMORY_UNAVAILABLE,
                "Runtime Memory turn does not provide ReactAgent inputs.",
                metadata={"stage": "begin_turn"},
            )
        kwargs = build_kwargs()
        if not isinstance(kwargs, Mapping):
            raise RuntimeException(
                RuntimeErrorCode.MEMORY_UNAVAILABLE,
                "Runtime Memory turn returned invalid ReactAgent inputs.",
                metadata={"stage": "begin_turn"},
            )

        required = ("context_text", "session_id", "run_id", "manage_memory")
        if any(name not in kwargs for name in required):
            raise RuntimeException(
                RuntimeErrorCode.MEMORY_UNAVAILABLE,
                "Runtime Memory turn returned incomplete ReactAgent inputs.",
                metadata={"stage": "begin_turn"},
            )
        if kwargs["session_id"] != getattr(turn, "session_id", None):
            raise RuntimeException(
                RuntimeErrorCode.MEMORY_UNAVAILABLE,
                "Runtime Memory turn returned mismatched session inputs.",
                metadata={"stage": "begin_turn"},
            )
        if kwargs["run_id"] != getattr(turn, "run_id", None):
            raise RuntimeException(
                RuntimeErrorCode.MEMORY_UNAVAILABLE,
                "Runtime Memory turn returned mismatched run inputs.",
                metadata={"stage": "begin_turn"},
            )
        if kwargs["manage_memory"] is not False:
            raise RuntimeException(
                RuntimeErrorCode.MEMORY_UNAVAILABLE,
                "Runtime Memory turn must disable ReactAgent memory management.",
                metadata={"stage": "begin_turn"},
            )
        if not isinstance(kwargs["context_text"], str):
            raise RuntimeException(
                RuntimeErrorCode.MEMORY_UNAVAILABLE,
                "Runtime Memory turn returned invalid context text.",
                metadata={"stage": "begin_turn"},
            )
        return dict(kwargs)

    def _run_agent(
        self,
        context: _RuntimeRequestContext,
    ) -> tuple[Any, Any]:
        """Call ReactAgent in the formal Runtime-owned memory mode."""

        run_with_result = getattr(self.react_agent, "run_with_result", None)
        if not callable(run_with_result):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime ReactAgent does not provide run_with_result.",
                metadata={"dependency": "react_agent"},
            )

        agent_kwargs = dict(context.react_agent_kwargs or {})
        agent_kwargs.update(
            {
                "event_callback": self._build_run_event_callback(context),
                "event_callback_visible_only": True,
            }
        )
        try:
            execution_result = run_with_result(
                context.request.input,
                **agent_kwargs,
            )
        except RuntimeException:
            raise
        except Exception as exc:
            raise RuntimeException(
                RuntimeErrorCode.AGENT_EXECUTION_FAILED,
                "ReactAgent execution failed.",
                metadata={
                    "stage": "agent_execution",
                    "session_id": self._context_session_id(context),
                    "run_id": self._context_run_id(context),
                },
                cause=exc,
            ) from exc

        self._replay_unprocessed_result_events(context, execution_result)

        build_feedback = getattr(self.output_feedback_processor, "build", None)
        if not callable(build_feedback):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime OutputFeedbackProcessor does not provide build.",
                metadata={"dependency": "output_feedback_processor"},
            )
        try:
            output_feedback = build_feedback(
                execution_result,
                include_internal=False,
                group_related=True,
            )
        except RuntimeException:
            raise
        except Exception as exc:
            raise RuntimeException(
                RuntimeErrorCode.AGENT_EXECUTION_FAILED,
                "Runtime could not build OutputFeedback.",
                metadata={"stage": "output_feedback"},
                cause=exc,
            ) from exc
        return execution_result, output_feedback

    def _build_run_event_callback(
        self,
        context: _RuntimeRequestContext,
    ) -> Callable[[Any], None]:
        def callback(event: Any) -> None:
            try:
                self._process_execution_event(context, event)
            except Exception:
                # Event handling is observational and must not change Agent
                # execution semantics, even for malformed adapter events.
                return

        return callback

    def _replay_unprocessed_result_events(
        self,
        context: _RuntimeRequestContext,
        execution_result: Any,
    ) -> None:
        for event in list(getattr(execution_result, "events", []) or []):
            try:
                self._process_execution_event(context, event)
            except Exception:
                # A result-event fallback is best effort for compatibility
                # executors that cannot accept callbacks.
                continue

    def _process_execution_event(
        self,
        context: _RuntimeRequestContext,
        event: Any,
    ) -> RuntimeEvent | None:
        coordinator = context.event_coordinator
        key = coordinator.key_for(event)
        if not coordinator.mark_processed(key):
            return None
        if context.event_sink is not None:
            coordinator.callback_event_count += 1

        stored_event = self._record_memory_event(context, event)
        runtime_event = self._build_runtime_event(
            context,
            event,
            sequence=coordinator.sequence,
            stored_event=stored_event,
        )
        if runtime_event is None or not runtime_event.visible_to_user:
            return runtime_event
        self._send_event_to_sink(context.event_sink, runtime_event)
        return runtime_event

    def _record_memory_event(
        self,
        context: _RuntimeRequestContext,
        event: Any,
    ) -> Any | None:
        turn = context.memory_turn
        if turn is None:
            return None
        record_event = getattr(self.memory_adapter, "record_event", None)
        if not callable(record_event):
            return None
        try:
            return record_event(turn, event)
        except Exception as exc:
            self._mark_event_persistence_failure(turn, exc)
            return None

    def _build_runtime_event(
        self,
        context: _RuntimeRequestContext,
        event: Any,
        *,
        sequence: int,
        stored_event: Any | None,
    ) -> RuntimeEvent | None:
        session_id = self._context_session_id(context)
        run_id = self._context_run_id(context)
        if not isinstance(session_id, str) or not isinstance(run_id, str):
            return None

        visible_to_user = self._event_is_visible(event)
        event_type = self._event_field(
            event,
            "type",
            self._event_field(event, "event_type", "system_notice"),
        )
        message = safe_serialize(
            self._event_field(event, "message", ""),
            debug=context.request.debug,
            max_text_chars=1200,
        )
        payload = safe_serialize(
            self._event_field(event, "payload", {}),
            debug=context.request.debug,
            max_depth=8,
            max_items=200,
            max_text_chars=1000,
        )
        source_event = self._safe_source_event(event, debug=context.request.debug)
        memory_event_id = self._event_field(stored_event, "event_id")
        runtime_event = RuntimeEvent(
            session_id=session_id,
            run_id=run_id,
            event_type=str(event_type or "system_notice"),
            message=message if isinstance(message, str) else "",
            visible_to_user=visible_to_user,
            payload=payload if isinstance(payload, dict) else {},
            source_event=source_event,
            sequence=sequence,
            event_id=memory_event_id if isinstance(memory_event_id, str) else None,
            created_at=str(
                self._event_field(
                    event,
                    "timestamp",
                    self._event_field(event, "created_at", _utc_now_iso()),
                )
            ),
        )
        return runtime_event

    def _safe_source_event(self, event: Any, *, debug: bool) -> dict[str, Any]:
        source = {
            "event_id": self._event_field(event, "event_id"),
            "execution_id": self._event_field(event, "execution_id"),
            "plan_id": self._event_field(event, "plan_id"),
            "task_id": self._event_field(event, "task_id"),
            "step_id": self._event_field(event, "step_id"),
            "type": self._event_field(
                event,
                "type",
                self._event_field(event, "event_type"),
            ),
            "timestamp": self._event_field(
                event,
                "timestamp",
                self._event_field(event, "created_at"),
            ),
            "visible_to_user": self._event_is_visible(event),
        }
        serialized = safe_serialize(
            source,
            debug=debug,
            max_depth=4,
            max_items=30,
            max_text_chars=500,
        )
        return serialized if isinstance(serialized, dict) else {}

    def _send_event_to_sink(
        self,
        event_sink: Callable[[Any], None] | None,
        event: RuntimeEvent,
    ) -> None:
        if event_sink is None:
            return
        try:
            event_sink(event)
        except Exception:
            # CLI/API sinks are observational and may disappear mid-run.
            return

    def _mark_event_persistence_failure(self, turn: Any, error: Any) -> None:
        try:
            from .errors import sanitize_error_message

            warning = (
                "Memory persistence unavailable while recording an execution "
                f"event ({sanitize_error_message(error)})."
            )
            setattr(turn, "persistence_available", False)
            if not getattr(turn, "persistence_warning", None):
                setattr(turn, "persistence_warning", warning)
        except Exception:
            return

    def _event_field(self, event: Any, name: str, default: Any = None) -> Any:
        if isinstance(event, Mapping):
            return event.get(name, default)
        return getattr(event, name, default)

    def _runtime_result_from_agent(
        self,
        context: _RuntimeRequestContext,
        execution_result: Any,
        output_feedback: Any,
        terminal_error: RuntimeException | None = None,
    ) -> RuntimeResult:
        status = self._runtime_status_from_execution(execution_result)
        feedback_output = getattr(output_feedback, "final_output", None)
        execution_output = getattr(execution_result, "output", None)
        output = (
            feedback_output
            if isinstance(feedback_output, str) and feedback_output.strip()
            else execution_output
            if isinstance(execution_output, str)
            else getattr(output_feedback, "summary", "")
        )
        requires_user_input = bool(
            getattr(output_feedback, "requires_user_input", False)
            or getattr(execution_result, "requires_user_input", False)
        )
        request_replan = bool(
            getattr(output_feedback, "request_replan", False)
            or getattr(execution_result, "request_replan", False)
        )
        pending_confirmation = getattr(
            output_feedback,
            "pending_confirmation",
            None,
        )
        if pending_confirmation is None:
            pending_confirmation = getattr(
                execution_result,
                "pending_confirmation",
                None,
            )
        execution_serialized = serialize_execution_result(
            execution_result,
            debug=context.request.debug,
        )
        feedback_serialized = serialize_output_feedback(
            output_feedback,
            debug=context.request.debug,
        )
        replan_reason = self._safe_replan_reason(
            getattr(output_feedback, "replan_reason", None)
            or getattr(execution_result, "replan_reason", None),
        )
        pending_serialized = serialize_pending_confirmation(
            pending_confirmation,
            debug=context.request.debug,
        )

        common = {
            "session_id": self._context_session_id(context),
            "run_id": self._context_run_id(context),
            "output": output if isinstance(output, str) else "",
            "execution_result": execution_serialized,
            "output_feedback": feedback_serialized,
            "requires_user_input": requires_user_input,
            "pending_confirmation": pending_serialized,
            "request_replan": request_replan,
            "replan_reason": replan_reason,
        }

        if terminal_error is not None:
            common["requires_user_input"] = False
            common["pending_confirmation"] = None
            memory_result = self._fail_memory_turn(context, terminal_error)
            return self._runtime_result_with_memory(
                context,
                RuntimeResult(
                    success=False,
                    status=terminal_error.status,
                    error_code=terminal_error.code,
                    error_message=terminal_error.message,
                    **common,
                ),
                memory_result,
            )

        if status == "waiting_user":
            self._register_pending_run(
                context,
                execution_result,
                pending_confirmation,
            )
            return self._runtime_result_with_memory_snapshot(
                context,
                RuntimeResult(
                    success=False,
                    status="waiting_user",
                    error_code=RuntimeErrorCode.WAITING_USER.value,
                    error_message=self._waiting_message(
                        execution_result,
                        output_feedback,
                        output,
                    ),
                    **common,
                ),
            )

        if status == "completed":
            memory_result = self._complete_memory_turn(context, output)
            return self._runtime_result_with_memory(
                context,
                RuntimeResult(
                    success=bool(getattr(execution_result, "success", False)),
                    status="completed",
                    **common,
                ),
                memory_result,
            )

        runtime_error = self._runtime_error_from_execution(
            execution_result,
            status=status,
            output=output,
        )
        memory_result = self._fail_memory_turn(context, runtime_error)
        return self._runtime_result_with_memory(
            context,
            RuntimeResult(
                success=False,
                status=runtime_error.status,
                error_code=runtime_error.code,
                error_message=runtime_error.message,
                **common,
            ),
            memory_result,
        )

    def _runtime_result_from_error(
        self,
        context: _RuntimeRequestContext,
        error: Any,
    ) -> RuntimeResult:
        runtime_error = map_exception(
            error,
            default_code=RuntimeErrorCode.AGENT_EXECUTION_FAILED,
            metadata={"stage": "agent_execution"},
        )
        memory_result = self._fail_memory_turn(context, runtime_error)
        return self._runtime_result_with_memory(
            context,
            runtime_result_from_exception(
                runtime_error,
                session_id=self._context_session_id(context),
                run_id=self._context_run_id(context),
            ),
            memory_result,
        )

    def _complete_memory_turn(
        self,
        context: _RuntimeRequestContext,
        output: str,
    ) -> Any | None:
        turn = context.memory_turn
        if turn is None:
            return None
        complete_turn = getattr(self.memory_adapter, "complete_turn", None)
        if not callable(complete_turn):
            self._mark_finalization_persistence_failure(
                turn,
                "Memory adapter does not provide complete_turn.",
            )
            return None
        try:
            return complete_turn(turn, output, include_timeline=True)
        except Exception as exc:
            self._mark_finalization_persistence_failure(turn, exc)
            return None

    def _fail_memory_turn(
        self,
        context: _RuntimeRequestContext,
        error: Any,
    ) -> Any | None:
        turn = context.memory_turn
        if turn is None:
            return None
        fail_turn = getattr(self.memory_adapter, "fail_turn", None)
        if not callable(fail_turn):
            self._mark_finalization_persistence_failure(
                turn,
                "Memory adapter does not provide fail_turn.",
            )
            return None
        try:
            runtime_error = map_exception(
                error,
                default_code=RuntimeErrorCode.AGENT_EXECUTION_FAILED,
            )
            return fail_turn(
                turn,
                {
                    "status": runtime_error.status,
                    "error_code": runtime_error.code,
                    "error_message": runtime_error.message,
                },
                include_timeline=True,
            )
        except Exception as exc:
            # Preserve the original Agent/Runtime error when finalization
            # itself fails; persistence is reported as a separate warning.
            self._mark_finalization_persistence_failure(turn, exc)
            return None

    def _runtime_result_with_memory(
        self,
        context: _RuntimeRequestContext,
        result: RuntimeResult,
        memory_result: Any | None,
    ) -> RuntimeResult:
        if memory_result is not None:
            result.memory_result = serialize_memory_result(
                memory_result,
                debug=context.request.debug,
            )
            result.timeline = self._serialize_timeline(
                getattr(memory_result, "timeline", None)
                if not isinstance(memory_result, Mapping)
                else memory_result.get("timeline"),
                debug=context.request.debug,
            )
            result.persistence_available = self._persistence_available(
                context,
                memory_result,
            )
            result.persistence_warning = self._persistence_warning(
                context,
                memory_result,
            )
        else:
            self._runtime_result_with_memory_snapshot(context, result)
        return result

    def _runtime_result_with_memory_snapshot(
        self,
        context: _RuntimeRequestContext,
        result: RuntimeResult,
    ) -> RuntimeResult:
        turn = context.memory_turn
        result.persistence_available = bool(
            getattr(turn, "persistence_available", True)
        )
        warning = getattr(turn, "persistence_warning", None)
        result.persistence_warning = (
            warning if isinstance(warning, str) and warning.strip() else None
        )
        result.timeline = self._timeline_for_context(
            context,
            debug=context.request.debug,
        )
        return result

    def _persistence_available(self, context: _RuntimeRequestContext, result: Any) -> bool:
        value = self._event_field(result, "persistence_available", None)
        if isinstance(value, bool):
            return value
        return bool(getattr(context.memory_turn, "persistence_available", True))

    def _persistence_warning(self, context: _RuntimeRequestContext, result: Any) -> str | None:
        value = self._event_field(result, "persistence_warning", None)
        if isinstance(value, str) and value.strip():
            return value
        value = getattr(context.memory_turn, "persistence_warning", None)
        return value if isinstance(value, str) and value.strip() else None

    def _timeline_for_context(
        self,
        context: _RuntimeRequestContext,
        *,
        debug: bool,
    ) -> list[dict[str, Any]]:
        session_id = self._context_session_id(context)
        get_timeline = getattr(self.memory_adapter, "get_timeline", None)
        if not isinstance(session_id, str) or not callable(get_timeline):
            return []
        try:
            return self._serialize_timeline(get_timeline(session_id), debug=debug)
        except Exception:
            return []

    def _serialize_timeline(self, value: Any, *, debug: bool) -> list[dict[str, Any]]:
        serialized = safe_serialize(
            value or [],
            debug=debug,
            max_depth=8,
            max_items=200,
            max_text_chars=4000,
        )
        if not isinstance(serialized, list):
            return []
        return [item for item in serialized if isinstance(item, dict)]

    def _runtime_error_from_execution(
        self,
        execution_result: Any,
        *,
        status: str,
        output: str,
    ) -> RuntimeException:
        code_by_status = {
            "blocked": RuntimeErrorCode.BLOCKED_BY_POLICY,
            "waiting_user": RuntimeErrorCode.WAITING_USER,
            "request_replan": RuntimeErrorCode.REQUEST_REPLAN,
            "cancelled": RuntimeErrorCode.CANCELLED,
            "interrupted": RuntimeErrorCode.INTERRUPTED,
        }
        message = sanitize_error_message(
            output
            or getattr(execution_result, "summary", None)
            or getattr(execution_result, "error_code", None)
            or RuntimeErrorCode.AGENT_EXECUTION_FAILED.value,
        )
        if status in code_by_status:
            return RuntimeException(code_by_status[status], message, status=status)
        return map_exception(
            execution_result,
            default_code=RuntimeErrorCode.AGENT_EXECUTION_FAILED,
            metadata={"stage": "agent_execution"},
        )

    def _waiting_message(
        self,
        execution_result: Any,
        output_feedback: Any,
        output: str,
    ) -> str:
        return sanitize_error_message(
            output
            or getattr(output_feedback, "user_input_request", None)
            or getattr(execution_result, "user_input_request", None)
            or "User input is required before the run can continue.",
        )

    def _safe_replan_reason(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = sanitize_error_message(value)
        return normalized if normalized else None

    def _register_pending_run(
        self,
        context: _RuntimeRequestContext,
        execution_result: Any,
        pending_confirmation: Any,
    ) -> None:
        register = getattr(self.pending_run_registry, "register", None)
        if not callable(register):
            return
        session_id = self._context_session_id(context)
        run_id = self._context_run_id(context)
        if not isinstance(session_id, str) or not isinstance(run_id, str):
            return
        register(
            session_id,
            run_id,
            self._pending_executor_context(context, execution_result),
            pending_confirmation,
            metadata={"status": "waiting_user"},
        )

    def _pending_executor_context(
        self,
        context: _RuntimeRequestContext,
        execution_result: Any,
    ) -> Any:
        actual_context = None
        for owner in (
            execution_result,
            self.react_agent,
            getattr(self.react_agent, "executor", None),
        ):
            if owner is None:
                continue
            for field_name in (
                "executor_context",
                "pending_executor_context",
                "last_execution_context",
            ):
                candidate = getattr(owner, field_name, None)
                if candidate is not None:
                    actual_context = candidate
                    break
            if actual_context is not None:
                break
        return _RuntimePendingContext(
            executor_context=actual_context,
            runtime_context=context,
        )

    def _pending_confirmation_mismatch(
        self,
        pending_record: Any,
        request: ResumeRequest,
    ) -> str | None:
        pending = getattr(pending_record, "pending_confirmation", None)
        if not isinstance(pending, Mapping):
            return None
        for field_name, label in (
            ("confirmation_id", "confirmation_id"),
            ("preview_hash", "preview_hash"),
        ):
            expected = pending.get(field_name)
            supplied = getattr(request, field_name, None)
            if (
                expected is not None
                and supplied is not None
                and str(expected) != str(supplied)
            ):
                return f"The supplied {label} does not match the pending confirmation."
        return None

    def _mark_finalization_persistence_failure(self, turn: Any, error: Any) -> None:
        try:
            warning = (
                "Memory persistence unavailable while finalizing the Runtime turn "
                f"({sanitize_error_message(error)})."
            )
            setattr(turn, "persistence_available", False)
            if not getattr(turn, "persistence_warning", None):
                setattr(turn, "persistence_warning", warning)
        except Exception:
            return

    def _runtime_status_from_execution(self, execution_result: Any) -> str:
        status = str(getattr(execution_result, "status", "") or "").strip().lower()
        if status == "partial_failed":
            return "failed"
        if status in {
            "completed",
            "failed",
            "blocked",
            "waiting_user",
            "request_replan",
            "cancelled",
            "interrupted",
        }:
            return status
        return "completed" if bool(getattr(execution_result, "success", False)) else "failed"

    def _context_session_id(self, context: _RuntimeRequestContext) -> str | None:
        turn = context.memory_turn
        return getattr(turn, "session_id", context.request.session_id)

    def _context_run_id(self, context: _RuntimeRequestContext) -> str | None:
        turn = context.memory_turn
        return getattr(turn, "run_id", None)

    def _event_is_visible(self, event: Any) -> bool:
        if isinstance(event, Mapping):
            return bool(event.get("visible_to_user", True))
        return bool(getattr(event, "visible_to_user", True))

    def _validate_runtime_request(self, request: RuntimeRequest) -> None:
        if not isinstance(request, RuntimeRequest):
            raise self._validation_error("request must be a RuntimeRequest")

        input_value = request.input
        if not isinstance(input_value, str) or not input_value.strip():
            raise self._validation_error("input must be a non-empty string")
        if len(input_value) > MAX_RUNTIME_INPUT_CHARS:
            raise self._validation_error(
                f"input exceeds the {MAX_RUNTIME_INPUT_CHARS}-character Runtime limit"
            )

        if request.session_id is not None:
            self._validate_session_id(request.session_id)
        if not isinstance(request.stream, bool):
            raise self._validation_error("stream must be a boolean")
        if not isinstance(request.debug, bool):
            raise self._validation_error("debug must be a boolean")
        self._validate_metadata(request.metadata)
        self._validate_optional_text(request.model_profile, "model_profile")
        self._validate_optional_text(request.agent_version, "agent_version")

    def _validate_resume_request(self, request: ResumeRequest) -> None:
        if not isinstance(request, ResumeRequest):
            raise self._validation_error("request must be a ResumeRequest")
        self._validate_session_id(request.session_id)
        self._validate_non_empty_text(request.run_id, "run_id")
        if not isinstance(request.approved, bool):
            raise self._validation_error("approved must be a boolean")
        self._validate_metadata(request.metadata)

    def _validate_cancel_request(self, request: CancelRequest) -> None:
        if not isinstance(request, CancelRequest):
            raise self._validation_error("request must be a CancelRequest")
        self._validate_session_id(request.session_id)
        self._validate_non_empty_text(request.run_id, "run_id")
        self._validate_metadata(request.metadata)

    def _validate_session_id(self, session_id: str) -> str:
        try:
            return validate_session_id(session_id)
        except (TypeError, ValueError) as exc:
            raise self._validation_error("session_id is invalid") from exc

    def _validate_metadata(self, metadata: Any) -> None:
        if not isinstance(metadata, dict):
            raise self._validation_error("metadata must be a dict")
        if len(metadata) > MAX_RUNTIME_METADATA_ITEMS:
            raise self._validation_error(
                f"metadata exceeds the {MAX_RUNTIME_METADATA_ITEMS}-item Runtime limit"
            )
        if self._contains_sensitive_metadata_key(metadata):
            raise self._validation_error("metadata contains a restricted field")

        serialized = safe_serialize(
            metadata,
            max_depth=8,
            max_items=MAX_RUNTIME_METADATA_ITEMS + 1,
            max_text_chars=MAX_RUNTIME_METADATA_BYTES + 1,
        )
        try:
            metadata_size = len(
                json.dumps(
                    serialized,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise self._validation_error("metadata cannot be safely serialized") from exc
        if metadata_size > MAX_RUNTIME_METADATA_BYTES:
            raise self._validation_error(
                f"metadata exceeds the {MAX_RUNTIME_METADATA_BYTES}-byte Runtime limit"
            )

    def _contains_sensitive_metadata_key(self, value: Mapping[str, Any]) -> bool:
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in SENSITIVE_FIELD_NAMES:
                return True
            if isinstance(item, Mapping) and self._contains_sensitive_metadata_key(item):
                return True
        return False

    def _validate_optional_text(self, value: Any, field_name: str) -> None:
        if value is not None:
            self._validate_non_empty_text(value, field_name)

    def _validate_non_empty_text(self, value: Any, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise self._validation_error(f"{field_name} must be a non-empty string")

    def _validation_error(self, message: str) -> RuntimeException:
        return RuntimeException(RuntimeErrorCode.VALIDATION_ERROR, message)

    def _execution_not_available_result(
        self,
        context: _RuntimeRequestContext,
    ) -> RuntimeResult:
        turn = context.memory_turn
        result = self._operation_not_available_result(
            session_id=getattr(turn, "session_id", context.request.session_id),
            run_id=getattr(turn, "run_id", None),
        )
        if turn is None:
            return result
        result.persistence_available = bool(
            getattr(turn, "persistence_available", True)
        )
        warning = getattr(turn, "persistence_warning", None)
        result.persistence_warning = (
            warning if isinstance(warning, str) and warning.strip() else None
        )
        return result

    def _operation_not_available_result(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> RuntimeResult:
        return runtime_result_from_exception(
            RuntimeException(
                RuntimeErrorCode.INTERNAL_ERROR,
                "Runtime operation is not available until its scheduled integration step.",
            ),
            session_id=session_id,
            run_id=run_id,
        )

    def _operation_not_available(self) -> Any:
        raise RuntimeException(
            RuntimeErrorCode.INTERNAL_ERROR,
            "Runtime operation is not available until its scheduled integration step.",
        )

    def _recover_interrupted_runs(self) -> int:
        recover = getattr(self.session_manager, "recover_interrupted_runs", None)
        if not callable(recover):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "SessionManager does not provide startup recovery.",
                metadata={"dependency": "session_manager"},
            )
        try:
            count = recover()
        except RuntimeException as exc:
            if exc.code == RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value:
                raise
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime startup recovery failed.",
                metadata={
                    "dependency": "session_manager",
                    "stage": "startup_recovery",
                },
                cause=exc,
            ) from exc
        except Exception as exc:
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime startup recovery failed.",
                metadata={
                    "dependency": "session_manager",
                    "stage": "startup_recovery",
                },
                cause=exc,
            ) from exc
        if isinstance(count, bool):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime startup recovery returned an invalid count.",
                metadata={"dependency": "session_manager"},
            )
        try:
            return max(int(count), 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime startup recovery returned an invalid count.",
                metadata={"dependency": "session_manager"},
                cause=exc,
            ) from exc

    def _close_candidates(self) -> Iterable[Any]:
        # The order keeps external tool connections ahead of their owners,
        # then releases Agent/Model resources. Most current V1 dependencies
        # are synchronous and expose no close method, which is intentional.
        return (
            getattr(self.tool_manager, "mcp_manager", None),
            getattr(self.tool_manager, "mcp_gateway", None),
            self.react_executor,
            self.react_agent,
            self.tool_manager,
            self.memory_adapter,
            self.context_builder,
            self.session_manager,
            self.model_manager,
            self.pending_run_registry,
            self.health_checker,
        )

    def _close_dependency(self, dependency: Any) -> None:
        for method_name in ("close", "shutdown", "stop", "dispose"):
            method = getattr(dependency, method_name, None)
            if not callable(method):
                continue
            try:
                method()
            except Exception as exc:
                self.close_errors.append(
                    RuntimeException(
                        RuntimeErrorCode.INTERNAL_ERROR,
                        "Runtime dependency close failed.",
                        metadata={"dependency": type(dependency).__name__},
                        cause=exc,
                    )
                )
            return


__all__ = [
    "MAX_RUNTIME_INPUT_CHARS",
    "MAX_RUNTIME_METADATA_BYTES",
    "MAX_RUNTIME_METADATA_ITEMS",
    "Runtime",
]
