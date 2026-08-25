from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.agent.react_agent import ReactAgent
from src.agent.react_executor_protocol import ExecutionEvent
from src.memory.config import MemoryConfig
from src.memory.session_manager import SessionManager


class ReactAgentMemoryAdaptationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_legacy_manage_memory_mode_uses_session_bound_short_term_memory(self) -> None:
        manager = SessionManager(config=_memory_config(self.root))
        manager.create_session("session_20260821_120000_alpha001")
        memory = manager.get_short_term_memory("session_20260821_120000_alpha001")
        executor = RecordingExecutor()
        agent = _agent(memory=memory, executor=executor)

        first = agent.run_with_result("remember alpha")
        second = agent.run_with_result("what did I ask you to remember?")

        self.assertEqual(first.output, "response to remember alpha")
        self.assertEqual(second.output, "response to what did I ask you to remember?")
        self.assertEqual(len(executor.calls), 2)
        self.assertIn("remember alpha", executor.calls[1].history)
        self.assertIn("response to remember alpha", executor.calls[1].history)

        reloaded = SessionManager(config=_memory_config(self.root))
        timeline = reloaded.get_session_timeline("session_20260821_120000_alpha001")
        self.assertEqual([item.role for item in timeline], ["user", "assistant", "user", "assistant"])

    def test_session_bound_short_term_memory_keeps_sessions_isolated(self) -> None:
        manager = SessionManager(config=_memory_config(self.root))
        manager.create_session("session_20260821_120000_alpha001")
        manager.create_session("session_20260821_120000_beta0001")
        alpha_memory = manager.get_short_term_memory("session_20260821_120000_alpha001")
        beta_memory = manager.get_short_term_memory("session_20260821_120000_beta0001")
        alpha_executor = RecordingExecutor()
        beta_executor = RecordingExecutor()

        _agent(memory=alpha_memory, executor=alpha_executor).run("remember alpha")
        _agent(memory=beta_memory, executor=beta_executor).run("remember beta")
        _agent(memory=alpha_memory, executor=alpha_executor).run("continue from before")

        self.assertIn("remember alpha", alpha_executor.calls[-1].history)
        self.assertNotIn("remember beta", alpha_executor.calls[-1].history)

    def test_runtime_managed_mode_consumes_external_context_without_memory_writes(self) -> None:
        memory = StrictMemory()
        executor = RecordingExecutor()
        agent = _agent(memory=memory, executor=executor, manage_memory=False)

        result = agent.run_with_result(
            "continue",
            context_text="[Session Summary]\nexternal context",
            session_id="session_20260821_120000_runtime1",
            run_id="run_20260821_120000_runtime01",
        )

        self.assertEqual(result.output, "response to continue")
        self.assertEqual(executor.calls[0].history, "[Session Summary]\nexternal context")
        self.assertEqual(memory.add_calls, [])

    def test_per_call_manage_memory_false_overrides_legacy_default(self) -> None:
        memory = StrictMemory()
        executor = RecordingExecutor()
        agent = _agent(memory=memory, executor=executor, manage_memory=True)

        agent.run(
            "continue",
            context_text="runtime context",
            manage_memory=False,
        )

        self.assertEqual(executor.calls[0].history, "runtime context")
        self.assertEqual(memory.add_calls, [])

    def test_event_callback_is_forwarded_to_executor(self) -> None:
        memory = StrictMemory()
        executor = RecordingEventExecutor()
        agent = _agent(memory=memory, executor=executor, manage_memory=False)
        seen: list[ExecutionEvent] = []

        agent.run_with_result(
            "calculate",
            context_text="context",
            event_callback=seen.append,
            event_callback_visible_only=True,
        )

        self.assertEqual([event.type for event in seen], ["progress_message"])
        self.assertEqual(executor.callback_visible_only_values, [True])

    def test_event_callback_can_include_internal_events_when_requested(self) -> None:
        memory = StrictMemory()
        executor = RecordingEventExecutor()
        agent = _agent(memory=memory, executor=executor, manage_memory=False)
        seen: list[ExecutionEvent] = []

        agent.run_with_result(
            "calculate",
            context_text="context",
            event_callback=seen.append,
            event_callback_visible_only=False,
        )

        self.assertEqual([event.type for event in seen], ["progress_message", "model_step_started"])
        self.assertEqual(executor.callback_visible_only_values, [False])

    def test_visible_executor_events_are_persisted_once_via_callback(self) -> None:
        manager = SessionManager(config=_memory_config(self.root))
        session, run = manager.create_user_turn("session_20260821_120000_gamma001", "hello")
        memory = manager.get_short_term_memory(session.session_id)
        executor = RecordingEventExecutor()
        agent = _agent(memory=memory, executor=executor, manage_memory=False)
        seen: list[ExecutionEvent] = []

        agent.run_with_result(
            "calculate",
            context_text="context",
            event_callback=seen.append,
            event_callback_visible_only=True,
            session_id=session.session_id,
            run_id=run.run_id,
        )

        timeline = manager.get_session_timeline(session.session_id)
        event_items = [item for item in timeline if item.item_kind == "execution_event"]
        self.assertEqual(len(event_items), 1)
        self.assertEqual(event_items[0].display_type, "plan_progress")
        self.assertEqual(event_items[0].content, "visible progress")
        self.assertEqual([event.type for event in seen], ["progress_message"])

    def test_result_events_are_used_as_fallback_when_executor_has_no_callback(self) -> None:
        manager = SessionManager(config=_memory_config(self.root))
        session, run = manager.create_user_turn("session_20260821_120000_delta001", "hello")
        memory = manager.get_short_term_memory(session.session_id)
        executor = ResultOnlyExecutor()
        agent = _agent(memory=memory, executor=executor, manage_memory=False)

        agent.run_with_result(
            "calculate",
            context_text="context",
            session_id=session.session_id,
            run_id=run.run_id,
        )

        timeline = manager.get_session_timeline(session.session_id)
        event_items = [item for item in timeline if item.item_kind == "execution_event"]
        self.assertEqual(len(event_items), 1)
        self.assertEqual(event_items[0].display_type, "plan_progress")
        self.assertEqual(event_items[0].content, "visible progress")


class RecordingCall:
    def __init__(self, plan, task, user_input: str, history: str) -> None:
        self.plan = plan
        self.task = task
        self.user_input = user_input
        self.history = history


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[RecordingCall] = []

    def execute(self, plan, task, user_input: str, history: str = ""):
        self.calls.append(RecordingCall(plan, task, user_input, history))
        return SimpleNamespace(success=True, output=f"response to {user_input}")


class RecordingEventExecutor:
    def __init__(self) -> None:
        self.callback_visible_only_values: list[bool] = []

    def execute(
        self,
        plan,
        task,
        user_input: str,
        history: str = "",
        *,
        event_callback=None,
        event_callback_visible_only: bool = False,
    ):
        self.callback_visible_only_values.append(event_callback_visible_only)
        visible = ExecutionEvent(
            execution_id="execution_step10",
            plan_id=plan.plan_id,
            type="progress_message",
            message="visible progress",
            visible_to_user=True,
        )
        internal = ExecutionEvent(
            execution_id="execution_step10",
            plan_id=plan.plan_id,
            type="model_step_started",
            message="internal progress",
            visible_to_user=False,
        )
        if event_callback is not None:
            event_callback(visible)
            if not event_callback_visible_only:
                event_callback(internal)
        return SimpleNamespace(success=True, output="done", events=[visible, internal])


class ResultOnlyExecutor:
    def execute(self, plan, task, user_input: str, history: str = ""):
        visible = ExecutionEvent(
            execution_id="execution_step11",
            plan_id=plan.plan_id,
            type="progress_message",
            message="visible progress",
            visible_to_user=True,
        )
        internal = ExecutionEvent(
            execution_id="execution_step11",
            plan_id=plan.plan_id,
            type="model_step_started",
            message="internal progress",
            visible_to_user=False,
        )
        return SimpleNamespace(success=True, output="done", events=[visible, internal])


class RecordingPlanner:
    def create_plan(self, user_input: str, task):
        return SimpleNamespace(
            plan_id=f"plan_{len(user_input)}",
            source_trace_id=getattr(task, "trace_id", None),
        )


class RecordingAnalyzer:
    def analyze(self, user_input: str):
        return SimpleNamespace(trace_id=f"trace_{len(user_input)}")


class StrictMemory:
    def __init__(self) -> None:
        self.add_calls: list[tuple[str, str]] = []

    def add_message(self, role: str, content: str) -> None:
        self.add_calls.append((role, content))
        raise AssertionError("ReactAgent should not write memory in runtime-managed mode")

    def get_history_text(self) -> str:
        raise AssertionError("ReactAgent should use the provided context_text")


def _agent(*, memory, executor, manage_memory: bool = True) -> ReactAgent:
    return ReactAgent(
        model_manager=SimpleNamespace(),
        short_term_memory=memory,
        long_term_memory=SimpleNamespace(),
        tool_manager=SimpleNamespace(),
        rag_system=SimpleNamespace(),
        complexity_analyzer=RecordingAnalyzer(),
        planner=RecordingPlanner(),
        executor=executor,
        executor_type="react",
        manage_memory=manage_memory,
    )


def _memory_config(root: Path) -> MemoryConfig:
    return MemoryConfig(
        database_path=root / "memory.db",
        log_path=root / "memory.log",
    )


if __name__ == "__main__":
    unittest.main()
