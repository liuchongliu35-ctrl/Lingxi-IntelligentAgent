from __future__ import annotations

import unittest
import sys
import types

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode
from src.tools.policy import ToolPolicy
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.registry import ToolRegistry, ToolSpec
from src.tools.runtime import ToolRuntime
from src.tools.tool_manager import ToolManager


class RecordingLogger:
    def __init__(self):
        self.records = []

    def log(self, request, result, decision=None):
        self.records.append((request, result, decision))


class CountingHandler:
    def __init__(self, value="ok"):
        self.value = value
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.value


class TimeoutAwareHandler:
    def __init__(self):
        self.timeout_seconds = None

    def run(self, value, timeout_seconds=99):
        self.timeout_seconds = timeout_seconds
        return value


class ToolRuntimeV1Test(unittest.TestCase):
    def test_formal_request_executes_and_injects_trusted_identity(self):
        handler = CountingHandler({"value": 1})
        logger = RecordingLogger()
        runtime = _runtime(
            ToolSpec(
                name="demo",
                description="Demo.",
                parameters_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
                required_params=["value"],
            ),
            handler,
            logger=logger,
        )
        request = _request(
            "demo",
            {"value": 1},
            context=ToolCallContext(
                trace_id="trace-1",
                execution_id="execution-1",
                step_id="step-1",
                source="test",
            ),
        )

        result = runtime.execute(request)

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"value": 1})
        self.assertEqual(result.code, ToolErrorCode.OK.value)
        self.assertEqual(result.tool_name, "demo")
        self.assertEqual(result.tool_category, "general")
        self.assertEqual(result.trace_id, "trace-1")
        self.assertEqual(result.execution_id, "execution-1")
        self.assertEqual(result.step_id, "step-1")
        self.assertTrue(result.call_id.startswith("tool_call_"))
        self.assertIsNotNone(result.started_at)
        self.assertIsNotNone(result.ended_at)
        self.assertIsNotNone(result.duration_ms)
        self.assertEqual(len(handler.calls), 1)
        self.assertEqual(len(logger.records), 1)

    def test_alias_is_resolved_and_result_uses_canonical_name(self):
        handler = CountingHandler("found")
        runtime = _runtime(
            ToolSpec(
                name="canonical",
                description="Canonical.",
                aliases=["legacy"],
                parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                required_params=["query"],
            ),
            handler,
        )

        result = runtime.execute(_request("legacy", {"query": "agent"}))

        self.assertTrue(result.success)
        self.assertEqual(result.tool_name, "canonical")
        self.assertEqual(handler.calls, [{"query": "agent"}])

    def test_invalid_args_are_rejected_before_handler(self):
        handler = CountingHandler()
        runtime = _runtime(
            ToolSpec(
                name="validated",
                description="Validated.",
                parameters_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
                required_params=["value"],
            ),
            handler,
        )

        result = runtime.execute(_request("validated", {"value": "wrong"}))

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.INVALID_ARGS.value)
        self.assertEqual(handler.calls, [])

    def test_policy_denial_and_confirmation_do_not_call_handler(self):
        denied_handler = CountingHandler()
        denied_runtime = _runtime(
            ToolSpec(
                name="writer",
                description="Writer.",
                workspace_scope="write_workspace",
            ),
            denied_handler,
        )
        denied = denied_runtime.execute(_request("writer", {"file_path": "out.txt"}))

        high_handler = CountingHandler()
        high_runtime = _runtime(
            ToolSpec(name="high", description="High.", risk_level="high"),
            high_handler,
        )
        pending = high_runtime.execute(_request("high", {}))

        self.assertFalse(denied.success)
        self.assertEqual(denied.code, ToolErrorCode.PERMISSION_DENIED.value)
        self.assertFalse(pending.success)
        self.assertEqual(pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
        self.assertEqual(denied_handler.calls, [])
        self.assertEqual(high_handler.calls, [])

    def test_handler_exception_becomes_internal_error(self):
        def broken():
            raise RuntimeError("boom")

        runtime = _runtime(
            ToolSpec(name="broken", description="Broken."),
            broken,
        )

        result = runtime.execute(_request("broken", {}))

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.INTERNAL_ERROR.value)
        self.assertEqual(result.error_type, "internal")
        self.assertIn("boom", result.error)
        self.assertIsNotNone(result.call_id)

    def test_tool_result_from_handler_is_normalized_with_runtime_identity(self):
        handler_result = ToolResult.ok(
            data={"ok": True},
            tool_name="forged",
            trace_id="forged-trace",
        )
        runtime = _runtime(
            ToolSpec(name="real_name", description="Real."),
            lambda: handler_result,
        )

        result = runtime.execute(_request("real_name", {}))

        self.assertTrue(result.success)
        self.assertEqual(result.tool_name, "real_name")
        self.assertIsNone(result.trace_id)
        self.assertEqual(result.code, ToolErrorCode.OK.value)
        self.assertNotEqual(result.call_id, handler_result.call_id)

    def test_timeout_is_clamped_to_tool_spec_and_passed_only_when_declared(self):
        handler = TimeoutAwareHandler()
        runtime = _runtime(
            ToolSpec(name="timeout", description="Timeout.", timeout_seconds=7),
            handler,
        )

        result = runtime.execute(
            _request(
                "timeout",
                {"value": "ok"},
                options=ToolCallOptions(timeout_seconds=99),
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(handler.timeout_seconds, 7)
        self.assertEqual(result.metadata["runtime"]["timeout_seconds"], 7)

    def test_dry_run_does_not_call_handler(self):
        handler = CountingHandler()
        runtime = _runtime(
            ToolSpec(
                name="previewable",
                description="Previewable.",
                supports_dry_run=True,
            ),
            handler,
        )

        result = runtime.execute(
            _request("previewable", {}, options=ToolCallOptions(dry_run=True))
        )

        self.assertTrue(result.success)
        self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
        self.assertEqual(handler.calls, [])
        self.assertIn("preview", result.metadata)

    def test_unsupported_dry_run_does_not_call_handler(self):
        handler = CountingHandler()
        runtime = _runtime(
            ToolSpec(name="ordinary", description="Ordinary."),
            handler,
        )

        result = runtime.execute(
            _request("ordinary", {}, options=ToolCallOptions(dry_run=True))
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.DRY_RUN_NOT_SUPPORTED.value)
        self.assertEqual(handler.calls, [])

    def test_missing_handler_is_structured_failure(self):
        runtime = ToolRuntime(
            registry=ToolRegistry([ToolSpec(name="unimplemented", description="Missing.")]),
            policy=ToolPolicy(),
            handlers={},
        )

        result = runtime.execute(_request("unimplemented", {}))

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.TOOL_NOT_IMPLEMENTED.value)
        self.assertEqual(result.tool_name, "unimplemented")

    def test_run_tool_compatibility_entry_uses_runtime_and_wraps_raw_result(self):
        manager = ToolManager(tools={"legacy": CountingHandler({"received": True})})

        result = manager.run_tool("legacy", value=1)

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"received": True})
        self.assertEqual(result.tool_name, "legacy")
        self.assertEqual(result.message, "{'received': True}")
        self.assertEqual(result.code, ToolErrorCode.OK.value)

    def test_run_tool_missing_and_no_run_keep_legacy_error_shape(self):
        class NoRun:
            pass

        manager = ToolManager(tools={"no_run": NoRun()})

        missing = manager.run_tool("missing")
        no_run = manager.run_tool("no_run")

        self.assertFalse(missing.success)
        self.assertEqual(missing.error, "Tool not found: missing")
        self.assertEqual(missing.code, ToolErrorCode.TOOL_NOT_FOUND.value)
        self.assertFalse(no_run.success)
        self.assertEqual(no_run.error, "Tool has no run method: no_run")
        self.assertEqual(no_run.code, ToolErrorCode.TOOL_NOT_IMPLEMENTED.value)


def _runtime(spec, handler, *, logger=None):
    registry = ToolRegistry([spec])
    handlers = {spec.name: handler}
    return ToolRuntime(
        registry=registry,
        policy=ToolPolicy(),
        handlers=handlers,
        logger=logger,
    )


def _request(
    tool_name,
    args,
    *,
    context=None,
    options=None,
):
    return ToolCallRequest(
        tool_name=tool_name,
        args=args,
        context=context or ToolCallContext(source="test"),
        options=options or ToolCallOptions(),
    )


if __name__ == "__main__":
    unittest.main()
