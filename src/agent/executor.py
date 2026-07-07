from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from src.agent.planner import PlanStep, TaskPlan
from src.tools.base import ToolResult


@dataclass
class StepExecution:
    step: PlanStep
    result: ToolResult


@dataclass
class ExecutionResult:
    success: bool
    output: str
    steps: List[StepExecution] = field(default_factory=list)


class Executor:
    """Execute structured plans through tools or model generation."""

    def __init__(self, model_manager: Any, tool_manager: Any):
        self.model_manager = model_manager
        self.tool_manager = tool_manager

    def execute(self, plan: TaskPlan, task: Any, user_input: str, history: str = "") -> ExecutionResult:
        if plan.mode == "macro":
            return ExecutionResult(
                success=False,
                output="这个需求目前还不够明确。请补充目标、对象、输入材料或期望输出格式，我再继续拆解和执行。",
            )

        step_results: List[StepExecution] = []
        for step in plan.steps:
            result = self._execute_step(step, task, user_input, history)
            step_results.append(StepExecution(step=step, result=result))
            if not result.success:
                return ExecutionResult(success=False, output=result.to_text(), steps=step_results)

        output = step_results[-1].result.to_text() if step_results else ""
        return ExecutionResult(success=True, output=output, steps=step_results)

    def _execute_step(self, step: PlanStep, task: Any, user_input: str, history: str) -> ToolResult:
        if step.tool_name:
            return self.tool_manager.run_tool(step.tool_name, **step.args)

        prompt = (
            "You are a task-oriented assistant. Generate a concise response based on the task analysis.\n"
            f"User input: {user_input}\n"
            f"History: {history}\n"
            f"Intent: {task.intent}\n"
            f"Complexity: {task.complexity_level}\n"
            f"Execution strategy: {task.execution_strategy}\n"
            f"Parameters: {task.parameters}\n"
            f"Risk flags: {task.risk_flags}\n"
            f"Step: {step.description}\n"
        )
        response = self.model_manager.generate(prompt)
        return ToolResult.ok(data=response, message=response)
