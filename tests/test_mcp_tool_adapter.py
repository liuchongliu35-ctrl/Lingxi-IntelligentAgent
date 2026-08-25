from __future__ import annotations

import json
import unittest

from src.tools import (
    MCPRemoteTool,
    MCPResolvedServerConfig,
    MCPToolDiscoveryResult,
    ToolErrorCode,
    adapt_mcp_discovery_to_specs,
    adapt_mcp_remote_tool,
    infer_mcp_tool_risk,
    mcp_dynamic_source,
    mcp_local_tool_name,
    normalize_mcp_local_tool_segment,
    register_mcp_tool_specs,
    remove_mcp_tool_specs,
)
from src.tools.registry import ToolRegistry, ToolSpec


def resolved_config(**overrides):
    values = {
        "server_id": "github",
        "display_name": "GitHub",
        "enabled": True,
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "runtime-secret"},
        "cwd": "H:/project/agentProject",
        "pass_env": False,
        "allowed_tools": [],
        "tool_policies": {},
        "default_risk_level": "medium",
        "timeout_seconds": 30,
    }
    values.update(overrides)
    return MCPResolvedServerConfig(**values)


def remote_tool(name: str = "search", **overrides):
    values = {
        "server_id": "github",
        "name": name,
        "description": "Search repositories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    }
    values.update(overrides)
    return MCPRemoteTool(**values)


class MCPToolAdapterTest(unittest.TestCase):
    def test_local_names_are_stable_and_encoded_when_needed(self):
        self.assertEqual(mcp_local_tool_name("github", "search"), "mcp.github.search")

        encoded = normalize_mcp_local_tool_segment("search-repositories")
        encoded_again = normalize_mcp_local_tool_segment("search-repositories")
        other = normalize_mcp_local_tool_segment("search_repositories")

        self.assertEqual(encoded, encoded_again)
        self.assertTrue(encoded.startswith("search_repositories_"))
        self.assertNotEqual(encoded, other)

    def test_remote_tool_becomes_safe_tool_spec_metadata(self):
        config = resolved_config()
        tool = remote_tool("search-repositories", title="Search", annotations={"readOnlyHint": True})

        spec = adapt_mcp_remote_tool(tool, config)
        model_spec = spec.to_model_spec()
        serialized = json.dumps(spec.to_dict(), ensure_ascii=False)
        model_serialized = json.dumps(model_spec, ensure_ascii=False)

        self.assertTrue(spec.name.startswith("mcp.github.search_repositories_"))
        self.assertEqual(spec.category, "mcp")
        self.assertEqual(spec.namespace, "mcp.github")
        self.assertEqual(spec.workspace_scope, "mcp")
        self.assertEqual(spec.required_params, ["query"])
        self.assertEqual(spec.risk_level, "low")
        self.assertFalse(spec.requires_confirmation)
        self.assertTrue(spec.supports_dry_run)
        self.assertTrue(model_spec["supports_dry_run"])
        self.assertEqual(spec.metadata["source_type"], "mcp")
        self.assertEqual(spec.metadata["source"], "mcp:github")
        self.assertEqual(spec.metadata["server_id"], "github")
        self.assertEqual(spec.metadata["remote_tool_name"], "search-repositories")
        self.assertEqual(spec.metadata["transport"], "stdio")
        self.assertEqual(spec.metadata["remote_schema_hash"], tool.schema_hash)
        self.assertNotIn("runtime-secret", serialized)
        self.assertNotIn("command", spec.metadata)
        self.assertNotIn("args", spec.metadata)
        self.assertNotIn("env", spec.metadata)
        self.assertNotIn("credential_ref", spec.metadata)
        self.assertNotIn("metadata", model_spec)
        self.assertNotIn("runtime-secret", model_serialized)

    def test_risk_keywords_and_user_policy_override(self):
        read = remote_tool("read_user")
        create = remote_tool("create_issue", description="Create an issue.")
        shell = remote_tool("run_shell", description="Execute shell command.")

        self.assertEqual(infer_mcp_tool_risk(read), "low")
        self.assertEqual(infer_mcp_tool_risk(create), "high")
        self.assertEqual(infer_mcp_tool_risk(shell), "blocked")

        config = resolved_config(
            tool_policies={
                "create_issue": {
                    "risk_level": "medium",
                    "requires_confirmation": False,
                    "timeout_seconds": 9,
                }
            }
        )
        spec = adapt_mcp_remote_tool(create, config)

        self.assertEqual(spec.risk_level, "medium")
        self.assertFalse(spec.requires_confirmation)
        self.assertEqual(spec.timeout_seconds, 9)
        self.assertEqual(spec.metadata["risk_inferred"], "high")
        self.assertEqual(spec.metadata["risk_configured"], "medium")

    def test_discovery_adapter_preserves_skipped_tools_and_schema_hashes(self):
        config = resolved_config()
        discovery = MCPToolDiscoveryResult(
            server_id="github",
            tools=[remote_tool("search"), remote_tool("create_issue")],
            skipped_tools=[
                {
                    "name": "broken",
                    "code": ToolErrorCode.MCP_SCHEMA_INVALID.value,
                    "reason": "inputSchema invalid",
                }
            ],
            raw_tool_count=3,
        )

        result = adapt_mcp_discovery_to_specs(discovery, config)

        self.assertEqual(result.source, "mcp:github")
        self.assertEqual([spec.name for spec in result.specs], ["mcp.github.search", "mcp.github.create_issue"])
        self.assertEqual(len(result.skipped_tools), 1)
        self.assertEqual(result.metadata["raw_tool_count"], 3)
        self.assertEqual(result.metadata["adapted_tool_count"], 2)
        self.assertIn("search", result.metadata["schema_hashes"])

    def test_dynamic_register_remove_and_refresh_replace_source(self):
        registry = ToolRegistry([ToolSpec(name="builtin", description="Builtin.")])
        config = resolved_config()
        first = adapt_mcp_discovery_to_specs(
            MCPToolDiscoveryResult(server_id="github", tools=[remote_tool("search")]),
            config,
        )
        second = adapt_mcp_discovery_to_specs(
            MCPToolDiscoveryResult(server_id="github", tools=[remote_tool("search"), remote_tool("query")]),
            config,
        )

        registered_first = register_mcp_tool_specs(registry, first)
        registered_second = register_mcp_tool_specs(registry, second)

        self.assertEqual(registered_first, ["mcp.github.search"])
        self.assertEqual(registered_second, ["mcp.github.search", "mcp.github.query"])
        self.assertTrue(registry.has_tool("mcp.github.search"))
        self.assertTrue(registry.has_tool("mcp.github.query"))
        removed = remove_mcp_tool_specs(registry, "github")

        self.assertEqual(removed, ["mcp.github.query", "mcp.github.search"])
        self.assertTrue(registry.has_tool("builtin"))
        self.assertFalse(registry.has_tool("mcp.github.search"))

    def test_register_rejects_conflict_without_removing_existing_source(self):
        registry = ToolRegistry(
            [
                ToolSpec(name="mcp.github.search", description="Builtin conflict."),
                ToolSpec(
                    name="mcp.github.old",
                    description="Old MCP.",
                    metadata={"source": "mcp:github"},
                ),
            ]
        )
        config = resolved_config()
        adapter_result = adapt_mcp_discovery_to_specs(
            MCPToolDiscoveryResult(server_id="github", tools=[remote_tool("search")]),
            config,
        )

        with self.assertRaises(ValueError):
            register_mcp_tool_specs(registry, adapter_result)

        self.assertTrue(registry.has_tool("mcp.github.search"))
        self.assertTrue(registry.has_tool("mcp.github.old"))
        self.assertEqual(registry.list_dynamic_sources(), {"mcp:github": ["mcp.github.old"]})

    def test_dynamic_source_helper_is_stable(self):
        self.assertEqual(mcp_dynamic_source("github"), "mcp:github")


if __name__ == "__main__":
    unittest.main()
