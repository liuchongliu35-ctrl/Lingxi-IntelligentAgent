from __future__ import annotations

import tempfile
import unittest
import sys
import types
from pathlib import Path

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.tool_manager import ToolManager


class WriteFileToolTest(unittest.TestCase):
    def test_create_new_file(self):
        with tempfile.TemporaryDirectory() as workspace:
            (Path(workspace) / "notes").mkdir()
            result = _execute(
                workspace,
                {"path": "notes/new.txt", "content": "hello", "write_mode": "create"},
            )

            target = Path(workspace) / "notes" / "new.txt"
            self.assertTrue(result.success)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")
            self.assertTrue(result.data["created"])
            self.assertEqual(result.data["bytes_written"], 5)
            self.assertIsNone(result.data["content_hash_before"])
            self.assertIsNotNone(result.data["content_hash_after"])

    def test_create_existing_returns_conflict(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            target.write_text("before", encoding="utf-8")

            result = _execute(
                workspace,
                {"path": "note.txt", "content": "after", "write_mode": "create"},
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.FILE_ALREADY_EXISTS.value)
            self.assertEqual(target.read_text(encoding="utf-8"), "before")

    def test_overwrite_existing_requires_confirmation_and_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            target.write_text("before", encoding="utf-8")

            pending = _execute(
                workspace,
                {"path": "note.txt", "content": "after", "write_mode": "overwrite"},
            )
            result = _confirmed_execute(
                workspace,
                {"path": "note.txt", "content": "after", "write_mode": "overwrite"},
            )

            self.assertFalse(pending.success)
            self.assertEqual(pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
            self.assertTrue(result.success)
            self.assertEqual(target.read_text(encoding="utf-8"), "after")
            self.assertTrue(result.data["overwritten"])
            self.assertIsNotNone(result.data["content_hash_before"])
            self.assertIsNotNone(result.data["content_hash_after"])

    def test_overwrite_missing_returns_file_not_found_after_confirmation(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _confirmed_execute(
                workspace,
                {"path": "missing.txt", "content": "after", "write_mode": "overwrite"},
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.FILE_NOT_FOUND.value)
            self.assertFalse((Path(workspace) / "missing.txt").exists())

    def test_append_existing_and_append_missing(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            target.write_text("before", encoding="utf-8")

            appended = _execute(
                workspace,
                {"path": "note.txt", "content": "+after", "write_mode": "append"},
            )
            missing = _execute(
                workspace,
                {"path": "missing.txt", "content": "after", "write_mode": "append"},
            )

            self.assertTrue(appended.success)
            self.assertEqual(target.read_text(encoding="utf-8"), "before+after")
            self.assertTrue(appended.data["appended"])
            self.assertFalse(missing.success)
            self.assertEqual(missing.code, ToolErrorCode.FILE_NOT_FOUND.value)

    def test_create_or_overwrite_handles_missing_and_existing_as_high_risk(self):
        with tempfile.TemporaryDirectory() as workspace:
            created = _confirmed_execute(
                workspace,
                {"path": "new.txt", "content": "new", "write_mode": "create_or_overwrite"},
            )
            overwritten = _confirmed_execute(
                workspace,
                {"path": "new.txt", "content": "changed", "write_mode": "create_or_overwrite"},
            )

            self.assertTrue(created.success)
            self.assertTrue(created.data["created"])
            self.assertTrue(overwritten.success)
            self.assertTrue(overwritten.data["overwritten"])
            self.assertEqual((Path(workspace) / "new.txt").read_text(encoding="utf-8"), "changed")

    def test_invalid_write_mode_and_encoding_are_structured_failures(self):
        with tempfile.TemporaryDirectory() as workspace:
            invalid_mode = _execute(
                workspace,
                {"path": "note.txt", "content": "x", "write_mode": "bad"},
            )
            invalid_encoding = _execute(
                workspace,
                {
                    "path": "note.txt",
                    "content": "x",
                    "write_mode": "create",
                    "encoding": "no-such-encoding",
                },
            )

            self.assertFalse(invalid_mode.success)
            self.assertEqual(invalid_mode.code, ToolErrorCode.INVALID_ARGS.value)
            self.assertFalse(invalid_encoding.success)
            self.assertEqual(invalid_encoding.code, ToolErrorCode.INVALID_ENCODING.value)

    def test_missing_parent_is_not_created_implicitly(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _execute(
                workspace,
                {"path": "missing/note.txt", "content": "x", "write_mode": "create"},
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.PARENT_DIRECTORY_NOT_FOUND.value)
            self.assertFalse((Path(workspace) / "missing").exists())

    def test_policy_blocks_workspace_escape_sensitive_path_and_missing_write_capability(self):
        with tempfile.TemporaryDirectory() as workspace:
            outside = str(Path(workspace).parent / "outside.txt")
            no_capability = _execute(
                workspace,
                {"path": "note.txt", "content": "x", "write_mode": "create"},
                allow_write_workspace=False,
            )
            escaped = _execute(
                workspace,
                {"path": outside, "content": "x", "write_mode": "create"},
            )
            sensitive = _execute(
                workspace,
                {"path": ".env", "content": "SECRET=1", "write_mode": "create"},
            )

            self.assertEqual(no_capability.code, ToolErrorCode.PERMISSION_DENIED.value)
            self.assertEqual(escaped.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)
            self.assertEqual(sensitive.code, ToolErrorCode.SENSITIVE_PATH_BLOCKED.value)

    def test_dry_run_returns_write_preview_without_side_effect(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            target.write_text("before", encoding="utf-8")

            result = _execute(
                workspace,
                {"path": "note.txt", "content": "after", "write_mode": "overwrite"},
                dry_run=True,
            )
            preview = result.metadata["output_control"]["preview"]

            self.assertTrue(result.success)
            self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
            self.assertEqual(target.read_text(encoding="utf-8"), "before")
            self.assertEqual(preview["write"]["path"], "note.txt")
            self.assertEqual(preview["write"]["write_mode"], "overwrite")
            self.assertEqual(preview["write"]["old_size_bytes"], 6)
            self.assertEqual(preview["write"]["new_size_bytes"], 5)
            self.assertTrue(preview["write"]["requires_confirmation"])
            self.assertIn("content_hash", preview["write"])

    def test_preview_conflict_blocks_confirmed_overwrite_before_handler(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            target.write_text("before", encoding="utf-8")
            args = {"path": "note.txt", "content": "after", "write_mode": "overwrite"}
            preview = _execute(workspace, args, dry_run=True)
            preview_hash = preview.metadata["output_control"]["preview_hash"]
            target.write_text("changed elsewhere", encoding="utf-8")

            result = _execute(
                workspace,
                args,
                confirmed=True,
                confirmation_id="confirmation-1",
                preview_hash=preview_hash,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.PREVIEW_CONFLICT.value)
            self.assertEqual(target.read_text(encoding="utf-8"), "changed elsewhere")

    def test_file_writer_alias_accepts_legacy_args_through_formal_runtime(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _execute(
                workspace,
                {"file_path": "legacy.txt", "content": "legacy", "overwrite": False},
                tool_name="file_writer",
            )

            self.assertTrue(result.success)
            self.assertEqual(result.tool_name, "write_file")
            self.assertEqual((Path(workspace) / "legacy.txt").read_text(encoding="utf-8"), "legacy")


def _execute(
    workspace: str,
    args: dict,
    *,
    tool_name: str = "write_file",
    allow_write_workspace: bool = True,
    dry_run: bool = False,
    confirmed: bool = False,
    confirmation_id: str | None = None,
    preview_hash: str | None = None,
):
    manager = ToolManager(workspace_root=workspace)
    request = ToolCallRequest(
        tool_name=tool_name,
        args=args,
        context=ToolCallContext(workspace_root=workspace, source="test"),
        options=ToolCallOptions(
            allow_write_workspace=allow_write_workspace,
            dry_run=dry_run,
            confirmed=confirmed,
            confirmation_id=confirmation_id,
            preview_hash=preview_hash,
        ),
    )
    return manager.execute(request)


def _confirmed_execute(workspace: str, args: dict):
    preview = _execute(workspace, args, dry_run=True)
    return _execute(
        workspace,
        args,
        confirmed=True,
        confirmation_id="confirmation-1",
        preview_hash=preview.metadata["output_control"]["preview_hash"],
    )


if __name__ == "__main__":
    unittest.main()
