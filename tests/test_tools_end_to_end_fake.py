from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor_protocol import ActionPacket
from src.agent.react_executor import CONFIRMATION_PENDING_CODE, ReActExecutor
from src.agent.react_executor_config import (
    DEFAULT_REACT_EXECUTOR_CONFIG,
    ReActExecutorConfig,
)
from src.agent.react_executor_logging import ReActExecutorLogger
from src.models import (
    ModelManager,
    ModelsConfig,
    ModelsRuntimeConfig,
    default_provider_specs,
    default_route_configs,
)
from src.tools import (
    MCPServerConfig,
    MCPStdioClient,
    MCPToolGateway,
    ToolErrorCode,
    WebSearchData,
    adapt_mcp_discovery_to_specs,
    default_tools_config,
    register_mcp_tool_specs,
)
from src.tools.base import ToolResult
from src.tools.tool_logger import NullToolLogger
from src.tools.tool_manager import ToolManager


FAKE_MCP_SERVER = Path(__file__).with_name("fixtures") / "fake_mcp_server.py"


class ScriptedActionModel:
    """Model stub that proposes ActionPackets through the real ModelManager."""

    def __init__(self) -> None:
        self.generate_calls: list[dict[str, Any]] = []
        self.action_packets: list[dict[str, Any]] = []
        self._planned_action_packets: list[dict[str, Any]] = []

    def bind_plan(self, plan: TaskPlan) -> None:
        packets: list[dict[str, Any]] = []
        for step in list(plan.steps or []):
            if step.tool_name:
                action_args = dict(step.args or {})
                if step.input_from:
                    action_args["input_from"] = list(step.input_from)
                packet = {
                    "action_type": "call_tool",
                    "action_target": step.tool_name,
                    "action_args": action_args,
                    "user_visible_message": step.description,
                }
                if step.requires_confirmation:
                    packet["requires_confirmation"] = True
                    packet["confirmation_type"] = "confirmation"
                packets.append(packet)
            elif step.step_type == "model":
                packets.append(
                    {
                        "action_type": "call_model",
                        "action_args": {
                            "goal": step.description,
                            "input": dict(step.args or {}),
                            "output_requirements": step.expected_output or "model result",
                        },
                        "user_visible_message": step.description,
                    }
                )
            else:
                packets.append(
                    {
                        "action_type": "finish",
                        "final_answer": step.expected_output or "Acceptance flow completed.",
                        "user_visible_message": step.description,
                    }
                )
        self._planned_action_packets = packets

    def generate(self, prompt: str, **kwargs: Any) -> str:
        call_type = str(kwargs.get("call_type") or "")
        self.generate_calls.append({"prompt": prompt, "kwargs": dict(kwargs), "call_type": call_type})
        if call_type in {"react_action_decision", "react_action_repair"}:
            if self._planned_action_packets:
                packet = self._planned_action_packets.pop(0)
            else:
                packet = {
                    "action_type": "finish",
                    "final_answer": "Acceptance flow completed.",
                }
            self.action_packets.append(packet)
            return json.dumps(packet, ensure_ascii=False)
        if "Generate an intermediate model result for ReActExecutor." in prompt:
            return "Acceptance model summary generated through Models V1."
        return "Acceptance model response."

    def stream_generate(self, prompt: str, **kwargs: Any):
        yield self.generate(prompt, **kwargs)


class RecordingToolManager:
    def __init__(self, manager: ToolManager) -> None:
        self.manager = manager
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return self.manager.execute(request)

    def list_tools(self):
        return self.manager.list_tools()

    def get_registry(self):
        return self.manager.get_registry()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.manager, name)


class PreviewAwarePatchToolManager:
    def __init__(self, manager: ToolManager, preview_hash: str = "preview-patch-1") -> None:
        self.manager = manager
        self.preview_hash = preview_hash
        self.requests: list[Any] = []

    def execute(self, request):
        self.requests.append(request)
        if request.options.dry_run:
            return ToolResult.ok(
                data={
                    "preview": {
                        "tool_name": request.tool_name,
                        "path": request.args.get("path"),
                        "patches": list(request.args.get("patches") or []),
                    },
                    "affected_resources": [str(request.args.get("path") or "")],
                },
                message="Dry-run preview prepared.",
                code="dry_run_preview",
                metadata={
                    "output_control": {
                        "preview_hash": self.preview_hash,
                        "preview": {
                            "tool_name": request.tool_name,
                            "path": request.args.get("path"),
                            "patches": list(request.args.get("patches") or []),
                        },
                        "affected_resources": [str(request.args.get("path") or "")],
                    }
                },
            )
        target_path = Path(self.manager.tools_config.workspace_root) / str(request.args.get("path") or "")
        text = target_path.read_text(encoding="utf-8")
        patch_count = 0
        for patch in list(request.args.get("patches") or []):
            old_text = str(patch.get("old_text") or "")
            new_text = str(patch.get("new_text") or "")
            if old_text not in text:
                return ToolResult.fail(
                    "Patch target text not found.",
                    code=ToolErrorCode.PATCH_OLD_TEXT_NOT_FOUND.value,
                )
            text = text.replace(old_text, new_text, 1)
            patch_count += 1
        target_path.write_text(text, encoding="utf-8")
        return ToolResult.ok(
            data={
                "path": str(request.args.get("path") or ""),
                "patch_count": patch_count,
                "applied_count": patch_count,
            },
            message="Applied patches.",
        )

    def list_tools(self):
        return self.manager.list_tools()

    def get_registry(self):
        return self.manager.get_registry()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.manager, name)


class ToolsEndToEndFakeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.model_script = ScriptedActionModel()
        self.model_manager = _model_manager(self.root, self.model_script)
        self.mcp_client: MCPStdioClient | None = None

    def tearDown(self) -> None:
        if self.mcp_client is not None:
            self.mcp_client.stop()
        self.temp_dir.cleanup()

    def test_read_file_tool_result_becomes_observation_and_model_summary(self):
        (self.root / "README.md").write_text(
            "# Acceptance\n\nTools V1 read path.",
            encoding="utf-8",
        )
        tool_manager = RecordingToolManager(_tool_manager(self.root, self.model_manager))
        plan = _plan(
            "read README",
            [
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Read README.md.",
                    step_type="tool",
                    tool_name="read_file",
                    args={"path": "README.md"},
                    output_key="readme",
                ),
                PlanStep(
                    id="step_2",
                    task_id="task_1",
                    description="Summarize the read result.",
                    step_type="model",
                    depends_on=["step_1"],
                    input_from=["step_1"],
                    expected_output="Brief file summary",
                ),
            ],
            available_tools=tool_manager.get_registry().tool_names(),
        )

        result = _execute(self, plan, tool_manager, _task(self.root, allow_read_workspace=True))

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual([request.tool_name for request in tool_manager.requests], ["read_file"])
        self.assertEqual(result.observations[0].tool_name, "read_file")
        self.assertTrue(result.observations[0].success)
        self.assertTrue(any(call["call_type"] == "react_action_decision" for call in self.model_script.generate_calls))
        self.assertTrue(any(event.type == "observation_created" for event in result.events))

    def test_patch_file_preview_confirmation_and_resume_mutates_file(self):
        target = self.root / "note.txt"
        target.write_text("def main():\n    return 1\n", encoding="utf-8")
        tool_manager = PreviewAwarePatchToolManager(_tool_manager(self.root, self.model_manager))
        plan = _plan(
            "patch note",
            [
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Replace one line.",
                    step_type="tool",
                    tool_name="patch_file",
                    args={
                        "path": "note.txt",
                        "patches": [
                            {
                                "operation": "replace",
                                "old_text": "    return 1\n",
                                "new_text": "    return 2\n",
                            }
                        ],
                    },
                    confirmation_reason="patch note.txt",
                )
            ],
            available_tools=tool_manager.get_registry().tool_names(),
        )
        task = _task(self.root, allow_write_workspace=True)
        packet = ActionPacket(
            execution_id="",
            plan_id="",
            task_id="task_1",
            step_id="step_1",
            action_type="call_tool",
            action_target="patch_file",
            action_args={
                "path": "note.txt",
                "patches": [
                    {
                        "operation": "replace",
                        "old_text": "    return 1\n",
                        "new_text": "    return 2\n",
                    }
                ],
            },
            requires_confirmation=True,
            confirmation_type="confirmation",
            user_visible_message="Patch note?",
        )
        initial, resumed, context, pending = _dispatch_and_resume(self, plan, tool_manager, task, packet)

        self.assertFalse(initial.success)
        self.assertEqual(initial.code, CONFIRMATION_PENDING_CODE)
        self.assertIsNotNone(pending)
        self.assertEqual(target.read_text(encoding="utf-8"), "def main():\n    return 2\n")
        self.assertTrue(any(request.options.dry_run for request in tool_manager.requests))
        self.assertTrue(any(request.options.confirmed for request in tool_manager.requests))
        self.assertTrue(any(observation.success for observation in resumed.observations))

    def test_command_tool_nonzero_exit_is_structured_failure_without_fallback_tool(self):
        tool_manager = RecordingToolManager(_tool_manager(self.root, self.model_manager))
        plan = _plan(
            "run failing test",
            [
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Run a failing test command.",
                    step_type="tool",
                    tool_name="command_tool",
                    args={
                        "command": 'python -c "raise SystemExit(3)"',
                        "cwd": ".",
                        "purpose": "Step 43 non-zero command acceptance",
                        "risk_level": "high",
                        "requires_confirmation": True,
                        "expected_result": "exit code 3",
                        "timeout_seconds": 5,
                    },
                    on_failure="stop",
                )
            ],
            available_tools=tool_manager.get_registry().tool_names(),
        )
        packet = ActionPacket(
            execution_id="",
            plan_id="",
            task_id="task_1",
            step_id="step_1",
            action_type="call_tool",
            action_target="command_tool",
            action_args={
                "command": 'python -c "raise SystemExit(3)"',
                "cwd": ".",
                "purpose": "Step 43 non-zero command acceptance",
                "risk_level": "high",
                "requires_confirmation": True,
                "expected_result": "exit code 3",
                "timeout_seconds": 5,
            },
            requires_confirmation=True,
            confirmation_type="confirmation",
            user_visible_message="Run failing command?",
        )
        initial, resumed, context, pending = _dispatch_and_resume(
            self,
            plan,
            tool_manager,
            _task(self.root, allow_command=True),
            packet,
        )

        self.assertFalse(initial.success)
        self.assertEqual(initial.code, CONFIRMATION_PENDING_CODE)
        self.assertIsNotNone(pending)
        self.assertTrue(any(request.options.dry_run for request in tool_manager.requests))
        self.assertTrue(any(request.options.confirmed for request in tool_manager.requests))
        self.assertTrue(
            any(observation.code == ToolErrorCode.COMMAND_NONZERO_EXIT.value for observation in resumed.observations)
        )
        self.assertTrue(all(request.tool_name == "command_tool" for request in tool_manager.requests))

    def test_shell_command_tool_requires_confirmation_and_does_not_go_through_command_tool(self):
        output = self.root / "shell-out.txt"
        tool_manager = RecordingToolManager(_tool_manager(self.root, self.model_manager))
        plan = _plan(
            "shell pipeline",
            [
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Run an explicit shell command.",
                    step_type="tool",
                    tool_name="shell_command_tool",
                    args={
                        "command": "echo shell-ok > shell-out.txt",
                        "shell": "cmd",
                        "cwd": ".",
                        "purpose": "Step 43 shell acceptance",
                        "timeout_seconds": 5,
                        "writes_files": True,
                    },
                    confirmation_reason="shell command writes shell-out.txt",
                )
            ],
            available_tools=tool_manager.get_registry().tool_names(),
        )
        packet = ActionPacket(
            execution_id="",
            plan_id="",
            task_id="task_1",
            step_id="step_1",
            action_type="call_tool",
            action_target="shell_command_tool",
            action_args={
                "command": "echo shell-ok > shell-out.txt",
                "shell": "cmd",
                "cwd": ".",
                "purpose": "Step 43 shell acceptance",
                "timeout_seconds": 5,
                "writes_files": True,
            },
            requires_confirmation=True,
            confirmation_type="confirmation",
            user_visible_message="Run an explicit shell command.",
        )
        initial, resumed, context, pending = _dispatch_and_resume(
            self,
            plan,
            tool_manager,
            _task(self.root, allow_shell_command=True, allow_write_workspace=True),
            packet,
        )

        self.assertFalse(initial.success)
        self.assertEqual(initial.code, CONFIRMATION_PENDING_CODE)
        self.assertIsNotNone(pending)
        self.assertTrue(resumed.success)
        self.assertTrue(output.exists())
        self.assertTrue(any(request.options.dry_run for request in tool_manager.requests))
        self.assertTrue(any(request.options.confirmed for request in tool_manager.requests))
        self.assertTrue(all(request.tool_name == "shell_command_tool" for request in tool_manager.requests))

    def test_web_search_fake_provider_flows_to_observation_then_model_summary(self):
        tool_manager = RecordingToolManager(_web_search_tool_manager(self.root, self.model_manager))
        plan = _plan(
            "search and summarize",
            [
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Search with fake provider.",
                    step_type="tool",
                    tool_name="web_search",
                    args={"query": "agent architecture", "provider": "fake"},
                    output_key="search_results",
                ),
                PlanStep(
                    id="step_2",
                    task_id="task_1",
                    description="Summarize search evidence.",
                    step_type="model",
                    depends_on=["step_1"],
                    input_from=["step_1"],
                    expected_output="Search summary",
                ),
            ],
            available_tools=tool_manager.get_registry().tool_names(),
        )

        result = _execute(
            self,
            plan,
            tool_manager,
            _task(self.root, allow_network=True),
        )

        self.assertTrue(result.success)
        self.assertEqual([request.tool_name for request in tool_manager.requests], ["web_search"])
        self.assertIsInstance(result.observations[0].data, dict)
        self.assertEqual(result.observations[0].data["provider"], "fake")
        self.assertEqual(result.observations[0].model_consumable_observation["data"]["provider"], "fake")

    def test_mcp_dynamic_tool_flows_through_gateway_and_observation(self):
        manager, self.mcp_client = _mcp_tool_manager(self.root, self.model_manager)
        tool_manager = RecordingToolManager(manager)
        mcp_tool_name = next(
            name
            for name in tool_manager.get_registry().tool_names()
            if name.startswith("mcp.fake.search")
        )
        plan = _plan(
            "mcp search",
            [
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Call fake MCP search.",
                    step_type="tool",
                    tool_name=mcp_tool_name,
                    args={"query": "agent"},
                    output_key="mcp_result",
                )
            ],
            available_tools=tool_manager.get_registry().tool_names(),
        )

        result = _execute(self, plan, tool_manager, _task(self.root, allow_mcp=True))

        self.assertTrue(result.success)
        self.assertEqual([request.tool_name for request in tool_manager.requests], [mcp_tool_name])
        self.assertEqual(result.observations[0].tool_name, mcp_tool_name)
        self.assertEqual(result.observations[0].code, ToolErrorCode.OK.value)


def _models_config(root: Path) -> ModelsConfig:
    return ModelsConfig(
        workspace_root=root,
        config_dir=root / "config" / "models",
        runtime=ModelsRuntimeConfig(
            logs_path=root / "logs" / "models.log",
            retry_backoff_base_seconds=0.0,
            retry_backoff_max_seconds=0.0,
        ),
        provider_specs=default_provider_specs(),
        provider_confs={},
        routes=default_route_configs(),
        structured_output={"repair_enabled": True, "default_repair_attempts": 1},
    )


def _model_manager(root: Path, script: ScriptedActionModel) -> ModelManager:
    manager = ModelManager(model_name="mock", models_config=_models_config(root))
    manager.model = script
    return manager


def _tool_manager(root: Path, model_manager: ModelManager) -> ToolManager:
    return ToolManager(
        workspace_root=root,
        model_manager=model_manager,
        logger=NullToolLogger(),
    )


def _web_search_tool_manager(root: Path, model_manager: ModelManager) -> ToolManager:
    config = default_tools_config(root)
    config.providers = {
        "web_search": {
            "provider": "fake",
            "fake": {"enabled": True, "scenario": "success"},
        }
    }
    return ToolManager(
        workspace_root=root,
        model_manager=model_manager,
        tools_config=config,
        logger=NullToolLogger(),
    )


def _mcp_tool_manager(root: Path, model_manager: ModelManager) -> tuple[ToolManager, MCPStdioClient]:
    server_config = MCPServerConfig.from_mapping(
        "fake",
        {
            "enabled": True,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(FAKE_MCP_SERVER)],
            "cwd": ".",
            "passEnv": False,
            "timeout_seconds": 2,
        },
        workspace_root=root,
    )
    client = MCPStdioClient(server_config.resolve_runtime(environment={}))
    discovery = client.list_tools()
    gateway = MCPToolGateway({"fake": client})
    manager = ToolManager(
        workspace_root=root,
        model_manager=model_manager,
        mcp_gateway=gateway,
        logger=NullToolLogger(),
    )
    register_mcp_tool_specs(
        manager.get_registry(),
        adapt_mcp_discovery_to_specs(discovery, client.config),
    )
    return manager, client


def _plan(goal: str, steps: list[PlanStep], *, available_tools: list[str]) -> TaskPlan:
    return TaskPlan(
        goal=goal,
        mode="meso",
        steps=steps,
        task_units=[
            TaskUnit(
                id="task_1",
                title=goal,
                step_ids=[step.id for step in steps],
            )
        ],
        available_tools=list(available_tools),
        required_tools=[step.tool_name for step in steps if step.tool_name],
        can_execute=True,
        plan_validation_status="valid",
    )


def _task(root: Path, **capabilities: bool) -> SimpleNamespace:
    values = {
        "trace_id": "trace_step43",
        "session_id": "session_step43",
        "mode": "solo",
        "task_type": "acceptance",
        "execution_strategy": "meso",
        "action_policy": "allow",
        "requires_clarification": False,
        "requires_confirmation": False,
        "confirmation_reason": None,
        "tool_strategy": "tool",
        "available_tools": [],
        "missing_tools": [],
        "intent": ["acceptance"],
        "intent_sequence": ["acceptance"],
        "parameters": {},
        "file_info": {},
        "workspace_root": str(root),
        "allow_read_workspace": True,
        "allow_write_workspace": False,
        "allow_network": False,
        "allow_command": False,
        "allow_shell_command": False,
        "allow_mcp": False,
    }
    values.update(capabilities)
    return SimpleNamespace(**values)


def _executor(
    root: Path,
    model_manager: ModelManager,
    tool_manager: RecordingToolManager,
) -> ReActExecutor:
    config_values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
    config_values.update(
        {
            "workspace_root": str(root),
            "react_executor_log_path": str(root / "logs" / "react_executor_step43.log"),
            "command_confirmation_policy": "ask",
            "enable_command_tool": True,
        }
    )
    config = ReActExecutorConfig(root=root, react_executor_config=config_values)
    return ReActExecutor(
        model_manager=model_manager,
        tool_manager=tool_manager,
        tool_registry=tool_manager.get_registry(),
        config=config,
        execution_logger=ReActExecutorLogger(config.react_executor_log_path),
    )


def _execute(
    testcase: ToolsEndToEndFakeTest,
    plan: TaskPlan,
    tool_manager: RecordingToolManager,
    task: SimpleNamespace,
):
    testcase.model_script.bind_plan(plan)
    executor = _executor(testcase.root, testcase.model_manager, tool_manager)
    return executor.execute(plan, task, plan.goal, history="")


def _execute_with_confirmation(
    testcase: ToolsEndToEndFakeTest,
    plan: TaskPlan,
    tool_manager: RecordingToolManager,
    task: SimpleNamespace,
):
    testcase.model_script.bind_plan(plan)
    executor = _executor(testcase.root, testcase.model_manager, tool_manager)
    context = executor._create_context(plan, task, plan.goal, history="")
    initial = executor._run_plan_precheck(context)
    if initial is None:
        initial = executor._execute_react_loop(context)
    pending = context.pending_confirmation
    testcase.assertIsNotNone(pending)
    resumed = executor.resume_after_confirmation(
        context,
        approved=True,
        confirmation_id=pending.confirmation_id,
        preview_hash=pending.preview_hash,
    )
    return initial, resumed


def _dispatch_and_resume(
    testcase: ToolsEndToEndFakeTest,
    plan: TaskPlan,
    tool_manager: Any,
    task: SimpleNamespace,
    packet: ActionPacket,
):
    executor = _executor(testcase.root, testcase.model_manager, tool_manager)
    context = executor._create_context(plan, task, plan.goal, history="")
    step = plan.steps[0]
    packet.execution_id = context.execution_id
    packet.plan_id = context.plan_id
    if not packet.task_id:
        packet.task_id = getattr(step, "task_id", None)
    if not packet.step_id:
        packet.step_id = getattr(step, "id", None)
    initial = executor.dispatch_action(context, packet, step=step)
    testcase.assertIsNotNone(context.pending_confirmation)
    pending = context.pending_confirmation
    resumed = executor.resume_after_confirmation(
        context,
        approved=True,
        confirmation_id=pending.confirmation_id,
        preview_hash=pending.preview_hash,
    )
    return initial, resumed, context, pending


if __name__ == "__main__":
    unittest.main()
