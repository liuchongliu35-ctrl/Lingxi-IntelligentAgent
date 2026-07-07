from __future__ import annotations

from typing import Any

from src.agent.executor import Executor
from src.agent.planner import Planner


class ReactAgent:
    """Orchestrate analysis, planning, execution, and memory updates."""

    def __init__(
        self,
        model_manager: Any,
        short_term_memory: Any,
        long_term_memory: Any,
        tool_manager: Any,
        rag_system: Any,
        complexity_analyzer: Any,
        planner: Planner | None = None,
        executor: Executor | None = None,
    ):
        self.model_manager = model_manager
        self.short_term_memory = short_term_memory
        self.long_term_memory = long_term_memory
        self.tool_manager = tool_manager
        self.rag_system = rag_system
        self.complexity_analyzer = complexity_analyzer
        self.planner = planner or Planner()
        self.executor = executor or Executor(model_manager=model_manager, tool_manager=tool_manager)

    def run(self, user_input: str) -> str:
        self.short_term_memory.add_message("user", user_input)

        task = self.complexity_analyzer.analyze(user_input)
        plan = self.planner.create_plan(user_input, task)
        history = self.short_term_memory.get_history_text()
        execution = self.executor.execute(plan, task, user_input, history=history)

        response = execution.output
        self.short_term_memory.add_message("assistant", response)
        return response
