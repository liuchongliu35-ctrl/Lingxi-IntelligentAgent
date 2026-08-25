from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List

from src.agent.react_executor_protocol import ObservationPacket


SENSITIVE_FIELD_MARKERS = {
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "authorization",
}

REDACTED_VALUE = "***REDACTED***"
DEFAULT_MODEL_CONTEXT_VALUE_CHARS = 2000
DEFAULT_RECENT_OBSERVATIONS = 5


@dataclass
class ObservationStore:
    observations: List[ObservationPacket] = field(default_factory=list)
    output_key_index: Dict[str, str] = field(default_factory=dict)

    def add(self, observation: ObservationPacket, output_key: str | None = None) -> ObservationPacket:
        self.observations.append(observation)
        key = output_key or _output_key_from_observation(observation)
        if key:
            self.output_key_index[key] = observation.observation_id
        return observation

    def get(self, observation_id: str) -> ObservationPacket | None:
        for observation in reversed(self.observations):
            if observation.observation_id == observation_id:
                return observation
        return None

    def get_by_step(self, step_id: str) -> List[ObservationPacket]:
        return [observation for observation in self.observations if observation.step_id == step_id]

    def get_by_output_key(self, output_key: str) -> ObservationPacket | None:
        observation_id = self.output_key_index.get(output_key)
        if observation_id:
            return self.get(observation_id)
        return None

    def get_latest_for_step(self, step_id: str) -> ObservationPacket | None:
        for observation in reversed(self.observations):
            if observation.step_id == step_id:
                return observation
        return None

    def resolve_input_refs(self, input_from: List[str], *, compact: bool = False, max_value_chars: int = DEFAULT_MODEL_CONTEXT_VALUE_CHARS) -> Dict[str, Any]:
        resolved: Dict[str, Any] = {}
        for ref in input_from:
            observation = self._resolve_ref(ref)
            if observation is None:
                resolved[ref] = {"missing": True, "ref": ref}
                continue
            value = _model_value(observation)
            resolved[ref] = _compact_model_value(value, max_value_chars=max_value_chars) if compact else value
        return resolved

    def to_model_context(self, input_from: List[str], *, max_value_chars: int = DEFAULT_MODEL_CONTEXT_VALUE_CHARS) -> List[Dict[str, Any]]:
        context: List[Dict[str, Any]] = []
        for ref in input_from:
            observation = self._resolve_ref(ref)
            if observation is None:
                context.append({"ref": ref, "missing": True})
                continue
            context.append(self._model_context_item(observation, ref=ref, max_value_chars=max_value_chars))
        return context

    def recent_model_context(
        self,
        *,
        max_observations: int = DEFAULT_RECENT_OBSERVATIONS,
        max_value_chars: int = DEFAULT_MODEL_CONTEXT_VALUE_CHARS,
    ) -> List[Dict[str, Any]]:
        if max_observations <= 0:
            return []
        selected = self.observations[-max_observations:]
        return [
            self._model_context_item(observation, ref=None, max_value_chars=max_value_chars)
            for observation in selected
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observations": [sanitize_sensitive(observation.to_dict()) for observation in self.observations],
            "output_key_index": dict(self.output_key_index),
        }

    def _resolve_ref(self, ref: str) -> ObservationPacket | None:
        by_output_key = self.get_by_output_key(ref)
        if by_output_key is not None:
            return by_output_key
        latest_for_step = self.get_latest_for_step(ref)
        if latest_for_step is not None:
            return latest_for_step
        return self.get(ref)

    def _model_context_item(self, observation: ObservationPacket, *, ref: str | None, max_value_chars: int) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "observation_id": observation.observation_id,
            "task_id": observation.task_id,
            "step_id": observation.step_id,
            "action_type": observation.action_type,
            "action_target": observation.action_target,
            "tool_name": observation.tool_name,
            "success": observation.success,
            "message": _truncate_text(str(observation.message or ""), max_value_chars),
            "code": observation.code,
            "model_consumable_observation": sanitize_sensitive(
                _compact_model_value(_model_value(observation), max_value_chars=max_value_chars)
            ),
        }
        if ref is not None:
            item["ref"] = ref
        if observation.error:
            item["error"] = _truncate_text(str(observation.error), max_value_chars)
        return item


def sanitize_sensitive(value: Any) -> Any:
    if is_dataclass(value):
        return sanitize_sensitive(asdict(value))
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key_text] = REDACTED_VALUE
            else:
                sanitized[key_text] = sanitize_sensitive(item)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [sanitize_sensitive(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def observation_to_text(observation: ObservationPacket) -> str:
    value = _model_value(observation)
    if isinstance(value, str):
        return value
    if value is None:
        return observation.message or ""
    return json.dumps(sanitize_sensitive(value), ensure_ascii=False)


def _output_key_from_observation(observation: ObservationPacket) -> str | None:
    checker_output_key = observation.checker_result.get("output_key")
    if isinstance(checker_output_key, str) and checker_output_key.strip():
        return checker_output_key
    input_output_key = observation.input_args.get("output_key")
    if isinstance(input_output_key, str) and input_output_key.strip():
        return input_output_key
    return None


def _model_value(observation: ObservationPacket) -> Any:
    if observation.model_consumable_observation is not None:
        return observation.model_consumable_observation
    if observation.data is not None:
        return observation.data
    return observation.message


def _compact_model_value(value: Any, *, max_value_chars: int) -> Any:
    max_chars = max(int(max_value_chars or DEFAULT_MODEL_CONTEXT_VALUE_CHARS), 100)
    sanitized = sanitize_sensitive(value)
    if isinstance(sanitized, str):
        return _truncate_text(sanitized, max_chars)
    if isinstance(sanitized, (int, float, bool)) or sanitized is None:
        return sanitized

    text = json.dumps(sanitized, ensure_ascii=False)
    if len(text) <= max_chars:
        return sanitized
    return {
        "truncated": True,
        "original_chars": len(text),
        "preview": text[:max_chars],
    }


def _truncate_text(text: str, max_chars: int) -> str:
    limit = max(int(max_chars or DEFAULT_MODEL_CONTEXT_VALUE_CHARS), 100)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated {len(text) - limit} chars]"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_FIELD_MARKERS)
