from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from src.tools.command_tool import CommandTool
from src.tools.errors import ToolErrorCode


class CommandToolV1Test(unittest.TestCase):
    def test_blocks_dangerous_command_without_launching(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = CommandTool().run(
                command="rm -rf .",
                cwd=".",
                purpose="dangerous",
                workspace_root=workspace,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.COMMAND_DELETE_NOT_ALLOWED.value)

    def test_blocks_workspace_escape_cwd(self):
        with tempfile.TemporaryDirectory() as workspace:
            outside = str(Path(workspace).parent)
            result = CommandTool().run(
                command="python --version",
                cwd=outside,
                purpose="escape",
                workspace_root=workspace,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)


if __name__ == "__main__":
    unittest.main()
