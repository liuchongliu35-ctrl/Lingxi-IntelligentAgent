from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tools import (
    MCPConfigError,
    MCPProtocolError,
    MCPServerConfig,
    MCPStdioClient,
    ToolErrorCode,
    build_stdio_environment,
    resolve_command,
)


FAKE_SERVER = Path(__file__).with_name("fixtures") / "fake_mcp_server.py"


def runtime_config(
    *,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    pass_env: bool = False,
    enabled: bool = True,
    timeout_seconds: int = 2,
):
    config = MCPServerConfig.from_mapping(
        "fake",
        {
            "enabled": enabled,
            "transport": "stdio",
            "command": command or sys.executable,
            "args": args if args is not None else [str(FAKE_SERVER)],
            "env": env or {},
            "cwd": ".",
            "passEnv": pass_env,
            "timeout_seconds": timeout_seconds,
        },
        workspace_root=Path.cwd(),
    )
    return config.resolve_runtime(environment=os.environ)


class MCPStdioClientTest(unittest.TestCase):
    def tearDown(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass

    def _client_for(self, **kwargs) -> MCPStdioClient:
        self._client = MCPStdioClient(runtime_config(**kwargs))
        return self._client

    def test_start_request_and_stop(self):
        client = self._client_for()

        info = client.start()
        result = client.request("echo", {"value": 42})
        stopped = client.stop()

        self.assertEqual(info.state, "starting")
        self.assertIsNotNone(info.pid)
        self.assertEqual(result, {"echo": {"value": 42}})
        self.assertEqual(stopped.state, "stopped")

    def test_notifications_and_mismatched_ids_do_not_satisfy_request(self):
        client = self._client_for()

        notified = client.request("notify_then_echo")
        mismatched = client.request("mismatch_then_echo")

        self.assertEqual(notified, {"ok": True})
        self.assertEqual(mismatched, {"ok": True})

    def test_invalid_stdout_json_has_stable_error(self):
        client = self._client_for()

        with self.assertRaises(MCPProtocolError) as caught:
            client.request("invalid_json")

        self.assertEqual(caught.exception.code, ToolErrorCode.MCP_STDOUT_INVALID_JSON.value)
        self.assertIn("line_preview", caught.exception.details)

    def test_remote_json_rpc_error_is_structured(self):
        client = self._client_for()

        with self.assertRaises(MCPProtocolError) as caught:
            client.request("remote_error")

        self.assertEqual(caught.exception.code, ToolErrorCode.MCP_REMOTE_ERROR.value)
        self.assertEqual(caught.exception.message, "remote failed")

    def test_timeout_does_not_pollute_next_request(self):
        client = self._client_for(timeout_seconds=1)

        with self.assertRaises(MCPProtocolError) as caught:
            client.request("sleep", {"seconds": 0.4}, timeout_seconds=0.05)
        self.assertEqual(caught.exception.code, ToolErrorCode.MCP_TIMEOUT.value)

        time.sleep(0.5)
        result = client.request("echo", {"after": "timeout"}, timeout_seconds=1)
        self.assertEqual(result, {"echo": {"after": "timeout"}})

    def test_process_exit_maps_to_protocol_error(self):
        client = self._client_for()

        with self.assertRaises(MCPProtocolError) as caught:
            client.request("exit_now", {"code": 7}, timeout_seconds=1)

        self.assertEqual(caught.exception.code, ToolErrorCode.MCP_PROCESS_EXITED.value)
        self.assertEqual(caught.exception.details["returncode"], 7)

    def test_command_not_found(self):
        client = self._client_for(command="definitely_missing_mcp_command_12345")

        with self.assertRaises(MCPProtocolError) as caught:
            client.start()

        self.assertEqual(caught.exception.code, ToolErrorCode.MCP_COMMAND_NOT_FOUND.value)

    def test_stderr_is_captured_as_preview(self):
        client = self._client_for(env={"FAKE_MCP_STDERR": "hello stderr"})

        client.start()
        client.request("echo", {"value": "stderr"})
        time.sleep(0.1)
        info = client.process.connection_info()

        self.assertIn("hello stderr", info.metadata["stderr_preview"])

    def test_environment_policy_and_command_summary_do_not_leak_secret_values(self):
        with patch.dict(
            os.environ,
            {
                "SECRET_PARENT_VALUE": "parent-secret",
                "PATH": os.environ.get("PATH", ""),
                "PATHEXT": os.environ.get("PATHEXT", ""),
                "SystemRoot": os.environ.get("SystemRoot", ""),
            },
            clear=False,
        ):
            with patch.dict(os.environ, {"CHILD_TOKEN": "child-secret"}, clear=False):
                client = self._client_for(
                    env={"CHILD_TOKEN": "${env:CHILD_TOKEN}"},
                    pass_env=False,
                )
            env = build_stdio_environment(client.config)

        self.assertIn("CHILD_TOKEN", env)
        self.assertNotIn("SECRET_PARENT_VALUE", env)
        self.assertNotIn("child-secret", json.dumps(client.config.to_safe_dict(), ensure_ascii=False))
        self.assertIsNotNone(resolve_command(sys.executable, env=env))

    def test_disabled_config_cannot_start(self):
        client = self._client_for(enabled=False)

        with self.assertRaises(MCPProtocolError) as caught:
            client.start()

        self.assertEqual(caught.exception.code, ToolErrorCode.MCP_SERVER_DISABLED.value)

    def test_config_still_rejects_cwd_outside_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(MCPConfigError):
                MCPServerConfig.from_mapping(
                    "fake",
                    {
                        "enabled": True,
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [str(FAKE_SERVER)],
                        "cwd": "..",
                    },
                    workspace_root=temp_dir,
                )


if __name__ == "__main__":
    unittest.main()
