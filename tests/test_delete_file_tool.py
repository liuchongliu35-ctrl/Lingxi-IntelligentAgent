from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.tool_manager import ToolManager


class DeleteFileToolTest(unittest.TestCase):
    def test_single_file_requires_confirmation_then_deletes(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "old.txt"
            _write_text(target, "remove me")

            pending = _execute(workspace, {"path": "old.txt"})
            result = _confirmed_execute(workspace, {"path": "old.txt"})

            self.assertFalse(pending.success)
            self.assertEqual(pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
            self.assertTrue(result.success)
            self.assertFalse(target.exists())
            self.assertEqual(result.data["deleted_count"], 1)
            self.assertEqual(result.data["deleted_paths"], ["old.txt"])
            self.assertEqual(result.data["total_size_bytes"], 9)

    def test_file_paths_deletes_explicit_list_after_confirmation(self):
        with tempfile.TemporaryDirectory() as workspace:
            first = Path(workspace) / "a.txt"
            second = Path(workspace) / "b.txt"
            _write_text(first, "a")
            _write_text(second, "bb")

            result = _confirmed_execute(
                workspace,
                {"file_paths": ["a.txt", "b.txt"]},
            )

            self.assertTrue(result.success)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertEqual(result.data["deleted_count"], 2)
            self.assertEqual(result.data["total_size_bytes"], 3)

    def test_path_and_file_paths_are_exactly_one_target_form(self):
        with tempfile.TemporaryDirectory() as workspace:
            _write_text(Path(workspace) / "a.txt", "a")

            both = _execute(
                workspace,
                {"path": "a.txt", "file_paths": ["a.txt"]},
                dry_run=True,
            )
            neither = _execute(workspace, {}, dry_run=True)
            recursive = _execute(
                workspace,
                {"path": "a.txt", "recursive": True},
                dry_run=True,
            )

            self.assertEqual(both.code, ToolErrorCode.INVALID_ARGS.value)
            self.assertEqual(neither.code, ToolErrorCode.MISSING_REQUIRED_PARAM.value)
            self.assertEqual(recursive.code, ToolErrorCode.INVALID_ARGS.value)

    def test_glob_and_directory_delete_are_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            directory = Path(workspace) / "dir"
            directory.mkdir()
            _write_text(Path(workspace) / "a.log", "a")

            globbed = _execute(workspace, {"path": "*.log"}, dry_run=True)
            directory_result = _execute(workspace, {"path": "dir"}, dry_run=True)

            self.assertFalse(globbed.success)
            self.assertEqual(globbed.code, ToolErrorCode.GLOB_DELETE_NOT_ALLOWED.value)
            self.assertFalse(directory_result.success)
            self.assertEqual(
                directory_result.code,
                ToolErrorCode.DELETE_DIRECTORY_NOT_ALLOWED.value,
            )
            self.assertTrue(directory.exists())

    def test_failed_prevalidation_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as workspace:
            keep = Path(workspace) / "keep.txt"
            _write_text(keep, "keep")

            result = _confirmed_execute(
                workspace,
                {"file_paths": ["keep.txt", "missing.txt"]},
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.FILE_NOT_FOUND.value)
            self.assertTrue(keep.exists())

    def test_policy_blocks_missing_capability_workspace_escape_and_sensitive_path(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "old.txt"
            _write_text(target, "remove")
            outside = str(Path(workspace).parent / "outside.txt")

            no_capability_preview = _execute(
                workspace,
                {"path": "old.txt"},
                dry_run=True,
                allow_write_workspace=False,
            )
            no_capability_execute = _execute(
                workspace,
                {"path": "old.txt"},
                allow_write_workspace=False,
            )
            escaped = _execute(workspace, {"path": outside}, dry_run=True)
            sensitive = _execute(workspace, {"path": ".env"}, dry_run=True)

            self.assertTrue(no_capability_preview.success)
            self.assertEqual(
                no_capability_preview.code,
                ToolErrorCode.DRY_RUN_PREVIEW.value,
            )
            self.assertEqual(no_capability_execute.code, ToolErrorCode.PERMISSION_DENIED.value)
            self.assertEqual(escaped.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)
            self.assertEqual(sensitive.code, ToolErrorCode.SENSITIVE_PATH_BLOCKED.value)
            self.assertTrue(target.exists())

    def test_dry_run_returns_delete_preview_without_side_effect(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "old.txt"
            _write_text(target, "remove")

            result = _execute(workspace, {"path": "old.txt"}, dry_run=True)
            preview = result.metadata["output_control"]["preview"]

            self.assertTrue(result.success)
            self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
            self.assertTrue(target.exists())
            self.assertEqual(preview["delete"]["paths"], ["old.txt"])
            self.assertEqual(preview["delete"]["total_count"], 1)
            self.assertEqual(preview["delete"]["total_size_bytes"], 6)
            self.assertTrue(preview["delete"]["requires_confirmation"])
            self.assertIn("content_hash", preview["delete"]["targets"][0])

    def test_preview_conflict_blocks_confirmed_delete_before_handler(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "old.txt"
            _write_text(target, "before")
            args = {"path": "old.txt"}
            preview = _execute(workspace, args, dry_run=True)
            preview_hash = preview.metadata["output_control"]["preview_hash"]
            _write_text(target, "changed elsewhere")

            result = _execute(
                workspace,
                args,
                confirmed=True,
                confirmation_id="confirmation-1",
                preview_hash=preview_hash,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.PREVIEW_CONFLICT.value)
            self.assertTrue(target.exists())
            self.assertEqual(_read_text(target), "changed elsewhere")

    def test_execution_failure_reports_deleted_failed_and_pending_files(self):
        with tempfile.TemporaryDirectory() as workspace:
            first = Path(workspace) / "a.txt"
            second = Path(workspace) / "b.txt"
            third = Path(workspace) / "c.txt"
            _write_text(first, "a")
            _write_text(second, "b")
            _write_text(third, "c")
            args = {"file_paths": ["a.txt", "b.txt", "c.txt"]}
            preview = _execute(workspace, args, dry_run=True)
            preview_hash = preview.metadata["output_control"]["preview_hash"]
            original_unlink = Path.unlink

            def flaky_unlink(path: Path, *args, **kwargs):
                if path.name == "b.txt":
                    raise OSError("locked")
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", flaky_unlink):
                result = _execute(
                    workspace,
                    args,
                    confirmed=True,
                    confirmation_id="confirmation-1",
                    preview_hash=preview_hash,
                )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.FILE_DELETE_FAILED.value)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(third.exists())
            self.assertEqual(result.data["deleted_paths"], ["a.txt"])
            self.assertEqual(result.data["failed_paths"], ["b.txt"])
            self.assertEqual(
                [item["path"] for item in result.data["pending_files"]],
                ["b.txt", "c.txt"],
            )


def _execute(
    workspace: str,
    args: dict,
    *,
    allow_write_workspace: bool = True,
    dry_run: bool = False,
    confirmed: bool = False,
    confirmation_id: str | None = None,
    preview_hash: str | None = None,
):
    manager = ToolManager(workspace_root=workspace)
    request = ToolCallRequest(
        tool_name="delete_file",
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
    if not preview.success:
        return preview
    return _execute(
        workspace,
        args,
        confirmed=True,
        confirmation_id="confirmation-1",
        preview_hash=preview.metadata["output_control"]["preview_hash"],
    )


def _write_text(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


if __name__ == "__main__":
    unittest.main()
