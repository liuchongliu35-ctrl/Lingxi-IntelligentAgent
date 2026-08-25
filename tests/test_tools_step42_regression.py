from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.base import ToolResult
from src.tools.command_policy import evaluate_command_policy, evaluate_shell_command_policy
from src.tools.config import clear_tools_config_cache, load_tools_config
from src.tools.errors import ToolErrorCode
from src.tools.mcp.config import MCPServerConfig
from src.tools.mcp.protocol import MCPResolvedServerConfig
from src.tools.mcp.adapter import (
    MCPRemoteTool,
    MCPToolDiscoveryResult,
    adapt_mcp_discovery_to_specs,
    register_mcp_tool_specs,
)
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.policy import ToolPolicy
from src.tools.registry import ToolRegistry, ToolSpec, build_default_tool_registry
from src.tools.tool_logger import JsonlToolLogger, NullToolLogger
from src.tools.tool_manager import ToolManager


class ToolsStep42RegressionTest(unittest.TestCase):
    def tearDown(self) -> None:
        clear_tools_config_cache()

    def test_default_registry_model_specs_are_enabled_and_implemented(self):
        manager = ToolManager(logger=NullToolLogger())
        registry = manager.get_registry()
        tool_names = set(manager.tools)
        aliases = registry.list_aliases()

        model_names = {spec["name"] for spec in registry.to_model_specs()}

        self.assertIn("web_search", model_names)
        self.assertIn("command_tool", model_names)
        self.assertIn("shell_command_tool", model_names)
        self.assertNotIn("code_executor", model_names)
        self.assertEqual(aliases["search_tool"], "web_search")
        self.assertEqual(aliases["shell_tool"], "shell_command_tool")

        for spec in registry.list_specs():
            if not spec.enabled or spec.metadata.get("implemented", True) is False:
                continue
            if spec.metadata.get("source_type") == "mcp":
                continue
            self.assertIn(spec.name, tool_names)

    def test_registry_rejects_alias_conflicts_and_disabled_tools_are_not_model_visible(self):
        registry = ToolRegistry(
            [
                ToolSpec(name="read_file", description="Read.", aliases=["reader"]),
                ToolSpec(
                    name="code_executor",
                    description="Disabled.",
                    enabled=False,
                    metadata={"implemented": False},
                ),
            ]
        )

        with self.assertRaises(ValueError):
            registry.register(ToolSpec(name="other", description="Other."), aliases=["reader"])
        with self.assertRaises(ValueError):
            registry.register_alias("read_file", "code_executor")

        self.assertEqual([item["name"] for item in registry.to_model_specs()], ["read_file"])

    def test_safety_matrix_for_file_boundaries_and_confirmation(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "repeat.txt").write_text("same\nsame\n", encoding="utf-8")
            (root / "existing.txt").write_text("old", encoding="utf-8")
            outside = root.parent / "outside-step42.txt"

            manager = ToolManager(workspace_root=root, logger=NullToolLogger())

            cases = {
                "workspace_outside_read": _execute(
                    manager,
                    "read_file",
                    {"path": str(outside)},
                    root,
                    ToolCallOptions(allow_read_workspace=True),
                ),
                "workspace_outside_write": _execute(
                    manager,
                    "write_file",
                    {"path": str(outside), "content": "x", "write_mode": "create"},
                    root,
                    ToolCallOptions(allow_write_workspace=True),
                ),
                "workspace_outside_delete": _execute(
                    manager,
                    "delete_file",
                    {"path": str(outside)},
                    root,
                    ToolCallOptions(
                        allow_write_workspace=True,
                        confirmed=True,
                        confirmation_id="confirmation-1",
                        preview_hash="preview-1",
                    ),
                ),
                "sensitive_read": _execute(
                    manager,
                    "read_file",
                    {"path": ".env"},
                    root,
                    ToolCallOptions(allow_read_workspace=True),
                ),
                "sensitive_write": _execute(
                    manager,
                    "write_file",
                    {"path": ".env", "content": "x", "write_mode": "create"},
                    root,
                    ToolCallOptions(allow_write_workspace=True),
                ),
                "sensitive_delete": _execute(
                    manager,
                    "delete_file",
                    {"path": ".env"},
                    root,
                    ToolCallOptions(
                        allow_write_workspace=True,
                        confirmed=True,
                        confirmation_id="confirmation-1",
                        preview_hash="preview-1",
                    ),
                ),
                "overwrite_no_confirmation": _execute(
                    manager,
                    "write_file",
                    {
                        "path": "existing.txt",
                        "content": "new",
                        "write_mode": "overwrite",
                    },
                    root,
                    ToolCallOptions(allow_write_workspace=True),
                ),
                "patch_ambiguous_match": _execute(
                    manager,
                    "patch_file",
                    {
                        "path": "repeat.txt",
                        "patches": [
                            {
                                "operation": "replace",
                                "old_text": "same",
                                "new_text": "changed",
                            }
                        ],
                    },
                    root,
                    ToolCallOptions(allow_write_workspace=True, dry_run=True),
                ),
                "glob_delete": _execute(
                    manager,
                    "delete_file",
                    {"path": "*.txt"},
                    root,
                    ToolCallOptions(allow_write_workspace=True, dry_run=True),
                ),
                "directory_delete": _execute(
                    manager,
                    "delete_file",
                    {"path": "."},
                    root,
                    ToolCallOptions(allow_write_workspace=True, dry_run=True),
                ),
            }

        self.assertEqual(
            {name: result.code for name, result in cases.items()},
            {
                "workspace_outside_read": ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value,
                "workspace_outside_write": ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value,
                "workspace_outside_delete": ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value,
                "sensitive_read": ToolErrorCode.CONFIRMATION_REQUIRED.value,
                "sensitive_write": ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
                "sensitive_delete": ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
                "overwrite_no_confirmation": ToolErrorCode.CONFIRMATION_REQUIRED.value,
                "patch_ambiguous_match": ToolErrorCode.PATCH_AMBIGUOUS_MATCH.value,
                "glob_delete": ToolErrorCode.GLOB_DELETE_NOT_ALLOWED.value,
                "directory_delete": ToolErrorCode.DELETE_DIRECTORY_NOT_ALLOWED.value,
            },
        )

    def test_command_shell_network_admin_mcp_and_model_forged_confirmation_matrix(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            manager = ToolManager(workspace_root=root, logger=NullToolLogger())

            command_delete = evaluate_command_policy(
                program="rm",
                args=["old.txt"],
                command_text="rm old.txt",
                workspace_root=root,
            )
            code_executor = _execute(
                manager,
                "code_executor",
                {"code": "print('x')"},
                root,
                ToolCallOptions(
                    allow_command=True,
                    confirmed=True,
                    confirmation_id="confirmation-1",
                    preview_hash="preview-1",
                ),
            )
            forged_confirmation = ToolPolicy().decide(
                ToolSpec(name="high", description="High risk.", risk_level="high"),
                ToolCallRequest(
                    tool_name="high",
                    args={"confirmed": True},
                    context=ToolCallContext(workspace_root=str(root), source="test"),
                    options=ToolCallOptions(),
                ),
            )

            shell_complex = evaluate_command_policy(
                program="powershell",
                args=["-Command", "Write-Output ok"],
                command_text="powershell -Command Write-Output ok",
                workspace_root=root,
            )
            admin = evaluate_command_policy(
                program="sudo",
                args=["pytest"],
                command_text="sudo pytest",
                workspace_root=root,
            )
            network = evaluate_shell_command_policy(
                command_text="curl https://example.invalid",
                shell="cmd",
                workspace_root=root,
                tool_call_options=ToolCallOptions(allow_shell_command=True, allow_network=False),
            )
            mcp_unauthorized = _mcp_manager(root).execute(
                ToolCallRequest(
                    tool_name="mcp.fake.search",
                    args={"query": "agent"},
                    context=ToolCallContext(workspace_root=str(root), source="test"),
                    options=ToolCallOptions(allow_mcp=False),
                )
            )
            http_spec = ToolSpec(
                name="mcp.remote.search",
                description="Remote MCP.",
                workspace_scope="mcp",
                metadata={
                    "source_type": "mcp",
                    "server_id": "remote",
                    "remote_tool_name": "search",
                    "transport": "streamable_http",
                },
            )
            mcp_remote = _mcp_manager(root, registry=ToolRegistry([http_spec])).execute(
                ToolCallRequest(
                    tool_name="mcp.remote.search",
                    args={},
                    context=ToolCallContext(workspace_root=str(root), source="test"),
                    options=ToolCallOptions(allow_mcp=True),
                )
            )

        self.assertEqual(command_delete.code, ToolErrorCode.COMMAND_DELETE_NOT_ALLOWED.value)
        self.assertEqual(shell_complex.code, ToolErrorCode.SHELL_REQUIRED.value)
        self.assertEqual(admin.code, ToolErrorCode.COMMAND_BLOCKED.value)
        self.assertEqual(network.code, ToolErrorCode.NETWORK_NOT_ALLOWED.value)
        self.assertEqual(mcp_unauthorized.code, ToolErrorCode.PERMISSION_DENIED.value)
        self.assertEqual(mcp_remote.code, ToolErrorCode.MCP_SERVER_NOT_FOUND.value)
        self.assertEqual(code_executor.code, ToolErrorCode.TOOL_DISABLED.value)
        self.assertEqual(forged_confirmation.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)

    def test_config_audit_is_conservative_and_does_not_persist_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = load_tools_config(root)

            self.assertFalse(config.policy.default_permissions["allow_write_workspace"])
            self.assertFalse(config.policy.default_permissions["allow_network"])
            self.assertFalse(config.policy.default_permissions["allow_command"])
            self.assertFalse(config.policy.default_permissions["allow_mcp"])

        clear_tools_config_cache()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_json(
                root,
                "providers.json",
                {"web_search": {"provider": "search_api", "api_key": "plain-secret"}},
            )

            with self.assertRaises(Exception) as caught:
                load_tools_config(root)

            self.assertEqual(getattr(caught.exception, "code", None), "plain_secret_in_config")

        clear_tools_config_cache()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_json(
                root,
                "providers.json",
                {"web_search": {"provider": "search_api", "api_key_env": "STEP42_API_KEY"}},
            )
            with patch.dict("os.environ", {"STEP42_API_KEY": "runtime-secret"}, clear=False):
                config = load_tools_config(root)

            serialized = json.dumps(config.to_dict(), ensure_ascii=False)

            self.assertIn("STEP42_API_KEY", serialized)
            self.assertNotIn("runtime-secret", serialized)

    def test_invalid_config_fallback_and_log_audit_redact_raw_secret_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config" / "tools"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "policies.json").write_text("{invalid", encoding="utf-8")
            manager = ToolManager(
                workspace_root=root,
                registry=ToolRegistry(),
                tools={},
                logger=NullToolLogger(),
            )

            self.assertIsNotNone(manager.config_error)
            self.assertFalse(manager.runtime.policy.default_permissions["allow_network"])
            self.assertFalse(manager.runtime.policy.default_permissions["allow_command"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = root / "logs" / "tools.log"
            logger = JsonlToolLogger(log_path)
            request = ToolCallRequest(
                tool_name="web_search",
                args={"query": "token check", "api_key": "input-secret"},
                context=ToolCallContext(workspace_root=str(root), source="test"),
                options=ToolCallOptions(allow_network=True),
            )
            result = ToolResult.ok(
                data={
                    "content": "Authorization: Bearer output-secret",
                    "token": "data-secret",
                    "count": 1,
                },
                raw_output="password=raw-secret",
            )

            self.assertTrue(logger.record(request, result))
            log_text = log_path.read_text(encoding="utf-8")
            record = json.loads(log_text)

            self.assertNotIn("input-secret", log_text)
            self.assertNotIn("output-secret", log_text)
            self.assertNotIn("data-secret", log_text)
            self.assertNotIn("raw-secret", log_text)
            self.assertEqual(record["input_summary"]["parameter_keys"], ["query"])
            self.assertIn("raw_output_hash", record)

    def test_disabled_mcp_server_does_not_register_dynamic_model_tools(self):
        registry = build_default_tool_registry(include_command_tool=True)
        disabled_config = MCPResolvedServerConfig(
            server_id="fake",
            display_name="Fake",
            enabled=False,
            transport="stdio",
            command=sys.executable,
            args=[],
            env={},
            cwd=str(Path.cwd()),
            pass_env=False,
            allowed_tools=[],
            tool_policies={},
            default_risk_level="medium",
            timeout_seconds=30,
        )
        discovery = MCPToolDiscoveryResult(
            server_id="fake",
            tools=[
                MCPRemoteTool(
                    server_id="fake",
                    name="search",
                    description="Search.",
                    input_schema={"type": "object", "properties": {}},
                )
            ],
        )

        if disabled_config.enabled:
            register_mcp_tool_specs(
                registry,
                adapt_mcp_discovery_to_specs(discovery, disabled_config),
            )

        self.assertNotIn("mcp.fake.search", registry.tool_names())
        self.assertNotIn("mcp.fake.search", {item["name"] for item in registry.to_model_specs()})

    def test_mcp_streamable_http_config_is_loadable_but_not_execution_ready(self):
        with tempfile.TemporaryDirectory() as workspace:
            config = MCPServerConfig.from_mapping(
                "remote",
                {
                    "enabled": True,
                    "transport": "streamable_http",
                    "endpoint_url": "https://mcp.example.invalid",
                },
                workspace_root=workspace,
            )

        self.assertFalse(config.transport_supported_for_execution)
        self.assertEqual(config.transport, "streamable_http")


def _execute(
    manager: ToolManager,
    tool_name: str,
    args: dict,
    workspace_root: Path,
    options: ToolCallOptions,
) -> ToolResult:
    return manager.execute(
        ToolCallRequest(
            tool_name=tool_name,
            args=args,
            context=ToolCallContext(workspace_root=str(workspace_root), source="test"),
            options=options,
        )
    )


def _write_json(root: Path, name: str, payload: object) -> None:
    config_dir = root / "config" / "tools"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / name).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _mcp_manager(
    workspace_root: Path,
    *,
    registry: ToolRegistry | None = None,
) -> ToolManager:
    if registry is None:
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="mcp.fake.search",
                    description="Fake MCP search.",
                    parameters_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                    required_params=["query"],
                    workspace_scope="mcp",
                    metadata={
                        "source_type": "mcp",
                        "server_id": "fake",
                        "remote_tool_name": "search",
                    },
                )
            ]
        )
    return ToolManager(
        workspace_root=workspace_root,
        registry=registry,
        tools={},
        logger=NullToolLogger(),
    )


if __name__ == "__main__":
    unittest.main()
