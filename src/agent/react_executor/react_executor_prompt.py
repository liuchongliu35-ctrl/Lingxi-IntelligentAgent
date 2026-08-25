from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from src.agent.react_executor_protocol import ACTION_TYPES


DEFAULT_MODEL_PROMPTS: Dict[str, Any] = {
    "system_instruction": (
        "You are the ReActExecutor decision model. Return exactly one ActionPacket JSON object. "
        "Do not return Markdown, prose, or mixed natural language outside JSON."
    ),
    "output_contract": [
        "Use only canonical action_type values.",
        "Do not invent tool names.",
        "Do not bypass plan.can_execute=false or invalid plans.",
        "Do not execute dangerous actions without confirmation.",
        "If you cannot continue, choose ask_user, fallback_to_model, request_replan, or fail.",
        "If the task is complete, choose finish and fill final_answer.",
    ],
    "safety_rules": [
        "Never reveal hidden chain-of-thought. Use thought_summary only as a short execution summary.",
        "Never expose secrets, tokens, passwords, api keys, or authorization values.",
        "Never request direct shell execution except through a registered Tool layer command tool.",
        "Never write outside the workspace or modify blocked files.",
    ],
    "max_context_chars": 4000,
    "max_observation_chars": 2000,
    "max_history_chars": 1200,
}


@dataclass
class ReActPromptContext:
    user_input: str
    analyzer_summary: Any = None
    task_plan: Any = None
    current_task_unit: Any = None
    current_step: Any = None
    available_tools: Any = None
    previous_action: Any = None
    previous_observation: Any = None
    execution_progress: Any = None
    history_summary: str = ""
    action_packet_schema: Dict[str, Any] | None = None
    extra_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_input": self.user_input,
            "analyzer_summary": _json_safe(self.analyzer_summary),
            "task_plan": _json_safe(self.task_plan),
            "current_task_unit": _json_safe(self.current_task_unit),
            "current_step": _json_safe(self.current_step),
            "previous_action": _json_safe(self.previous_action),
            "previous_observation": _json_safe(self.previous_observation),
            "execution_progress": _json_safe(self.execution_progress),
            "available_tools": _json_safe(self.available_tools),
            "history_summary": self.history_summary,
            "extra_context": _json_safe(self.extra_context),
        }


@lru_cache(maxsize=16)
def load_react_executor_model_prompts(root: str | Path | None = None) -> Dict[str, Any]:
    project_root = Path(root or Path.cwd()).resolve()
    path = project_root / "config" / "react_executor" / "model_prompts.json"
    if not path.exists():
        return dict(DEFAULT_MODEL_PROMPTS)
    with path.open("r", encoding="utf-8-sig") as file:
        loaded = json.load(file)
    if not isinstance(loaded, dict):
        return dict(DEFAULT_MODEL_PROMPTS)
    merged = dict(DEFAULT_MODEL_PROMPTS)
    merged.update(loaded)
    return merged


@lru_cache(maxsize=16)
def load_action_packet_schema(root: str | Path | None = None) -> Dict[str, Any]:
    project_root = Path(root or Path.cwd()).resolve()
    path = project_root / "config" / "react_executor" / "action_packet_schema.json"
    if not path.exists():
        return {"type": "object", "required": ["action_type"], "properties": {"action_type": {"enum": sorted(ACTION_TYPES)}}}
    with path.open("r", encoding="utf-8-sig") as file:
        loaded = json.load(file)
    if not isinstance(loaded, dict):
        return {"type": "object", "required": ["action_type"], "properties": {"action_type": {"enum": sorted(ACTION_TYPES)}}}
    return loaded


def build_react_executor_prompt(
    context: ReActPromptContext,
    *,
    root: str | Path | None = None,
    prompt_config: Dict[str, Any] | None = None,
) -> str:
    config = prompt_config or load_react_executor_model_prompts(root)
    schema = context.action_packet_schema or load_action_packet_schema(root)
    max_context_chars = _positive_int(config.get("max_context_chars"), 4000)
    max_observation_chars = _positive_int(config.get("max_observation_chars"), 2000)
    max_history_chars = _positive_int(config.get("max_history_chars"), 1200)

    context_payload = context.to_dict()
    context_payload["previous_observation"] = _truncate_json(
        context_payload.get("previous_observation"),
        max_observation_chars,
    )
    context_payload["history_summary"] = _truncate_text(context.history_summary, max_history_chars)

    sections = [
        "# System Instruction",
        str(config.get("system_instruction", DEFAULT_MODEL_PROMPTS["system_instruction"])),
        "# Output Contract",
        _bullet_lines(config.get("output_contract", [])),
        "# Safety Rules",
        _bullet_lines(config.get("safety_rules", [])),
        "# Allowed Action Types",
        json.dumps(sorted(ACTION_TYPES), ensure_ascii=False),
        "# ActionPacket JSON Schema",
        _truncate_text(json.dumps(schema, ensure_ascii=False, indent=2), max_context_chars),
        "# Execution Context",
        _truncate_text(json.dumps(context_payload, ensure_ascii=False, indent=2), max_context_chars),
        "# Required Response",
        "Return exactly one JSON object matching the ActionPacket schema. No Markdown fences. No extra prose.",
    ]
    return "\n\n".join(section for section in sections if section)


def build_prompt_log_summary(prompt: str) -> Dict[str, Any]:
    return {
        "prompt_length": len(prompt),
        "prompt_preview": _truncate_text(prompt.replace("\n", " "), 300),
    }


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _truncate_json(value: Any, max_chars: int) -> Any:
    text = json.dumps(_json_safe(value), ensure_ascii=False)
    if len(text) <= max_chars:
        return _json_safe(value)
    return {
        "truncated": True,
        "original_chars": len(text),
        "preview": text[:max_chars],
    }


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [truncated {len(text) - max_chars} chars]"


def _bullet_lines(items: Any) -> str:
    if not isinstance(items, list):
        return str(items)
    return "\n".join(f"- {item}" for item in items)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 1)
