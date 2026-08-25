from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.agent.executor import Executor
from src.agent.planner import Planner
from src.agent.react_agent import ReactAgent
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_config import DEFAULT_REACT_EXECUTOR_CONFIG, ReActExecutorConfig
from src.agent.react_executor_protocol import ExecutionResult as ReactExecutionResult
from src.tools.base import ToolResult


class FakeModelManager:
    def __init__(self):
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if '"tool_calls_allowed": false' in prompt or '"mode": "chat"' in prompt:
            return json.dumps(
                {
                    "action_type": "finish",
                    "final_answer": "model response",
                    "user_visible_message": "Answering.",
                }
            )
        return json.dumps(
            {
                "action_type": "call_tool",
                "action_target": "math_calculator",
                "action_args": {"expression": "2+3"},
                "user_visible_message": "Calculating.",
            }
        )


class FakeToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {
            "math_calculator": "Calculate expressions.",
            "document_parser": "Read files.",
            "text_processor": "Process text.",
            "file_writer": "Write files.",
            "search_tool": "Search information.",
        }

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return ToolResult.ok(data="tool response", message="tool response")

    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)


class FakeShortTermMemory:
    def __init__(self):
        self.messages = []

    def add_message(self, role: str, content: str) -> None:
        self.messages.append((role, content))

    def get_history_text(self) -> str:
        return "\n".join(f"{role}: {content}" for role, content in self.messages)


class FakeAnalyzer:
    def __init__(self, task):
        self.task = task
        self.calls = []

    def analyze(self, user_input: str):
        self.calls.append(user_input)
        return self.task


class ReactAgentWithReActExecutorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.model_manager = FakeModelManager()
        self.tool_manager = FakeToolManager()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_react_agent_uses_react_executor(self):
        agent = self._agent(_task())

        response = agent.run("calculate 2+3")

        self.assertEqual(agent.executor_type, "react")
        self.assertIsInstance(agent.executor, ReActExecutor)
        self.assertIn("Status: completed.", response)
        self.assertIn("tool response", response)
        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])
        self.assertEqual(len(self.model_manager.generate_calls), 1)
        self.assertEqual([role for role, _content in agent.short_term_memory.messages], ["user", "assistant"])

    def test_explicit_legacy_executor_type_keeps_old_executor_path(self):
        agent = self._agent(_task(), executor_type="legacy")

        response = agent.run("calculate 2+3")

        self.assertEqual(agent.executor_type, "legacy")
        self.assertIsInstance(agent.executor, Executor)
        self.assertEqual(response, "tool response")
        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])

    def test_environment_executor_type_can_switch_back_to_legacy(self):
        with patch.dict(os.environ, {"EXECUTOR_TYPE": "legacy"}):
            agent = self._agent(_task(), executor_type=None)

        response = agent.run("calculate 2+3")

        self.assertEqual(agent.executor_type, "legacy")
        self.assertIsInstance(agent.executor, Executor)
        self.assertEqual(response, "tool response")
        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])

    def test_injected_executor_still_takes_priority(self):
        injected = RecordingExecutor()
        agent = self._agent(_task(), executor=injected, executor_type="react")

        response = agent.run("calculate 2+3")

        self.assertIs(agent.executor, injected)
        self.assertEqual(response, "injected response")
        self.assertEqual(len(injected.calls), 1)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_run_with_result_exposes_react_events_and_observations(self):
        agent = self._agent(_task())

        result = agent.run_with_result("calculate 2+3")

        self.assertIsInstance(result, ReactExecutionResult)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.events)
        self.assertTrue(result.observations)
        self.assertIsNone(result.pending_confirmation)
        self.assertEqual(agent.short_term_memory.messages[-1], ("assistant", result.output))

    def test_run_with_result_supports_chat_plan(self):
        agent = self._agent(_task(mode="chat", intent=["chat"], intent_sequence=["chat"]))

        result = agent.run_with_result("explain this")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIn("model response", result.output)
        self.assertTrue(result.events)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_run_with_result_exposes_pending_confirmation(self):
        agent = self._agent(
            _task(
                action_policy="confirm",
                requires_confirmation=True,
                confirmation_reason="delete_file",
                intent=["delete_file"],
                intent_sequence=["delete_file"],
                execution_strategy="meso",
            )
        )

        result = agent.run_with_result("delete report.txt")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "waiting_user")
        self.assertTrue(result.requires_user_input)
        self.assertIsNotNone(result.pending_confirmation)
        self.assertEqual(result.pending_confirmation.confirmation_type, "confirmation")

    def test_run_with_result_adapts_legacy_result_to_structured_result(self):
        agent = self._agent(_task(), executor_type="legacy")

        result = agent.run_with_result("calculate 2+3")

        self.assertIsInstance(result, ReactExecutionResult)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output, "tool response")
        self.assertEqual(result.events, [])
        self.assertEqual(result.observations, [])
        self.assertIsNone(result.pending_confirmation)

    def test_run_stream_returns_visible_events_and_final_result(self):
        agent = self._agent(_task())
        stream = agent.run_stream("calculate 2+3")
        events = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as stop:
                result = stop.value
                break

        self.assertIsInstance(result, ReactExecutionResult)
        self.assertTrue(result.success)
        self.assertTrue(events)
        self.assertTrue(all(event.visible_to_user for event in events))
        self.assertEqual(agent.short_term_memory.messages[-1], ("assistant", result.output))

    def test_react_executor_main_path_short_circuits_special_policy_plans(self):
        cases = [
            (
                _task(action_policy="block", risk_flags=["dangerous_command"], execution_strategy="meso"),
                "delete system files",
                "Task policy blocks execution",
                "Status: blocked.",
            ),
            (
                _task(
                    requires_clarification=True,
                    clarification_questions=["Which file?"],
                    missing_parameters=["file_path"],
                    execution_strategy="macro",
                ),
                "read file",
                "Which file?",
                "Status: waiting_user.",
            ),
            (
                _task(
                    action_policy="confirm",
                    requires_confirmation=True,
                    confirmation_reason="delete_file",
                    intent=["delete_file"],
                    intent_sequence=["delete_file"],
                    execution_strategy="meso",
                ),
                "delete report.txt",
                "Confirmation required before execution",
                "Status: waiting_user.",
            ),
            (
                _task(mode="chat", intent=["chat"], intent_sequence=["chat"], execution_strategy="meso"),
                "only explain how to do it",
                "model response",
                "Status: completed.",
            ),
            (
                _task(
                    tool_strategy="blocked_missing_tools",
                    missing_tools=["excel_parser"],
                    execution_strategy="meso",
                ),
                "analyze excel",
                "Missing tools: excel_parser",
                "Status: blocked.",
            ),
        ]

        for task, user_input, expected_text, expected_status in cases:
            with self.subTest(text=expected_text):
                self.tool_manager.run_calls.clear()
                self.model_manager.generate_calls.clear()
                agent = self._agent(task)

                response = agent.run(user_input)

                self.assertIsInstance(agent.executor, ReActExecutor)
                self.assertIn(expected_status, response)
                self.assertIn(expected_text, response)
                self.assertEqual(self.tool_manager.run_calls, [])
                expected_model_calls = 1 if expected_status == "Status: completed." else 0
                self.assertEqual(len(self.model_manager.generate_calls), expected_model_calls)

    def _agent(self, task, *, executor_type: str | None = "react", executor=None) -> ReactAgent:
        return ReactAgent(
            model_manager=self.model_manager,
            short_term_memory=FakeShortTermMemory(),
            long_term_memory=SimpleNamespace(),
            tool_manager=self.tool_manager,
            rag_system=SimpleNamespace(),
            complexity_analyzer=FakeAnalyzer(task),
            planner=Planner(model_manager=None),
            executor=executor,
            executor_type=executor_type,
            react_executor_config=_react_config(self.root),
        )


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, plan, task, user_input, history=""):
        self.calls.append((plan, task, user_input, history))
        return SimpleNamespace(success=True, output="injected response")


def _task(**overrides):
    defaults = {
        "trace_id": "trace_react_agent",
        "mode": "solo",
        "task_type": "tool_operation",
        "execution_strategy": "micro",
        "action_policy": "allow",
        "requires_clarification": False,
        "clarification_questions": [],
        "missing_parameters": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "tool_strategy": "tool",
        "available_tools": ["math_calculator", "document_parser", "text_processor", "file_writer", "search_tool"],
        "missing_tools": [],
        "intent": ["calculate"],
        "intent_sequence": ["calculate"],
        "parameters": {"expression": "2+3"},
        "file_info": {},
        "edit_mode": None,
        "project_stage": None,
        "tech_stacks": [],
        "complexity_level": "simple",
        "risk_flags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _react_config(root: Path) -> ReActExecutorConfig:
    values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
    values.update(
        {
            "workspace_root": str(root),
            "react_executor_log_path": str(root / "logs" / "react_agent_react_executor.log"),
        }
    )
    return ReActExecutorConfig(root=root, react_executor_config=values)


if __name__ == "__main__":
    unittest.main()
