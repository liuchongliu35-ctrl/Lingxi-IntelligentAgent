from __future__ import annotations

import json
import unittest

from src.tools.errors import (
    ToolErrorCode,
    ToolErrorType,
    error_type_for_code,
    is_retryable_code,
    normalize_error_code,
)
from src.tools.registry import ToolRegistry, ToolSpec


class ToolErrorsV1Test(unittest.TestCase):
    def test_core_error_codes_have_stable_types(self):
        codes = [
            ToolErrorCode.TOOL_NOT_FOUND,
            ToolErrorCode.TOOL_DISABLED,
            ToolErrorCode.INVALID_ARGS,
            ToolErrorCode.MISSING_REQUIRED_PARAM,
            ToolErrorCode.PERMISSION_DENIED,
            ToolErrorCode.CONFIRMATION_REQUIRED,
            ToolErrorCode.USER_REJECTED,
            ToolErrorCode.BLOCKED_BY_POLICY,
            ToolErrorCode.WORKSPACE_OUT_OF_SCOPE,
            ToolErrorCode.SENSITIVE_PATH_BLOCKED,
            ToolErrorCode.TIMEOUT,
            ToolErrorCode.INTERNAL_ERROR,
            ToolErrorCode.PROVIDER_NOT_CONFIGURED,
            ToolErrorCode.PROVIDER_TIMEOUT,
            ToolErrorCode.PROVIDER_ERROR,
            ToolErrorCode.NETWORK_NOT_ALLOWED,
            ToolErrorCode.SEARCH_NOT_CONFIGURED,
            ToolErrorCode.FILE_NOT_FOUND,
            ToolErrorCode.FILE_CONFLICT,
            ToolErrorCode.PATCH_AMBIGUOUS_MATCH,
            ToolErrorCode.COMMAND_BLOCKED,
            ToolErrorCode.SHELL_REQUIRED,
            ToolErrorCode.COMMAND_DELETE_NOT_ALLOWED,
            ToolErrorCode.COMMAND_TIMEOUT,
            ToolErrorCode.COMMAND_NONZERO_EXIT,
            ToolErrorCode.COMMAND_LAUNCH_FAILED,
            ToolErrorCode.DOCUMENT_PARSE_FAILED,
            ToolErrorCode.UNSUPPORTED_DOCUMENT_TYPE,
            ToolErrorCode.DOCUMENT_TOO_LARGE,
            ToolErrorCode.DOCUMENT_ENCRYPTED,
            ToolErrorCode.DEPENDENCY_NOT_AVAILABLE,
            ToolErrorCode.MCP_NOT_CONFIGURED,
            ToolErrorCode.MCP_SERVER_DISABLED,
            ToolErrorCode.MCP_CONNECTION_FAILED,
            ToolErrorCode.MCP_TOOL_NOT_FOUND,
            ToolErrorCode.MCP_INVALID_ARGS,
            ToolErrorCode.MCP_TIMEOUT,
        ]

        for code in codes:
            self.assertTrue(error_type_for_code(code))

        self.assertEqual(error_type_for_code(ToolErrorCode.INVALID_ARGS), ToolErrorType.VALIDATION.value)
        self.assertEqual(error_type_for_code(ToolErrorCode.INTERNAL_ERROR), ToolErrorType.INTERNAL.value)

    def test_unknown_codes_normalize_to_internal_error(self):
        self.assertEqual(normalize_error_code("not_a_real_code"), "internal_error")
        self.assertEqual(error_type_for_code("not_a_real_code"), "internal")
        self.assertFalse(is_retryable_code("not_a_real_code"))

    def test_retryable_boundary_is_explicit(self):
        self.assertTrue(is_retryable_code("provider_timeout"))
        self.assertTrue(is_retryable_code("provider_rate_limited"))
        self.assertTrue(is_retryable_code("mcp_connection_failed"))
        self.assertTrue(is_retryable_code("temporary_file_lock"))
        self.assertFalse(is_retryable_code("invalid_args"))
        self.assertFalse(is_retryable_code("permission_denied"))
        self.assertFalse(is_retryable_code("confirmation_required"))
        self.assertFalse(is_retryable_code("user_rejected"))
        self.assertFalse(is_retryable_code("blocked_by_policy"))
        self.assertFalse(is_retryable_code("file_not_found"))
        self.assertFalse(is_retryable_code("patch_ambiguous_match"))

    def test_validation_result_reports_missing_types_and_unknown_args(self):
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="demo",
                    description="Demo",
                    parameters_schema={
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                    required_params=["name"],
                )
            ]
        )

        missing = registry.validate_tool_args("demo", {})
        invalid = registry.validate_tool_args("demo", {"name": 1, "extra": True})
        valid = registry.validate_tool_args("demo", {"name": "ok", "extra": True})

        self.assertEqual(missing.code, "missing_required_param")
        self.assertEqual(missing.error_type, "validation")
        self.assertEqual(missing.missing_params, ["name"])
        self.assertEqual(invalid.code, "invalid_args")
        self.assertEqual(invalid.unknown_params, ["extra"])
        self.assertEqual(valid.code, "ok")
        self.assertEqual(valid.unknown_params, ["extra"])

    def test_validation_result_rejects_unknown_args_when_schema_disallows_them(self):
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="strict",
                    description="Strict",
                    parameters_schema={
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "additionalProperties": False,
                    },
                )
            ]
        )

        result = registry.validate_tool_args("strict", {"value": 1, "extra": True})

        self.assertFalse(result.success)
        self.assertEqual(result.code, "invalid_args")
        self.assertEqual(result.unknown_params, ["extra"])

    def test_validation_result_normalizes_without_sharing_input(self):
        args = {"name": "before"}
        result = ToolRegistry(
            [
                ToolSpec(
                    name="demo",
                    description="Demo",
                    parameters_schema={"properties": {"name": {"type": "string"}}},
                )
            ]
        ).validate_tool_args("demo", args)

        result.normalized_args["name"] = "after"
        self.assertEqual(args["name"], "before")
        self.assertEqual(result.canonical_tool_name, "demo")

    def test_non_object_and_unknown_tool_are_structured(self):
        registry = ToolRegistry()

        invalid = registry.validate_tool_args("missing", "args")
        missing = registry.validate_tool_args("missing", {})

        self.assertEqual(invalid.code, "tool_not_found")
        self.assertEqual(invalid.errors, ["tool not found: missing"])
        self.assertEqual(missing.code, "tool_not_found")
        self.assertEqual(missing.canonical_tool_name, None)

        known = ToolRegistry(
            [
                ToolSpec(
                    name="known",
                    description="Known",
                    parameters_schema={"properties": {}},
                )
            ]
        ).validate_tool_args("known", "args")
        self.assertEqual(known.code, "invalid_args")
        self.assertEqual(known.errors, ["tool args must be object"])

    def test_validation_result_to_dict_is_json_safe(self):
        result = ToolRegistry(
            [
                ToolSpec(
                    name="known",
                    description="Known",
                    parameters_schema={"properties": {}},
                )
            ]
        ).validate_tool_args("known", {})

        encoded = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertIn('"code": "ok"', encoded)


if __name__ == "__main__":
    unittest.main()
