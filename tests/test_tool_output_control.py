from __future__ import annotations

import unittest

from src.tools.base import ToolResult
from src.tools.output_control import OutputController, truncate_text
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.registry import ToolSpec


class ToolOutputControlTest(unittest.TestCase):
    def test_raw_output_is_truncated_with_hash_and_artifact_reference(self):
        controller = OutputController(
            max_output_chars=20,
            max_raw_output_chars=10,
            max_observation_chars=30,
        )
        spec = ToolSpec(name="output", description="Output.")
        request = _request(
            "output",
            ToolCallOptions(
                max_output_chars=20,
                max_raw_output_chars=10,
                max_observation_chars=30,
            ),
        )
        result = ToolResult.ok(
            data={"keep": "x", "large": "z" * 100},
            message="m" * 100,
            raw_output="raw-" * 20,
        )

        controlled = controller.apply(result, spec, request)
        metadata = controlled.metadata["output_control"]

        self.assertTrue(controlled.raw_output_truncated)
        self.assertLessEqual(len(controlled.raw_output), 10)
        self.assertEqual(len(metadata["raw_output_hash"]), 64)
        self.assertEqual(metadata["raw_output_chars"], 80)
        self.assertTrue(metadata["artifact_ref"].startswith("artifact://tool-output/"))
        self.assertTrue(metadata["raw_ref"].startswith("artifact://tool-output/"))
        self.assertLessEqual(len(controlled.message), 20)
        self.assertIn("data_summary", metadata)

    def test_data_structure_is_retained_while_large_values_are_limited(self):
        controller = OutputController(max_output_chars=24)
        spec = ToolSpec(name="output", description="Output.")
        request = _request("output", ToolCallOptions(max_output_chars=24))
        result = ToolResult.ok(
            data={"first": "a" * 100, "second": "b" * 100},
        )

        controlled = controller.apply(result, spec, request)

        self.assertEqual(set(controlled.data), {"first", "second"})
        self.assertLess(len(controlled.data["first"]), 100)
        self.assertLess(len(controlled.data["second"]), 100)

    def test_preview_argument_summary_redacts_sensitive_values_and_hashes_content(self):
        controller = OutputController()
        spec = ToolSpec(name="writer", description="Writer.", workspace_scope="write_workspace")
        request = _request(
            "writer",
            ToolCallOptions(),
            args={
                "file_path": "notes.txt",
                "content": "private content",
                "api_key": "secret-value",
            },
        )

        preview = controller.build_preview(spec, request)

        args = preview.payload["args"]
        self.assertEqual(args["file_path"], "notes.txt")
        self.assertEqual(args["api_key"], "<redacted>")
        self.assertEqual(args["content"]["chars"], len("private content"))
        self.assertNotIn("private content", str(preview.payload))
        self.assertNotIn("secret-value", str(preview.payload))
        self.assertEqual(len(preview.preview_hash), 64)

    def test_truncate_text_respects_small_limits(self):
        self.assertEqual(truncate_text("abcdef", 0), "")
        self.assertEqual(len(truncate_text("abcdef", 3)), 3)
        self.assertEqual(truncate_text("abc", 3), "abc")


def _request(
    tool_name: str,
    options: ToolCallOptions,
    *,
    args: dict | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        args=args or {},
        context=ToolCallContext(source="test"),
        options=options,
    )


if __name__ == "__main__":
    unittest.main()
