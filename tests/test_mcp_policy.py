from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.tools import (
    MCPRemoteTool,
    MCPResolvedServerConfig,
    MCPServerConfig,
    MCPStdioClient,
    MCPToolDiscoveryResult,
    MCPToolGateway,
    ToolErrorCode,
    adapt_mcp_discovery_to_specs,
    register_mcp_tool_specs,
)
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest, ToolCallSource
from src.tools.registry import ToolRegistry
from src.tools.tool_logger import JsonlToolLogger
from src.tools.tool_manager import ToolManager


FAKE_SERVER = Path(__file__).with_name("fixtures") / "fake_mcp_server.py"


def runtime_config(
    *,
    tool_policies: dict | None = None,
    env: dict[str, str] | None = None,
) -> MCPResolvedServerConfig:
    config = MCPServerConfig.from_mapping(
        "fake",
        {
            "enabled": True,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(FAKE_SERVER)],
            "env": env or {},
            "cwd": ".",
            "passEnv": False,
            "tool_policies": tool_policies or {},
            "timeout_seconds": 2,
        },
        workspace_root=Path.cwd(),
    )
    return config.resolve_runtime(environment=os.environ)


def request(
    tool_name: str,
    args: dict | None = None,
    *,
    options: ToolCallOptions | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        args=dict(args or {}),
        context=ToolCallContext(
            source=ToolCallSource.TEST.value,
            trace_id="trace-mcp-policy",
            workspace_root=Path.cwd(),
        ),
        options=options or ToolCallOptions(allow_mcp=True),
    )


def remote_tool(name: str = "search") -> MCPRemoteTool:
    return MCPRemoteTool(
        server_id="fake",
        name=name,
        description=f"{name} tool.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


class MCPPolicyTest(unittest.TestCase):
    def tearDown(self) -> None:
        for client in getattr(self, "_clients", []):
            try:
                client.stop()
            except Exception:
                pass

    def _manager(
        self,
        *,
        tool_policies: dict | None = None,
        logger=None,
        gateway_clients: bool = True,
        env: dict[str, str] | None = None,
    ) -> ToolManager:
        self._clients = getattr(self, "_clients", [])
        client = MCPStdioClient(runtime_config(tool_policies=tool_policies, env=env))
        self._clients.append(client)
        discovery = client.list_tools()
        registry = ToolRegistry()
        adapter_result = adapt_mcp_discovery_to_specs(discovery, client.config)
        register_mcp_tool_specs(registry, adapter_result)
        gateway = MCPToolGateway({"fake": client} if gateway_clients else {})
        return ToolManager(
            tools={},
            registry=registry,
            mcp_gateway=gateway,
            logger=logger,
        )

    def test_allow_mcp_false_blocks_before_gateway(self):
        manager = self._manager(gateway_clients=False)

        result = manager.execute(
            request(
                "mcp.fake.search",
                {"query": "agent"},
                options=ToolCallOptions(allow_mcp=False),
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.PERMISSION_DENIED.value)

    def test_high_risk_mcp_requires_confirmation_and_confirmed_call_runs(self):
        manager = self._manager(tool_policies={"search": {"risk_level": "high"}})

        pending = manager.execute(request("mcp.fake.search", {"query": "agent"}))
        dry_run = manager.execute(
            request(
                "mcp.fake.search",
                {"query": "agent"},
                options=ToolCallOptions(allow_mcp=True, dry_run=True),
            )
        )
        preview_hash = dry_run.metadata["output_control"]["preview_hash"]
        confirmed = manager.execute(
            request(
                "mcp.fake.search",
                {"query": "agent"},
                options=ToolCallOptions(
                    allow_mcp=True,
                    confirmed=True,
                    confirmation_id="confirm-mcp-1",
                    preview_hash=preview_hash,
                ),
            )
        )

        self.assertFalse(pending.success)
        self.assertEqual(pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
        self.assertTrue(dry_run.success)
        self.assertEqual(dry_run.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
        self.assertTrue(confirmed.success)
        self.assertEqual(confirmed.code, ToolErrorCode.OK.value)

    def test_blocked_mcp_cannot_be_released_by_confirmation(self):
        manager = self._manager(tool_policies={"search": {"risk_level": "blocked"}})

        result = manager.execute(
            request(
                "mcp.fake.search",
                {"query": "agent"},
                options=ToolCallOptions(
                    allow_mcp=True,
                    confirmed=True,
                    confirmation_id="confirm-blocked",
                    preview_hash="any-preview",
                ),
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.BLOCKED_BY_POLICY.value)

    def test_mcp_dry_run_is_local_preview_only_and_does_not_require_client(self):
        config = runtime_config(tool_policies={"search": {"risk_level": "high"}})
        registry = ToolRegistry()
        adapter_result = adapt_mcp_discovery_to_specs(
            MCPToolDiscoveryResult(server_id="fake", tools=[remote_tool("search")]),
            config,
        )
        register_mcp_tool_specs(registry, adapter_result)
        manager = ToolManager(
            tools={},
            registry=registry,
            mcp_gateway=MCPToolGateway(),
        )

        result = manager.execute(
            request(
                "mcp.fake.search",
                {"query": "agent", "api_token": "argument-secret"},
                options=ToolCallOptions(allow_mcp=True, dry_run=True),
            )
        )
        preview = result.metadata["output_control"]["preview"]["mcp"]
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)

        self.assertTrue(result.success)
        self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
        self.assertEqual(preview["server_id"], "fake")
        self.assertEqual(preview["remote_tool_name"], "search")
        self.assertFalse(preview["remote_simulation_performed"])
        self.assertEqual(preview["dry_run_scope"], "local_precheck_only")
        self.assertEqual(preview["arguments_summary"]["api_token"], "<redacted>")
        self.assertNotIn("argument-secret", serialized)

    def test_mcp_log_has_safe_audit_fields_and_no_secret_values(self):
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "tools.log"
            logger = JsonlToolLogger(log_path)
            manager = self._manager(
                logger=logger,
                env={"FAKE_MCP_STDERR": "token=stderr-secret"},
            )

            result = manager.execute(request("mcp.fake.search", {"query": "agent"}))
            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertTrue(result.success)
        self.assertTrue(records)
        record = records[-1]
        mcp = record["metadata"]["mcp"]
        log_text = json.dumps(record, ensure_ascii=False)

        self.assertEqual(mcp["server_id"], "fake")
        self.assertEqual(mcp["remote_tool_name"], "search")
        self.assertEqual(mcp["transport"], "stdio")
        self.assertEqual(mcp["command_summary"]["command"], Path(sys.executable).name)
        self.assertEqual(mcp["command_summary"]["args_count"], 1)
        self.assertEqual(mcp["argument_keys"], ["query"])
        self.assertFalse(mcp["fallback_performed"])
        self.assertNotIn("stderr-secret", log_text)
        self.assertNotIn("FAKE_MCP_STDERR", log_text)

    def test_mcp_failure_does_not_invoke_command_fallback_inside_tools(self):
        manager = self._manager()
        calls: list[dict] = []

        class CommandFallback:
            def run(self, **kwargs):
                calls.append(kwargs)
                return {"unexpected": True}

        manager.tools["command_tool"] = CommandFallback()

        result = manager.execute(request("mcp.fake.remote_error_tool"))

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.MCP_REMOTE_ERROR.value)
        self.assertEqual(calls, [])
        self.assertFalse(result.metadata["mcp_gateway"]["fallback_performed"])


if __name__ == "__main__":
    unittest.main()
