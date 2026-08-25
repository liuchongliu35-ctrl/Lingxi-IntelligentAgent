from __future__ import annotations

import os
from inspect import Parameter, signature
from typing import Any, Callable, Generator

from src.agent.executor import Executor
from src.agent.output_feedback import OutputFeedback, OutputFeedbackProcessor
from src.agent.planner import Planner
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_config import ReActExecutorConfig
from src.agent.react_executor_protocol import (
    ExecutionEvent,
    ExecutionResult as ReactExecutionResult,
    new_id,
)
from src.tools.registry import ToolRegistry


EXECUTOR_TYPE_ENV = "EXECUTOR_TYPE"
DEFAULT_EXECUTOR_TYPE = "react"
REACT_EXECUTOR_TYPES = {"react", "react_executor", "reactexecutor"}
LEGACY_EXECUTOR_TYPES = {"legacy", "old", "executor", "sequential"}


class ReactAgent:
    """Orchestrate analysis, planning, execution, and memory updates."""

    def __init__(
        self,
        model_manager: Any,
        short_term_memory: Any,
        long_term_memory: Any,
        tool_manager: Any,
        rag_system: Any,
        complexity_analyzer: Any,
        planner: Planner | None = None,
        executor: Any | None = None,
        executor_type: str | None = None,
        react_executor_config: ReActExecutorConfig | None = None,
        tool_registry: ToolRegistry | None = None,
        manage_memory: bool = True,
    ):
        self.model_manager = model_manager
        self.short_term_memory = short_term_memory
        self.long_term_memory = long_term_memory
        self.tool_manager = tool_manager
        self.rag_system = rag_system
        self.complexity_analyzer = complexity_analyzer
        self.planner = planner or Planner(model_manager=model_manager)
        self.executor_type = self._resolve_executor_type(executor_type)
        self.manage_memory = manage_memory
        self.executor = executor or self._create_executor(
            executor_type=self.executor_type,
            model_manager=model_manager,
            tool_manager=tool_manager,
            react_executor_config=react_executor_config,
            tool_registry=tool_registry,
        )
        self.output_feedback_processor = OutputFeedbackProcessor()

    def run(
        self,
        user_input: str,
        *,
        history: str | None = None,
        context_text: str | None = None,
        event_callback: Callable[[ExecutionEvent], None] | None = None,
        event_callback_visible_only: bool = True,
        manage_memory: bool | None = None,
    ) -> str:
        return self.run_with_result(
            user_input,
            history=history,
            context_text=context_text,
            event_callback=event_callback,
            event_callback_visible_only=event_callback_visible_only,
            manage_memory=manage_memory,
        ).output

    def run_with_result(
        self,
        user_input: str,
        *,
        history: str | None = None,
        context_text: str | None = None,
        event_callback: Callable[[ExecutionEvent], None] | None = None,
        event_callback_visible_only: bool = True,
        manage_memory: bool | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> ReactExecutionResult:
        """Execute a request and preserve the executor's structured result.

        The legacy string-returning API delegates here so memory updates and
        Analyzer/Planner/Executor ordering remain identical for both callers.
        Runtime-owned Memory can pass context_text and manage_memory=False to
        avoid duplicate user/assistant message writes.
        """
        execution, plan = self._execute_request(
            user_input,
            history=history,
            context_text=context_text,
            event_callback=event_callback,
            event_callback_visible_only=event_callback_visible_only,
            manage_memory=manage_memory,
            session_id=session_id,
            run_id=run_id,
        )
        result = self._coerce_execution_result(execution, plan=plan)
        if self._should_manage_memory(manage_memory):
            self._remember_assistant_response(result.output)
        return result

    def run_feedback(
        self,
        user_input: str,
        *,
        history: str | None = None,
        context_text: str | None = None,
        event_callback: Callable[[ExecutionEvent], None] | None = None,
        event_callback_visible_only: bool = True,
        manage_memory: bool | None = None,
    ) -> OutputFeedback:
        result = self.run_with_result(
            user_input,
            history=history,
            context_text=context_text,
            event_callback=event_callback,
            event_callback_visible_only=event_callback_visible_only,
            manage_memory=manage_memory,
        )
        return self.output_feedback_processor.build(result)

    def run_stream(
        self,
        user_input: str,
        *,
        include_internal: bool = False,
        history: str | None = None,
        context_text: str | None = None,
        event_callback: Callable[[ExecutionEvent], None] | None = None,
        event_callback_visible_only: bool = True,
        manage_memory: bool | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> Generator[ExecutionEvent, None, ReactExecutionResult]:
        """Stream user-visible execution events and return the final result.

        The final result is delivered as the generator's return value
        (`StopIteration.value`) after the event iterator is exhausted. Internal
        executor logs are never written to short-term memory.
        """
        execution_stream = self._build_execution_stream(
            user_input,
            include_internal=include_internal,
            history=history,
            context_text=context_text,
            event_callback=event_callback,
            event_callback_visible_only=event_callback_visible_only,
            manage_memory=manage_memory,
            session_id=session_id,
            run_id=run_id,
        )
        result = yield from execution_stream
        result = self._coerce_execution_result(result)
        if self._should_manage_memory(manage_memory):
            self._remember_assistant_response(result.output)
        return result

    def _execute_request(
        self,
        user_input: str,
        *,
        history: str | None,
        context_text: str | None,
        event_callback: Callable[[ExecutionEvent], None] | None,
        event_callback_visible_only: bool,
        manage_memory: bool | None,
        session_id: str | None,
        run_id: str | None,
    ) -> tuple[Any, Any]:
        if self._should_manage_memory(manage_memory):
            self.short_term_memory.add_message("user", user_input)

        task = self.complexity_analyzer.analyze(user_input)
        plan = self.planner.create_plan(user_input, task)
        resolved_history = self._resolve_history(
            history=history,
            context_text=context_text,
        )
        execution, used_callback = self._execute_with_optional_callback(
            plan,
            task,
            user_input,
            history=resolved_history,
            session_id=session_id,
            run_id=run_id,
            event_callback=event_callback,
            event_callback_visible_only=event_callback_visible_only,
        )
        self._persist_execution_result_events(
            execution,
            session_id=session_id,
            run_id=run_id,
            event_callback=event_callback,
            event_callback_visible_only=event_callback_visible_only,
            skip_if_callback_used=used_callback,
        )
        return execution, plan

    def _build_execution_stream(
        self,
        user_input: str,
        *,
        include_internal: bool,
        history: str | None,
        context_text: str | None,
        event_callback: Callable[[ExecutionEvent], None] | None,
        event_callback_visible_only: bool,
        manage_memory: bool | None,
        session_id: str | None,
        run_id: str | None,
    ) -> Generator[ExecutionEvent, None, Any]:
        if self._should_manage_memory(manage_memory):
            self.short_term_memory.add_message("user", user_input)

        task = self.complexity_analyzer.analyze(user_input)
        plan = self.planner.create_plan(user_input, task)
        resolved_history = self._resolve_history(
            history=history,
            context_text=context_text,
        )

        execution, used_callback = self._execute_with_optional_callback(
            plan,
            task,
            user_input,
            history=resolved_history,
            session_id=session_id,
            run_id=run_id,
            event_callback=event_callback,
            event_callback_visible_only=event_callback_visible_only,
        )
        self._persist_execution_result_events(
            execution,
            session_id=session_id,
            run_id=run_id,
            event_callback=event_callback,
            event_callback_visible_only=event_callback_visible_only,
            skip_if_callback_used=used_callback,
        )
        result = self._coerce_execution_result(execution, plan=plan)
        for event in result.events:
            if include_internal or self._event_is_visible(event):
                yield event
        return result

    def _resolve_history(
        self,
        *,
        history: str | None,
        context_text: str | None,
    ) -> str:
        if context_text is not None:
            return context_text
        if history is not None:
            return history
        return str(self.short_term_memory.get_history_text())

    def _should_manage_memory(self, manage_memory: bool | None) -> bool:
        return self.manage_memory if manage_memory is None else manage_memory

    def _execute_with_optional_callback(
        self,
        plan: Any,
        task: Any,
        user_input: str,
        *,
        history: str,
        session_id: str | None,
        run_id: str | None,
        event_callback: Callable[[ExecutionEvent], None] | None,
        event_callback_visible_only: bool,
    ) -> tuple[Any, bool]:
        execute = self.executor.execute
        kwargs: dict[str, Any] = {"history": history}
        supported = self._supported_keyword_parameters(execute)
        accepts_arbitrary_keywords = any(
            parameter.kind == Parameter.VAR_KEYWORD
            for parameter in supported.values()
        )
        dispatch_event = self._build_execution_event_dispatcher(
            session_id=session_id,
            run_id=run_id,
            external_callback=event_callback,
            external_visible_only=event_callback_visible_only,
        )
        used_event_callback = dispatch_event is not None and (
            accepts_arbitrary_keywords or "event_callback" in supported
        )
        if used_event_callback:
            kwargs["event_callback"] = dispatch_event
            if (
                accepts_arbitrary_keywords
                or "event_callback_visible_only" in supported
            ):
                kwargs["event_callback_visible_only"] = event_callback_visible_only
        execution = execute(plan, task, user_input, **kwargs)
        return execution, used_event_callback

    def _build_execution_event_dispatcher(
        self,
        *,
        session_id: str | None,
        run_id: str | None,
        external_callback: Callable[[ExecutionEvent], None] | None,
        external_visible_only: bool,
    ) -> Callable[[ExecutionEvent], None] | None:
        memory_callback = self._build_memory_event_callback(session_id, run_id)
        if memory_callback is None and external_callback is None:
            return None

        def dispatch(event: ExecutionEvent) -> None:
            if memory_callback is not None:
                memory_callback(event)
            if external_callback is None:
                return
            if external_visible_only and not self._event_is_visible(event):
                return
            external_callback(event)

        return dispatch

    def _build_memory_event_callback(
        self,
        session_id: str | None,
        run_id: str | None,
    ) -> Callable[[ExecutionEvent], None] | None:
        resolved_session_id = session_id or getattr(self.short_term_memory, "session_id", None)
        session_manager = getattr(self.short_term_memory, "session_manager", None)
        if resolved_session_id is None or run_id is None or session_manager is None:
            return None

        def persist(event: ExecutionEvent) -> None:
            if not self._event_is_visible(event):
                return
            try:
                session_manager.append_execution_event(resolved_session_id, run_id, event)
            except Exception:
                return

        return persist

    def _persist_execution_result_events(
        self,
        execution: Any,
        *,
        session_id: str | None,
        run_id: str | None,
        event_callback: Callable[[ExecutionEvent], None] | None,
        event_callback_visible_only: bool,
        skip_if_callback_used: bool,
    ) -> None:
        if skip_if_callback_used:
            return
        if session_id is None and run_id is None and event_callback is None:
            return
        events = list(getattr(execution, "events", []) or [])
        if not events:
            return
        dispatch = self._build_execution_event_dispatcher(
            session_id=session_id,
            run_id=run_id,
            external_callback=event_callback,
            external_visible_only=event_callback_visible_only,
        )
        if dispatch is None:
            return
        for event in events:
            dispatch(event)

    def _supported_keyword_parameters(self, method: Any) -> dict[str, Parameter]:
        try:
            parameters = signature(method).parameters
        except (TypeError, ValueError):
            return {}
        return {
            name: parameter
            for name, parameter in parameters.items()
            if parameter.kind
            in {
                Parameter.POSITIONAL_OR_KEYWORD,
                Parameter.KEYWORD_ONLY,
                Parameter.VAR_KEYWORD,
            }
        }

    def _notify_event_callback(
        self,
        event: ExecutionEvent,
        *,
        event_callback: Callable[[ExecutionEvent], None] | None,
        event_callback_visible_only: bool,
    ) -> None:
        if event_callback is None:
            return
        if event_callback_visible_only and not self._event_is_visible(event):
            return
        event_callback(event)

    def _coerce_execution_result(
        self,
        execution: Any,
        *,
        plan: Any | None = None,
    ) -> ReactExecutionResult:
        if isinstance(execution, ReactExecutionResult):
            return execution

        success = bool(getattr(execution, "success", False))
        status = getattr(execution, "status", None)
        if status not in {
            "completed",
            "failed",
            "blocked",
            "waiting_user",
            "request_replan",
            "partial_failed",
            "cancelled",
        }:
            status = "completed" if success else "failed"

        output = str(getattr(execution, "output", "") or "")
        return ReactExecutionResult(
            execution_id=str(getattr(execution, "execution_id", "") or new_id("agent_execution")),
            plan_id=str(getattr(execution, "plan_id", "") or getattr(plan, "plan_id", "")),
            source_trace_id=getattr(execution, "source_trace_id", None)
            or getattr(plan, "source_trace_id", None),
            status=status,
            success=success,
            output=output,
            summary=str(getattr(execution, "summary", "") or output),
            task_statuses=dict(getattr(execution, "task_statuses", {}) or {}),
            step_statuses=dict(getattr(execution, "step_statuses", {}) or {}),
            observations=list(getattr(execution, "observations", []) or []),
            events=list(getattr(execution, "events", []) or []),
            failed_step_id=getattr(execution, "failed_step_id", None),
            error_code=getattr(execution, "error_code", None),
            requires_user_input=bool(getattr(execution, "requires_user_input", False)),
            user_input_request=getattr(execution, "user_input_request", None),
            pending_confirmation=getattr(execution, "pending_confirmation", None),
            request_replan=bool(getattr(execution, "request_replan", False)),
            replan_reason=getattr(execution, "replan_reason", None),
        )

    def _event_is_visible(self, event: Any) -> bool:
        if isinstance(event, dict):
            return bool(event.get("visible_to_user", True))
        return bool(getattr(event, "visible_to_user", True))

    def _remember_assistant_response(self, response: str) -> None:
        self.short_term_memory.add_message("assistant", response)

    def _resolve_executor_type(self, executor_type: str | None) -> str:
        requested = executor_type or os.getenv(EXECUTOR_TYPE_ENV, DEFAULT_EXECUTOR_TYPE)
        normalized = str(requested or DEFAULT_EXECUTOR_TYPE).strip().lower()
        if normalized in REACT_EXECUTOR_TYPES:
            return "react"
        if normalized in LEGACY_EXECUTOR_TYPES:
            return "legacy"
        return DEFAULT_EXECUTOR_TYPE

    def _create_executor(
        self,
        *,
        executor_type: str,
        model_manager: Any,
        tool_manager: Any,
        react_executor_config: ReActExecutorConfig | None,
        tool_registry: ToolRegistry | None,
    ) -> Any:
        if executor_type == "legacy":
            return Executor(model_manager=model_manager, tool_manager=tool_manager)
        return ReActExecutor(
            model_manager=model_manager,
            tool_manager=tool_manager,
            tool_registry=tool_registry,
            config=react_executor_config,
        )
