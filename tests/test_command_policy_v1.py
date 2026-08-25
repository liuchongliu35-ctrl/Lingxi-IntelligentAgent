from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.base import ToolResult
from src.tools.command_policy import (
    evaluate_command_policy,
    evaluate_shell_command_policy,
)
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.tool_logger import JsonlToolLogger
from src.tools.tool_manager import ToolManager


class CommandPolicyV1Test(unittest.TestCase):
    def test_command_delete_is_redirected_to_delete_file(self):
        with tempfile.TemporaryDirectory() as workspace:
            decision = evaluate_command_policy(
                program="rm",
                args=["old.txt"],
                command_text="rm old.txt",
                workspace_root=workspace,
            )

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.code, ToolErrorCode.COMMAND_DELETE_NOT_ALLOWED.value)
            self.assertIn("delete_file", decision.message)
            self.assertIn("delete_command", decision.risk_matches)

    def test_blocked_matrix_covers_destructive_and_permission_bypass(self):
        with tempfile.TemporaryDirectory() as workspace:
            cases = [
                evaluate_command_policy(
                    program="shutdown",
                    args=[],
                    command_text="shutdown",
                    workspace_root=workspace,
                ),
                evaluate_command_policy(
                    program="reg",
                    args=["delete", "HKCU\\Software\\Demo"],
                    command_text="reg delete HKCU\\Software\\Demo",
                    workspace_root=workspace,
                ),
                evaluate_command_policy(
                    program="chmod",
                    args=["777", "file.txt"],
                    command_text="chmod 777 file.txt",
                    workspace_root=workspace,
                ),
            ]

            self.assertTrue(all(not item.allowed for item in cases))
            self.assertEqual(
                {item.code for item in cases},
                {ToolErrorCode.COMMAND_BLOCKED.value},
            )

    def test_network_false_does_not_override_obvious_network_program(self):
        with tempfile.TemporaryDirectory() as workspace:
            decision = evaluate_command_policy(
                program="curl",
                args=["https://example.invalid"],
                command_text="curl https://example.invalid",
                network_required=False,
                workspace_root=workspace,
                tool_call_options=ToolCallOptions(allow_network=False),
            )
            allowed = evaluate_command_policy(
                program="curl",
                args=["https://example.invalid"],
                command_text="curl https://example.invalid",
                network_required=False,
                workspace_root=workspace,
                tool_call_options=ToolCallOptions(allow_network=True),
            )

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.code, ToolErrorCode.NETWORK_NOT_ALLOWED.value)
            self.assertIn("network_program", decision.risk_matches)
            self.assertTrue(allowed.allowed)
            self.assertIn("network_program", allowed.risk_matches)

    def test_command_tool_policy_ignores_model_claimed_low_risk(self):
        with tempfile.TemporaryDirectory() as workspace:
            preview = _execute_command(
                workspace,
                {
                    "program": "shutdown",
                    "args": [],
                    "cwd": ".",
                    "purpose": "model claims low risk",
                    "risk_level": "low",
                    "requires_confirmation": True,
                    "timeout_seconds": 10,
                },
                dry_run=True,
            )
            result = _execute_command(
                workspace,
                {
                    "program": "shutdown",
                    "args": [],
                    "cwd": ".",
                    "purpose": "model claims low risk",
                    "risk_level": "low",
                    "requires_confirmation": True,
                    "timeout_seconds": 10,
                },
                confirmed=True,
                confirmation_id="confirmation-1",
                preview_hash=preview.metadata["output_control"]["preview_hash"],
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.COMMAND_BLOCKED.value)

    def test_powershell_is_not_blanket_blocked_but_shell_eval_routes_to_shell_tool(self):
        with tempfile.TemporaryDirectory() as workspace:
            normal = evaluate_command_policy(
                program="powershell",
                args=["-NoProfile"],
                command_text="powershell -NoProfile",
                workspace_root=workspace,
            )
            eval_mode = evaluate_command_policy(
                program="powershell",
                args=["-Command", "Write-Output ok"],
                command_text="powershell -Command Write-Output ok",
                workspace_root=workspace,
            )

            self.assertTrue(normal.allowed)
            self.assertFalse(eval_mode.allowed)
            self.assertEqual(eval_mode.code, ToolErrorCode.SHELL_REQUIRED.value)

    def test_target_paths_and_shell_redirection_must_stay_in_workspace(self):
        with tempfile.TemporaryDirectory() as workspace:
            outside = str(Path(workspace).parent / "outside-command-policy.txt")
            command_decision = evaluate_command_policy(
                program="python",
                args=["-c", "print('x')"],
                command_text="python -c print",
                target_paths=[outside],
                workspace_root=workspace,
            )
            shell_decision = evaluate_shell_command_policy(
                command_text=f"echo x > {outside}",
                shell="cmd",
                workspace_root=workspace,
                tool_call_options=ToolCallOptions(allow_shell_command=True),
            )

            self.assertFalse(command_decision.allowed)
            self.assertEqual(command_decision.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)
            self.assertFalse(shell_decision.allowed)
            self.assertEqual(shell_decision.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)

    def test_shell_preview_blocks_network_without_network_capability(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = _execute_shell(
                workspace,
                {
                    "command": "curl https://example.invalid",
                    "shell": "cmd",
                    "cwd": ".",
                    "purpose": "network shell",
                    "timeout_seconds": 10,
                    "network_required": False,
                },
                dry_run=True,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.NETWORK_NOT_ALLOWED.value)

    def test_tools_log_records_command_output_hashes_not_full_text(self):
        with tempfile.TemporaryDirectory() as workspace:
            log_path = Path(workspace) / "tools.log"
            logger = JsonlToolLogger(log_path)
            request = ToolCallRequest(
                tool_name="command_tool",
                args={"program": "python", "args": ["-c", "print('x')"]},
                context=ToolCallContext(workspace_root=workspace, source="test"),
                options=ToolCallOptions(allow_command=True),
            )
            result = ToolResult.ok(
                data={
                    "stdout": "complete command stdout must not be logged",
                    "stderr": "complete command stderr must not be logged",
                    "stdout_preview": "stdout preview must not be logged",
                    "stderr_preview": "stderr preview must not be logged",
                    "stdout_bytes": 42,
                    "stderr_bytes": 41,
                },
                message="done",
            )

            self.assertTrue(logger.record(request, result))
            text = log_path.read_text(encoding="utf-8")
            record = json.loads(text)

            self.assertNotIn("complete command stdout must not be logged", text)
            self.assertNotIn("complete command stderr must not be logged", text)
            self.assertNotIn("stdout preview must not be logged", text)
            self.assertNotIn("stderr preview must not be logged", text)
            self.assertEqual(
                set(record["output_summary"]["data"]["stdout"]),
                {"chars", "sha256"},
            )


def _execute_command(
    workspace: str,
    args: dict,
    *,
    dry_run: bool = False,
    confirmed: bool = False,
    confirmation_id: str | None = None,
    preview_hash: str | None = None,
):
    manager = ToolManager(workspace_root=workspace)
    return manager.execute(
        ToolCallRequest(
            tool_name="command_tool",
            args=args,
            context=ToolCallContext(workspace_root=workspace, source="test"),
            options=ToolCallOptions(
                allow_command=True,
                dry_run=dry_run,
                confirmed=confirmed,
                confirmation_id=confirmation_id,
                preview_hash=preview_hash,
            ),
        )
    )


def _execute_shell(
    workspace: str,
    args: dict,
    *,
    dry_run: bool = False,
    allow_network: bool = False,
):
    manager = ToolManager(workspace_root=workspace)
    return manager.execute(
        ToolCallRequest(
            tool_name="shell_command_tool",
            args=args,
            context=ToolCallContext(workspace_root=workspace, source="test"),
            options=ToolCallOptions(
                allow_shell_command=True,
                allow_network=allow_network,
                dry_run=dry_run,
            ),
        )
    )


if __name__ == "__main__":
    unittest.main()
