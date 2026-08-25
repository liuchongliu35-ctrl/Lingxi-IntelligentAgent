from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.tools import MCPManager, MCPToolGateway
from src.tools.mcp.config import MCPServersConfig
from src.tools.registry import ToolRegistry


def _integration_enabled() -> bool:
    return (
        os.getenv("RUN_TOOL_INTEGRATION_TESTS", "").lower() == "true"
        and os.getenv("RUN_MCP_INTEGRATION_TESTS", "").lower() == "true"
        and bool(os.getenv("MCP_REAL_STDIO_COMMAND"))
    )


@unittest.skipUnless(
    _integration_enabled(),
    "real MCP STDIO integration requires RUN_TOOL_INTEGRATION_TESTS=true, "
    "RUN_MCP_INTEGRATION_TESTS=true and MCP_REAL_STDIO_COMMAND",
)
class RealMCPStdioIntegrationTest(unittest.TestCase):
    def test_real_stdio_server_can_initialize_and_list_tools(self):
        command = os.environ["MCP_REAL_STDIO_COMMAND"]
        try:
            args = json.loads(os.getenv("MCP_REAL_STDIO_ARGS_JSON", "[]"))
        except json.JSONDecodeError as exc:
            self.skipTest(f"MCP_REAL_STDIO_ARGS_JSON is not valid JSON: {exc}")
        if not isinstance(args, list):
            self.skipTest("MCP_REAL_STDIO_ARGS_JSON must be a JSON array")

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = MCPServersConfig.from_mapping(
                {
                    "mcpServers": {
                        "real": {
                            "enabled": True,
                            "transport": "stdio",
                            "command": command,
                            "args": [str(item) for item in args],
                            "cwd": ".",
                            "passEnv": bool(
                                os.getenv("MCP_REAL_STDIO_PASS_ENV", "").lower() == "true"
                            ),
                            "timeout_seconds": int(
                                os.getenv("MCP_REAL_STDIO_TIMEOUT_SECONDS", "30")
                            ),
                        }
                    }
                },
                workspace_root=root,
            )
            registry = ToolRegistry()
            gateway = MCPToolGateway()
            manager = MCPManager(
                config,
                registry=registry,
                gateway=gateway,
                environment=os.environ,
            )
            managed = manager.start_server("real")
            try:
                self.assertEqual(managed.connection_info.state, "ready")
                self.assertGreater(len(managed.discovery.tools), 0)
                self.assertGreater(len(registry.to_model_specs()), 0)
            finally:
                manager.stop_server("real")


if __name__ == "__main__":
    unittest.main()
