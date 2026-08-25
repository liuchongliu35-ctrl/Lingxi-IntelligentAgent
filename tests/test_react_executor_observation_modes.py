from __future__ import annotations

import json
import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_observation import REDACTED_VALUE
from src.agent.react_executor_protocol import ActionPacket
from src.tools.base import ToolResult
from src.tools.data_types import WebSearchData, WebSearchResult
from src.tools.registry import ToolRegistry, ToolSpec


class FakeToolManager:
    def __init__(self, result: ToolResult):
        self.result = result
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return self.result


class ReActExecutorObservationModesTest(unittest.TestCase):
    def test_minimal_observation_keeps_status_without_full_data(self):
        result = ToolResult.ok(
            data={
                "path": "notes.txt",
                "content": "secret-free body",
                "content_preview": "secret-free body",
                "line_count": 1,
            },
            message="Read file notes.txt.",
            tool_name="read_file",
            metadata={"output_control": {"data_summary": "{\"path\":\"notes.txt\"}"}},
        )
        executor, manager, step = _executor("read_file", result, default_observation_mode="minimal")
        context = executor._create_context(_plan("read_file", step), task={}, user_input="read", history="")

        observation = executor.dispatch_action(context, _packet(context, "read_file", {"path": "notes.txt"}), step=step)

        self.assertTrue(observation.success)
        self.assertEqual(observation.observation_mode, "minimal")
        self.assertEqual(observation.data, {"path": "notes.txt", "line_count": 1})
        self.assertNotIn("content", json.dumps(observation.model_consumable_observation, ensure_ascii=False))
        self.assertEqual(manager.requests[0].options.observation_mode, "minimal")

    def test_standard_observation_uses_preview_and_redacts_sensitive_values(self):
        result = ToolResult.ok(
            data={
                "path": "notes.txt",
                "content": "full body should stay out of standard",
                "content_preview": "full body",
                "api_key": "plain-secret",
            },
            message="Read file notes.txt.",
            tool_name="read_file",
            metadata={
                "output_control": {
                    "preview": {"args": {"api_key": "<redacted>"}},
                    "preview_hash": "h" * 64,
                }
            },
        )
        executor, _manager, step = _executor("read_file", result)
        context = executor._create_context(_plan("read_file", step), task={}, user_input="read", history="")

        observation = executor.dispatch_action(context, _packet(context, "read_file", {"path": "notes.txt"}), step=step)
        payload = json.dumps(observation.model_consumable_observation, ensure_ascii=False)

        self.assertEqual(observation.observation_mode, "standard")
        self.assertEqual(observation.data["content_preview"], "full body")
        self.assertNotIn("full body should stay out of standard", payload)
        self.assertNotIn("plain-secret", payload)
        self.assertEqual(observation.model_consumable_observation["preview"]["args"]["api_key"], REDACTED_VALUE)
        self.assertIn("preview_hash", observation.included_fields)

    def test_full_observation_keeps_bounded_data_but_never_raw_output(self):
        result = ToolResult.ok(
            data={"content": "x" * 4000, "content_hash": "abc"},
            message="Read file big.txt.",
            tool_name="read_file",
            raw_output="raw-output-that-must-not-enter-model-context",
            raw_output_truncated=True,
            metadata={
                "output_control": {
                    "raw_ref": "artifact://tool-output/hash",
                    "artifact_ref": "artifact://tool-output/hash",
                    "raw_output_hash": "h" * 64,
                }
            },
        )
        executor, _manager, step = _executor("read_file", result, default_observation_mode="full")
        context = executor._create_context(
            _plan("read_file", step),
            task={"max_observation_chars": 1200},
            user_input="read",
            history="",
        )

        observation = executor.dispatch_action(context, _packet(context, "read_file", {"path": "big.txt"}), step=step)
        payload = json.dumps(observation.model_consumable_observation, ensure_ascii=False)

        self.assertIn(observation.observation_mode, {"full", "standard", "minimal"})
        self.assertNotIn("raw-output-that-must-not-enter-model-context", payload)
        self.assertEqual(observation.artifact_ref, "artifact://tool-output/hash")
        self.assertTrue(observation.model_consumable_observation["raw_output_truncated"])
        self.assertLessEqual(len(payload), 1200)

    def test_full_observation_includes_safe_content_when_budget_allows(self):
        result = ToolResult.ok(
            data={"path": "small.txt", "content": "small body", "content_hash": "abc"},
            message="Read file small.txt.",
            tool_name="read_file",
            raw_output="raw output stays out",
        )
        executor, _manager, step = _executor("read_file", result, default_observation_mode="full")
        context = executor._create_context(_plan("read_file", step), task={}, user_input="read", history="")

        observation = executor.dispatch_action(context, _packet(context, "read_file", {"path": "small.txt"}), step=step)
        payload = json.dumps(observation.model_consumable_observation, ensure_ascii=False)

        self.assertEqual(observation.observation_mode, "full")
        self.assertEqual(observation.data["content"], "small body")
        self.assertIn("small body", payload)
        self.assertNotIn("raw output stays out", payload)

    def test_failure_observation_preserves_error_code_type_and_retryability(self):
        result = ToolResult.fail(
            "Provider failed.",
            code="provider_error",
            data={"retry": False, "details": "short"},
            tool_name="provider_tool",
            error_type="provider",
            retryable=True,
        )
        executor, _manager, step = _executor("provider_tool", result, default_observation_mode="minimal")
        context = executor._create_context(_plan("provider_tool", step), task={}, user_input="run", history="")

        observation = executor.dispatch_action(context, _packet(context, "provider_tool", {}), step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, "provider_error")
        self.assertEqual(observation.error, "Provider failed.")
        self.assertEqual(observation.model_consumable_observation["error_type"], "provider")
        self.assertTrue(observation.model_consumable_observation["retryable"])
        self.assertEqual(observation.data["retry"], False)

    def test_web_search_observation_uses_candidate_views_without_raw_content(self):
        data = WebSearchData(
            query="agent architecture",
            provider="fake",
            provider_type="fake",
            summary="Search summary.",
            results=[
                WebSearchResult(
                    title="Result",
                    url="https://example.test",
                    snippet="Snippet",
                    content="Content",
                    raw_content="RAW CONTENT SHOULD NOT APPEAR",
                )
            ],
        )
        data.metadata["observation_views"] = {
            "minimal_data": {"query": data.query, "result_count": 1},
            "standard_data": {
                "query": data.query,
                "result_count": 1,
                "results": [{"title": "Result", "url": "https://example.test", "snippet": "Snippet"}],
            },
            "full_data": {
                "query": data.query,
                "result_count": 1,
                "results": [{"title": "Result", "url": "https://example.test", "content": "Content"}],
            },
        }
        result = ToolResult.ok(data=data, message="Found 1 result.", tool_name="web_search", provider="fake")
        executor, _manager, step = _executor("web_search", result)
        context = executor._create_context(_plan("web_search", step), task={}, user_input="search", history="")

        observation = executor.dispatch_action(
            context,
            _packet(context, "web_search", {"query": "agent architecture"}),
            step=step,
        )
        payload = json.dumps(observation.model_consumable_observation, ensure_ascii=False)

        self.assertEqual(observation.observation_mode, "standard")
        self.assertEqual(observation.data["results"][0]["title"], "Result")
        self.assertNotIn("RAW CONTENT SHOULD NOT APPEAR", payload)
        self.assertNotIn("raw_content", payload)

    def test_action_packet_observation_mode_is_control_not_tool_arg(self):
        result = ToolResult.ok(data={"path": "a.txt", "content": "body"}, message="ok", tool_name="read_file")
        executor, manager, step = _executor("read_file", result)
        context = executor._create_context(_plan("read_file", step), task={}, user_input="read", history="")

        observation = executor.dispatch_action(
            context,
            _packet(context, "read_file", {"path": "a.txt", "observation_mode": "minimal"}),
            step=step,
        )

        self.assertEqual(observation.observation_mode, "minimal")
        self.assertEqual(manager.requests[0].args, {"path": "a.txt"})


def _executor(tool_name: str, result: ToolResult, *, default_observation_mode: str = "standard"):
    spec = ToolSpec(
        name=tool_name,
        description="Test tool.",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}, "query": {"type": "string"}}, "additionalProperties": True},
        default_observation_mode=default_observation_mode,
    )
    registry = ToolRegistry([spec])
    manager = FakeToolManager(result)
    step = PlanStep(
        id="step-1",
        task_id="task-1",
        description="Execute test tool.",
        step_type="tool",
        tool_name=tool_name,
        args={},
    )
    return ReActExecutor(tool_manager=manager, tool_registry=registry), manager, step


def _plan(tool_name: str, step: PlanStep) -> TaskPlan:
    return TaskPlan(
        goal="observation modes",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task-1", title="Observation", step_ids=["step-1"])],
        available_tools=[tool_name],
        required_tools=[tool_name],
        can_execute=True,
        plan_validation_status="valid",
    )


def _packet(context, tool_name: str, args: dict) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task-1",
        step_id="step-1",
        action_type="call_tool",
        action_target=tool_name,
        action_args=dict(args),
    )


if __name__ == "__main__":
    unittest.main()
