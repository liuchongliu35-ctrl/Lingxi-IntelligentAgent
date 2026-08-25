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
from src.tools.shell_command_tool import ShellCommandTool
from src.tools.tool_manager import ToolManager


class ShellCommandToolTest(unittest.TestCase):
    def test_unconfirmed_shell_command_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "out.txt"
            result = _execute(
                workspace,
                {
                    "command": "echo hello > out.txt",
                    "shell": "cmd",
                    "cwd": ".",
                    "purpose": "write through shell",
                    "timeout_seconds": 10,
                },
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
            self.assertFalse(target.exists())

    def test_missing_shell_capability_allows_preview_but_blocks_execution(self):
        with tempfile.TemporaryDirectory() as workspace:
            args = {
                "command": "echo hello",
                "shell": "cmd",
                "cwd": ".",
                "purpose": "missing capability",
                "timeout_seconds": 10,
            }
            preview = _execute(
                workspace,
                args,
                allow_shell_command=False,
                dry_run=True,
            )
            result = _execute(
                workspace,
                args,
                allow_shell_command=False,
            )

            self.assertTrue(preview.success)
            self.assertEqual(preview.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.COMMAND_BLOCKED.value)

    def test_dry_run_returns_preview_without_execution(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "out.txt"
            result = _execute(
                workspace,
                {
                    "command": "echo hello > out.txt",
                    "shell": "cmd",
                    "cwd": ".",
                    "purpose": "preview shell",
                    "timeout_seconds": 10,
                },
                dry_run=True,
            )
            preview = result.metadata["output_control"]["preview"]

            self.assertTrue(result.success)
            self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
            self.assertFalse(target.exists())
            self.assertEqual(preview["shell_command"]["shell"], "cmd")
            self.assertEqual(preview["shell_command"]["cwd"], ".")
            self.assertFalse(preview["shell_command"]["will_execute"])
            self.assertTrue(preview["shell_command"]["requires_confirmation"])

    def test_confirmed_cmd_redirection_executes(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "out.txt"
            result = _confirmed_execute(
                workspace,
                {
                    "command": "echo hello > out.txt",
                    "shell": "cmd",
                    "cwd": ".",
                    "purpose": "redirect output",
                    "timeout_seconds": 10,
                },
            )

            self.assertTrue(result.success)
            self.assertTrue(target.exists())
            self.assertIn("hello", target.read_text(encoding="utf-8").lower())
            self.assertEqual(result.tool_name, "shell_command_tool")
            self.assertEqual(result.data["shell"], "cmd")
            self.assertEqual(result.data["program"], "cmd")

    def test_blocked_delete_command_uses_delete_file_guidance(self):
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "old.txt"
            target.write_text("old", encoding="utf-8")
            result = _execute(
                workspace,
                {
                    "command": "del old.txt",
                    "shell": "cmd",
                    "cwd": ".",
                    "purpose": "delete through shell",
                    "timeout_seconds": 10,
                },
                dry_run=True,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.COMMAND_DELETE_NOT_ALLOWED.value)
            self.assertTrue(target.exists())

    def test_cwd_outside_workspace_is_blocked(self):
        with tempfile.TemporaryDirectory() as workspace:
            outside = str(Path(workspace).parent)
            result = _execute(
                workspace,
                {
                    "command": "echo hello",
                    "shell": "cmd",
                    "cwd": outside,
                    "purpose": "cwd escape",
                    "timeout_seconds": 10,
                },
                dry_run=True,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)

    def test_timeout_returns_command_timeout(self):
        with tempfile.TemporaryDirectory() as workspace:
            script = Path(workspace) / "sleep.py"
            script.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            command = f"{sys.executable} sleep.py"
            result = _confirmed_execute(
                workspace,
                {
                    "command": command,
                    "shell": "cmd",
                    "cwd": ".",
                    "purpose": "timeout",
                    "timeout_seconds": 1,
                },
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.COMMAND_TIMEOUT.value)
            self.assertTrue(result.data["timed_out"])
            self.assertEqual(result.data["shell"], "cmd")

    def test_output_truncation_uses_command_execution_data_shape(self):
        with tempfile.TemporaryDirectory() as workspace:
            tool = ShellCommandTool(max_stream_chars=30, preview_chars=12)
            script = Path(workspace) / "output.py"
            script.write_text(
                "import sys\nprint('x' * 80)\nprint('y' * 80, file=sys.stderr)\nsys.exit(2)\n",
                encoding="utf-8",
            )
            command = f"{sys.executable} output.py"

            result = tool.run(
                command=command,
                shell="cmd",
                cwd=".",
                purpose="truncate",
                timeout_seconds=10,
                workspace_root=workspace,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.COMMAND_NONZERO_EXIT.value)
            self.assertTrue(result.data["stdout_truncated"])
            self.assertTrue(result.data["stderr_truncated"])
            self.assertGreater(result.data["stdout_bytes"], len(result.data["stdout"]))
            self.assertLessEqual(len(result.data["stdout_preview"]), 12)

    def test_shell_tool_alias_resolves_to_canonical_tool(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _confirmed_execute(
                workspace,
                {
                    "command": "echo alias-ok",
                    "shell": "cmd",
                    "cwd": ".",
                    "purpose": "alias",
                    "timeout_seconds": 10,
                },
                tool_name="shell_tool",
            )

            self.assertTrue(result.success)
            self.assertEqual(result.tool_name, "shell_command_tool")
            self.assertIn("alias-ok", result.data["stdout"].lower())

    def test_invalid_shell_is_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _execute(
                workspace,
                {
                    "command": "echo no",
                    "shell": "C:/Windows/System32/cmd.exe",
                    "cwd": ".",
                    "purpose": "invalid shell",
                    "timeout_seconds": 10,
                },
                dry_run=True,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.INVALID_ARGS.value)


def _execute(
    workspace: str,
    args: dict,
    *,
    tool_name: str = "shell_command_tool",
    allow_shell_command: bool = True,
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
            allow_shell_command=allow_shell_command,
            dry_run=dry_run,
            confirmed=confirmed,
            confirmation_id=confirmation_id,
            preview_hash=preview_hash,
        ),
    )
    return manager.execute(request)


def _confirmed_execute(
    workspace: str,
    args: dict,
    *,
    tool_name: str = "shell_command_tool",
):
    preview = _execute(workspace, args, tool_name=tool_name, dry_run=True)
    if not preview.success:
        return preview
    return _execute(
        workspace,
        args,
        tool_name=tool_name,
        confirmed=True,
        confirmation_id="confirmation-1",
        preview_hash=preview.metadata["output_control"]["preview_hash"],
    )


if __name__ == "__main__":
    unittest.main()
