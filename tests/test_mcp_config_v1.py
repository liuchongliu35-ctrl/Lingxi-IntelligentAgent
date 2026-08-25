from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tools import (
    MCPConfigError,
    MCPServersConfig,
    MCP_TRANSPORT_STREAMABLE_HTTP,
    ToolsConfigError,
    clear_tools_config_cache,
    load_mcp_servers_config_file,
    load_tools_config,
    save_mcp_servers_config_file,
)


def _stdio_config(**overrides):
    values = {
        "mcpServers": {
            "github": {
                "display_name": "GitHub",
                "enabled": True,
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "${env:GITHUB_TOKEN}"},
                "cwd": ".",
                "passEnv": False,
                "allowed_tools": ["search_repositories"],
                "tool_policies": {
                    "create_issue": {
                        "risk_level": "high",
                        "requires_confirmation": True,
                    }
                },
                "default_risk_level": "medium",
                "timeout_seconds": 30,
            }
        }
    }
    values["mcpServers"]["github"].update(overrides)
    return values


class MCPConfigV1Test(unittest.TestCase):
    def test_loads_valid_stdio_config_from_tools_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config" / "tools"
            config_dir.mkdir(parents=True)
            (config_dir / "mcp_servers.json").write_text(
                json.dumps(_stdio_config(), ensure_ascii=False),
                encoding="utf-8",
            )

            clear_tools_config_cache()
            config = load_tools_config(root)
            self.addCleanup(clear_tools_config_cache)

        server = config.mcp_servers.get("github")
        self.assertIsNotNone(server)
        assert server is not None
        self.assertEqual(server.server_id, "github")
        self.assertEqual(server.transport, "stdio")
        self.assertEqual(server.command, "npx")
        self.assertEqual(
            server.args,
            ["-y", "@modelcontextprotocol/server-github"],
        )
        self.assertTrue(server.stdio_execution_enabled)
        self.assertTrue(Path(server.cwd_resolved or "").is_absolute())
        self.assertEqual(server.env["GITHUB_TOKEN"], "${env:GITHUB_TOKEN}")

    def test_rejects_invalid_schema_values(self):
        cases = [
            ({"mcpServers": {"bad id": {"transport": "stdio", "command": "npx"}}}, "server_id"),
            (_stdio_config(args="-y package"), "args"),
            (_stdio_config(env=["GITHUB_TOKEN"]), "env"),
            (_stdio_config(env={"BAD-NAME": "value"}), "env"),
            (_stdio_config(command=""), "command"),
            (_stdio_config(cwd=".."), "cwd"),
            (_stdio_config(env={"GITHUB_TOKEN": "plain-secret"}), "env"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            for raw, expected_field in cases:
                with self.subTest(expected_field=expected_field, raw=raw):
                    with self.assertRaises(MCPConfigError) as caught:
                        MCPServersConfig.from_mapping(raw, workspace_root=temp_dir)
                    self.assertEqual(caught.exception.details.get("field"), expected_field)
                    self.assertNotIn("plain-secret", str(caught.exception))

    def test_streamable_http_can_be_saved_as_reserved_config(self):
        raw = {
            "mcpServers": {
                "remote": {
                    "display_name": "Remote",
                    "enabled": False,
                    "transport": MCP_TRANSPORT_STREAMABLE_HTTP,
                    "endpoint_url": "https://mcp.example.test/mcp",
                    "headers": {"Authorization": "${env:MCP_AUTH_TOKEN}"},
                    "credential_ref": "vault:mcp/remote",
                }
            }
        }

        config = MCPServersConfig.from_mapping(raw, workspace_root=".")
        server = config.get("remote")

        self.assertIsNotNone(server)
        assert server is not None
        self.assertEqual(server.transport, MCP_TRANSPORT_STREAMABLE_HTTP)
        self.assertFalse(server.stdio_execution_enabled)
        self.assertFalse(server.transport_supported_for_execution)
        self.assertEqual(server.command, None)

    def test_env_reference_resolves_only_at_runtime_and_serialization_stays_safe(self):
        config = MCPServersConfig.from_mapping(_stdio_config(), workspace_root=".")
        server = config.get("github")
        assert server is not None

        runtime = server.resolve_runtime(
            environment={"GITHUB_TOKEN": "runtime-secret-token"}
        )
        safe = server.to_safe_dict()
        serialized = json.dumps(config.to_config_dict(), ensure_ascii=False)

        self.assertEqual(runtime.env["GITHUB_TOKEN"], "runtime-secret-token")
        self.assertEqual(safe["env"]["GITHUB_TOKEN"], "${env:GITHUB_TOKEN}")
        self.assertNotIn("runtime-secret-token", serialized)
        self.assertNotIn("runtime-secret-token", json.dumps(safe, ensure_ascii=False))

    def test_missing_env_reference_fails_without_secret_value(self):
        config = MCPServersConfig.from_mapping(_stdio_config(), workspace_root=".")
        server = config.get("github")
        assert server is not None

        with self.assertRaises(MCPConfigError) as caught:
            server.resolve_runtime(environment={})

        self.assertEqual(caught.exception.details["env_ref"], "GITHUB_TOKEN")
        self.assertNotIn("runtime-secret", str(caught.exception))

    def test_save_and_load_uses_mcp_servers_root_and_keeps_disabled_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mcp_servers.json"
            saved = save_mcp_servers_config_file(
                _stdio_config(enabled=False),
                path,
                workspace_root=temp_dir,
            )
            loaded = load_mcp_servers_config_file(path, workspace_root=temp_dir)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(sorted(saved.servers), ["github"])
        self.assertIn("mcpServers", payload)
        self.assertNotIn("servers", payload)
        self.assertFalse(loaded.get("github").enabled)
        self.assertEqual(
            payload["mcpServers"]["github"]["env"]["GITHUB_TOKEN"],
            "${env:GITHUB_TOKEN}",
        )

    def test_load_tools_config_maps_mcp_errors_to_tools_config_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "custom-tools"
            config_dir.mkdir(parents=True)
            (config_dir / "mcp_servers.json").write_text(
                json.dumps({"mcpServers": {"bad id": {"command": "npx"}}}),
                encoding="utf-8",
            )

            clear_tools_config_cache()
            with patch.dict("os.environ", {"TOOLS_CONFIG_DIR": str(config_dir)}, clear=False):
                with self.assertRaises(ToolsConfigError) as caught:
                    load_tools_config(root)
            self.addCleanup(clear_tools_config_cache)

        self.assertEqual(caught.exception.details.get("field"), "server_id")


if __name__ == "__main__":
    unittest.main()
