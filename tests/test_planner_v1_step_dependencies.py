from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.agent.executor import Executor
from src.agent.planner import Planner
from src.tools.base import ToolResult


def make_task(**overrides):
    defaults = {
        "trace_id": "trace_dependencies",
        "mode": "solo",
        "task_type": "research",
        "execution_strategy": "meso",
        "action_policy": "allow",
        "requires_clarification": False,
        "clarification_questions": [],
        "missing_parameters": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "tool_strategy": "tool",
        "available_tools": ["search_tool", "text_processor", "document_parser", "file_writer", "translator"],
        "missing_tools": [],
        "intent": ["search", "summarize", "write_file"],
        "intent_sequence": ["search", "summarize", "write_file"],
        "parameters": {"topic": "planner architecture", "target_path": "out/planner.md"},
        "file_info": {},
        "edit_mode": None,
        "project_stage": None,
        "tech_stacks": [],
        "complexity_level": "medium",
        "risk_flags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FakeModelManager:
    def __init__(self):
        self.calls = []

    def generate(self, prompt: str, **kwargs):
        self.calls.append(prompt)
        return "converted model output"


class DependencyAwareToolManager:
    def __init__(self):
        self.calls = []

    def run_tool(self, tool_name: str, **kwargs):
        self.calls.append((tool_name, kwargs))
        if tool_name == "search_tool":
            return ToolResult.ok(data="search data", message="search output")
        if tool_name == "document_parser":
            return ToolResult.ok(data="file data", message="file content")
        if tool_name == "text_processor":
            return ToolResult.ok(data="processed data", message=f"processed: {kwargs['text']}")
        if tool_name == "translator":
            return ToolResult.ok(data="translated data", message=f"translated: {kwargs['text']}")
        if tool_name == "file_writer":
            return ToolResult.ok(data={"path": kwargs["file_path"]}, message=f"wrote: {kwargs['content']}")
        return ToolResult.ok(data="ok", message="ok")


class PlannerStepDependencyTest(unittest.TestCase):
    def setUp(self):
        self.planner = Planner()
        self.model_manager = FakeModelManager()
        self.tool_manager = DependencyAwareToolManager()
        self.executor = Executor(self.model_manager, self.tool_manager)

    def test_search_summarize_write_file_injects_prior_outputs(self):
        task = make_task()
        plan = self.planner.create_plan("search planner architecture and save notes", task)

        result = self.executor.execute(plan, task, "search planner architecture and save notes")

        self.assertTrue(result.success)
        self.assertEqual(self.tool_manager.calls[0], ("search_tool", {"query": "planner architecture", "max_results": 5}))
        self.assertEqual(
            self.tool_manager.calls[1],
            ("text_processor", {"operation": "summary", "text": "search output"}),
        )
        self.assertEqual(
            self.tool_manager.calls[2],
            ("file_writer", {"file_path": "out/planner.md", "content": "processed: search output", "overwrite": False}),
        )

    def test_read_extract_write_file_injects_file_content_then_extract_output(self):
        task = make_task(
            task_type="document_understanding",
            intent=["read_file", "extract", "write_file"],
            intent_sequence=["read_file", "extract", "write_file"],
            parameters={"file_path": "docs/source.md", "target_path": "out/extract.md"},
        )
        plan = self.planner.create_plan("extract docs/source.md into out/extract.md", task)

        result = self.executor.execute(plan, task, "extract docs/source.md into out/extract.md")

        self.assertTrue(result.success)
        self.assertEqual(self.tool_manager.calls[0], ("document_parser", {"file_path": "docs/source.md"}))
        self.assertEqual(
            self.tool_manager.calls[1],
            ("text_processor", {"operation": "keywords", "text": "file content"}),
        )
        self.assertEqual(
            self.tool_manager.calls[2],
            ("file_writer", {"file_path": "out/extract.md", "content": "processed: file content", "overwrite": False}),
        )

    def test_model_step_output_can_feed_file_writer(self):
        task = make_task(
            task_type="file_operation",
            intent=["convert_format"],
            intent_sequence=["convert_format"],
            parameters={
                "file_path": "docs/source.md",
                "file_type": "md",
                "target_format": "txt",
                "target_path": "out/source.txt",
            },
        )
        plan = self.planner.create_plan("convert docs/source.md to out/source.txt", task)

        result = self.executor.execute(plan, task, "convert docs/source.md to out/source.txt")

        self.assertTrue(result.success)
        self.assertEqual(self.tool_manager.calls[0], ("document_parser", {"file_path": "docs/source.md"}))
        self.assertEqual(len(self.model_manager.calls), 1)
        self.assertIn("file content", self.model_manager.calls[0])
        self.assertEqual(
            self.tool_manager.calls[1],
            ("file_writer", {"file_path": "out/source.txt", "content": "converted model output", "overwrite": False}),
        )

    def test_invalid_plan_missing_required_file_path_does_not_execute_tools(self):
        task = make_task(
            task_type="document_understanding",
            intent=["read_file"],
            intent_sequence=["read_file"],
            parameters={},
        )
        plan = self.planner.create_plan("read a file", task)

        result = self.executor.execute(plan, task, "read a file")

        self.assertEqual(plan.plan_validation_status, "invalid")
        self.assertFalse(plan.can_execute)
        self.assertFalse(result.success)
        self.assertIn("document_parser requires file_path", result.output)
        self.assertEqual(self.tool_manager.calls, [])


if __name__ == "__main__":
    unittest.main()
