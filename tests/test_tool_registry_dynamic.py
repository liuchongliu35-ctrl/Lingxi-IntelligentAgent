from __future__ import annotations

import unittest

from src.tools.registry import ToolRegistry, ToolSpec


class ToolRegistryDynamicTest(unittest.TestCase):
    def test_mcp_source_is_inferred_and_can_be_removed_precisely(self):
        builtin = ToolSpec(name="builtin", description="Builtin.")
        github_search = ToolSpec(
            name="mcp.github.search",
            description="Search GitHub.",
            namespace="mcp.github",
            metadata={
                "source_type": "mcp",
                "server_id": "github",
                "remote_tool_name": "search",
            },
        )
        mysql_query = ToolSpec(
            name="mcp.mysql.query",
            description="Query MySQL.",
            metadata={"source": "mcp:mysql"},
        )
        registry = ToolRegistry([builtin])
        registry.register(github_search)
        registry.register(mysql_query)

        self.assertEqual(
            registry.list_dynamic_sources(),
            {
                "mcp:github": ["mcp.github.search"],
                "mcp:mysql": ["mcp.mysql.query"],
            },
        )

        removed = registry.remove_dynamic_source("mcp:github")

        self.assertEqual(removed, ["mcp.github.search"])
        self.assertFalse(registry.has_tool("mcp.github.search"))
        self.assertTrue(registry.has_tool("mcp.mysql.query"))
        self.assertTrue(registry.has_tool("builtin"))

    def test_explicit_source_and_aliases_are_registered_atomically(self):
        registry = ToolRegistry()
        spec = ToolSpec(name="mcp.local.read", description="Read.", aliases=["read_file"])

        registered = registry.register(
            spec,
            source="mcp:local",
            aliases=["legacy_read"],
        )

        self.assertIs(registered, spec)
        self.assertEqual(registry.resolve_name("read_file"), "mcp.local.read")
        self.assertEqual(registry.resolve_name("legacy_read"), "mcp.local.read")
        self.assertEqual(
            registry.list_dynamic_sources(),
            {"mcp:local": ["mcp.local.read"]},
        )

    def test_unregister_by_alias_removes_canonical_tool_and_all_aliases(self):
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="canonical",
                    description="Canonical.",
                    aliases=["old_name", "older_name"],
                )
            ]
        )

        removed = registry.unregister("old_name")

        self.assertEqual(removed.name, "canonical")
        self.assertFalse(registry.has_tool("canonical"))
        self.assertIsNone(registry.resolve_name("old_name"))
        self.assertIsNone(registry.resolve_name("older_name"))

    def test_implemented_false_is_hidden_from_model_but_not_registry(self):
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="mock_tool",
                    description="Mock.",
                    metadata={"implemented": False, "mock": True},
                ),
                ToolSpec(name="real_tool", description="Real."),
            ]
        )

        self.assertTrue(registry.has_tool("mock_tool"))
        self.assertEqual(
            [item["name"] for item in registry.to_model_specs()],
            ["real_tool"],
        )


if __name__ == "__main__":
    unittest.main()
