from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.models.errors import ModelErrorCode


@dataclass
class JsonParseResult:
    success: bool
    data: dict[str, Any] | list[Any] | None = None
    raw_json_text: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class JsonSchemaValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


def parse_json_output(
    content: Any,
    *,
    parse_mode: str = "lenient",
) -> JsonParseResult:
    """Parse model output as JSON without applying business-specific validation."""
    if isinstance(content, dict):
        return JsonParseResult(success=True, data=content, raw_json_text=json.dumps(content, ensure_ascii=False))
    if isinstance(content, list):
        return JsonParseResult(success=True, data=content, raw_json_text=json.dumps(content, ensure_ascii=False))
    if not isinstance(content, str):
        return JsonParseResult(success=False, error="model output must be a string, object, or array")

    text = content.strip()
    if not text:
        return JsonParseResult(success=False, error="model output is empty")

    mode = str(parse_mode or "lenient").strip().lower()
    if mode not in {"strict", "lenient"}:
        return JsonParseResult(success=False, error=f"unsupported parse_mode: {parse_mode}")

    candidates = _candidate_json_texts(text, parse_mode=mode)
    errors: list[str] = []
    for candidate, source in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(f"{source}: {exc.msg} at {exc.lineno}:{exc.colno}")
            continue
        if isinstance(data, (dict, list)):
            return JsonParseResult(
                success=True,
                data=data,
                raw_json_text=candidate,
                metadata={"json_source": source},
            )
        errors.append(f"{source}: top-level JSON must be object or array")

    return JsonParseResult(
        success=False,
        error="; ".join(errors) if errors else "no JSON object or array found",
        metadata={"parse_mode": mode},
    )


def validate_json_schema(
    data: dict[str, Any] | list[Any],
    schema: dict[str, Any] | None,
) -> JsonSchemaValidationResult:
    """Lightweight JSON-schema subset validation for Models V1 structured output."""
    if not schema:
        return JsonSchemaValidationResult(valid=True)
    errors: list[str] = []
    _validate_schema_node(data, schema, "$", errors)
    return JsonSchemaValidationResult(valid=not errors, errors=errors)


def build_json_repair_prompt(
    *,
    original_prompt: str,
    raw_output: Any,
    parse_error: str,
    parse_mode: str,
    schema_name: str | None = None,
    schema: dict[str, Any] | None = None,
) -> str:
    sections = [
        "Repair the previous model response so it is valid JSON.",
        "Return only one JSON object or array. Do not include Markdown fences or extra prose.",
        f"Required parse mode: {parse_mode}.",
    ]
    if schema_name:
        sections.append(f"Schema name: {schema_name}.")
    if schema:
        sections.extend(["Target schema summary:", json.dumps(_schema_summary(schema), ensure_ascii=False, indent=2)])
    sections.extend(
        [
            "Parse or validation error:",
            str(parse_error),
            "Original user/model prompt:",
            str(original_prompt),
            "Previous raw output:",
            str(raw_output),
        ]
    )
    return "\n\n".join(sections)


def _candidate_json_texts(text: str, *, parse_mode: str) -> list[tuple[str, str]]:
    if parse_mode == "strict":
        return [(text, "strict")]

    candidates: list[tuple[str, str]] = [(text, "direct")]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.append((fenced.group(1).strip(), "fenced"))
    extracted = _first_json_text(text)
    if extracted is not None:
        candidates.append((extracted, "embedded"))
    return _dedupe_candidates(candidates)


def _dedupe_candidates(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for text, source in candidates:
        if text in seen:
            continue
        seen.add(text)
        result.append((text, source))
    return result


def _first_json_text(text: str) -> str | None:
    positions = [(idx, opener) for opener in ("{", "[") if (idx := text.find(opener)) >= 0]
    for start, opener in sorted(positions):
        closer = "}" if opener == "{" else "]"
        end = _matching_json_end(text, start, opener, closer)
        if end is not None:
            return text[start : end + 1]
    return None


def _matching_json_end(text: str, start: int, opener: str, closer: str) -> int | None:
    stack: list[str] = []
    in_string = False
    escape = False
    pairs = {"{": "}", "[": "]"}
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if char in {"}", "]"}:
            if not stack or stack[-1] != char:
                return None
            stack.pop()
            if not stack:
                return index
    return None


def _validate_schema_node(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type and not _matches_json_type(value, str(expected_type)):
        errors.append(f"{path}: expected {expected_type}")
        return

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        errors.append(f"{path}: value is not in enum")

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{path}: missing required key {key}")
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    _validate_schema_node(value[key], child_schema, f"{path}.{key}", errors)
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_node(item, item_schema, f"{path}[{index}]", errors)


def _matches_json_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _schema_summary(schema: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {"type", "required", "properties", "items", "enum", "description"}
    return {key: value for key, value in schema.items() if key in allowed_keys}


__all__ = [
    "JsonParseResult",
    "JsonSchemaValidationResult",
    "build_json_repair_prompt",
    "parse_json_output",
    "validate_json_schema",
]
