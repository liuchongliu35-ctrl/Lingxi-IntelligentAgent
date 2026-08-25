from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.command_tool import CommandTool
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.tool_manager import ToolManager


class CommandToolArgvTest(unittest.TestCase):
    def test_program_args_execute_with_shell_false(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _confirmed_execute(
                workspace,
                {
                    "program": sys.executable,
                    "args": ["-c", "import sys; print(sys.argv[1])", "arg with space"],
                    "cwd": ".",
                    "purpose": "argv test",
                    "timeout_seconds": 10,
                },
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["program"], sys.executable)
            self.assertEqual(result.data["args"][-1], "arg with space")
            self.assertEqual(result.data["cwd"], ".")
            self.assertEqual(result.data["exit_code"], 0)
            self.assertEqual(result.data["stdout"].strip(), "arg with space")

    def test_compatible_command_string_is_parsed_as_argv(self):
        with tempfile.TemporaryDirectory() as workspace:
            command = f'"{sys.executable}" -c "print(123)"'

            result = _confirmed_execute(
                workspace,
                {
                    "command": command,
                    "cwd": ".",
                    "purpose": "compat command",
                    "timeout_seconds": 10,
                },
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["program"], sys.executable)
            self.assertEqual(result.data["args"], ["-c", "print(123)"])
            self.assertEqual(result.data["stdout"].strip(), "123")

    def test_command_string_keeps_quoted_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as workspace:
            scripts = Path(workspace) / "dir with spaces"
            scripts.mkdir()
            script = scripts / "script.py"
            script.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")
            command = f'"{sys.executable}" "{script}" "value with spaces"'

            result = _confirmed_execute(
                workspace,
                {
                    "command": command,
                    "cwd": ".",
                    "purpose": "quoted path",
                    "timeout_seconds": 10,
                },
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["args"][0], str(script))
            self.assertEqual(result.data["stdout"].strip(), "value with spaces")

    def test_shell_metacharacters_return_shell_required(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _confirmed_execute(
                workspace,
                {
                    "command": f'"{sys.executable}" --version | more',
                    "cwd": ".",
                    "purpose": "requires shell",
                    "timeout_seconds": 10,
                },
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.SHELL_REQUIRED.value)

    def test_delete_commands_are_redirected_to_delete_file(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _confirmed_execute(
                workspace,
                {
                    "program": "rm",
                    "args": ["old.txt"],
                    "cwd": ".",
                    "purpose": "delete",
                    "timeout_seconds": 10,
                },
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.COMMAND_DELETE_NOT_ALLOWED.value)
            self.assertIn("delete_file", result.message)

    def test_cwd_outside_workspace_is_blocked_by_policy(self):
        with tempfile.TemporaryDirectory() as workspace:
            outside = str(Path(workspace).parent)
            result = _execute(
                workspace,
                {
                    "program": sys.executable,
                    "args": ["--version"],
                    "cwd": outside,
                    "purpose": "cwd escape",
                    "timeout_seconds": 10,
                },
                dry_run=True,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)

    def test_timeout_returns_structured_command_timeout(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _confirmed_execute(
                workspace,
                {
                    "program": sys.executable,
                    "args": ["-c", "import time; time.sleep(2)"],
                    "cwd": ".",
                    "purpose": "timeout",
                    "timeout_seconds": 1,
                },
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.COMMAND_TIMEOUT.value)
            self.assertTrue(result.data["timed_out"])
            self.assertEqual(result.data["timeout_seconds"], 1)

    def test_nonzero_exit_preserves_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _confirmed_execute(
                workspace,
                {
                    "program": sys.executable,
                    "args": [
                        "-c",
                        "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
                    ],
                    "cwd": ".",
                    "purpose": "nonzero",
                    "timeout_seconds": 10,
                },
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.COMMAND_NONZERO_EXIT.value)
            self.assertEqual(result.data["exit_code"], 3)
            self.assertEqual(result.data["stdout"].strip(), "out")
            self.assertEqual(result.data["stderr"].strip(), "err")

    def test_stdout_and_stderr_are_truncated_with_byte_counts(self):
        with tempfile.TemporaryDirectory() as workspace:
            tool = CommandTool(max_stream_chars=30, preview_chars=12)
            result = tool.run(
                program=sys.executable,
                args=[
                    "-c",
                    "import sys; print('x'*80); print('y'*80, file=sys.stderr); sys.exit(2)",
                ],
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

    def test_timeout_argument_is_clamped_by_tool_spec(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _confirmed_execute(
                workspace,
                {
                    "program": sys.executable,
                    "args": ["-c", "print('ok')"],
                    "cwd": ".",
                    "purpose": "timeout clamp",
                    "timeout_seconds": 999,
                },
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["timeout_seconds"], 30)


def _execute(
    workspace: str,
    args: dict,
    *,
    dry_run: bool = False,
    confirmed: bool = False,
    confirmation_id: str | None = None,
    preview_hash: str | None = None,
    allow_network: bool = False,
    allow_write_workspace: bool = False,
):
    manager = ToolManager(workspace_root=workspace)
    request = ToolCallRequest(
        tool_name="command_tool",
        args=args,
        context=ToolCallContext(workspace_root=workspace, source="test"),
        options=ToolCallOptions(
            allow_command=True,
            allow_network=allow_network,
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
