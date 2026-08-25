from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_config import DEFAULT_REACT_EXECUTOR_CONFIG, ReActExecutorConfig
from src.agent.react_executor_logging import ReActExecutorLogger
from src.agent.react_executor_protocol import ActionPacket
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "react_executor_cases.json"
LOOP_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "react_executor_loop_cases.json"


class FixtureToolManager:
    def __init__(self, tool_results: dict | None = None):
        self.tool_results = {name: list(results) for name, results in dict(tool_results or {}).items()}
        self.run_calls = []

    def list_tools(self):
        return {name: f"Fixture tool: {name}" for name in _registry().tool_names()}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        queued = self.tool_results.get(tool_name) or []
        if queued:
            spec = queued.pop(0)
            self.tool_results[tool_name] = queued
            return _tool_result_from_spec(spec)
        if tool_name == "math_calculator":
            return ToolResult.ok(data="5", message="5")
        if tool_name == "document_parser":
            return ToolResult.ok(data=f"content from {kwargs.get('file_path')}", message="document parsed")
        if tool_name == "text_processor":
            text = kwargs.get("text", "")
            operation = kwargs.get("operation", "process")
            return ToolResult.ok(data=f"{operation}: {text}", message=f"{operation} complete")
        if tool_name == "file_writer":
            return ToolResult.ok(data={"file_path": kwargs.get("file_path"), "written": True}, message="file written")
        if tool_name == "primary_tool":
            return ToolResult.ok(data="primary data", message="primary ok")
        if tool_name == "fallback_tool":
            return ToolResult.ok(data="fallback data", message="fallback ok")
        if tool_name == "command_tool":
            return ToolResult.ok(
                data={
                    "command": kwargs.get("command"),
                    "cwd": kwargs.get("cwd"),
                    "purpose": kwargs.get("purpose"),
                    "exit_code": 0,
                    "stdout_summary": "ok",
                    "stderr_summary": "",
                },
                message="ok",
            )
        return ToolResult.ok(data=kwargs, message="ok")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class FixtureModelManager:
    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or ["model output"])
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "model output"


class SequenceActionModelManager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class ReActExecutorFixtureV1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.loop_cases = json.loads(LOOP_FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_cases(self):
        self.assertGreaterEqual(len(self.cases), 20)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self._run_case(case)

    def test_model_sequence_loop_fixture_cases(self):
        self.assertGreaterEqual(len(self.loop_cases), 20)
        for case in self.loop_cases:
            with self.subTest(case=case["id"]):
                self._run_loop_case(case)

    def test_execute_multistep_plan_passes_output_key_into_next_step_in_order(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            tool_manager = FixtureToolManager()
            model_manager = SequenceActionModelManager(
                [
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "document_parser",
                            "action_args": {"file_path": "README.md"},
                            "user_visible_message": "Reading the document.",
                        }
                    ),
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "text_processor",
                            "action_args": {"operation": "summary"},
                            "user_visible_message": "Summarizing the document.",
                        }
                    ),
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "file_writer",
                            "action_args": {"file_path": "summary.md"},
                            "user_visible_message": "Writing the summary.",
                        }
                    ),
                ]
            )
            executor = ReActExecutor(
                model_manager=model_manager,
                tool_manager=tool_manager,
                tool_registry=_registry(),
                config=_config(root, root / "logs" / "multistep.log"),
                execution_logger=ReActExecutorLogger(root / "logs" / "multistep.log"),
            )
            steps = [
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Read document.",
                    step_type="tool",
                    tool_name="document_parser",
                    args={"file_path": "README.md"},
                    output_key="file_content",
                ),
                PlanStep(
                    id="step_2",
                    task_id="task_1",
                    description="Summarize document.",
                    step_type="tool",
                    tool_name="text_processor",
                    args={"operation": "summary"},
                    depends_on=["step_1"],
                    input_from=["file_content"],
                    output_key="summary",
                ),
                PlanStep(
                    id="step_3",
                    task_id="task_1",
                    description="Write summary.",
                    step_type="tool",
                    tool_name="file_writer",
                    args={"file_path": "summary.md"},
                    depends_on=["step_2"],
                    input_from=["summary"],
                ),
            ]
            plan = TaskPlan(
                goal="read summarize write",
                mode="meso",
                steps=steps,
                task_units=[TaskUnit(id="task_1", title="Document workflow", step_ids=["step_1", "step_2", "step_3"])],
                available_tools=["document_parser", "text_processor", "file_writer"],
                required_tools=["document_parser", "text_processor", "file_writer"],
            )

            result = executor.execute(plan, task=_task_from_case({}), user_input="read and summarize")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.step_statuses, {"step_1": "completed", "step_2": "completed", "step_3": "completed"})
        self.assertEqual(len(model_manager.generate_calls), 3)
        self.assertEqual([name for name, _kwargs in tool_manager.run_calls], ["document_parser", "text_processor", "file_writer"])
        self.assertEqual(tool_manager.run_calls[1][1]["text"], "content from README.md")
        self.assertIn("summary: content from README.md", tool_manager.run_calls[2][1]["content"])

    def test_execute_does_not_run_dependent_step_after_upstream_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            tool_manager = FixtureToolManager({"document_parser": [{"success": False, "code": "file_not_found", "message": "missing"}]})
            model_manager = SequenceActionModelManager(
                [
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "document_parser",
                            "action_args": {"file_path": "missing.md"},
                        }
                    ),
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "text_processor",
                            "action_args": {"operation": "summary"},
                        }
                    ),
                ]
            )
            executor = ReActExecutor(
                model_manager=model_manager,
                tool_manager=tool_manager,
                tool_registry=_registry(),
                config=_config(root, root / "logs" / "dependency.log"),
                execution_logger=ReActExecutorLogger(root / "logs" / "dependency.log"),
            )
            step_1 = PlanStep(
                id="step_1",
                task_id="task_1",
                description="Read missing document.",
                step_type="tool",
                tool_name="document_parser",
                args={"file_path": "missing.md"},
                output_key="file_content",
            )
            step_2 = PlanStep(
                id="step_2",
                task_id="task_1",
                description="Summarize document.",
                step_type="tool",
                tool_name="text_processor",
                args={"operation": "summary"},
                depends_on=["step_1"],
                input_from=["file_content"],
            )
            plan = TaskPlan(
                goal="read then summarize",
                mode="meso",
                steps=[step_1, step_2],
                task_units=[TaskUnit(id="task_1", title="Dependency failure", step_ids=["step_1", "step_2"])],
                available_tools=["document_parser", "text_processor"],
                required_tools=["document_parser", "text_processor"],
            )

            result = executor.execute(plan, task=_task_from_case({}), user_input="read missing and summarize")

        self.assertFalse(result.success)
        self.assertIn(result.status, {"failed", "blocked", "partial_failed"})
        self.assertEqual(result.step_statuses["step_1"], "failed")
        self.assertEqual(result.step_statuses["step_2"], "blocked")
        self.assertEqual([name for name, _kwargs in tool_manager.run_calls], ["document_parser"])
        self.assertEqual(len(model_manager.generate_calls), 1)

    def _run_case(self, case: dict) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            log_path = root / "logs" / "react_executor.log"
            tool_manager = FixtureToolManager(case.get("tool_results"))
            model_manager = FixtureModelManager(case.get("model_responses"))
            executor = ReActExecutor(
                model_manager=model_manager,
                tool_manager=tool_manager,
                tool_registry=_registry(),
                config=_config(root, log_path),
                execution_logger=ReActExecutorLogger(log_path),
            )
            plan = _plan_from_case(case["plan"])
            task = _task_from_case(case.get("task") or {})
            context = None
            result = None
            last_observation = None

            for operation in case.get("operations", []):
                op_type = operation["type"]
                if op_type == "execute":
                    result = executor.execute(plan, task=task, user_input=case.get("description", "fixture"))
                    context = None
                else:
                    if context is None:
                        context = executor._create_context(plan, task=task, user_input=case.get("description", "fixture"), history="")
                    if op_type == "dispatch":
                        packet = _packet_from_operation(context, operation)
                        step = context.step_lookup.get(packet.step_id or "") if packet.step_id else None
                        last_observation = executor.dispatch_action(
                            context,
                            packet,
                            step=step,
                            confirmed=bool(operation.get("confirmed", False)),
                        )
                    elif op_type == "handle_confirmation":
                        last_observation = executor.handle_confirmation_response(
                            context,
                            approved=bool(operation.get("approved", False)),
                            reason=str(operation.get("reason", "")),
                        )
                    elif op_type == "set_step_status":
                        _set_step_status(executor, context, operation)
                    elif op_type == "build_result":
                        result = executor._build_result(
                            context,
                            status=str(operation["status"]),
                            success=bool(operation["success"]),
                        )
                    else:
                        raise AssertionError(f"Unsupported fixture operation: {op_type}")

            self.assertIsNotNone(result, f"{case['id']} did not produce ExecutionResult")
            records = _read_log_records(log_path)
            self._assert_case(case, result, context, last_observation, tool_manager, model_manager, records)

    def _run_loop_case(self, case: dict) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            log_path = root / "logs" / "react_executor_loop.log"
            tool_manager = FixtureToolManager(case.get("tool_results"))
            model_manager = SequenceActionModelManager(case.get("model_responses") or [])
            executor = ReActExecutor(
                model_manager=model_manager,
                tool_manager=tool_manager,
                tool_registry=_registry(),
                config=_config(root, log_path, case.get("config")),
                execution_logger=ReActExecutorLogger(log_path),
                retry_sleep_fn=lambda _seconds: None,
            )
            plan = _plan_from_case(case["plan"])
            task = _task_from_case(case.get("task") or {})
            execution_mode = str(case.get("execution_mode", "execute"))

            if execution_mode == "execute":
                result = executor.execute(
                    plan,
                    task=task,
                    user_input=case.get("description", "loop fixture"),
                )
            elif execution_mode == "context_resume":
                context = executor._create_context(
                    plan,
                    task=task,
                    user_input=case.get("description", "loop fixture"),
                    history="",
                )
                precheck_result = executor._run_plan_precheck(context)
                if precheck_result is not None:
                    result = precheck_result
                elif not context.step_lookup:
                    result = executor._empty_plan_result(context)
                else:
                    result = executor._execute_react_loop(context)
                resume = case.get("resume")
                if resume is not None:
                    result = executor.resume_after_confirmation(
                        context,
                        approved=bool(resume.get("approved", False)),
                        reason=str(resume.get("reason", "")),
                    )
            else:
                raise AssertionError(f"Unsupported loop execution_mode: {execution_mode}")

            records = _read_log_records(log_path)
            self._assert_loop_case(case, result, tool_manager, model_manager, records)

    def _assert_case(
        self,
        case: dict,
        result,
        context,
        last_observation,
        tool_manager: FixtureToolManager,
        model_manager: FixtureModelManager,
        records: list[dict],
    ) -> None:
        expected = case.get("expect", {})
        if "status" in expected:
            self.assertEqual(result.status, expected["status"])
        if "success" in expected:
            self.assertEqual(result.success, expected["success"])
        if "error_code" in expected:
            self.assertEqual(result.error_code, expected["error_code"])
        if "failed_step_id" in expected:
            self.assertEqual(result.failed_step_id, expected["failed_step_id"])
        if "requires_user_input" in expected:
            self.assertEqual(result.requires_user_input, expected["requires_user_input"])
        if "request_replan" in expected:
            self.assertEqual(result.request_replan, expected["request_replan"])
        if "replan_reason_contains" in expected:
            self.assertIn(expected["replan_reason_contains"], result.replan_reason or "")
        if "observation_count" in expected:
            self.assertEqual(len(result.observations), expected["observation_count"])
        if "latest_observation_code" in expected:
            self.assertIsNotNone(last_observation)
            self.assertEqual(last_observation.code, expected["latest_observation_code"])
        if "latest_observation_success" in expected:
            self.assertIsNotNone(last_observation)
            self.assertEqual(last_observation.success, expected["latest_observation_success"])
        if "fallback_used_count" in expected:
            self.assertEqual(
                sum(1 for observation in result.observations if getattr(observation, "fallback_used", False)),
                expected["fallback_used_count"],
            )
        if "tool_call_count" in expected:
            self.assertEqual(len(tool_manager.run_calls), expected["tool_call_count"])
        if "model_call_count" in expected:
            self.assertEqual(len(model_manager.generate_calls), expected["model_call_count"])
        if "tool_calls_include" in expected:
            called_names = [name for name, _kwargs in tool_manager.run_calls]
            for tool_name in expected["tool_calls_include"]:
                self.assertIn(tool_name, called_names)
        if "step_statuses" in expected:
            for step_id, status in expected["step_statuses"].items():
                self.assertEqual(result.step_statuses.get(step_id), status)
        event_types = [event.type for event in result.events]
        for event_type in expected.get("events_include", []):
            self.assertIn(event_type, event_types)
        for event_type in expected.get("events_exclude", []):
            self.assertNotIn(event_type, event_types)
        for text in expected.get("output_contains", []):
            self.assertIn(text, result.output)
        record_types = [record.get("record_type") for record in records]
        for record_type in expected.get("log_record_types_include", []):
            self.assertIn(record_type, record_types)

    def _assert_loop_case(
        self,
        case: dict,
        result,
        tool_manager: FixtureToolManager,
        model_manager: SequenceActionModelManager,
        records: list[dict],
    ) -> None:
        expected = case.get("expect", {})
        for field_name in (
            "status",
            "success",
            "error_code",
            "tool_call_count",
            "model_call_count",
            "step_statuses",
            "observations",
            "events_include",
            "log_record_types_include",
        ):
            self.assertIn(field_name, expected, f"{case['id']} must declare expect.{field_name}")

        self.assertEqual(result.status, expected["status"])
        self.assertEqual(result.success, expected["success"])
        self.assertEqual(result.error_code, expected["error_code"])
        if "request_replan" in expected:
            self.assertEqual(result.request_replan, expected["request_replan"])
        if "requires_user_input" in expected:
            self.assertEqual(result.requires_user_input, expected["requires_user_input"])
        self.assertEqual(len(tool_manager.run_calls), expected["tool_call_count"])
        self.assertEqual(len(model_manager.generate_calls), expected["model_call_count"])

        for step_id, status in expected["step_statuses"].items():
            self.assertEqual(result.step_statuses.get(step_id), status)

        observation_expectation = expected["observations"]
        self.assertEqual(len(result.observations), observation_expectation["count"])
        observation_codes = {observation.code for observation in result.observations if observation.code}
        for code in observation_expectation.get("codes_include", []):
            self.assertIn(code, observation_codes)
        if "successes" in observation_expectation:
            self.assertEqual(
                [observation.success for observation in result.observations],
                observation_expectation["successes"],
            )

        event_types = [event.type for event in result.events]
        for event_type in expected["events_include"]:
            self.assertIn(event_type, event_types)
        for event_type in expected.get("events_exclude", []):
            self.assertNotIn(event_type, event_types)

        record_types = [record.get("record_type") for record in records]
        for record_type in expected["log_record_types_include"]:
            self.assertIn(record_type, record_types)
        for text in expected.get("output_contains", []):
            self.assertIn(text, result.output)


def _tool_result_from_spec(spec: dict) -> ToolResult:
    if spec.get("success"):
        return ToolResult.ok(data=spec.get("data"), message=str(spec.get("message", "")))
    return ToolResult.fail(str(spec.get("message", "failed")), code=spec.get("code"), data=spec.get("data"))


def _packet_from_operation(context, operation: dict) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id=operation.get("task_id", "task_1"),
        step_id=operation.get("step_id"),
        action_type=operation["action_type"],
        action_target=operation.get("target"),
        action_args=dict(operation.get("args") or {}),
        requires_confirmation=bool(operation.get("requires_confirmation", False)),
        confirmation_type=operation.get("confirmation_type"),
        request_replan_reason=operation.get("request_replan_reason"),
        final_answer=operation.get("final_answer"),
        user_visible_message=operation.get("user_visible_message", ""),
    )


def _set_step_status(executor: ReActExecutor, context, operation: dict) -> None:
    step_id = str(operation["step_id"])
    state = context.step_states[step_id]
    state.status = str(operation["status"])
    state.error_code = operation.get("error_code")
    if operation.get("message"):
        state.message = str(operation["message"])
    if state.status in {"failed", "blocked"}:
        context.failed_step_id = context.failed_step_id or step_id
        context.error_code = context.error_code or state.error_code
    for task_state in context.task_states.values():
        if step_id in task_state.step_statuses:
            task_state.step_statuses[step_id] = state.status
    executor._sync_task_statuses_from_steps(context)


def _plan_from_case(plan_spec: dict) -> TaskPlan:
    steps = [_step_from_spec(step_spec) for step_spec in plan_spec.get("steps", [])]
    task_unit_specs = plan_spec.get("task_units")
    if task_unit_specs:
        task_units = [
            TaskUnit(
                id=str(unit_spec["id"]),
                title=str(unit_spec.get("title", unit_spec["id"])),
                step_ids=[str(step_id) for step_id in unit_spec.get("step_ids", [])],
            )
            for unit_spec in task_unit_specs
        ]
    else:
        task_units = [
            TaskUnit(id="task_1", title=str(plan_spec.get("task_title", "Fixture")), step_ids=[step.id for step in steps])
        ]
    available_tools = plan_spec.get("available_tools")
    if available_tools is None:
        available_tools = _registry().tool_names()
    return TaskPlan(
        goal=str(plan_spec.get("goal", "fixture")),
        mode=str(plan_spec.get("mode", "micro")),
        steps=steps,
        task_units=task_units,
        available_tools=list(available_tools),
        required_tools=[step.tool_name for step in steps if step.tool_name],
        can_execute=bool(plan_spec.get("can_execute", True)),
        plan_validation_status=str(plan_spec.get("plan_validation_status", "valid")),
        plan_validation_notes=list(plan_spec.get("plan_validation_notes", []) or []),
    )


def _task_from_case(task_spec: dict):
    values = {"action_policy": "allow", "requires_confirmation": False}
    values.update(task_spec)
    return SimpleNamespace(**values)


def _step_from_spec(step_spec: dict) -> PlanStep:
    return PlanStep(
        id=str(step_spec["id"]),
        task_id=str(step_spec.get("task_id", "task_1")),
        description=str(step_spec.get("description", "")),
        step_type=str(step_spec.get("step_type", "tool")),
        tool_name=step_spec.get("tool_name"),
        args=dict(step_spec.get("args") or {}),
        expected_output=str(step_spec.get("expected_output", "")),
        depends_on=list(step_spec.get("depends_on", []) or []),
        input_from=list(step_spec.get("input_from", []) or []),
        output_key=step_spec.get("output_key"),
        requires_confirmation=bool(step_spec.get("requires_confirmation", False)),
        confirmation_reason=step_spec.get("confirmation_reason"),
        on_failure=str(step_spec.get("on_failure", "stop")),
        retryable=bool(step_spec.get("retryable", False)),
        max_retries=int(step_spec.get("max_retries", 3)),
        fallback_tools=list(step_spec.get("fallback_tools", []) or []),
        allow_model_reasoning=bool(step_spec.get("allow_model_reasoning", False)),
        metadata=dict(step_spec.get("metadata") or {}),
    )


def _config(root: Path, log_path: Path, overrides: dict | None = None) -> ReActExecutorConfig:
    values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
    values.update(
        {
            "workspace_root": str(root),
            "react_executor_log_path": str(log_path),
            "command_confirmation_policy": "low_risk_auto",
        }
    )
    values.update(dict(overrides or {}))
    return ReActExecutorConfig(root=root, react_executor_config=values)


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="math_calculator",
                description="Calculate.",
                parameters_schema={"type": "object", "properties": {"expression": {"type": "string"}}},
                required_params=["expression"],
                risk_level="low",
                workspace_scope="none",
            ),
            ToolSpec(
                name="document_parser",
                description="Read document.",
                parameters_schema={"type": "object", "properties": {"file_path": {"type": "string"}}},
                required_params=["file_path"],
                risk_level="medium",
                workspace_scope="read_workspace",
            ),
            ToolSpec(
                name="text_processor",
                description="Process text.",
                parameters_schema={"type": "object", "properties": {"text": {"type": "string"}, "operation": {"type": "string"}}},
                required_params=["text", "operation"],
                risk_level="low",
                workspace_scope="none",
            ),
            ToolSpec(
                name="file_writer",
                description="Write file.",
                parameters_schema={"type": "object", "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}}},
                required_params=["file_path", "content"],
                risk_level="medium",
                workspace_scope="write_workspace",
            ),
            ToolSpec(
                name="primary_tool",
                description="Primary.",
                parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                required_params=["query"],
                risk_level="low",
                workspace_scope="none",
                fallback_tools=["fallback_tool"],
            ),
            ToolSpec(
                name="fallback_tool",
                description="Fallback.",
                parameters_schema={"type": "object", "properties": {"query": {"type": "string"}, "fallback_reason": {"type": "string"}}},
                required_params=["query"],
                risk_level="low",
                workspace_scope="none",
            ),
            ToolSpec(
                name="command_tool",
                description="Command.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "purpose": {"type": "string"},
                        "risk_level": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                    },
                },
                required_params=["command", "cwd", "purpose"],
                risk_level="high",
                requires_confirmation=True,
                workspace_scope="command",
            ),
        ]
    )


def _read_log_records(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
