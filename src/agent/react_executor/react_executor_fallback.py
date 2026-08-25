from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List

from src.agent.react_executor_checker import CheckerResult
from src.agent.react_executor_protocol import ObservationPacket


FALLBACK_SCHEDULED_CODE = "fallback_scheduled"
FALLBACK_TARGET_NOT_FOUND_CODE = "fallback_target_not_found"
FALLBACK_TOOL_NOT_AVAILABLE_CODE = "fallback_tool_not_available"
FALLBACK_TOOL_NOT_ALLOWED_CODE = "fallback_tool_not_allowed"
FALLBACK_MODEL_NOT_ALLOWED_CODE = "fallback_model_not_allowed"
FALLBACK_NOT_ALLOWED_CODE = "fallback_not_allowed"
FALLBACK_UNSUPPORTED_ACTION_CODE = "fallback_unsupported_action"

FALLBACKABLE_ACTION_TYPES = {"call_tool", "call_model", "fallback_to_tool", "fallback_to_model"}
MODEL_FALLBACK_TOOL_ALIASES = {"fallback_to_model", "model", "llm", "call_model"}


@dataclass
class FallbackDecision:
    can_fallback: bool
    fallback_type: str | None
    reason: str
    code: str
    fallback_tool: str | None = None
    source_observation_id: str | None = None
    source_packet_id: str | None = None
    action_type: str | None = None
    action_target: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


class FallbackPolicy:
    """Choose a fallback target without executing it."""

    def build_decision(
        self,
        observation: ObservationPacket,
        checker_result: CheckerResult,
        *,
        step: Any | None = None,
        requested_type: str | None = None,
        requested_tool: str | None = None,
        available_tools: List[str] | set[str] | None = None,
        registry_fallback_tools: List[str] | None = None,
    ) -> FallbackDecision:
        metadata = {
            "checker_result": checker_result.to_dict(),
            "source_code": observation.code,
            "step_fallback_tools": [str(tool) for tool in list(getattr(step, "fallback_tools", []) or []) if tool],
            "registry_fallback_tools": [str(tool) for tool in list(registry_fallback_tools or []) if tool],
            "requested_type": requested_type,
            "requested_tool": requested_tool,
        }
        base = {
            "source_observation_id": observation.observation_id,
            "source_packet_id": observation.packet_id,
            "action_type": observation.action_type,
            "action_target": observation.action_target,
            "metadata": metadata,
        }

        if observation.action_type not in FALLBACKABLE_ACTION_TYPES:
            return self._blocked("Action type is not fallbackable.", FALLBACK_UNSUPPORTED_ACTION_CODE, **base)

        allowed_tools = self._available_tool_set(available_tools)
        fallback_tools = self._candidate_tools(
            requested_tool=requested_tool,
            step=step,
            registry_fallback_tools=registry_fallback_tools,
        )
        wants_model = self._wants_model_fallback(checker_result, step, requested_type, requested_tool, fallback_tools)

        if requested_type != "model" and (requested_type == "tool" or checker_result.checker_status == "fallback_to_tool" or fallback_tools):
            tool = self._first_available_tool(fallback_tools, allowed_tools)
            if tool is not None:
                return FallbackDecision(
                    can_fallback=True,
                    fallback_type="tool",
                    reason=checker_result.reason or "Fallback to tool.",
                    code=FALLBACK_SCHEDULED_CODE,
                    fallback_tool=tool,
                    **base,
                )
            unavailable = [tool for tool in fallback_tools if tool not in MODEL_FALLBACK_TOOL_ALIASES]
            if unavailable and not wants_model:
                return self._blocked(
                    "Fallback tool is not available.",
                    FALLBACK_TOOL_NOT_AVAILABLE_CODE,
                    **base,
                )

        if wants_model:
            return FallbackDecision(
                can_fallback=True,
                fallback_type="model",
                reason=checker_result.reason or "Fallback to model.",
                code=FALLBACK_SCHEDULED_CODE,
                **base,
            )

        if requested_type == "model":
            return self._blocked("Model fallback is not allowed for this step.", FALLBACK_MODEL_NOT_ALLOWED_CODE, **base)
        if requested_type == "tool":
            return self._blocked("Fallback tool is not allowed for this step.", FALLBACK_TOOL_NOT_ALLOWED_CODE, **base)
        return self._blocked("No fallback path is available.", FALLBACK_NOT_ALLOWED_CODE, **base)

    def _candidate_tools(
        self,
        *,
        requested_tool: str | None,
        step: Any | None,
        registry_fallback_tools: List[str] | None,
    ) -> List[str]:
        candidates: List[str] = []
        if requested_tool:
            return [str(requested_tool)]
        candidates.extend(str(tool) for tool in list(getattr(step, "fallback_tools", []) or []) if tool)
        candidates.extend(str(tool) for tool in list(registry_fallback_tools or []) if tool)
        result: List[str] = []
        seen: set[str] = set()
        for tool in candidates:
            if tool and tool not in seen:
                seen.add(tool)
                result.append(tool)
        return result

    def _first_available_tool(self, fallback_tools: List[str], available_tools: set[str] | None) -> str | None:
        for tool in fallback_tools:
            if tool in MODEL_FALLBACK_TOOL_ALIASES:
                continue
            if available_tools is None or tool in available_tools:
                return tool
        return None

    def _wants_model_fallback(
        self,
        checker_result: CheckerResult,
        step: Any | None,
        requested_type: str | None,
        requested_tool: str | None,
        fallback_tools: List[str],
    ) -> bool:
        if requested_type == "model" or checker_result.checker_status == "fallback_to_model":
            return True
        if requested_tool in MODEL_FALLBACK_TOOL_ALIASES:
            return True
        if any(tool in MODEL_FALLBACK_TOOL_ALIASES for tool in fallback_tools):
            return True
        on_failure = str(getattr(step, "on_failure", "") or "").lower()
        return bool(getattr(step, "allow_model_reasoning", False) or on_failure in {"fallback_to_model", "fallback_model", "model", "fallback"})

    def _available_tool_set(self, available_tools: List[str] | set[str] | None) -> set[str] | None:
        if available_tools is None:
            return None
        return {str(tool) for tool in available_tools}

    def _blocked(self, reason: str, code: str, **kwargs: Any) -> FallbackDecision:
        return FallbackDecision(can_fallback=False, fallback_type=None, reason=reason, code=code, **kwargs)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
