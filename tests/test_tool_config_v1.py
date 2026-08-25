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

from src.tools.config import (
    ToolsConfigError,
    clear_tools_config_cache,
    load_tools_config,
)
from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallRequest
from src.tools.registry import ToolRegistry, ToolSpec
from src.tools.tool_logger import NullToolLogger
from src.tools.tool_manager import ToolManager


class ToolConfigV1Test(unittest.TestCase):
    def tearDown(self) -> None:
        clear_tools_config_cache()

    def _write_config(self, root: Path, name: str, payload: object) -> None:
        config_dir = root / "config" / "tools"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_missing_config_uses_conservative_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            config = load_tools_config(root)

            self.assertTrue(config.runtime.enabled)
            self.assertEqual(config.runtime.default_timeout_seconds, 30)
            self.assertEqual(config.runtime.read_file_small_bytes, 64 * 1024)
            self.assertEqual(config.runtime.read_file_medium_bytes, 512 * 1024)
            self.assertEqual(config.runtime.read_file_hard_bytes, 8 * 1024 * 1024)
            self.assertEqual(config.runtime.read_file_preview_chars, 4000)
            self.assertEqual(config.runtime.read_file_range_max_lines, 400)
            self.assertEqual(config.runtime.logs_path, (root / "logs" / "tools.log").resolve())
            self.assertEqual(
                config.policy.default_permissions,
                {
                    "allow_read_workspace": True,
                    "allow_write_workspace": False,
                    "allow_network": False,
                    "allow_command": False,
                    "allow_shell_command": False,
                    "allow_mcp": False,
                },
            )
            self.assertEqual(config.providers, {})
            self.assertEqual(config.mcp_servers.servers, {})
            json.dumps(config.to_dict(), ensure_ascii=False)

    def test_loads_config_and_keeps_env_reference_without_secret_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_config(
                root,
                "defaults.json",
                {
                    "enabled": False,
                    "default_timeout_seconds": 17,
                    "max_output_chars": 123,
                    "read_file_small_bytes": 16,
                    "read_file_medium_bytes": 32,
                    "read_file_hard_bytes": 64,
                    "read_file_preview_chars": 12,
                    "read_file_range_max_lines": 7,
                    "default_observation_mode": "minimal",
                    "logs_path": "var/tool-audit.log",
                },
            )
            self._write_config(
                root,
                "policies.json",
                {
                    "default_permissions": {
                        "allow_network": True,
                        "allow_command": False,
                    },
                    "risk_policy": {
                        "low": "confirm",
                        "medium": "allow",
                        "high": "confirm",
                        "blocked": "block",
                    },
                    "sensitive_paths": ["private"],
                    "ignored_directories": [".git"],
                },
            )
            self._write_config(
                root,
                "providers.json",
                {
                    "web_search": {
                        "providers": {
                            "search_api": {
                                "enabled": True,
                                "api_key_env": "TEST_TAVILY_API_KEY",
                            }
                        }
                    }
                },
            )
            self._write_config(root, "mcp_servers.json", {"servers": []})

            with patch.dict(
                "os.environ",
                {"TEST_TAVILY_API_KEY": "tool-secret-value"},
                clear=False,
            ):
                config = load_tools_config(root)

            self.assertFalse(config.runtime.enabled)
            self.assertEqual(config.runtime.default_timeout_seconds, 17)
            self.assertEqual(config.runtime.read_file_small_bytes, 16)
            self.assertEqual(config.runtime.read_file_medium_bytes, 32)
            self.assertEqual(config.runtime.read_file_hard_bytes, 64)
            self.assertEqual(config.runtime.read_file_preview_chars, 12)
            self.assertEqual(config.runtime.read_file_range_max_lines, 7)
            self.assertEqual(config.runtime.default_observation_mode, "minimal")
            self.assertEqual(config.runtime.logs_path, (root / "var" / "tool-audit.log").resolve())
            self.assertTrue(config.policy.default_permissions["allow_network"])
            serialized = json.dumps(config.to_dict(), ensure_ascii=False)
            self.assertIn("TEST_TAVILY_API_KEY", serialized)
            self.assertNotIn("tool-secret-value", serialized)

    def test_invalid_json_raises_structured_error_and_manager_falls_back_conservatively(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config" / "tools"
            config_dir.mkdir(parents=True)
            (config_dir / "policies.json").write_text("{invalid", encoding="utf-8")

            with self.assertRaises(ToolsConfigError) as caught:
                load_tools_config(root)
            self.assertEqual(caught.exception.code, "invalid_json")
            json.dumps(caught.exception.to_dict(), ensure_ascii=False)

            clear_tools_config_cache()
            manager = ToolManager(
                tools={},
                registry=ToolRegistry(),
                logger=NullToolLogger(),
                workspace_root=root,
            )

            self.assertIsNotNone(manager.config_error)
            self.assertEqual(manager.config_error.code, "invalid_json")
            self.assertFalse(manager.runtime.policy.default_permissions["allow_network"])
            self.assertFalse(manager.runtime.policy.default_permissions["allow_command"])
            self.assertFalse(manager.runtime.policy.default_permissions["allow_mcp"])

    def test_plaintext_secret_is_rejected_but_env_reference_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_config(
                root,
                "providers.json",
                {
                    "web_search": {
                        "api_key": "must-not-be-here",
                    }
                },
            )

            with self.assertRaises(ToolsConfigError) as caught:
                load_tools_config(root)
            self.assertEqual(caught.exception.code, "plain_secret_in_config")
            self.assertIn("api_key", caught.exception.to_dict()["details"]["keys"][0])

        clear_tools_config_cache()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_config(
                root,
                "providers.json",
                {
                    "web_search": {
                        "api_key_env": "TAVILY_API_KEY",
                    }
                },
            )

            config = load_tools_config(root)

            self.assertEqual(
                config.providers["web_search"]["api_key_env"],
                "TAVILY_API_KEY",
            )

    def test_manager_applies_disabled_runtime_and_configured_risk_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_config(root, "defaults.json", {"enabled": False})
            registry = ToolRegistry([ToolSpec(name="demo", description="Demo.")])
            manager = ToolManager(
                tools={"demo": lambda: "ok"},
                registry=registry,
                logger=NullToolLogger(),
                workspace_root=root,
            )

            disabled = manager.execute(_request("demo"))

            self.assertFalse(disabled.success)
            self.assertEqual(disabled.code, ToolErrorCode.TOOL_DISABLED.value)

        clear_tools_config_cache()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_config(
                root,
                "policies.json",
                {
                    "risk_policy": {
                        "low": "block",
                        "medium": "allow",
                        "high": "confirm",
                        "blocked": "block",
                    }
                },
            )
            registry = ToolRegistry([ToolSpec(name="demo", description="Demo.")])
            manager = ToolManager(
                tools={"demo": lambda: "ok"},
                registry=registry,
                logger=NullToolLogger(),
                workspace_root=root,
            )

            blocked = manager.execute(_request("demo"))

            self.assertFalse(blocked.success)
            self.assertEqual(blocked.code, ToolErrorCode.BLOCKED_BY_POLICY.value)

    def test_manager_applies_configured_default_observation_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_config(
                root,
                "defaults.json",
                {"default_observation_mode": "minimal"},
            )
            registry = ToolRegistry([ToolSpec(name="demo", description="Demo.")])
            manager = ToolManager(
                tools={"demo": lambda: ToolResult.ok(data={"value": "ok"})},
                registry=registry,
                logger=NullToolLogger(),
                workspace_root=root,
            )

            result = manager.execute(_request("demo"))

            self.assertTrue(result.success)
            self.assertEqual(
                result.metadata["output_control"]["observation_mode"],
                "minimal",
            )


def _request(tool_name: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        context=ToolCallContext(source="test"),
    )


if __name__ == "__main__":
    unittest.main()
