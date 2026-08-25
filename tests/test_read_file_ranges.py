from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.config import clear_tools_config_cache
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.tool_logger import NullToolLogger
from src.tools.tool_manager import ToolManager


class ReadFileRangesTest(unittest.TestCase):
    def tearDown(self) -> None:
        clear_tools_config_cache()

    def test_chunk_reads_middle_range_with_one_based_boundaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_lines(root / "sample.txt", 6)
            manager = _manager(root)

            result = manager.execute(
                _request(
                    root,
                    "read_file_chunk",
                    {"path": "sample.txt", "start_line": 3, "line_count": 2},
                )
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["start_line"], 3)
            self.assertEqual(result.data["end_line"], 4)
            self.assertEqual(result.data["line_count"], 2)
            self.assertEqual(result.data["content"], "line 3\nline 4\n")
            self.assertTrue(result.data["has_more_before"])
            self.assertTrue(result.data["has_more_after"])
            self.assertEqual(result.data["mode"], "chunk")

    def test_head_returns_first_lines_and_reports_short_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_lines(root / "short.txt", 3)
            manager = _manager(root)

            result = manager.execute(
                _request(
                    root,
                    "read_file_head",
                    {"path": "short.txt", "line_count": 5},
                )
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["start_line"], 1)
            self.assertEqual(result.data["end_line"], 3)
            self.assertEqual(result.data["line_count"], 3)
            self.assertEqual(result.data["content"], "line 1\nline 2\nline 3\n")
            self.assertFalse(result.data["has_more_before"])
            self.assertFalse(result.data["has_more_after"])
            self.assertEqual(result.data["mode"], "head")

    def test_tail_returns_last_lines_with_correct_start_and_end(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_lines(root / "sample.txt", 5)
            manager = _manager(root)

            result = manager.execute(
                _request(
                    root,
                    "read_file_tail",
                    {"path": "sample.txt", "line_count": 2},
                )
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["start_line"], 4)
            self.assertEqual(result.data["end_line"], 5)
            self.assertEqual(result.data["line_count"], 2)
            self.assertEqual(result.data["content"], "line 4\nline 5\n")
            self.assertTrue(result.data["has_more_before"])
            self.assertFalse(result.data["has_more_after"])
            self.assertEqual(result.data["mode"], "tail")

    def test_chunk_start_beyond_eof_is_empty_but_reports_prior_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_lines(root / "sample.txt", 3)
            manager = _manager(root)

            result = manager.execute(
                _request(
                    root,
                    "read_file_chunk",
                    {"path": "sample.txt", "start_line": 10, "line_count": 2},
                )
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["start_line"], 10)
            self.assertEqual(result.data["end_line"], 9)
            self.assertEqual(result.data["line_count"], 0)
            self.assertEqual(result.data["content"], "")
            self.assertTrue(result.data["has_more_before"])
            self.assertFalse(result.data["has_more_after"])

    def test_range_line_count_is_capped_by_configured_hard_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_defaults(root, {"read_file_range_max_lines": 3})
            _write_lines(root / "sample.txt", 10)
            manager = _manager(root)

            result = manager.execute(
                _request(
                    root,
                    "read_file_head",
                    {"path": "sample.txt", "line_count": 10},
                )
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["line_count"], 3)
            self.assertEqual(result.data["effective_line_count"], 3)
            self.assertEqual(result.data["requested_line_count"], 10)
            self.assertEqual(result.data["line_count_limit"], 3)
            self.assertTrue(result.data["line_count_capped"])
            self.assertTrue(result.data["has_more_after"])

    def test_range_rejects_invalid_ranges_binary_files_and_workspace_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"
            root.mkdir()
            outside = root.parent / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            _write_lines(root / "sample.txt", 2)
            (root / "binary.bin").write_bytes(b"line\x00binary")
            manager = _manager(root)

            invalid = manager.execute(
                _request(
                    root,
                    "read_file_chunk",
                    {"path": "sample.txt", "start_line": 0, "line_count": 2},
                )
            )
            binary = manager.execute(
                _request(
                    root,
                    "read_file_tail",
                    {"path": "binary.bin", "line_count": 1},
                )
            )
            escaped = manager.execute(
                _request(
                    root,
                    "read_file_head",
                    {"path": "../outside.txt", "line_count": 1},
                )
            )

            self.assertFalse(invalid.success)
            self.assertEqual(invalid.code, ToolErrorCode.INVALID_ARGS.value)
            self.assertFalse(binary.success)
            self.assertEqual(binary.code, ToolErrorCode.BINARY_FILE_NOT_SUPPORTED.value)
            self.assertFalse(escaped.success)
            self.assertEqual(escaped.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)

    def test_range_sensitive_file_requires_confirmation_and_reads_after_ticket(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_bytes(b"TOKEN=secret\nSECOND=value\n")
            manager = _manager(root)

            pending = manager.execute(
                _request(
                    root,
                    "read_file_head",
                    {"path": ".env", "line_count": 1},
                )
            )
            preview = manager.execute(
                _request(
                    root,
                    "read_file_head",
                    {"path": ".env", "line_count": 1},
                    options=ToolCallOptions(
                        dry_run=True,
                        allow_read_workspace=True,
                    ),
                )
            )
            confirmed = manager.execute(
                _request(
                    root,
                    "read_file_head",
                    {"path": ".env", "line_count": 1},
                    options=ToolCallOptions(
                        allow_read_workspace=True,
                        confirmed=True,
                        confirmation_id="confirmation-1",
                        preview_hash=preview.metadata["output_control"]["preview_hash"],
                    ),
                )
            )

            self.assertFalse(pending.success)
            self.assertEqual(pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
            self.assertTrue(preview.success)
            self.assertTrue(confirmed.success)
            self.assertTrue(confirmed.data["is_sensitive"])
            self.assertEqual(confirmed.data["content"], "TOKEN=secret\n")

    def test_tail_handles_many_lines_without_loading_all_lines_into_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_lines(root / "large.log", 10000)
            manager = _manager(root)

            result = manager.execute(
                _request(
                    root,
                    "read_file_tail",
                    {"path": "large.log", "line_count": 3},
                )
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["start_line"], 9998)
            self.assertEqual(result.data["end_line"], 10000)
            self.assertEqual(result.data["content"], "line 9998\nline 9999\nline 10000\n")
            self.assertEqual(result.data["line_count"], 3)


def _manager(root: Path) -> ToolManager:
    return ToolManager(logger=NullToolLogger(), workspace_root=root)


def _request(
    root: Path,
    tool_name: str,
    args: dict,
    *,
    options: ToolCallOptions | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        args=args,
        context=ToolCallContext(workspace_root=root, source="test"),
        options=options or ToolCallOptions(allow_read_workspace=True),
    )


def _write_lines(path: Path, count: int) -> None:
    path.write_bytes(
        "".join(f"line {index}\n" for index in range(1, count + 1)).encode("utf-8")
    )


def _write_defaults(root: Path, values: dict) -> None:
    config_dir = root / "config" / "tools"
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": True,
        "default_timeout_seconds": 30,
        "max_output_chars": 12000,
        "max_raw_output_chars": 50000,
        "max_observation_chars": 16000,
        "default_observation_mode": "standard",
        "workspace_root_policy": "workspace_only",
        "logs_path": "logs/tools.log",
    }
    payload.update(values)
    (config_dir / "defaults.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
