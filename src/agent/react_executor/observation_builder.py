from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from src.agent.react_executor_observation import sanitize_sensitive
from src.tools.base import ToolResult
from src.tools.output_control import DEFAULT_MAX_OBSERVATION_CHARS, truncate_text
from src.tools.protocol import ToolCallRequest
from src.tools.registry import ToolSpec


OBSERVATION_MODES = {"minimal", "standard", "full"}
_MODE_FALLBACKS = {
    "full": ("full", "standard", "minimal"),
    "standard": ("standard", "minimal"),
    "minimal": ("minimal",),
}
_MINIMAL_DATA_KEYS = {
    "affected_count",
    "affected_resources",
    "cache_hit",
    "content_hash",
    "content_truncated",
    "cwd",
    "deleted_count",
    "duration_ms",
    "encoding",
    "evidence_level",
    "exit_code",
    "file_path",
    "line_count",
    "matched_count",
    "path",
    "provider",
    "query",
    "read_strategy",
    "result_count",
    "retry",
    "size_bytes",
    "source_quality",
    "stderr_bytes",
    "stderr_summary",
    "stdout_bytes",
    "stdout_summary",
    "timed_out",
}
_PREVIEW_KEYS = {
    "content": "content_preview",
    "stderr": "stderr_preview",
    "stdout": "stdout_preview",
    "text": "text_preview",
}
_DROP_KEYS = {
    "raw_content",
    "raw_observation",
    "raw_output",
    "stack",
    "stack_trace",
    "traceback",
}
_OUTPUT_CONTROL_KEYS = {
    "affected_resources",
    "artifact_ref",
    "data_summary",
    "message_truncated",
    "preview",
    "preview_hash",
    "raw_output_bytes",
    "raw_output_chars",
    "raw_output_hash",
    "raw_ref",
    "requires_confirmation",
}


@dataclass(frozen=True)
class ToolObservationView:
    data: Any
    model_consumable_observation: dict[str, Any]
    observation_mode: str
    requested_observation_mode: str
    included_fields: list[str]
    data_summary: str | None = None
    raw_ref: str | None = None
    artifact_ref: str | None = None


def build_tool_observation_view(
    tool_result: ToolResult,
    *,
    spec: ToolSpec | None = None,
    request: ToolCallRequest | None = None,
    code: str | None = None,
) -> ToolObservationView:
    requested_mode = _requested_mode(tool_result, spec, request)
    max_chars = _max_observation_chars(request)
    for candidate_mode in _MODE_FALLBACKS[requested_mode]:
        data = _data_for_mode(tool_result, candidate_mode, max_chars=max_chars)
        payload = _model_payload(
            tool_result,
            mode=candidate_mode,
            requested_mode=requested_mode,
            data=data,
            spec=spec,
            request=request,
            code=code,
        )
        if _json_chars(payload) <= max_chars or candidate_mode == "minimal":
            bounded_payload = _fit_to_budget(payload, max_chars)
            bounded_data = _fit_to_budget(data, max(max_chars // 2, 100))
            included_fields = _included_fields(bounded_payload)
            data_summary = _output_control(tool_result).get("data_summary")
            return ToolObservationView(
                data=bounded_data,
                model_consumable_observation=bounded_payload,
                observation_mode=candidate_mode,
                requested_observation_mode=requested_mode,
                included_fields=included_fields,
                data_summary=str(data_summary) if data_summary is not None else None,
                raw_ref=_string_or_none(bounded_payload.get("raw_ref")),
                artifact_ref=_string_or_none(bounded_payload.get("artifact_ref")),
            )
    raise AssertionError("unreachable observation mode selection")


def _requested_mode(
    tool_result: ToolResult,
    spec: ToolSpec | None,
    request: ToolCallRequest | None,
) -> str:
    output_mode = _output_control(tool_result).get("observation_mode")
    candidates = [
        getattr(getattr(request, "options", None), "observation_mode", None),
        output_mode,
        getattr(spec, "default_observation_mode", None),
        "standard",
    ]
    for candidate in candidates:
        normalized = str(candidate or "").strip().lower()
        if normalized in OBSERVATION_MODES:
            return normalized
    return "standard"


def _max_observation_chars(request: ToolCallRequest | None) -> int:
    value = getattr(getattr(request, "options", None), "max_observation_chars", None)
    if value is None:
        return DEFAULT_MAX_OBSERVATION_CHARS
    return max(int(value), 100)


def _model_payload(
    tool_result: ToolResult,
    *,
    mode: str,
    requested_mode: str,
    data: Any,
    spec: ToolSpec | None,
    request: ToolCallRequest | None,
    code: str | None,
) -> dict[str, Any]:
    output_control = _output_control(tool_result)
    payload: dict[str, Any] = {
        "success": bool(tool_result.success),
        "tool_name": tool_result.tool_name or getattr(spec, "name", "") or getattr(request, "tool_name", ""),
        "tool_category": tool_result.tool_category or getattr(spec, "category", ""),
        "tool_namespace": tool_result.tool_namespace or getattr(spec, "namespace", ""),
        "code": code if code is not None else tool_result.code,
        "message": tool_result.to_text(),
        "observation_mode": mode,
        "requested_observation_mode": requested_mode,
    }
    if mode != requested_mode:
        payload["mode_downgraded"] = True
        payload["downgrade_reason"] = "max_observation_chars"
    if tool_result.error:
        payload["error"] = tool_result.error
    if tool_result.error_type:
        payload["error_type"] = tool_result.error_type
    if not tool_result.success or tool_result.retryable:
        payload["retryable"] = bool(tool_result.retryable)
    if tool_result.provider:
        payload["provider"] = tool_result.provider
    if data not in ({}, None):
        payload["data"] = data
    raw_truncated = bool(tool_result.raw_output_truncated or output_control.get("raw_output_truncated"))
    if raw_truncated:
        payload["raw_output_truncated"] = True
    for key in _OUTPUT_CONTROL_KEYS:
        value = output_control.get(key)
        if value not in ({}, [], None, ""):
            payload[key] = value
    if mode == "minimal":
        payload.pop("preview", None)
        payload.pop("data_summary", None)
    return _drop_unsafe(payload)


def _data_for_mode(tool_result: ToolResult, mode: str, *, max_chars: int) -> Any:
    value = _clean_value(tool_result.data)
    web_view = _web_search_candidate(value, mode)
    if web_view is not None:
        return web_view
    if mode == "minimal":
        return _minimal_data(value, keep_data=not tool_result.success)
    if mode == "standard":
        return _standard_data(value)
    return _fit_to_budget(value, max_chars)


def _web_search_candidate(value: Any, mode: str) -> Any | None:
    if not isinstance(value, Mapping):
        return None
    metadata = value.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    views = metadata.get("observation_views")
    if not isinstance(views, Mapping):
        return None
    candidate = views.get(f"{mode}_data")
    if candidate is None:
        return None
    return _drop_unsafe(_clean_value(candidate))


def _minimal_data(value: Any, *, keep_data: bool = False) -> Any:
    value = _clean_value(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        result = {
            str(key): item
            for key, item in value.items()
            if str(key) in _MINIMAL_DATA_KEYS
        }
        if keep_data:
            for key, item in value.items():
                normalized = str(key)
                if normalized in result:
                    continue
                if _json_chars(result) + _json_chars({normalized: item}) <= 1200:
                    result[normalized] = item
        return result
    return None


def _standard_data(value: Any) -> Any:
    value = _clean_value(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_standard_data(item) for item in value[:20]]
    if not isinstance(value, Mapping):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text in _DROP_KEYS:
            continue
        preview_key = _PREVIEW_KEYS.get(key_text)
        if preview_key:
            if preview_key in value:
                result[preview_key] = value[preview_key]
            elif isinstance(item, str):
                result[preview_key] = truncate_text(item, 1200)
            continue
        if key_text in {"results"} and isinstance(item, list):
            result[key_text] = [_standard_data(entry) for entry in item[:5]]
            continue
        if isinstance(item, Mapping):
            result[key_text] = _standard_data(item)
        elif isinstance(item, list):
            result[key_text] = [_standard_data(entry) for entry in item[:20]]
        else:
            result[key_text] = item
    return result


def _clean_value(value: Any) -> Any:
    return _drop_unsafe(sanitize_sensitive(value))


def _drop_unsafe(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if normalized in _DROP_KEYS:
                continue
            result[key_text] = _drop_unsafe(item)
        return result
    if isinstance(value, list):
        return [_drop_unsafe(item) for item in value]
    return value


def _fit_to_budget(value: Any, max_chars: int) -> Any:
    value = _clean_value(value)
    if _json_chars(value) <= max_chars:
        return value
    if isinstance(value, str):
        return truncate_text(value, max_chars)
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            candidate = _fit_to_budget(item, max(max_chars // 4, 100))
            if _json_chars(result + [candidate]) > max_chars:
                result.append({"truncated": True, "remaining_items": len(value) - len(result)})
                break
            result.append(candidate)
        return result
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        original_chars = _json_chars(value)
        priority_keys = [
            "success",
            "tool_name",
            "code",
            "message",
            "error",
            "error_type",
            "retryable",
            "observation_mode",
            "requested_observation_mode",
            "mode_downgraded",
            "downgrade_reason",
            "artifact_ref",
            "raw_ref",
            "preview_hash",
            "raw_output_truncated",
            "data_summary",
            "preview",
            "data",
        ]
        keys = [key for key in priority_keys if key in value]
        keys.extend(key for key in value if key not in keys)
        item_budget = max(max_chars // max(len(keys), 1), 100)
        for key in keys:
            item = _fit_to_budget(value[key], item_budget)
            candidate = {**result, str(key): item}
            if _json_chars(candidate) > max_chars:
                continue
            result[str(key)] = item
        result["truncated"] = True
        result["original_chars"] = original_chars
        while _json_chars(result) > max_chars and len(result) > 2:
            removable = [
                key for key in result
                if key not in {"success", "tool_name", "code", "message", "observation_mode", "truncated", "original_chars"}
            ]
            if not removable:
                break
            result.pop(removable[-1], None)
        return result
    return truncate_text(str(value), max_chars)


def _included_fields(payload: Mapping[str, Any]) -> list[str]:
    return sorted(str(key) for key in payload.keys())


def _output_control(tool_result: ToolResult) -> Mapping[str, Any]:
    metadata = tool_result.metadata if isinstance(tool_result.metadata, Mapping) else {}
    output_control = metadata.get("output_control")
    return output_control if isinstance(output_control, Mapping) else {}


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


__all__ = [
    "OBSERVATION_MODES",
    "ToolObservationView",
    "build_tool_observation_view",
]
