from __future__ import annotations

import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.tool_logger import NullToolLogger
from src.tools.tool_manager import ToolManager


class FileListingToolsTest(unittest.TestCase):
    def test_list_files_lists_directory_entries_in_stable_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "b.txt").write_text("b", encoding="utf-8")
            (root / "a_dir").mkdir()
            (root / "a.txt").write_text("a", encoding="utf-8")
            manager = _manager(root)

            result = manager.execute(_request(root, "list_files", {"path": "."}))

            self.assertTrue(result.success)
            paths = [entry["path"] for entry in result.data["entries"]]
            self.assertEqual(paths, ["a_dir", "a.txt", "b.txt"])
            self.assertEqual(result.data["entry_count"], 3)
            self.assertFalse(result.data["truncated"])

    def test_list_files_filters_hidden_and_skips_ignored_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".hidden").write_text("hidden", encoding="utf-8")
            node_modules = root / "node_modules"
            node_modules.mkdir()
            (node_modules / "pkg.json").write_text("{}", encoding="utf-8")
            (root / "visible.txt").write_text("visible", encoding="utf-8")
            manager = _manager(root)

            hidden_filtered = manager.execute(_request(root, "list_files", {"path": "."}))
            hidden_included = manager.execute(
                _request(root, "list_files", {"path": ".", "include_hidden": True})
            )

            self.assertEqual(
                [entry["path"] for entry in hidden_filtered.data["entries"]],
                ["visible.txt"],
            )
            self.assertEqual(hidden_filtered.data["ignored_count"], 1)
            self.assertEqual(
                [entry["path"] for entry in hidden_included.data["entries"]],
                [".hidden", "visible.txt"],
            )
            self.assertEqual(hidden_included.data["ignored_count"], 1)

    def test_list_files_recursive_respects_max_entries_and_workspace_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"
            root.mkdir()
            for index in range(5):
                directory = root / f"dir_{index}"
                directory.mkdir()
                (directory / f"file_{index}.txt").write_text(str(index), encoding="utf-8")
            outside = root.parent / "outside"
            outside.mkdir()
            manager = _manager(root)

            limited = manager.execute(
                _request(
                    root,
                    "list_files",
                    {"path": ".", "recursive": True, "max_entries": 3},
                )
            )
            escaped = manager.execute(
                _request(root, "list_files", {"path": "../outside"})
            )

            self.assertTrue(limited.success)
            self.assertEqual(limited.data["entry_count"], 3)
            self.assertTrue(limited.data["truncated"])
            self.assertFalse(escaped.success)
            self.assertEqual(escaped.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)

    def test_list_files_reports_missing_not_directory_and_ignored_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "file.txt").write_text("file", encoding="utf-8")
            ignored = root / "node_modules"
            ignored.mkdir()
            manager = _manager(root)

            missing = manager.execute(_request(root, "list_files", {"path": "missing"}))
            not_directory = manager.execute(_request(root, "list_files", {"path": "file.txt"}))
            directory_ignored = manager.execute(
                _request(root, "list_files", {"path": "node_modules"})
            )

            self.assertEqual(missing.code, ToolErrorCode.FILE_NOT_FOUND.value)
            self.assertEqual(not_directory.code, ToolErrorCode.NOT_A_DIRECTORY.value)
            self.assertEqual(directory_ignored.code, ToolErrorCode.DIRECTORY_IGNORED.value)

    def test_file_info_returns_bounded_file_metadata_and_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content = "hello\nworld\n"
            target = root / "notes.txt"
            target.write_text(content, encoding="utf-8")
            actual_bytes = target.read_bytes()
            manager = _manager(root)

            result = manager.execute(
                _request(root, "file_info", {"path": "notes.txt", "include_hash": True})
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["path"], "notes.txt")
            self.assertEqual(result.data["type"], "file")
            self.assertEqual(result.data["size_bytes"], len(actual_bytes))
            self.assertEqual(result.data["line_count"], 2)
            self.assertEqual(result.data["encoding_guess"], "utf-8")
            self.assertEqual(
                result.data["hash"],
                hashlib.sha256(actual_bytes).hexdigest(),
            )
            self.assertNotIn("path_resolved", result.data)

    def test_file_info_returns_directory_metadata_without_directory_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "docs").mkdir()
            manager = _manager(root)

            result = manager.execute(_request(root, "file_info", {"path": "docs"}))

            self.assertTrue(result.success)
            self.assertEqual(result.data["path"], "docs")
            self.assertEqual(result.data["type"], "directory")
            self.assertIsNone(result.data["size_bytes"])
            self.assertEqual(result.data["hash_skipped_reason"], "directory")

    def test_file_info_sensitive_path_returns_limited_metadata_without_hash_or_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
            manager = _manager(root)

            result = manager.execute(
                _request(root, "file_info", {"path": ".env", "include_hash": True})
            )

            self.assertTrue(result.success)
            self.assertTrue(result.data["is_sensitive"])
            self.assertIsNone(result.data["hash"])
            self.assertEqual(result.data["hash_skipped_reason"], "sensitive_path")
            self.assertEqual(result.data["encoding_guess"], "not_read_sensitive")
            self.assertNotIn("TOKEN=secret", str(result.to_dict()))

    def test_file_info_binary_and_large_hash_skip_are_structured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "binary.bin").write_bytes(b"a\x00b")
            big = root / "big.txt"
            big.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
            manager = _manager(root)

            binary = manager.execute(_request(root, "file_info", {"path": "binary.bin"}))
            big_info = manager.execute(
                _request(root, "file_info", {"path": "big.txt", "include_hash": True})
            )

            self.assertTrue(binary.success)
            self.assertEqual(binary.data["encoding_guess"], "binary")
            self.assertIsNone(binary.data["line_count"])
            self.assertTrue(big_info.success)
            self.assertIsNone(big_info.data["hash"])
            self.assertEqual(big_info.data["hash_skipped_reason"], "file_too_large")
            self.assertTrue(big_info.data["line_count_truncated"])


def _manager(root: Path) -> ToolManager:
    return ToolManager(logger=NullToolLogger(), workspace_root=root)


def _request(root: Path, tool_name: str, args: dict) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        args=args,
        context=ToolCallContext(workspace_root=root, source="test"),
        options=ToolCallOptions(allow_read_workspace=True),
    )


if __name__ == "__main__":
    unittest.main()
