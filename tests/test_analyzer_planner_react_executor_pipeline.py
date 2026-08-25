from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.agent.analyzer_config import load_analyzer_config
from src.agent.complexity_analyzer import ComplexityAnalyzer
from src.agent.planner import Planner
from src.agent.planner_config import load_planner_config
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_config import DEFAULT_REACT_EXECUTOR_CONFIG, ReActExecutorConfig
from src.agent.react_executor_logging import ReActExecutorLogger
from src.tools.base import ToolResult


class PipelineToolManager:
    TOOL_NAMES = (
        "document_parser",
        "text_processor",
        "math_calculator",
        "translator",
        "time_query",
        "search_tool",
        "file_writer",
        "code_executor",
    )

    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {name: f"Pipeline fixture tool: {name}" for name in self.TOOL_NAMES}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return ToolResult.ok(data={"tool_name": tool_name, "args": kwargs}, message=f"{tool_name} fixture result")

    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)


class PipelineModelManager:
    def __init__(self):
        self.generate_calls = []
        self.action_packet_calls = 0
        self.action_packets = []
        self._planned_action_packets = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if "Generate an intermediate model result for ReActExecutor." in prompt:
            return "pipeline fixture intermediate model result"
        if self._planned_action_packets:
            packet = self._planned_action_packets.pop(0)
            self.action_packet_calls += 1
            self.action_packets.append(packet)
            return json.dumps(packet)
        return "pipeline fixture model response"

    def bind_plan(self, plan):
        self._planned_action_packets = []
        for step in list(getattr(plan, "steps", []) or []):
            step_type = str(getattr(step, "step_type", "") or "")
            tool_name = getattr(step, "tool_name", None)
            if tool_name:
                action_args = dict(getattr(step, "args", {}) or {})
                input_from = list(getattr(step, "input_from", []) or [])
                if input_from:
                    action_args["input_from"] = input_from
                self._planned_action_packets.append(
                    {
                        "action_type": "call_tool",
                        "action_target": str(tool_name),
                        "action_args": action_args,
                    }
                )
            elif step_type == "model":
                self._planned_action_packets.append(
                    {
                        "action_type": "call_model",
                        "action_args": {
                            "goal": str(getattr(step, "description", "") or "Generate a model result."),
                            "input": dict(getattr(step, "args", {}) or {}),
                            "output_requirements": str(getattr(step, "expected_output", "") or "fixture result"),
                        },
                    }
                )
            else:
                self._planned_action_packets.append(
                    {
                        "action_type": "finish",
                        "final_answer": "pipeline fixture model response",
                    }
                )


class AnalyzerPlannerReActExecutorPipelineTest(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self._copy_config("analyzer")
        self._copy_config("planner")
        (self.root / "README.md").write_text(
            "# Fixture README\n\nThis file contains test information.",
            encoding="utf-8",
        )
        load_analyzer_config.cache_clear()
        load_planner_config.cache_clear()
        self.analyzer_config = load_analyzer_config(self.root)
        self.planner_config = load_planner_config(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()
        load_analyzer_config.cache_clear()
        load_planner_config.cache_clear()

    def _copy_config(self, name: str) -> None:
        source_dir = self.repo_root / "config" / name
        target_dir = self.root / "config" / name
        target_dir.mkdir(parents=True)
        for source_path in source_dir.glob("*.json"):
            shutil.copyfile(source_path, target_dir / source_path.name)

        config_name = "analyzer_config.json" if name == "analyzer" else "planner_config.json"
        config_path = target_dir / config_name
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if name == "analyzer":
            config["log_path"] = "logs/analyzer_react_pipeline.log"
            config["pending_intents_path"] = "storage/analyzer/pending_intents_react_pipeline.json"
        else:
            config["planner_log_path"] = "logs/planner_react_pipeline.log"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run_pipeline(self, user_input: str):
        tool_manager = PipelineToolManager()
        model_manager = PipelineModelManager()
        analyzer = ComplexityAnalyzer(
            analyzer_config=self.analyzer_config,
            tool_manager=tool_manager,
            model_manager=model_manager,
        )
        planner = Planner(planner_config=self.planner_config, model_manager=None)
        task = analyzer.analyze(user_input)
        plan = planner.create_plan(user_input, task)
        model_manager.bind_plan(plan)

        react_config_values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
        react_config_values.update(
            {
                "workspace_root": str(self.root),
                "react_executor_log_path": str(self.root / "logs" / "react_executor_react_pipeline.log"),
                "command_confirmation_policy": "ask",
            }
        )
        react_config = ReActExecutorConfig(root=self.root, react_executor_config=react_config_values)
        executor = ReActExecutor(
            model_manager=model_manager,
            tool_manager=tool_manager,
            config=react_config,
            execution_logger=ReActExecutorLogger(react_config.react_executor_log_path),
        )
        result = executor.execute(plan, task, user_input)
        return task, plan, result, tool_manager, model_manager

    def test_pipeline_cases(self):
        cases = [
            {
                "id": "calculate",
                "input": "计算 2+3*4",
                "intents": ["calculate"],
                "plan_mode": "micro",
                "tool_names": ["math_calculator"],
                "result_error": None,
                "result_status": "completed",
                "result_success": True,
                "events": {"step_started", "step_completed", "final_answer"},
            },
            {
                "id": "read_summarize",
                "input": "读取 README.md 并总结",
                "intents": ["read_file", "summarize"],
                "plan_mode": "meso",
                "tool_names": ["document_parser", "text_processor"],
                "result_error": None,
                "result_status": "completed",
                "result_success": True,
                "events": {"step_started", "step_completed", "final_answer"},
            },
            {
                "id": "search_summarize",
                "input": "搜索关于 Python 测试框架的资料并总结",
                "intents": ["search", "summarize"],
                "plan_mode": "meso",
                "tool_names": ["search_tool", "text_processor"],
                "result_error": None,
                "result_status": "completed",
                "result_success": True,
                "events": {"step_started", "step_completed", "final_answer"},
            },
            {
                "id": "read_extract_write",
                "input": "读取 README.md 提取重点并写入文件 summary.md，关于 README 重点",
                "intents": ["read_file", "extract", "write_file"],
                "plan_mode": "meso",
                "tool_names": ["document_parser", "text_processor", "file_writer"],
                "result_error": None,
                "result_status": "completed",
                "result_success": True,
                "events": {"step_started", "step_completed", "final_answer"},
            },
            {
                "id": "chat_no_tool",
                "input": "只告诉我如何运行 Python 单元测试，不要执行工具",
                "intents": ["chat"],
                "plan_mode": "chat",
                "tool_names": [],
                "result_error": None,
                "result_status": "completed",
                "result_success": True,
                "events": {"step_started", "step_completed", "final_answer"},
            },
            {
                "id": "dangerous_command_block",
                "input": "执行命令 rm -rf /",
                "intents": ["execute_code"],
                "plan_mode": "blocked",
                "tool_names": [],
                "result_error": "task_policy_blocked",
                "result_status": "blocked",
                "events": {"system_notice", "final_answer"},
            },
            {
                "id": "missing_parameter_clarify",
                "input": "读取文件并总结",
                "intents": ["read_file", "summarize"],
                "plan_mode": "clarify",
                "tool_names": [],
                "result_error": "clarification_required",
                "result_status": "waiting_user",
                "events": {"system_notice", "final_answer"},
            },
            {
                "id": "delete_confirmation",
                "input": "删除 report.txt",
                "intents": ["delete_file"],
                "plan_mode": "confirm",
                "tool_names": [],
                "result_error": "confirmation_required",
                "result_status": "waiting_user",
                "events": {"confirmation_requested"},
            },
        ]

        for case in cases:
            with self.subTest(case=case["id"]):
                task, plan, result, tool_manager, model_manager = self._run_pipeline(case["input"])

                self.assertTrue(task.trace_id)
                self.assertTrue(set(case["intents"]).issubset(set(task.intent_sequence)))
                self.assertEqual(plan.source_trace_id, task.trace_id)
                self.assertEqual(plan.mode, case["plan_mode"])
                self.assertEqual([step.tool_name for step in plan.steps if step.tool_name], case["tool_names"])
                self.assertEqual(result.plan_id, plan.plan_id)
                self.assertEqual(result.source_trace_id, task.trace_id)
                self.assertEqual(result.error_code, case["result_error"])
                self.assertEqual(result.status, case["result_status"])
                self.assertEqual(result.success, case.get("result_success", False))
                self.assertTrue(result.events)
                self.assertTrue(set(case["events"]).issubset({event.type for event in result.events}))
                if result.success:
                    self.assertEqual([name for name, _kwargs in tool_manager.run_calls], case["tool_names"])
                    expected_action_packet_count = len(
                        [
                            step
                            for step in plan.steps
                            if getattr(step, "tool_name", None) or getattr(step, "step_type", "") in {"model", "respond"}
                        ]
                    )
                    self.assertGreater(len(model_manager.generate_calls), 0)
                    self.assertEqual(model_manager.action_packet_calls, expected_action_packet_count)
                    self.assertEqual(
                        [
                            packet.get("action_target")
                            for packet in model_manager.action_packets
                            if packet.get("action_type") == "call_tool"
                        ],
                        case["tool_names"],
                    )
                else:
                    self.assertEqual(tool_manager.run_calls, [])
                    self.assertEqual(model_manager.generate_calls, [])
                    self.assertEqual(model_manager.action_packets, [])

    def test_pipeline_preserves_plan_dependencies_for_file_flow(self):
        task, plan, result, tool_manager, model_manager = self._run_pipeline(
            "读取 README.md 提取重点并写入文件 summary.md，关于 README 重点"
        )

        self.assertEqual(task.file_info["source_path"], "README.md")
        self.assertEqual(task.file_info["target_path"], "summary.md")
        self.assertEqual(plan.steps[1].depends_on, ["step_1"])
        self.assertEqual(plan.steps[1].input_from, ["step_1"])
        self.assertEqual(plan.steps[2].depends_on, ["step_2"])
        self.assertEqual(plan.steps[2].input_from, ["step_2"])
        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.step_statuses, {"step_1": "completed", "step_2": "completed", "step_3": "completed"})
        self.assertEqual([name for name, _kwargs in tool_manager.run_calls], ["document_parser", "text_processor", "file_writer"])
        self.assertEqual(model_manager.action_packet_calls, 3)
        self.assertEqual(
            [packet["action_target"] for packet in model_manager.action_packets],
            ["document_parser", "text_processor", "file_writer"],
        )
        self.assertIn("document_parser", tool_manager.run_calls[1][1]["text"])
        self.assertIn("text_processor", tool_manager.run_calls[2][1]["content"])


if __name__ == "__main__":
    unittest.main()
