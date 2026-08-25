from __future__ import annotations

import inspect
import time
from copy import copy
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .base import ToolResult, new_tool_call_id
from .errors import (
    ToolErrorCode,
    error_type_for_code,
    is_retryable_code,
)
from .output_control import OutputController, PreviewData
from .policy import ToolPolicy, ToolPolicyDecision
from .protocol import ToolCallRequest
from .registry import ToolRegistry, ToolSpec
from .tool_logger import NullToolLogger, ToolLogger


HandlerResolver = Callable[[str], Any | None]


class ToolRuntime:
    """The single formal execution pipeline for ToolCallRequest."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: ToolPolicy | None = None,
        handlers: Mapping[str, Any] | None = None,
        handler_resolver: HandlerResolver | None = None,
        logger: ToolLogger | None = None,
        output_controller: OutputController | None = None,
        max_timeout_seconds: int = 300,
        enabled: bool = True,
    ) -> None:
        self.registry = registry
        self.policy = policy or ToolPolicy()
        self.handlers = handlers if handlers is not None else {}
        self.handler_resolver = handler_resolver
        self.logger = logger or NullToolLogger()
        self.output_controller = output_controller or OutputController()
        self.max_timeout_seconds = max(int(max_timeout_seconds), 1)
        self.enabled = bool(enabled)

    def execute(self, request: ToolCallRequest) -> ToolResult:
        call_id = new_tool_call_id()
        started_at = _utc_now()
        started_monotonic = time.monotonic()
        decision: ToolPolicyDecision | None = None
        preview: PreviewData | None = None
        canonical_name: str | None = None
        spec: ToolSpec | None = None

        try:
            if not isinstance(request, ToolCallRequest):
                return self._finish(
                    request=None,
                    result=ToolResult.fail(
                        "request must be ToolCallRequest",
                        code=ToolErrorCode.INVALID_ARGS.value,
                    ),
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                )

            if not self.enabled:
                return self._finish(
                    request=request,
                    result=ToolResult.fail(
                        "Tool runtime is disabled by configuration.",
                        code=ToolErrorCode.TOOL_DISABLED.value,
                    ),
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                )

            canonical_name = self.registry.resolve_name(request.tool_name)
            if canonical_name is None:
                return self._finish(
                    request=request,
                    result=ToolResult.fail(
                        f"Tool not found: {request.tool_name}",
                        code=ToolErrorCode.TOOL_NOT_FOUND.value,
                    ),
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                )

            spec = self.registry.get(canonical_name)
            if spec is None:
                return self._finish(
                    request=request,
                    result=ToolResult.fail(
                        f"Tool not found: {request.tool_name}",
                        code=ToolErrorCode.TOOL_NOT_FOUND.value,
                    ),
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                )

            validation = self.registry.validate_tool_args(request.tool_name, request.args)
            if not validation.success:
                return self._finish(
                    request=request,
                    result=ToolResult.fail(
                        "; ".join(validation.errors) or f"Invalid args for {canonical_name}",
                        code=validation.code,
                        metadata={"validation": validation.to_dict()},
                    ),
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    spec=spec,
                    canonical_name=canonical_name,
                )

            decision = self.policy.decide(spec, request)
            if not decision.allowed:
                return self._finish(
                    request=request,
                    result=ToolResult.fail(
                        decision.reason,
                        code=decision.code,
                        metadata={"policy": decision.to_dict()},
                    ),
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    spec=spec,
                    canonical_name=canonical_name,
                    decision=decision,
                )

            preview = self.output_controller.build_preview(spec, request, decision)
            if request.options.has_confirmation_ticket or request.options.preview_hash:
                decision = self.policy.decide(
                    spec,
                    request,
                    expected_preview_hash=preview.preview_hash,
                )
                if not decision.allowed:
                    return self._finish(
                        request=request,
                        result=ToolResult.fail(
                            decision.reason,
                            code=decision.code,
                            metadata={"policy": decision.to_dict()},
                        ),
                        call_id=call_id,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        spec=spec,
                        canonical_name=canonical_name,
                        decision=decision,
                    )

            preview_error = (
                preview.payload.get("preview_error")
                if isinstance(preview.payload, dict)
                else None
            )
            if isinstance(preview_error, dict):
                return self._finish(
                    request=request,
                    result=ToolResult.fail(
                        str(preview_error.get("message") or "Tool preview failed."),
                        code=str(preview_error.get("code") or ToolErrorCode.INVALID_ARGS.value),
                        data=preview_error.get("data"),
                        metadata={"policy": decision.to_dict(), "preview": preview.payload},
                    ),
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    spec=spec,
                    canonical_name=canonical_name,
                    decision=decision,
                )

            if request.options.dry_run:
                result = self._dry_run_result(spec, request, decision, preview)
                return self._finish(
                    request=request,
                    result=result,
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    spec=spec,
                    canonical_name=canonical_name,
                    decision=decision,
                )

            handler = self._resolve_handler(canonical_name)
            if handler is None:
                return self._finish(
                    request=request,
                    result=ToolResult.fail(
                        f"Tool not implemented: {canonical_name}",
                        code=ToolErrorCode.TOOL_NOT_IMPLEMENTED.value,
                    ),
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    spec=spec,
                    canonical_name=canonical_name,
                    decision=decision,
                )

            if not callable(handler) and not hasattr(handler, "run"):
                return self._finish(
                    request=request,
                    result=ToolResult.fail(
                        f"Tool has no run method: {canonical_name}",
                        code=ToolErrorCode.TOOL_NOT_IMPLEMENTED.value,
                    ),
                    call_id=call_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    spec=spec,
                    canonical_name=canonical_name,
                    decision=decision,
                )

            timeout_seconds = self._effective_timeout(spec, request)
            raw_result = self._invoke_handler(
                handler,
                request.args,
                timeout_seconds=timeout_seconds,
                request=request,
            )
            result = self._coerce_result(raw_result)
            result.metadata.setdefault("runtime", {})
            result.metadata["runtime"].update(
                {"timeout_seconds": timeout_seconds}
            )
            result = self.output_controller.apply(
                result,
                spec,
                request,
                preview=preview,
                decision=decision,
            )
            return self._finish(
                request=request,
                result=result,
                call_id=call_id,
                started_at=started_at,
                started_monotonic=started_monotonic,
                spec=spec,
                canonical_name=canonical_name,
                decision=decision,
            )
        except TimeoutError as exc:
            return self._finish(
                request=request,
                result=ToolResult.fail(
                    str(exc) or "Tool execution timed out.",
                    code=ToolErrorCode.TIMEOUT.value,
                ),
                call_id=call_id,
                started_at=started_at,
                started_monotonic=started_monotonic,
                spec=spec,
                canonical_name=canonical_name,
                decision=decision,
            )
        except Exception as exc:
            return self._finish(
                request=request,
                result=ToolResult.fail(
                    f"Tool {canonical_name or getattr(request, 'tool_name', 'unknown')} failed: {exc}",
                    code=ToolErrorCode.INTERNAL_ERROR.value,
                ),
                call_id=call_id,
                started_at=started_at,
                started_monotonic=started_monotonic,
                spec=spec,
                canonical_name=canonical_name,
                decision=decision,
            )

    def _resolve_handler(self, canonical_name: str) -> Any | None:
        if self.handler_resolver is not None:
            return self.handler_resolver(canonical_name)
        return self.handlers.get(canonical_name)

    def _effective_timeout(
        self,
        spec: ToolSpec,
        request: ToolCallRequest,
    ) -> int:
        requested = request.options.timeout_seconds
        requested_arg = _positive_int_or_none(request.args.get("timeout_seconds"))
        values = [spec.timeout_seconds, self.max_timeout_seconds]
        if requested is not None:
            values.append(requested)
        if requested_arg is not None:
            values.append(requested_arg)
        return max(min(values), 1)

    def _invoke_handler(
        self,
        handler: Any,
        args: dict[str, Any],
        *,
        timeout_seconds: int,
        request: ToolCallRequest,
    ) -> Any:
        callable_handler = handler.run if hasattr(handler, "run") else handler
        call_args = dict(args)
        if _accepts_keyword(
            callable_handler,
            "timeout_seconds",
        ):
            call_args["timeout_seconds"] = timeout_seconds
        if "workspace_root" not in call_args and _accepts_keyword(
            callable_handler,
            "workspace_root",
        ):
            call_args["workspace_root"] = request.context.workspace_root
        if "tool_call_context" not in call_args and _accepts_keyword(
            callable_handler,
            "tool_call_context",
        ):
            call_args["tool_call_context"] = request.context
        if "tool_call_options" not in call_args and _accepts_keyword(
            callable_handler,
            "tool_call_options",
        ):
            call_args["tool_call_options"] = request.options
        return callable_handler(**call_args)

    def _dry_run_result(
        self,
        spec: ToolSpec,
        request: ToolCallRequest,
        decision: ToolPolicyDecision,
        preview: PreviewData,
    ) -> ToolResult:
        if (
            not spec.supports_dry_run
            and spec.risk_level not in {"high", "blocked"}
            and spec.workspace_scope == "none"
        ):
            return ToolResult.fail(
                f"Dry run is not supported by tool: {spec.name}",
                code=ToolErrorCode.DRY_RUN_NOT_SUPPORTED.value,
                metadata={"policy": decision.to_dict()},
            )
        result = ToolResult.ok(
            data={
                "tool_name": spec.name,
                "preview": preview.payload,
                "affected_resources": list(decision.affected_resources),
            },
            message=f"Dry-run preview prepared for {spec.name}.",
            code=ToolErrorCode.DRY_RUN_PREVIEW.value,
            metadata={
                "policy": decision.to_dict(),
                "requires_confirmation": decision.requires_confirmation,
                "preview": preview.payload,
            },
        )
        return self.output_controller.apply(
            result,
            spec,
            request,
            preview=preview,
            decision=decision,
        )

    def _coerce_result(self, value: Any) -> ToolResult:
        if isinstance(value, ToolResult):
            result = copy(value)
            result.metadata = dict(value.metadata)
            return result
        return ToolResult.ok(data=value, message=str(value))

    def _finish(
        self,
        *,
        request: ToolCallRequest | None,
        result: ToolResult,
        call_id: str,
        started_at: str,
        started_monotonic: float,
        spec: ToolSpec | None = None,
        canonical_name: str | None = None,
        decision: ToolPolicyDecision | None = None,
    ) -> ToolResult:
        ended_at = _utc_now()
        duration_ms = max(int((time.monotonic() - started_monotonic) * 1000), 0)
        if request is not None:
            context = request.context
            requested_name = request.tool_name
            result.trace_id = context.trace_id
            result.execution_id = context.execution_id
            result.step_id = context.step_id
        else:
            requested_name = canonical_name or ""

        result.call_id = call_id
        result.tool_name = canonical_name or requested_name
        result.tool_category = spec.category if spec else result.tool_category
        result.tool_namespace = spec.namespace if spec else result.tool_namespace
        if spec:
            result.provider = result.provider or spec.metadata.get("provider")
        result.started_at = started_at
        result.ended_at = ended_at
        result.duration_ms = duration_ms
        if result.code is None:
            result.code = (
                ToolErrorCode.OK.value
                if result.success
                else ToolErrorCode.INTERNAL_ERROR.value
            )
        if result.error_type is None:
            result.error_type = error_type_for_code(result.code)
        if not result.retryable:
            result.retryable = is_retryable_code(result.code)
        if decision is not None:
            result.metadata.setdefault("policy", decision.to_dict())
        result.metadata = dict(result.metadata)
        try:
            self.logger.log(request, result, decision)
        except Exception:
            # Logging must not turn a real tool result into a handler failure.
            result.metadata.setdefault("runtime", {})
            result.metadata["runtime"]["logger_error"] = True
        return result


def _accepts_keyword(callable_handler: Callable[..., Any], name: str) -> bool:
    try:
        signature = inspect.signature(callable_handler)
    except (TypeError, ValueError):
        return False
    if name in signature.parameters:
        parameter = signature.parameters[name]
        return parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 1 else None


__all__ = ["ToolRuntime"]
