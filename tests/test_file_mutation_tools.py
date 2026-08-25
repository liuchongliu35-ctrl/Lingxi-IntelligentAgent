from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.tool_manager import ToolManager


class FileMutationToolsTest(unittest.TestCase):
    def test_copy_file_to_new_target_without_confirmation(self):
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "source.txt"
            target = Path(workspace) / "target.txt"
            _write_text(source, "hello")

            result = _execute(
                workspace,
                "copy_file",
                {"source_path": "source.txt", "target_path": "target.txt"},
            )

            self.assertTrue(result.success)
            self.assertEqual(_read_text(source), "hello")
            self.assertEqual(_read_text(target), "hello")
            self.assertEqual(result.data["operation"], "copy")
            self.assertEqual(result.data["bytes_copied"], 5)
            self.assertFalse(result.data["overwritten"])

    def test_copy_file_existing_target_requires_overwrite_and_confirmation(self):
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "source.txt"
            target = Path(workspace) / "target.txt"
            _write_text(source, "new")
            _write_text(target, "old")

            conflict = _execute(
                workspace,
                "copy_file",
                {"source_path": "source.txt", "target_path": "target.txt"},
            )
            pending = _execute(
                workspace,
                "copy_file",
                {"source_path": "source.txt", "target_path": "target.txt", "overwrite": True},
            )
            result = _confirmed_execute(
                workspace,
                "copy_file",
                {"source_path": "source.txt", "target_path": "target.txt", "overwrite": True},
            )

            self.assertFalse(conflict.success)
            self.assertEqual(conflict.code, ToolErrorCode.FILE_ALREADY_EXISTS.value)
            self.assertFalse(pending.success)
            self.assertEqual(pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
            self.assertTrue(result.success)
            self.assertEqual(_read_text(source), "new")
            self.assertEqual(_read_text(target), "new")
            self.assertTrue(result.data["overwritten"])
            self.assertIsNotNone(result.data["target_hash_before"])

    def test_move_file_requires_confirmation_and_removes_source(self):
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "source.txt"
            target = Path(workspace) / "target.txt"
            _write_text(source, "move me")

            pending = _execute(
                workspace,
                "move_file",
                {"source_path": "source.txt", "target_path": "target.txt"},
            )
            result = _confirmed_execute(
                workspace,
                "move_file",
                {"source_path": "source.txt", "target_path": "target.txt"},
            )

            self.assertFalse(pending.success)
            self.assertEqual(pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
            self.assertTrue(result.success)
            self.assertFalse(source.exists())
            self.assertEqual(_read_text(target), "move me")
            self.assertEqual(result.data["operation"], "move")
            self.assertTrue(result.data["will_remove_source"])

    def test_rename_file_requires_plain_new_name_and_stays_in_directory(self):
        with tempfile.TemporaryDirectory() as workspace:
            docs = Path(workspace) / "docs"
            docs.mkdir()
            source = docs / "source.txt"
            _write_text(source, "rename me")

            injected = _execute(
                workspace,
                "rename_file",
                {"source_path": "docs/source.txt", "new_name": "../escape.txt"},
                dry_run=True,
            )
            result = _confirmed_execute(
                workspace,
                "rename_file",
                {"source_path": "docs/source.txt", "new_name": "renamed.txt"},
            )

            self.assertFalse(injected.success)
            self.assertEqual(injected.code, ToolErrorCode.INVALID_ARGS.value)
            self.assertTrue(result.success)
            self.assertFalse(source.exists())
            self.assertEqual(_read_text(docs / "renamed.txt"), "rename me")
            self.assertEqual(result.data["target_path"], "docs/renamed.txt")

    def test_source_missing_directories_and_same_target_are_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "source.txt"
            directory = Path(workspace) / "dir"
            _write_text(source, "same")
            directory.mkdir()

            missing = _execute(
                workspace,
                "copy_file",
                {"source_path": "missing.txt", "target_path": "target.txt"},
            )
            source_dir = _execute(
                workspace,
                "copy_file",
                {"source_path": "dir", "target_path": "target.txt"},
            )
            same = _execute(
                workspace,
                "copy_file",
                {"source_path": "source.txt", "target_path": "source.txt"},
            )

            self.assertEqual(missing.code, ToolErrorCode.FILE_NOT_FOUND.value)
            self.assertEqual(source_dir.code, ToolErrorCode.NOT_A_FILE.value)
            self.assertEqual(same.code, ToolErrorCode.FILE_CONFLICT.value)

    def test_policy_blocks_missing_capability_workspace_escape_and_sensitive_paths(self):
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "source.txt"
            _write_text(source, "secret")
            outside = str(Path(workspace).parent / "outside.txt")

            no_capability = _execute(
                workspace,
                "copy_file",
                {"source_path": "source.txt", "target_path": "target.txt"},
                allow_write_workspace=False,
            )
            escaped = _execute(
                workspace,
                "copy_file",
                {"source_path": "source.txt", "target_path": outside},
            )
            sensitive_target = _execute(
                workspace,
                "copy_file",
                {"source_path": "source.txt", "target_path": ".env"},
            )
            sensitive_source = _execute(
                workspace,
                "copy_file",
                {"source_path": ".env", "target_path": "target.txt"},
            )

            self.assertEqual(no_capability.code, ToolErrorCode.PERMISSION_DENIED.value)
            self.assertEqual(escaped.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)
            self.assertEqual(sensitive_target.code, ToolErrorCode.SENSITIVE_PATH_BLOCKED.value)
            self.assertEqual(sensitive_source.code, ToolErrorCode.SENSITIVE_PATH_BLOCKED.value)

    def test_dry_run_returns_mutation_preview_without_side_effect(self):
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "source.txt"
            target = Path(workspace) / "target.txt"
            _write_text(source, "before")

            result = _execute(
                workspace,
                "move_file",
                {"source_path": "source.txt", "target_path": "target.txt"},
                dry_run=True,
            )
            preview = result.metadata["output_control"]["preview"]

            self.assertTrue(result.success)
            self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
            self.assertTrue(source.exists())
            self.assertFalse(target.exists())
            self.assertEqual(preview["mutation"]["operation"], "move")
            self.assertEqual(preview["mutation"]["source_path"], "source.txt")
            self.assertEqual(preview["mutation"]["target_path"], "target.txt")
            self.assertTrue(preview["mutation"]["requires_confirmation"])
            self.assertTrue(preview["mutation"]["will_remove_source"])
            self.assertIn("source_hash", preview["mutation"])

    def test_preview_conflict_blocks_confirmed_overwrite_before_handler(self):
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "source.txt"
            target = Path(workspace) / "target.txt"
            _write_text(source, "new")
            _write_text(target, "old")
            args = {"source_path": "source.txt", "target_path": "target.txt", "overwrite": True}
            preview = _execute(workspace, "copy_file", args, dry_run=True)
            preview_hash = preview.metadata["output_control"]["preview_hash"]
            _write_text(source, "changed elsewhere")

            result = _execute(
                workspace,
                "copy_file",
                args,
                confirmed=True,
                confirmation_id="confirmation-1",
                preview_hash=preview_hash,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.PREVIEW_CONFLICT.value)
            self.assertEqual(_read_text(target), "old")


def _execute(
    workspace: str,
    tool_name: str,
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


def _confirmed_execute(workspace: str, tool_name: str, args: dict):
    preview = _execute(workspace, tool_name, args, dry_run=True)
    if not preview.success:
        return preview
    return _execute(
        workspace,
        tool_name,
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
