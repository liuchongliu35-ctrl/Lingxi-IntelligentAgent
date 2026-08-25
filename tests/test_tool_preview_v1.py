from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.tools.errors import ToolErrorCode
from src.tools.policy import ToolPolicy
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.registry import ToolRegistry, ToolSpec
from src.tools.runtime import ToolRuntime


class WritingHandler:
    def __init__(self, path: Path):
        self.path = path
        self.calls = 0

    def run(self, file_path: str, content: str, **kwargs):
        del kwargs
        self.calls += 1
        self.path.write_text(content, encoding="utf-8")
        return {"written": True}


class CommandHandler:
    def __init__(self):
        self.calls = 0

    def run(self, command: str, cwd: str = ".", **kwargs):
        del command, cwd, kwargs
        self.calls += 1
        return {"executed": True}


class ToolPreviewV1Test(unittest.TestCase):
    def test_write_dry_run_returns_preview_without_side_effect(self):
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "note.txt"
            path.write_text("before", encoding="utf-8")
            handler = WritingHandler(path)
            runtime = _runtime(
                ToolSpec(
                    name="writer",
                    description="Write.",
                    workspace_scope="write_workspace",
                    parameters_schema={
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                    required_params=["file_path", "content"],
                ),
                handler,
            )
            request = _request(
                workspace,
                "writer",
                {"file_path": "note.txt", "content": "after"},
                ToolCallOptions(dry_run=True, allow_write_workspace=True),
            )

            result = runtime.execute(request)
            preview = result.metadata["output_control"]["preview"]

            self.assertTrue(result.success)
            self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
            self.assertEqual(path.read_text(encoding="utf-8"), "before")
            self.assertEqual(handler.calls, 0)
            self.assertEqual(result.metadata["requires_confirmation"], False)
            self.assertEqual(preview["affected_resources"], ["note.txt"])
            self.assertNotIn("after", str(preview))
            self.assertIn("resource_snapshot", preview)

    def test_high_risk_dry_run_is_previewable_before_confirmation(self):
        handler = CommandHandler()
        runtime = _runtime(
            ToolSpec(
                name="command",
                description="Command.",
                risk_level="high",
                workspace_scope="command",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                    },
                },
                required_params=["command", "cwd"],
            ),
            handler,
        )

        result = runtime.execute(
            _request(
                ".",
                "command",
                {"command": "python -V", "cwd": "."},
                ToolCallOptions(dry_run=True, allow_command=True),
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
        self.assertTrue(result.metadata["requires_confirmation"])
        self.assertEqual(handler.calls, 0)

    def test_blocked_dry_run_is_not_fake_success(self):
        handler = CommandHandler()
        runtime = _runtime(
            ToolSpec(
                name="blocked",
                description="Blocked.",
                risk_level="blocked",
                workspace_scope="command",
            ),
            handler,
        )

        result = runtime.execute(
            _request(
                ".",
                "blocked",
                {},
                ToolCallOptions(dry_run=True, allow_command=True),
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.BLOCKED_BY_POLICY.value)
        self.assertEqual(handler.calls, 0)

    def test_resource_change_after_preview_returns_conflict_before_handler(self):
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "note.txt"
            path.write_text("before", encoding="utf-8")
            handler = WritingHandler(path)
            spec = ToolSpec(
                name="writer",
                description="Write.",
                workspace_scope="write_workspace",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
                required_params=["file_path", "content"],
            )
            runtime = _runtime(spec, handler)
            preview_request = _request(
                workspace,
                "writer",
                {"file_path": "note.txt", "content": "after"},
                ToolCallOptions(dry_run=True, allow_write_workspace=True),
            )
            preview_result = runtime.execute(preview_request)
            preview_hash = preview_result.metadata["output_control"]["preview_hash"]
            path.write_text("changed elsewhere", encoding="utf-8")

            execute_request = _request(
                workspace,
                "writer",
                {"file_path": "note.txt", "content": "after"},
                ToolCallOptions(
                    allow_write_workspace=True,
                    confirmed=True,
                    confirmation_id="confirmation-1",
                    preview_hash=preview_hash,
                ),
            )
            result = runtime.execute(execute_request)

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.PREVIEW_CONFLICT.value)
        self.assertEqual(handler.calls, 0)

    def test_unchanged_resource_after_preview_can_execute_with_matching_ticket(self):
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "note.txt"
            path.write_text("before", encoding="utf-8")
            handler = WritingHandler(path)
            spec = ToolSpec(
                name="writer",
                description="Write.",
                risk_level="high",
                requires_confirmation=True,
                workspace_scope="write_workspace",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
                required_params=["file_path", "content"],
            )
            runtime = _runtime(spec, handler)
            preview_result = runtime.execute(
                _request(
                    workspace,
                    "writer",
                    {"file_path": "note.txt", "content": "after"},
                    ToolCallOptions(dry_run=True, allow_write_workspace=True),
                )
            )
            preview_hash = preview_result.metadata["output_control"]["preview_hash"]

            result = runtime.execute(
                _request(
                    workspace,
                    "writer",
                    {"file_path": "note.txt", "content": "after"},
                    ToolCallOptions(
                        allow_write_workspace=True,
                        confirmed=True,
                        confirmation_id="confirmation-1",
                        preview_hash=preview_hash,
                    ),
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(result.code, ToolErrorCode.OK.value)
        self.assertEqual(handler.calls, 1)


def _runtime(spec: ToolSpec, handler) -> ToolRuntime:
    return ToolRuntime(
        registry=ToolRegistry([spec]),
        policy=ToolPolicy(),
        handlers={spec.name: handler},
    )


def _request(
    workspace_root: str,
    tool_name: str,
    args: dict,
    options: ToolCallOptions,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        args=args,
        context=ToolCallContext(workspace_root=workspace_root, source="test"),
        options=options,
    )


if __name__ == "__main__":
    unittest.main()
