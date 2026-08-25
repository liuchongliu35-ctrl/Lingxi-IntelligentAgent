from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.tools import (
    ToolCallContext,
    ToolCallOptions,
    ToolCallRequest,
    ToolCallSource,
)


class ToolCallProtocolTest(unittest.TestCase):
    def test_context_defaults_are_normalized_and_source_is_validated(self):
        context = ToolCallContext(source=ToolCallSource.REACT_EXECUTOR, workspace_root=".")

        self.assertEqual(context.source, "react_executor")
        self.assertTrue(Path(context.workspace_root).is_absolute())
        self.assertEqual(context.initiated_by, "runtime")

        with self.assertRaises(ValueError):
            ToolCallContext(source="model")

    def test_options_defaults_are_conservative(self):
        options = ToolCallOptions()

        self.assertIsNone(options.timeout_seconds)
        self.assertFalse(options.dry_run)
        self.assertFalse(options.confirmed)
        self.assertTrue(options.allow_read_workspace)
        self.assertFalse(options.allow_write_workspace)
        self.assertFalse(options.allow_network)
        self.assertFalse(options.allow_command)
        self.assertFalse(options.allow_shell_command)
        self.assertFalse(options.allow_mcp)
        self.assertFalse(options.has_confirmation_ticket)

    def test_request_requires_object_args_and_keeps_confirmation_out_of_args(self):
        request = ToolCallRequest(
            tool_name="demo",
            args={"confirmed": True, "query": "hello"},
        )

        self.assertEqual(request.args["confirmed"], True)
        self.assertFalse(request.options.confirmed)
        self.assertFalse(request.options.has_confirmation_ticket)

        with self.assertRaises(ValueError):
            ToolCallRequest(tool_name="demo", args=["not", "an", "object"])

    def test_confirmation_ticket_is_explicitly_bound(self):
        unbound = ToolCallOptions(confirmed=True)
        bound = ToolCallOptions(
            confirmed=True,
            confirmation_id="confirmation-1",
            preview_hash="hash-1",
            approval_scope="one_call",
            approval_source="user_ui",
        )

        self.assertFalse(unbound.has_confirmation_ticket)
        self.assertTrue(bound.has_confirmation_ticket)

    def test_legacy_kwargs_can_be_converted_to_restricted_request(self):
        request = ToolCallRequest.from_legacy("search_tool", {"query": "agent"})

        self.assertEqual(request.tool_name, "search_tool")
        self.assertEqual(request.args, {"query": "agent"})
        self.assertEqual(request.context.source, "historical_executor")
        self.assertFalse(request.options.confirmed)
        self.assertFalse(request.options.allow_network)
        self.assertFalse(request.options.allow_write_workspace)

    def test_request_to_dict_is_json_safe_and_redacts_obvious_secrets(self):
        request = ToolCallRequest(
            tool_name="web_search",
            args={
                "query": "agent",
                "api_key": "do-not-leak",
                "nested": {"access_token": "also-do-not-leak"},
            },
            context=ToolCallContext(
                trace_id="trace-1",
                execution_id="execution-1",
                workspace_root=".",
                source="test",
                initiated_by="unit_test",
            ),
            options=ToolCallOptions(observation_mode="standard"),
        )

        encoded = json.dumps(request.to_dict(), ensure_ascii=False)
        payload = json.loads(encoded)
        self.assertEqual(payload["args"]["api_key"], "<redacted>")
        self.assertEqual(payload["args"]["nested"]["access_token"], "<redacted>")
        self.assertEqual(payload["context"]["source"], "test")
        self.assertNotIn("do-not-leak", encoded)
        self.assertNotIn("also-do-not-leak", encoded)

    def test_default_context_and_options_are_not_shared(self):
        first = ToolCallRequest(tool_name="one")
        second = ToolCallRequest(tool_name="two")

        first.args["value"] = 1

        self.assertEqual(second.args, {})
        self.assertIsNot(first.context, second.context)
        self.assertIsNot(first.options, second.options)

    def test_invalid_option_values_are_rejected(self):
        with self.assertRaises(ValueError):
            ToolCallOptions(timeout_seconds=0)
        with self.assertRaises(ValueError):
            ToolCallOptions(max_output_chars=-1)
        with self.assertRaises(ValueError):
            ToolCallOptions(observation_mode="unsafe")
        with self.assertRaises(ValueError):
            ToolCallOptions(approval_scope="session-wide")


if __name__ == "__main__":
    unittest.main()
