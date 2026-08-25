from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.errors import ToolErrorCode
from src.tools.file_tools.find import MAX_TEXT_SCAN_BYTES
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.tool_logger import NullToolLogger
from src.tools.tool_manager import ToolManager


class FindFilesToolTest(unittest.TestCase):
    def test_find_files_by_name_pattern_in_stable_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "b_test.py").write_text("b", encoding="utf-8")
            (root / "a_test.py").write_text("a", encoding="utf-8")
            (root / "note.md").write_text("note", encoding="utf-8")
            manager = _manager(root)

            result = manager.execute(
                _request(root, {"path": ".", "name_pattern": "*_test.py"})
            )

            self.assertTrue(result.success)
            self.assertEqual(
                [match["path"] for match in result.data["matches"]],
                ["a_test.py", "b_test.py"],
            )
            self.assertEqual(result.data["match_count"], 2)
            self.assertFalse(result.data["truncated"])
            self.assertEqual(result.tool_name, "find_files")

    def test_find_files_by_text_pattern_returns_line_location_and_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes.txt").write_text(
                "alpha\nNeedle is here\nomega\n",
                encoding="utf-8",
            )
            manager = _manager(root)

            result = manager.execute(
                _request(root, {"path": ".", "text_pattern": "needle"})
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["match_count"], 1)
            match = result.data["matches"][0]
            self.assertEqual(match["path"], "notes.txt")
            self.assertEqual(match["line_number"], 2)
            self.assertEqual(match["line_preview"], "Needle is here")

    def test_find_files_combines_name_and_text_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "keep.py").write_text("target", encoding="utf-8")
            (root / "skip.py").write_text("other", encoding="utf-8")
            (root / "keep.md").write_text("target", encoding="utf-8")
            manager = _manager(root)

            result = manager.execute(
                _request(
                    root,
                    {
                        "path": ".",
                        "name_pattern": "*.py",
                        "text_pattern": "target",
                    },
                )
            )

            self.assertTrue(result.success)
            self.assertEqual(
                [match["path"] for match in result.data["matches"]],
                ["keep.py"],
            )

    def test_find_files_honors_case_sensitive_option_for_name_and_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Report.TXT").write_text("Needle", encoding="utf-8")
            manager = _manager(root)

            insensitive = manager.execute(
                _request(
                    root,
                    {
                        "name_pattern": "report.txt",
                        "text_pattern": "needle",
                        "case_sensitive": False,
                    },
                )
            )
            sensitive = manager.execute(
                _request(
                    root,
                    {
                        "name_pattern": "report.txt",
                        "text_pattern": "needle",
                        "case_sensitive": True,
                    },
                )
            )

            self.assertTrue(insensitive.success)
            self.assertEqual(insensitive.data["match_count"], 1)
            self.assertTrue(sensitive.success)
            self.assertEqual(sensitive.data["match_count"], 0)

    def test_find_files_truncates_at_max_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(5):
                (root / f"file_{index}.txt").write_text("match", encoding="utf-8")
            manager = _manager(root)

            result = manager.execute(
                _request(root, {"name_pattern": "*.txt", "max_results": 3})
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["match_count"], 3)
            self.assertTrue(result.data["truncated"])
            self.assertEqual(result.data["max_results"], 3)

    def test_find_files_skips_ignored_sensitive_binary_and_large_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "visible.txt").write_text("needle", encoding="utf-8")
            node_modules = root / "node_modules"
            node_modules.mkdir()
            (node_modules / "package.txt").write_text("needle", encoding="utf-8")
            (root / ".env").write_text("needle=secret", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"needle\x00secret")
            (root / "large.txt").write_bytes(b"x" * (MAX_TEXT_SCAN_BYTES + 1))
            manager = _manager(root)

            result = manager.execute(
                _request(root, {"path": ".", "text_pattern": "needle"})
            )

            self.assertTrue(result.success)
            self.assertEqual(
                [match["path"] for match in result.data["matches"]],
                ["visible.txt"],
            )
            self.assertEqual(result.data["skipped_count"], 4)
            self.assertNotIn("secret", str(result.to_dict()))

    def test_find_files_reports_validation_and_path_errors_structurally(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"
            root.mkdir()
            outside = root.parent / "outside"
            outside.mkdir()
            (root / "file.txt").write_text("text", encoding="utf-8")
            ignored = root / "node_modules"
            ignored.mkdir()
            manager = _manager(root)

            missing_pattern = manager.execute(_request(root, {"path": "."}))
            missing_root = manager.execute(
                _request(root, {"path": "missing", "name_pattern": "*"})
            )
            not_directory = manager.execute(
                _request(root, {"path": "file.txt", "name_pattern": "*"})
            )
            escaped = manager.execute(
                _request(root, {"path": "../outside", "name_pattern": "*"})
            )
            ignored_root = manager.execute(
                _request(root, {"path": "node_modules", "name_pattern": "*"})
            )

            self.assertFalse(missing_pattern.success)
            self.assertEqual(missing_pattern.code, ToolErrorCode.MISSING_REQUIRED_PARAM.value)
            self.assertFalse(missing_root.success)
            self.assertEqual(missing_root.code, ToolErrorCode.FILE_NOT_FOUND.value)
            self.assertFalse(not_directory.success)
            self.assertEqual(not_directory.code, ToolErrorCode.NOT_A_DIRECTORY.value)
            self.assertFalse(escaped.success)
            self.assertEqual(escaped.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)
            self.assertFalse(ignored_root.success)
            self.assertEqual(ignored_root.code, ToolErrorCode.DIRECTORY_IGNORED.value)


def _manager(root: Path) -> ToolManager:
    return ToolManager(logger=NullToolLogger(), workspace_root=root)


def _request(root: Path, args: dict) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name="find_files",
        args=args,
        context=ToolCallContext(workspace_root=root, source="test"),
        options=ToolCallOptions(allow_read_workspace=True),
    )


if __name__ == "__main__":
    unittest.main()
