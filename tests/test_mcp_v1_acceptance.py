from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.tools import (
    MCPManager,
    MCPToolGateway,
    ToolErrorCode,
    load_mcp_servers_config_file,
    save_mcp_servers_config_file,
)
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest, ToolCallSource
from src.tools.registry import ToolRegistry
from src.tools.tool_logger import JsonlToolLogger
from src.tools.tool_manager import ToolManager


FAKE_SERVER = Path(__file__).with_name("fixtures") / "fake_mcp_server.py"


class MCPV1AcceptanceTest(unittest.TestCase):
    def test_fake_mcp_full_chain_from_config_to_tool_result_and_cleanup(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config" / "tools" / "mcp_servers.json"
            log_path = root / "logs" / "tools.log"
            with patch.dict(
                os.environ,
                {
                    "FAKE_MCP_STDERR": "token=stderr-secret",
                    "FAKE_MCP_REFRESH_CHANGES": "1",
                },
                clear=False,
            ):
                save_mcp_servers_config_file(
                    {
                        "mcpServers": {
                            "fake": {
                                "enabled": True,
                                "transport": "stdio",
                                "command": sys.executable,
                                "args": [str(FAKE_SERVER)],
                                "env": {
                                    "FAKE_MCP_STDERR": "${env:FAKE_MCP_STDERR}",
                                    "FAKE_MCP_REFRESH_CHANGES": "${env:FAKE_MCP_REFRESH_CHANGES}",
                                },
                                "cwd": ".",
                                "passEnv": False,
                                "tool_policies": {
                                    "structured": {"risk_level": "high"},
                                    "resource": {"risk_level": "blocked"},
                                },
                                "timeout_seconds": 2,
                            }
                        }
                    },
                    config_path,
                    workspace_root=root,
                )
                loaded_config = load_mcp_servers_config_file(
                    config_path,
                    workspace_root=root,
                )
                registry = ToolRegistry()
                gateway = MCPToolGateway()
                mcp_manager = MCPManager(
                    loaded_config,
                    registry=registry,
                    gateway=gateway,
                    environment=os.environ,
                )
                managed = mcp_manager.start_server("fake")
                started_state = managed.connection_info.state
                logger = JsonlToolLogger(log_path)
                tool_manager = ToolManager(
                    tools={},
                    registry=registry,
                    mcp_gateway=gateway,
                    logger=logger,
                    workspace_root=root,
                )

                model_tool_names = {
                    item["name"]
                    for item in registry.to_model_specs()
                }
                success = tool_manager.execute(
                    _request("mcp.fake.search", {"query": "agent"}, workspace_root=root)
                )
                invalid_args = tool_manager.execute(
                    _request("mcp.fake.search", {}, workspace_root=root)
                )
                remote_error = tool_manager.execute(
                    _request("mcp.fake.remote_error_tool", workspace_root=root)
                )
                high_pending = tool_manager.execute(
                    _request(
                        "mcp.fake.structured",
                        {"value": "ok"},
                        workspace_root=root,
                    )
                )
                dry_run = tool_manager.execute(
                    _request(
                        "mcp.fake.structured",
                        {"value": "ok"},
                        workspace_root=root,
                        options=ToolCallOptions(allow_mcp=True, dry_run=True),
                    )
                )
                confirmed = tool_manager.execute(
                    _request(
                        "mcp.fake.structured",
                        {"value": "ok"},
                        workspace_root=root,
                        options=ToolCallOptions(
                            allow_mcp=True,
                            confirmed=True,
                            confirmation_id="confirm-structured",
                            preview_hash=dry_run.metadata["output_control"]["preview_hash"],
                        ),
                    )
                )
                blocked = tool_manager.execute(
                    _request("mcp.fake.resource", workspace_root=root)
                )
                refreshed = mcp_manager.refresh_server("fake")
                process_exit = tool_manager.execute(
                    _request("mcp.fake.exit_now", {"code": 7}, workspace_root=root)
                )
                removed = mcp_manager.stop_server("fake")

            log_text = log_path.read_text(encoding="utf-8")
            records = [
                json.loads(line)
                for line in log_text.splitlines()
                if line.strip()
            ]

        self.assertEqual(started_state, "ready")
        self.assertIn("mcp.fake.search", model_tool_names)
        self.assertTrue(success.success)
        self.assertEqual(success.code, ToolErrorCode.OK.value)
        self.assertEqual(success.data.remote_tool_name, "search")
        self.assertFalse(invalid_args.success)
        self.assertEqual(invalid_args.code, ToolErrorCode.MISSING_REQUIRED_PARAM.value)
        self.assertFalse(remote_error.success)
        self.assertEqual(remote_error.code, ToolErrorCode.MCP_REMOTE_ERROR.value)
        self.assertFalse(high_pending.success)
        self.assertEqual(high_pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
        self.assertTrue(dry_run.success)
        self.assertEqual(dry_run.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
        self.assertFalse(
            dry_run.metadata["output_control"]["preview"]["mcp"]["remote_simulation_performed"]
        )
        self.assertTrue(confirmed.success)
        self.assertFalse(blocked.success)
        self.assertEqual(blocked.code, ToolErrorCode.BLOCKED_BY_POLICY.value)
        self.assertEqual(refreshed.discovery.metadata["changed_tools"], ["search"])
        self.assertFalse(process_exit.success)
        self.assertEqual(process_exit.code, ToolErrorCode.MCP_PROCESS_EXITED.value)
        self.assertIn("mcp.fake.search", removed)
        self.assertFalse(registry.has_tool("mcp.fake.search"))
        self.assertIsNone(gateway.get_client("fake"))
        self.assertTrue(records)
        self.assertIn("mcp", records[0]["metadata"])
        self.assertNotIn("stderr-secret", log_text)
        self.assertNotIn("FAKE_MCP_STDERR", log_text)

    def test_fake_timeout_is_structured_and_cleanup_removes_specs(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config" / "tools" / "mcp_servers.json"
            save_mcp_servers_config_file(
                {
                    "mcpServers": {
                        "fake": {
                            "enabled": True,
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": [str(FAKE_SERVER)],
                            "cwd": ".",
                            "passEnv": False,
                            "timeout_seconds": 2,
                        }
                    }
                },
                config_path,
                workspace_root=root,
            )
            registry = ToolRegistry()
            gateway = MCPToolGateway()
            mcp_manager = MCPManager(
                load_mcp_servers_config_file(config_path, workspace_root=root),
                registry=registry,
                gateway=gateway,
                environment=os.environ,
            )
            mcp_manager.start_server("fake")
            tool_manager = ToolManager(
                tools={},
                registry=registry,
                mcp_gateway=gateway,
                workspace_root=root,
            )

            timeout = tool_manager.execute(
                _request(
                    "mcp.fake.sleep",
                    {"seconds": 2},
                    workspace_root=root,
                    options=ToolCallOptions(allow_mcp=True, timeout_seconds=1),
                )
            )
            removed = mcp_manager.stop_server("fake")

        self.assertFalse(timeout.success)
        self.assertEqual(timeout.code, ToolErrorCode.MCP_TIMEOUT.value)
        self.assertIn("mcp.fake.sleep", removed)


def _request(
    tool_name: str,
    args: dict | None = None,
    *,
    workspace_root: Path,
    options: ToolCallOptions | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        args=dict(args or {}),
        context=ToolCallContext(
            source=ToolCallSource.TEST.value,
            trace_id="trace-mcp-acceptance",
            workspace_root=workspace_root,
        ),
        options=options or ToolCallOptions(allow_mcp=True),
    )


if __name__ == "__main__":
    unittest.main()
