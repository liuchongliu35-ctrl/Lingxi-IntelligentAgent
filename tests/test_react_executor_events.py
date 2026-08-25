from __future__ import annotations

import json
import unittest

from src.agent.react_executor_events import EventStream, payload_summary, sanitize_event_payload, timeline_item
from src.agent.react_executor_observation import REDACTED_VALUE
from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_protocol import ActionPacket
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


class ReActExecutorEventStreamTest(unittest.TestCase):
    def test_emit_event_creates_serializable_execution_event(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")

        event = stream.emit_event(
            "step_started",
            "Starting step.",
            {"description": "Read file"},
            task_id="task_1",
            step_id="step_1",
        )

        self.assertEqual(event.execution_id, "exec_1")
        self.assertEqual(event.plan_id, "plan_1")
        self.assertEqual(event.type, "step_started")
        self.assertEqual(event.payload["description"], "Read file")
        self.assertEqual(len(stream.events), 1)
        json.dumps(stream.to_dict(), ensure_ascii=False)

    def test_invalid_event_type_is_rejected(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")

        with self.assertRaises(ValueError):
            stream.emit_event("debug_dump", "bad")

    def test_visible_and_internal_events_are_separated(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        stream.emit_event("progress_message", "Visible.")
        stream.emit_event("system_notice", "Internal.", visible_to_user=False)

        self.assertEqual([event.message for event in stream.visible_events()], ["Visible."])
        self.assertEqual([event.message for event in stream.internal_events()], ["Internal."])
        self.assertEqual(stream.to_dict(include_internal=False)["event_count"], 1)
        self.assertEqual(stream.to_dict(include_internal=True)["event_count"], 2)

    def test_subscribe_receives_events_in_emit_order_and_can_unsubscribe(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        received = []
        unsubscribe = stream.subscribe(received.append, visible_only=True)

        first = stream.emit_event("progress_message", "Visible.")
        stream.emit_event("system_notice", "Internal.", visible_to_user=False)
        unsubscribe()
        stream.emit_event("final_answer", "Done.")

        self.assertEqual(received, [first])

    def test_visible_payload_redacts_sensitive_and_internal_fields(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")

        event = stream.emit_event(
            "tool_started",
            "Starting tool.",
            {
                "api_key": "secret",
                "safe": "ok",
                "full_prompt": "hidden prompt",
                "nested": {"authorization": "Bearer token", "traceback": "stack"},
            },
        )

        self.assertEqual(event.payload["api_key"], REDACTED_VALUE)
        self.assertEqual(event.payload["full_prompt"], REDACTED_VALUE)
        self.assertEqual(event.payload["nested"]["authorization"], REDACTED_VALUE)
        self.assertEqual(event.payload["nested"]["traceback"], REDACTED_VALUE)
        self.assertEqual(event.payload["safe"], "ok")

    def test_internal_payload_still_redacts_sensitive_but_keeps_debug_shape(self):
        event_payload = sanitize_event_payload(
            {"password": "pw", "traceback": "stack", "debug": "details"},
            visible_to_user=False,
        )

        self.assertEqual(event_payload["password"], REDACTED_VALUE)
        self.assertEqual(event_payload["traceback"], "stack")
        self.assertEqual(event_payload["debug"], "details")

    def test_long_messages_and_payload_text_are_truncated(self):
        stream = EventStream(
            execution_id="exec_1",
            plan_id="plan_1",
            max_message_chars=20,
            max_payload_text_chars=15,
        )

        event = stream.emit_event("progress_message", "x" * 80, {"text": "y" * 80})

        self.assertIn("[truncated", event.message)
        self.assertIn("[truncated", event.payload["text"])

    def test_timeline_mapping_supports_codex_like_rendering(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        command = stream.emit_event("command_started", "Running tests.", {"command": "python -B -m unittest"})
        tool = stream.emit_event("tool_finished", "Tool done.", {"tool_name": "math_calculator", "summary": "5"})
        edit = stream.emit_event("file_edited", "Edited file.", {"file_path": "a.py", "added": 2, "removed": 1})
        final = stream.emit_event("final_answer", "Done.", {})

        self.assertEqual(timeline_item(command)["render_as"], "ran_command")
        self.assertEqual(timeline_item(command)["title"], "Ran commands")
        self.assertEqual(timeline_item(tool)["render_as"], "tool_record")
        self.assertEqual(timeline_item(edit)["render_as"], "file_edit")
        self.assertEqual(timeline_item(final)["render_as"], "final_answer")
        self.assertEqual(len(stream.to_user_timeline()), 4)

    def test_started_and_finished_events_are_grouped_by_correlation_id(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        stream.emit_event(
            "command_started",
            "Running tests.",
            {"command_id": "cmd_1", "command": "python -B -m unittest", "cwd": "."},
            step_id="step_1",
        )
        stream.emit_event(
            "command_finished",
            "Tests passed.",
            {"command_id": "cmd_1", "exit_code": 0, "stdout_summary": "OK"},
            step_id="step_1",
        )
        stream.emit_event(
            "tool_started",
            "Calling tool.",
            {"tool_call_id": "tool_1", "tool_name": "math_calculator"},
            step_id="step_2",
        )
        stream.emit_event(
            "tool_finished",
            "Tool done.",
            {"tool_call_id": "tool_1", "summary": "5"},
            step_id="step_2",
        )

        timeline = stream.to_user_timeline()

        self.assertEqual(len(timeline), 2)
        self.assertEqual(timeline[0]["render_as"], "ran_command")
        self.assertEqual(timeline[0]["status"], "completed")
        self.assertEqual(timeline[0]["finished_event_id"], stream.events[1].event_id)
        self.assertEqual(timeline[0]["payload"]["exit_code"], 0)
        self.assertEqual(timeline[1]["render_as"], "tool_record")
        self.assertEqual(timeline[1]["payload"]["summary"], "5")

    def test_tool_failed_and_model_step_events_are_grouped_safely(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        stream.emit_event(
            "tool_started",
            "Calling tool.",
            {"tool_call_id": "tool_1", "tool_name": "command_tool"},
            step_id="step_1",
        )
        stream.emit_event(
            "tool_failed",
            "Command failed.",
            {"tool_call_id": "tool_1", "raw_output": "hidden full output", "code": "command_failed"},
            step_id="step_1",
        )
        stream.emit_event(
            "model_step_started",
            "Calling model.",
            {"model_call_id": "model_1", "input_summary": {"safe": "ok"}},
            step_id="step_2",
        )
        stream.emit_event(
            "model_step_finished",
            "Model done.",
            {"model_call_id": "model_1", "output_summary": "short answer"},
            step_id="step_2",
        )

        timeline = stream.to_user_timeline()
        timeline_json = json.dumps(timeline, ensure_ascii=False)

        self.assertEqual(len(timeline), 2)
        self.assertEqual(timeline[0]["render_as"], "tool_record")
        self.assertEqual(timeline[0]["status"], "failed")
        self.assertEqual(timeline[1]["render_as"], "model_step")
        self.assertEqual(stream.validate_timeline_integrity(), [])
        self.assertNotIn("hidden full output", timeline_json)
        self.assertIn(REDACTED_VALUE, timeline_json)

    def test_user_timeline_hides_full_tool_args_and_hidden_reasoning_fields(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        stream.emit_event(
            "action_selected",
            "Selected action.",
            {
                "action_args": {"file_path": "a.py", "content": "large patch"},
                "input_args": {"query": "private"},
                "thought_summary": "hidden internal reasoning",
                "safe_summary": "will edit file",
            },
        )

        item = stream.to_user_timeline()[0]

        self.assertEqual(item["payload"]["action_args"], REDACTED_VALUE)
        self.assertEqual(item["payload"]["input_args"], REDACTED_VALUE)
        self.assertEqual(item["payload"]["thought_summary"], REDACTED_VALUE)
        self.assertEqual(item["payload"]["safe_summary"], "will edit file")

    def test_step_timeline_validation_detects_missing_events(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        stream.emit_event("step_started", "Start 1.", step_id="step_1")
        stream.emit_event("step_completed", "Done 1.", step_id="step_1")
        stream.emit_event("step_started", "Start 2.", step_id="step_2")

        issues = stream.validate_step_timeline(["step_1", "step_2", "step_3"])

        self.assertEqual(
            issues,
            [
                "step_2: missing step_completed or step_failed",
                "step_3: missing step_started",
                "step_3: missing step_completed or step_failed",
            ],
        )

    def test_timeline_integrity_detects_early_final_answer_and_missing_finish(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        stream.emit_event("tool_started", "Tool.", {"tool_call_id": "tool_1"})
        stream.emit_event("final_answer", "Done.")
        stream.emit_event("observation_created", "Late observation.", {"observation_id": "obs_1"})

        issues = stream.validate_timeline_integrity()

        self.assertIn("final_answer appears before later observation_created", issues)
        self.assertIn("tool_started missing matching finish for tool_1", issues)

    def test_counts_lookup_and_step_filtering(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        stream.emit_event("tool_started", "Tool 1.", step_id="step_1")
        stream.emit_event("tool_finished", "Tool 1 done.", step_id="step_1")
        stream.emit_event("tool_started", "Tool 2.", step_id="step_2")

        self.assertEqual(stream.count_by_type()["tool_started"], 2)
        self.assertEqual(len(stream.by_type("tool_started")), 2)
        self.assertEqual(len(stream.for_step("step_1")), 2)

    def test_to_model_context_returns_recent_summaries(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1")
        stream.emit_event("progress_message", "One.", {"items": [1, 2, 3]})
        stream.emit_event("tool_finished", "Two.", {"data": {"a": 1, "b": 2}})
        stream.emit_event("final_answer", "Three.", {"text": "done"})

        context = stream.to_model_context(max_events=2)

        self.assertEqual([item["message"] for item in context], ["Two.", "Three."])
        self.assertEqual(context[0]["payload_summary"]["data"]["type"], "object")
        self.assertEqual(context[1]["payload_summary"]["text"], "done")

    def test_payload_summary_is_compact_and_sanitized(self):
        summary = payload_summary(
            {
                "api_key": "secret",
                "long": "x" * 500,
                "items": [1, 2, 3],
                "object": {"a": 1, "b": 2},
            }
        )

        self.assertEqual(summary["api_key"], REDACTED_VALUE)
        self.assertIn("[truncated", summary["long"])
        self.assertEqual(summary["items"], {"type": "list", "items": 3})
        self.assertEqual(summary["object"], {"type": "object", "keys": ["a", "b"]})

    def test_disabled_stream_records_internal_disabled_event(self):
        stream = EventStream(execution_id="exec_1", plan_id="plan_1", enabled=False)

        event = stream.emit_event("progress_message", "Should not show.", {"safe": "ok"})

        self.assertFalse(event.visible_to_user)
        self.assertEqual(event.payload, {"event_stream_disabled": True})
        self.assertEqual(stream.visible_events(), [])
        self.assertEqual(len(stream.internal_events()), 1)

    def test_main_loop_user_timeline_is_grouped_safe_and_final_answer_is_last(self):
        model = SequenceModel(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "2+3", "api_key": "secret-token"},
                        "user_visible_message": "Calculating.",
                    }
                ),
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "file_writer",
                        "action_args": {"file_path": "out.txt", "content": "5"},
                        "user_visible_message": "Writing.",
                    }
                ),
            ]
        )
        tools = TimelineToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())

        result = executor.execute(_multistep_plan(), task={}, user_input="calculate and write")

        self.assertTrue(result.success)
        visible_events = [event for event in result.events if event.visible_to_user]
        visible_types = [event.type for event in visible_events]
        self.assertEqual(visible_types.count("final_answer"), 1)
        self.assertLess(visible_types.index("observation_created"), visible_types.index("final_answer"))
        self.assertLess(visible_types.index("step_completed"), visible_types.index("final_answer"))
        self.assertEqual(result.events[-1].type, "final_answer")
        self.assertEqual(result.events[-1].visible_to_user, True)
        self.assertEqual(result.events[-1].payload["status"], "completed")

        timeline = EventStream(result.execution_id, result.plan_id, events=list(result.events)).to_user_timeline()
        timeline_json = json.dumps(timeline, ensure_ascii=False)
        self.assertEqual(EventStream(result.execution_id, result.plan_id, events=list(result.events)).validate_timeline_integrity(), [])
        self.assertIn("tool_record", {item["render_as"] for item in timeline})
        self.assertIn("action_args_summary", timeline_json)
        self.assertNotIn('"action_args": {', timeline_json)
        self.assertNotIn('"input_args": {', timeline_json)
        self.assertNotIn("raw_tool_result", timeline_json)
        self.assertNotIn("raw_observation", timeline_json)
        self.assertNotIn("prompt", timeline_json)
        self.assertNotIn("secret-token", timeline_json)
        self.assertIn(REDACTED_VALUE, timeline_json)

    def test_main_loop_retry_fallback_and_replan_events_have_stable_order(self):
        retry_result = ReActExecutor(
            model_manager=SequenceModel(
                [
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "math_calculator",
                            "action_args": {"expression": "2+3"},
                        }
                    )
                ]
            ),
            tool_manager=TimelineToolManager([ToolResult.fail("timeout", code="command_timeout"), ToolResult.ok(data="5", message="5")]),
            tool_registry=_registry(),
            retry_sleep_fn=lambda _seconds: None,
        ).execute(_single_tool_plan("math_calculator"), task={}, user_input="retry")
        self.assertEqual(_visible_type_order(retry_result, "retry_scheduled", "retry_finished", "step_completed", "final_answer"), ["retry_scheduled", "retry_finished", "step_completed", "final_answer"])

        fallback_result = ReActExecutor(
            model_manager=SequenceModel(
                [
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "primary_tool",
                            "action_args": {"query": "topic"},
                        }
                    )
                ]
            ),
            tool_manager=TimelineToolManager([ToolResult.fail("exists", code="file_exists"), ToolResult.ok(data="fallback", message="fallback ok")]),
            tool_registry=_registry(),
        ).execute(_single_tool_plan("primary_tool", fallback_tools=["fallback_tool"]), task={}, user_input="fallback")
        self.assertEqual(_visible_type_order(fallback_result, "fallback_started", "fallback_finished", "step_completed", "final_answer"), ["fallback_started", "fallback_finished", "step_completed", "final_answer"])

        replan_result = ReActExecutor(
            model_manager=SequenceModel(
                [
                    json.dumps(
                        {
                            "action_type": "request_replan",
                            "request_replan_reason": "The plan needs a different tool.",
                        }
                    )
                ]
            ),
            tool_manager=TimelineToolManager(),
            tool_registry=_registry(),
        ).execute(_single_tool_plan("math_calculator"), task={}, user_input="replan")
        self.assertEqual(_visible_type_order(replan_result, "request_replan", "final_answer"), ["request_replan", "final_answer"])

    def test_confirmation_event_sequence_is_visible_and_does_not_run_tool(self):
        executor = ReActExecutor(model_manager=None, tool_manager=TimelineToolManager(), tool_registry=_registry())
        plan = _single_tool_plan("math_calculator", requires_confirmation=True)
        context = executor._create_context(plan, task={}, user_input="confirm", history="")
        step = context.step_lookup["step_1"]
        packet = ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id="task_1",
            step_id="step_1",
            action_type="call_tool",
            action_target="math_calculator",
            action_args={"expression": "2+3"},
            requires_confirmation=True,
            user_visible_message="Run calculator?",
        )

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(TimelineToolManager.last_run_calls, [])
        self.assertEqual(_visible_types_from_context(context), ["action_selected", "confirmation_requested", "observation_created"])
        timeline_json = json.dumps(context.event_stream.to_user_timeline(), ensure_ascii=False)
        self.assertNotIn('"action_args": {', timeline_json)
        self.assertNotIn('"input_args": {', timeline_json)
        self.assertIn("action_args_summary", timeline_json)

    def test_execute_event_callback_receives_result_events_in_order(self):
        received = []
        executor = ReActExecutor(
            model_manager=SequenceModel(
                [
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "math_calculator",
                            "action_args": {"expression": "2+3"},
                        }
                    )
                ]
            ),
            tool_manager=TimelineToolManager(),
            tool_registry=_registry(),
        )

        result = executor.execute(_single_tool_plan("math_calculator"), task={}, user_input="calculate", event_callback=received.append)

        self.assertTrue(result.success)
        self.assertEqual(received, result.events)
        self.assertEqual([event.type for event in received], [event.type for event in result.events])
        self.assertEqual(received[-1].type, "final_answer")

    def test_execute_stream_yields_events_and_returns_consistent_result(self):
        direct = ReActExecutor(
            model_manager=SequenceModel(
                [
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "math_calculator",
                            "action_args": {"expression": "2+3"},
                        }
                    )
                ]
            ),
            tool_manager=TimelineToolManager(),
            tool_registry=_registry(),
        ).execute(_single_tool_plan("math_calculator"), task={}, user_input="calculate")
        stream_executor = ReActExecutor(
            model_manager=SequenceModel(
                [
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "math_calculator",
                            "action_args": {"expression": "2+3"},
                        }
                    )
                ]
            ),
            tool_manager=TimelineToolManager(),
            tool_registry=_registry(),
        )

        events, streamed = _drain_stream(stream_executor.execute_stream(_single_tool_plan("math_calculator"), task={}, user_input="calculate"))

        self.assertTrue(streamed.success)
        self.assertEqual(streamed.status, direct.status)
        self.assertEqual(streamed.success, direct.success)
        self.assertEqual(streamed.step_statuses, direct.step_statuses)
        self.assertEqual(streamed.output, direct.output)
        self.assertEqual(events, streamed.events)
        self.assertEqual([event.type for event in events], [event.type for event in streamed.events])
        self.assertEqual(events[-1].type, "final_answer")

    def test_execute_stream_stops_with_waiting_user_result_and_pending_confirmation(self):
        executor = ReActExecutor(model_manager=SequenceModel([]), tool_manager=TimelineToolManager(), tool_registry=_registry())

        events, result = _drain_stream(executor.execute_stream(_single_tool_plan("math_calculator", requires_confirmation=True), task={}, user_input="confirm"))

        self.assertFalse(result.success)
        self.assertEqual(result.status, "waiting_user")
        self.assertTrue(result.requires_user_input)
        self.assertIsNotNone(result.pending_confirmation)
        self.assertIn("confirmation_requested", [event.type for event in events])
        self.assertNotIn("tool_started", [event.type for event in events])
        self.assertEqual([event.type for event in events], [event.type for event in result.events])


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class TimelineToolManager:
    last_run_calls = []

    def __init__(self, results=None):
        self.results = list(results or [])
        self.run_calls = []
        TimelineToolManager.last_run_calls = self.run_calls

    def list_tools(self):
        return {name: f"Tool {name}." for name in _registry().tool_names()}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        if self.results:
            return self.results.pop(0)
        if tool_name == "file_writer":
            return ToolResult.ok(data={"file_path": kwargs.get("file_path"), "written": True}, message="file written")
        if tool_name == "fallback_tool":
            return ToolResult.ok(data="fallback", message="fallback ok")
        return ToolResult.ok(data="5", message="5")

    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="math_calculator",
                description="Calculate.",
                parameters_schema={"type": "object", "properties": {"expression": {"type": "string"}, "api_key": {"type": "string"}}},
                required_params=["expression"],
                risk_level="low",
            ),
            ToolSpec(
                name="file_writer",
                description="Write a file.",
                parameters_schema={"type": "object", "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}}},
                required_params=["file_path", "content"],
                risk_level="medium",
            ),
            ToolSpec(
                name="primary_tool",
                description="Primary tool.",
                parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                required_params=["query"],
                fallback_tools=["fallback_tool"],
                risk_level="low",
            ),
            ToolSpec(
                name="fallback_tool",
                description="Fallback tool.",
                parameters_schema={"type": "object", "properties": {"query": {"type": "string"}, "fallback_reason": {"type": "string"}}},
                required_params=["query"],
                risk_level="low",
            ),
        ]
    )


def _multistep_plan() -> TaskPlan:
    steps = [
        PlanStep(id="step_1", task_id="task_1", description="Calculate.", step_type="tool", tool_name="math_calculator", args={"expression": "2+3"}, output_key="calc"),
        PlanStep(id="step_2", task_id="task_1", description="Write.", step_type="tool", tool_name="file_writer", args={"file_path": "out.txt"}, depends_on=["step_1"], input_from=["calc"]),
    ]
    return TaskPlan(
        goal="calculate and write",
        mode="meso",
        steps=steps,
        task_units=[TaskUnit(id="task_1", title="Timeline", step_ids=["step_1", "step_2"])],
        available_tools=["math_calculator", "file_writer"],
        required_tools=["math_calculator", "file_writer"],
        can_execute=True,
        plan_validation_status="valid",
    )


def _single_tool_plan(tool_name: str, *, fallback_tools=None, requires_confirmation: bool = False) -> TaskPlan:
    step = PlanStep(
        id="step_1",
        task_id="task_1",
        description="Run tool.",
        step_type="tool",
        tool_name=tool_name,
        args={"expression": "2+3"} if tool_name == "math_calculator" else {"query": "topic"},
        output_key="result",
        fallback_tools=fallback_tools or [],
        retryable=True,
        max_retries=1,
        requires_confirmation=requires_confirmation,
    )
    return TaskPlan(
        goal="run tool",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task_1", title="Timeline", step_ids=["step_1"])],
        available_tools=[tool_name, *(fallback_tools or [])],
        required_tools=[tool_name],
        can_execute=True,
        plan_validation_status="valid",
    )


def _visible_type_order(result, *event_types: str) -> list[str]:
    selected = []
    wanted = set(event_types)
    for event in result.events:
        if event.visible_to_user and event.type in wanted:
            selected.append(event.type)
    return selected


def _visible_types_from_context(context) -> list[str]:
    return [event.type for event in context.event_stream.visible_events()]


def _drain_stream(stream):
    events = []
    while True:
        try:
            events.append(next(stream))
        except StopIteration as stop:
            return events, stop.value


if __name__ == "__main__":
    unittest.main()

    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
