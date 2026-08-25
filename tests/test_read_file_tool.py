from __future__ import annotations

import hashlib
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
from src.tools.tool_logger import JsonlToolLogger, NullToolLogger
from src.tools.tool_manager import ToolManager


class ReadFileToolTest(unittest.TestCase):
    def tearDown(self) -> None:
        clear_tools_config_cache()

    def test_read_file_reads_utf8_text_with_hash_preview_and_line_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content = "alpha\nbeta\nlast"
            target = root / "notes.txt"
            target.write_bytes(content.encode("utf-8"))
            manager = _manager(root)

            result = manager.execute(_request(root, {"path": "notes.txt"}))

            self.assertTrue(result.success)
            self.assertEqual(result.tool_name, "read_file")
            self.assertEqual(result.data["path"], "notes.txt")
            self.assertEqual(result.data["encoding"], "utf-8")
            self.assertEqual(result.data["size_bytes"], len(target.read_bytes()))
            self.assertEqual(result.data["line_count"], 3)
            self.assertEqual(result.data["content"], content)
            self.assertEqual(result.data["content_preview"], content)
            self.assertFalse(result.data["content_truncated"])
            self.assertEqual(
                result.data["content_hash"],
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )
            self.assertNotIn("path_resolved", result.data)

    def test_read_file_handles_empty_file_and_safe_encoding_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "empty.txt").write_text("", encoding="utf-8")
            (root / "gb.txt").write_bytes("你好".encode("gb18030"))
            manager = _manager(root)

            empty = manager.execute(_request(root, {"path": "empty.txt"}))
            gb = manager.execute(_request(root, {"path": "gb.txt"}))

            self.assertTrue(empty.success)
            self.assertEqual(empty.data["content"], "")
            self.assertEqual(empty.data["line_count"], 0)
            self.assertTrue(gb.success)
            self.assertEqual(gb.data["encoding"], "gb18030")
            self.assertEqual(gb.data["content"], "你好")

    def test_read_file_max_bytes_truncates_returned_content_without_changing_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "story.txt"
            target.write_text("abcdef", encoding="utf-8")
            manager = _manager(root)

            result = manager.execute(
                _request(root, {"path": "story.txt", "max_bytes": 3})
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["content"], "abc")
            self.assertEqual(result.data["line_count"], 1)
            self.assertTrue(result.data["content_truncated"])
            self.assertEqual(
                result.data["content_hash"],
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )

    def test_read_file_uses_configured_size_limits_and_returns_large_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_defaults(
                root,
                {
                    "read_file_small_bytes": 8,
                    "read_file_medium_bytes": 16,
                    "read_file_hard_bytes": 64,
                    "read_file_preview_chars": 10,
                },
            )
            (root / "large.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
            (root / "hard.txt").write_text("x" * 65, encoding="utf-8")
            manager = _manager(root)

            large = manager.execute(_request(root, {"path": "large.txt"}))
            hard = manager.execute(_request(root, {"path": "hard.txt"}))

            self.assertFalse(large.success)
            self.assertEqual(large.code, ToolErrorCode.FILE_TOO_LARGE.value)
            self.assertEqual(large.data["line_count"], 3)
            self.assertEqual(large.data["too_large_reason"], "medium_limit_exceeded")
            self.assertIn("read_file_chunk", large.data["recommended_tools"])
            self.assertIsNone(large.data["content"])
            self.assertFalse(hard.success)
            self.assertEqual(hard.code, ToolErrorCode.FILE_TOO_LARGE.value)
            self.assertEqual(hard.data["too_large_reason"], "hard_limit_exceeded")
            self.assertIsNone(hard.data["line_count"])

    def test_read_file_rejects_binary_and_invalid_paths_structurally(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"
            root.mkdir()
            outside = root.parent / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"a\x00b")
            (root / "docs").mkdir()
            manager = _manager(root)

            binary = manager.execute(_request(root, {"path": "binary.bin"}))
            missing = manager.execute(_request(root, {"path": "missing.txt"}))
            not_file = manager.execute(_request(root, {"path": "docs"}))
            escaped = manager.execute(_request(root, {"path": "../outside.txt"}))

            self.assertFalse(binary.success)
            self.assertEqual(binary.code, ToolErrorCode.BINARY_FILE_NOT_SUPPORTED.value)
            self.assertIsNone(binary.data["content"])
            self.assertFalse(missing.success)
            self.assertEqual(missing.code, ToolErrorCode.FILE_NOT_FOUND.value)
            self.assertFalse(not_file.success)
            self.assertEqual(not_file.code, ToolErrorCode.NOT_A_FILE.value)
            self.assertFalse(escaped.success)
            self.assertEqual(escaped.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)

    def test_read_file_sensitive_file_requires_confirmation_ticket(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
            manager = _manager(root)

            unconfirmed = manager.execute(_request(root, {"path": ".env"}))
            model_claim = manager.execute(
                _request(root, {"path": ".env", "confirmed": True})
            )
            dry_run = manager.execute(
                _request(
                    root,
                    {"path": ".env"},
                    options=ToolCallOptions(
                        dry_run=True,
                        allow_read_workspace=True,
                    ),
                )
            )
            preview_hash = dry_run.metadata["output_control"]["preview_hash"]
            confirmed = manager.execute(
                _request(
                    root,
                    {"path": ".env"},
                    options=ToolCallOptions(
                        allow_read_workspace=True,
                        confirmed=True,
                        confirmation_id="confirmation-1",
                        preview_hash=preview_hash,
                    ),
                )
            )

            self.assertFalse(unconfirmed.success)
            self.assertEqual(unconfirmed.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
            self.assertNotIn("TOKEN=secret", str(unconfirmed.to_dict()))
            self.assertFalse(model_claim.success)
            self.assertEqual(model_claim.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
            self.assertTrue(dry_run.success)
            self.assertEqual(dry_run.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
            self.assertTrue(confirmed.success)
            self.assertTrue(confirmed.data["is_sensitive"])
            self.assertEqual(confirmed.data["content"], "TOKEN=secret")

    def test_read_file_log_records_summary_without_full_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_body = "full body should stay out of tools log"
            (root / "note.txt").write_text(secret_body, encoding="utf-8")
            log_path = root / "logs" / "tools.log"
            manager = ToolManager(
                logger=JsonlToolLogger(log_path),
                workspace_root=root,
            )

            result = manager.execute(_request(root, {"path": "note.txt"}))
            text = log_path.read_text(encoding="utf-8")
            record = json.loads(text)

            self.assertTrue(result.success)
            self.assertNotIn(secret_body, text)
            self.assertEqual(record["tool_name"], "read_file")
            self.assertEqual(record["output_summary"]["data"]["content"]["chars"], len(secret_body))


def _manager(root: Path) -> ToolManager:
    return ToolManager(logger=NullToolLogger(), workspace_root=root)


def _request(
    root: Path,
    args: dict,
    *,
    options: ToolCallOptions | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name="read_file",
        args=args,
        context=ToolCallContext(workspace_root=root, source="test"),
        options=options or ToolCallOptions(allow_read_workspace=True),
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
