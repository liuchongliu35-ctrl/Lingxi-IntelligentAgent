from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.agent.planner import PlanStep, TaskPlan
from src.models.compat import ModelCallFailure, require_model_content
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
        if getattr(task, "action_policy", "allow") == "block" or plan.mode == "blocked":
            return ExecutionResult(
                success=False,
                output="这个请求包含高风险操作，当前不会执行。",
            )

        if getattr(task, "requires_clarification", False) or plan.mode in {"macro", "clarify"}:
            questions = list(getattr(task, "clarification_questions", []))
            if questions:
                return ExecutionResult(success=False, output="\n".join(questions))
            return ExecutionResult(
                success=False,
                output="这个需求目前还不够明确。请补充目标、对象、输入材料或期望输出格式，我再继续拆解和执行。",
            )

        if getattr(task, "requires_confirmation", False) or getattr(task, "action_policy", "allow") == "confirm" or plan.mode == "confirm":
            reason = getattr(task, "confirmation_reason", None) or "risky_action"
            return ExecutionResult(
                success=False,
                output=f"这个操作需要你确认后才能继续执行。确认原因：{reason}",
            )

        if getattr(task, "tool_strategy", None) == "blocked_missing_tools" or plan.mode == "missing_tools":
            missing_tools = list(getattr(task, "missing_tools", []))
            if missing_tools:
                return ExecutionResult(
                    success=False,
                    output="当前缺少完成该任务所需的工具：" + "、".join(missing_tools),
                )
            return ExecutionResult(success=False, output="当前缺少完成该任务所需的工具。")

        if not plan.can_execute or plan.plan_validation_status == "invalid":
            notes = "；".join(plan.plan_validation_notes) if plan.plan_validation_notes else "计划缺少必要参数。"
            return ExecutionResult(success=False, output=f"计划校验失败：{notes}")

        step_results: List[StepExecution] = []
        step_context: Dict[str, ToolResult] = {}
        for step in plan.steps:
            result = self._execute_step(step, task, user_input, history, step_context)
            step_results.append(StepExecution(step=step, result=result))
            if not result.success:
                return ExecutionResult(success=False, output=result.to_text(), steps=step_results)
            step_context[step.id] = result
            if step.output_key:
                step_context[step.output_key] = result

        output = step_results[-1].result.to_text() if step_results else ""
        return ExecutionResult(success=True, output=output, steps=step_results)

    def _execute_step(
        self,
        step: PlanStep,
        task: Any,
        user_input: str,
        history: str,
        step_context: Dict[str, ToolResult],
    ) -> ToolResult:
        missing_inputs = [ref for ref in step.input_from if ref not in step_context]
        if missing_inputs:
            return ToolResult.fail(
                f"Step {step.id} is missing input_from references: {', '.join(missing_inputs)}",
                code="missing_step_input",
            )

        resolved_args = self._resolve_step_args(step, step_context)
        if step.tool_name:
            return self.tool_manager.run_tool(step.tool_name, **resolved_args)

        dependency_context = self._dependency_text(step, step_context)
        prompt = (
            "You are a task-oriented assistant. Generate a concise response based on the task analysis.\n"
            f"User input: {user_input}\n"
            f"History: {history}\n"
            f"Intent: {task.intent}\n"
            f"Complexity: {task.complexity_level}\n"
            f"Execution strategy: {task.execution_strategy}\n"
            f"Parameters: {task.parameters}\n"
            f"Risk flags: {task.risk_flags}\n"
            f"Dependency outputs: {dependency_context}\n"
            f"Step: {step.description}\n"
        )
        try:
            response = require_model_content(self.model_manager.generate(prompt))
        except ModelCallFailure as failure:
            return ToolResult.fail(
                failure.result.error or "model call failed",
                code=failure.result.code or "model_call_failed",
            )
        return ToolResult.ok(data=response, message=response)

    def _resolve_step_args(self, step: PlanStep, step_context: Dict[str, ToolResult]) -> Dict[str, Any]:
        args = dict(step.args or {})
        dependency_text = self._dependency_text(step, step_context)
        if not dependency_text:
            return args

        if step.tool_name in {"text_processor", "translator"} and not self._has_value(args.get("text")):
            args["text"] = dependency_text
        elif step.tool_name in {"file_writer", "write_file"} and not self._has_value(args.get("content")):
            args["content"] = dependency_text
        return args

    def _dependency_text(self, step: PlanStep, step_context: Dict[str, ToolResult]) -> str:
        chunks = [step_context[ref].to_text() for ref in step.input_from if ref in step_context]
        return "\n\n".join(chunk for chunk in chunks if chunk)

    def _has_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True
