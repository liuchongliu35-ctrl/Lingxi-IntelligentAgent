from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class PlanStep:
    id: str
    description: str
    tool_name: str | None = None
    args: Dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""


@dataclass
class TaskPlan:
    goal: str
    mode: str
    steps: List[PlanStep] = field(default_factory=list)


class Planner:
    """Create deterministic baseline plans before LLM planning is added."""

    def create_plan(self, user_input: str, task: Any) -> TaskPlan:
        mode = task.execution_strategy
        if mode == "micro":
            return self._micro_plan(user_input, task)
        if mode == "macro":
            return TaskPlan(
                goal=user_input,
                mode="macro",
                steps=[
                    PlanStep(
                        id="clarify",
                        description="Ask the user for missing goal, scope, input material, or output format.",
                        expected_output="Clarification question",
                    )
                ],
            )
        return self._meso_plan(user_input, task)

    def _micro_plan(self, user_input: str, task: Any) -> TaskPlan:
        intent = task.intent[0] if task.intent else "direct"
        tool_name: str | None = None
        args: Dict[str, Any] = {}

        if intent == "calculate":
            tool_name = "math_calculator"
            args = {"expression": task.parameters.get("expression", user_input)}
        elif intent in {"read", "read_file"} and task.parameters.get("file"):
            tool_name = "document_parser"
            args = {"file_path": task.parameters["file"]}
        elif intent == "translate":
            tool_name = "translator"
            args = {"text": user_input}

        return TaskPlan(
            goal=user_input,
            mode="micro",
            steps=[
                PlanStep(
                    id="step_1",
                    description=f"Execute {intent} task.",
                    tool_name=tool_name,
                    args=args,
                    expected_output="Direct task result",
                )
            ],
        )

    def _meso_plan(self, user_input: str, task: Any) -> TaskPlan:
        return TaskPlan(
            goal=user_input,
            mode=task.execution_strategy,
            steps=[
                PlanStep(
                    id="analyze",
                    description="Summarize task intent, parameters, risks, and missing information.",
                    expected_output="Task analysis",
                ),
                PlanStep(
                    id="respond",
                    description="Generate a user-facing answer or next-step proposal.",
                    expected_output="Final response",
                ),
            ],
        )

