from __future__ import annotations

import enum
import json
import unittest
from pathlib import Path

from src.tools import (
    CommandExecutionData,
    DocumentParseData,
    FileDeleteData,
    FilePatchData,
    FileReadData,
    FileWriteData,
    MCPToolData,
    ToolResult,
    WebSearchData,
    WebSearchResult,
)


class ResultStatus(enum.Enum):
    READY = "ready"


class ToolResultV1Test(unittest.TestCase):
    def test_v1_fields_and_legacy_constructor_are_compatible(self):
        result = ToolResult(
            success=True,
            data="x",
            message="ok",
            error=None,
            code=None,
            tool_name="demo",
            tool_category="utility",
            tool_namespace="builtin",
            provider="local",
            duration_ms=12,
            trace_id="trace-1",
            execution_id="exec-1",
            step_id="step-1",
        )

        expected_fields = {
            "success",
            "tool_name",
            "tool_category",
            "tool_namespace",
            "data",
            "message",
            "error",
            "code",
            "error_type",
            "retryable",
            "provider",
            "started_at",
            "ended_at",
            "duration_ms",
            "trace_id",
            "execution_id",
            "step_id",
            "call_id",
            "raw_output",
            "raw_output_truncated",
            "metadata",
        }
        self.assertTrue(expected_fields.issubset(result.to_dict()))
        self.assertEqual(result.data, "x")
        self.assertEqual(result.duration_ms, 12)
        self.assertTrue(result.call_id)

    def test_factories_preserve_success_and_failure_invariants(self):
        ok = ToolResult.ok(data=123)
        failed = ToolResult.fail(
            "boom",
            code="tool_failed",
            success=True,
            message="must not replace error",
        )

        self.assertTrue(ok.success)
        self.assertEqual(ok.message, "123")
        self.assertFalse(failed.success)
        self.assertEqual(failed.error, "boom")
        self.assertEqual(failed.message, "boom")
        self.assertEqual(failed.code, "tool_failed")

    def test_metadata_and_call_ids_are_not_shared(self):
        first = ToolResult.ok()
        second = ToolResult.ok()

        first.metadata["key"] = "value"
        self.assertEqual(second.metadata, {})
        self.assertNotEqual(first.call_id, second.call_id)

    def test_to_dict_is_json_safe_for_nested_values(self):
        nested = FileReadData(path=Path("notes.txt"), size_bytes=3)
        result = ToolResult.ok(
            data={
                "schema": nested,
                "enum": ResultStatus.READY,
                "set": {"a", "b"},
                "exception": ValueError("bad"),
                "bytes": b"hello",
            },
            raw_output={"secret": "kept but controlled"},
            metadata={"path": Path("notes.txt")},
        )

        encoded = json.dumps(result.to_dict(), ensure_ascii=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["data"]["schema"]["path"], "notes.txt")
        self.assertEqual(decoded["data"]["enum"], "ready")
        self.assertEqual(decoded["data"]["bytes"], "hello")
        self.assertEqual(decoded["raw_output"]["secret"], "kept but controlled")

    def test_to_text_is_short_and_does_not_use_raw_output(self):
        raw_output = "SENSITIVE_RAW_OUTPUT"
        result = ToolResult(
            success=True,
            data={"value": "visible"},
            message="summary",
            raw_output=raw_output,
        )
        self.assertEqual(result.to_text(), "summary")
        self.assertNotIn(raw_output, result.to_text())

        long_result = ToolResult.ok(data="x" * 1000)
        self.assertLessEqual(len(long_result.to_text()), 600)

    def test_data_schemas_are_constructible_and_json_safe(self):
        schemas = [
            FileReadData(path="a.txt", content="a"),
            FileWriteData(path="a.txt", bytes_written=1),
            FilePatchData(path="a.txt", patch_count=1, patch_results=[{"applied": True}]),
            FileDeleteData(paths=["a.txt"], deleted_paths=["a.txt"], total_count=1),
            CommandExecutionData(command="echo hi", program="echo", args=["hi"]),
            DocumentParseData(path="a.pdf", file_type="pdf", tables=[{"rows": 1}]),
            WebSearchData(
                query="agent",
                results=[
                    WebSearchResult(title="Result", url="https://example.test"),
                    {"title": "Second", "url": "https://example.test/2"},
                ],
            ),
            MCPToolData(server_id="local", remote_tool_name="read"),
        ]

        for schema in schemas:
            encoded = json.dumps(schema.to_dict(), ensure_ascii=False)
            self.assertIsInstance(json.loads(encoded), dict)

    def test_web_search_results_are_normalized_and_lists_are_independent(self):
        first = WebSearchData(
            query="one",
            results=[{"title": "A", "url": "https://example.test"}],
        )
        second = WebSearchData(query="two")

        self.assertIsInstance(first.results[0], WebSearchResult)
        self.assertEqual(first.result_count, 1)
        first.results.append(WebSearchResult(title="B"))
        self.assertEqual(second.results, [])
        self.assertEqual(first.metadata, {})
        self.assertEqual(first.usage, {})


if __name__ == "__main__":
    unittest.main()
