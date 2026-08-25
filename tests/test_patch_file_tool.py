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


class PatchFileToolTest(unittest.TestCase):
    def test_replace_with_unique_old_text_requires_confirmation_then_applies(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "app.py"
            _write_text(target, "def main():\n    return 1\n")
            args = {
                "path": "app.py",
                "patches": [
                    {
                        "operation": "replace",
                        "old_text": "    return 1\n",
                        "new_text": "    return 2\n",
                    }
                ],
            }

            pending = _execute(workspace, args)
            result = _confirmed_execute(workspace, args)

            self.assertFalse(pending.success)
            self.assertEqual(pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
            self.assertTrue(result.success)
            self.assertEqual(_read_text(target), "def main():\n    return 2\n")
            self.assertEqual(result.data["patch_count"], 1)
            self.assertEqual(result.data["applied_count"], 1)
            self.assertIn("-    return 1", result.data["diff_preview"])
            self.assertIn("+    return 2", result.data["diff_preview"])

    def test_insert_before_after_and_delete_block(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            _write_text(target, "alpha\nbeta\ngamma\n")
            args = {
                "path": "note.txt",
                "patches": [
                    {
                        "operation": "insert_before",
                        "old_text": "alpha\n",
                        "new_text": "start\n",
                    },
                    {
                        "operation": "insert_after",
                        "old_text": "gamma\n",
                        "new_text": "end\n",
                    },
                    {
                        "operation": "delete_block",
                        "old_text": "beta\n",
                    },
                ],
            }

            result = _confirmed_execute(workspace, args)

            self.assertTrue(result.success)
            self.assertEqual(_read_text(target), "start\nalpha\ngamma\nend\n")
            self.assertEqual(result.data["patch_count"], 3)

    def test_line_range_must_match_exact_old_text(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            _write_text(target, "one\ntwo\nthree\n")
            valid_args = {
                "path": "note.txt",
                "patches": [
                    {
                        "operation": "replace",
                        "line_start": 2,
                        "line_end": 2,
                        "old_text": "two\n",
                        "new_text": "TWO\n",
                    }
                ],
            }
            invalid_args = {
                "path": "note.txt",
                "patches": [
                    {
                        "operation": "replace",
                        "line_start": 2,
                        "line_end": 2,
                        "old_text": "wrong\n",
                        "new_text": "TWO\n",
                    }
                ],
            }

            invalid = _execute(workspace, invalid_args, dry_run=True)
            valid = _confirmed_execute(workspace, valid_args)

            self.assertFalse(invalid.success)
            self.assertEqual(invalid.code, ToolErrorCode.PATCH_LINE_MISMATCH.value)
            self.assertTrue(valid.success)
            self.assertEqual(_read_text(target), "one\nTWO\nthree\n")

    def test_ambiguous_match_can_be_resolved_by_occurrence(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            _write_text(target, "item\nitem\nitem\n")
            ambiguous_args = {
                "path": "note.txt",
                "patches": [
                    {"operation": "replace", "old_text": "item\n", "new_text": "chosen\n"}
                ],
            }
            occurrence_args = {
                "path": "note.txt",
                "patches": [
                    {
                        "operation": "replace",
                        "old_text": "item\n",
                        "new_text": "chosen\n",
                        "occurrence": 2,
                    }
                ],
            }

            ambiguous = _execute(workspace, ambiguous_args, dry_run=True)
            result = _confirmed_execute(workspace, occurrence_args)

            self.assertFalse(ambiguous.success)
            self.assertEqual(ambiguous.code, ToolErrorCode.PATCH_AMBIGUOUS_MATCH.value)
            self.assertTrue(result.success)
            self.assertEqual(_read_text(target), "item\nchosen\nitem\n")

    def test_anchor_limits_match_range(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            _write_text(target, "[a]\nvalue=1\n[b]\nvalue=1\n")
            args = {
                "path": "note.txt",
                "patches": [
                    {
                        "operation": "replace",
                        "anchor_before": "[b]\n",
                        "old_text": "value=1\n",
                        "new_text": "value=2\n",
                    }
                ],
            }
            missing_anchor_args = {
                "path": "note.txt",
                "patches": [
                    {
                        "operation": "replace",
                        "anchor_before": "[missing]\n",
                        "old_text": "value=1\n",
                        "new_text": "value=2\n",
                    }
                ],
            }

            missing = _execute(workspace, missing_anchor_args, dry_run=True)
            result = _confirmed_execute(workspace, args)

            self.assertFalse(missing.success)
            self.assertEqual(missing.code, ToolErrorCode.PATCH_ANCHOR_NOT_FOUND.value)
            self.assertTrue(result.success)
            self.assertEqual(_read_text(target), "[a]\nvalue=1\n[b]\nvalue=2\n")

    def test_missing_old_text_and_overlapping_patches_are_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            _write_text(target, "abcdef\n")
            missing_args = {
                "path": "note.txt",
                "patches": [
                    {"operation": "replace", "old_text": "missing", "new_text": "x"}
                ],
            }
            overlap_args = {
                "path": "note.txt",
                "patches": [
                    {"operation": "replace", "old_text": "abc", "new_text": "ABC"},
                    {"operation": "replace", "old_text": "bcde", "new_text": "BCDE"},
                ],
            }

            missing = _execute(workspace, missing_args, dry_run=True)
            overlap = _execute(workspace, overlap_args, dry_run=True)

            self.assertEqual(missing.code, ToolErrorCode.PATCH_OLD_TEXT_NOT_FOUND.value)
            self.assertEqual(overlap.code, ToolErrorCode.PATCH_CONFLICT.value)
            self.assertEqual(_read_text(target), "abcdef\n")

    def test_dry_run_returns_precise_preview_without_side_effect(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            _write_text(target, "before\n")
            args = {
                "path": "note.txt",
                "patches": [
                    {"operation": "replace", "old_text": "before\n", "new_text": "after\n"}
                ],
            }

            result = _execute(workspace, args, dry_run=True)
            preview = result.metadata["output_control"]["preview"]

            self.assertTrue(result.success)
            self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
            self.assertEqual(_read_text(target), "before\n")
            self.assertEqual(preview["patch"]["path"], "note.txt")
            self.assertEqual(preview["patch"]["patch_count"], 1)
            self.assertTrue(preview["patch"]["requires_confirmation"])
            self.assertIn("before_hash", preview["patch"])
            self.assertIn("-before", preview["patch"]["diff_preview"])
            self.assertIn("+after", preview["patch"]["diff_preview"])

    def test_preview_conflict_blocks_confirmed_execution_before_patch(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            _write_text(target, "before\n")
            args = {
                "path": "note.txt",
                "patches": [
                    {"operation": "replace", "old_text": "before\n", "new_text": "after\n"}
                ],
            }
            preview = _execute(workspace, args, dry_run=True)
            preview_hash = preview.metadata["output_control"]["preview_hash"]
            _write_text(target, "changed elsewhere\n")

            result = _execute(
                workspace,
                args,
                confirmed=True,
                confirmation_id="confirmation-1",
                preview_hash=preview_hash,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.PREVIEW_CONFLICT.value)
            self.assertEqual(_read_text(target), "changed elsewhere\n")

    def test_policy_blocks_missing_write_capability_workspace_escape_and_sensitive_path(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "note.txt"
            _write_text(target, "before\n")
            outside = str(Path(workspace).parent / "outside.txt")
            args = {
                "path": "note.txt",
                "patches": [
                    {"operation": "replace", "old_text": "before\n", "new_text": "after\n"}
                ],
            }

            no_capability_preview = _execute(
                workspace,
                args,
                dry_run=True,
                allow_write_workspace=False,
            )
            no_capability_execute = _execute(
                workspace,
                args,
                allow_write_workspace=False,
            )
            escaped = _execute(
                workspace,
                {"path": outside, "patches": args["patches"]},
                dry_run=True,
            )
            sensitive = _execute(
                workspace,
                {"path": ".env", "patches": args["patches"]},
                dry_run=True,
            )

            self.assertTrue(no_capability_preview.success)
            self.assertEqual(
                no_capability_preview.code,
                ToolErrorCode.DRY_RUN_PREVIEW.value,
            )
            self.assertEqual(no_capability_execute.code, ToolErrorCode.PERMISSION_DENIED.value)
            self.assertEqual(escaped.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)
            self.assertEqual(sensitive.code, ToolErrorCode.SENSITIVE_PATH_BLOCKED.value)


def _write_text(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


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
        tool_name="patch_file",
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


if __name__ == "__main__":
    unittest.main()
