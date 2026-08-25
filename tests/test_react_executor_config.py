from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agent.react_executor_config import (
    COMMAND_CONFIRMATION_POLICIES,
    load_react_executor_config,
)


class ReActExecutorConfigTest(unittest.TestCase):
    def tearDown(self):
        load_react_executor_config.cache_clear()

    def test_missing_config_file_uses_stable_defaults(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = load_react_executor_config(tmp_dir)

            self.assertEqual(config.max_execution_turns, 20)
            self.assertEqual(config.max_step_turns, 5)
            self.assertEqual(config.max_action_packet_repair_attempts, 5)
            self.assertEqual(config.default_tool_max_retries, 3)
            self.assertEqual(config.command_confirmation_policy, "ask")
            self.assertTrue(config.enable_llm_reasoning)
            self.assertTrue(config.enable_llm_checker)
            self.assertTrue(config.enable_command_tool)
            self.assertEqual(config.workspace_root, Path(tmp_dir).resolve())
            self.assertEqual(config.react_executor_log_path, (Path(tmp_dir) / "logs" / "react_executor.log").resolve())
            self.assertEqual(config.max_model_observation_chars, 2000)
            self.assertEqual(config.max_recent_observations, 5)

    def test_loads_config_file_and_resolves_paths_from_project_root(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_dir = root / "config" / "react_executor"
            config_dir.mkdir(parents=True)
            (config_dir / "react_executor_config.json").write_text(
                json.dumps(
                    {
                        "max_execution_turns": 9,
                        "max_step_turns": 4,
                        "max_action_packet_repair_attempts": 2,
                        "default_tool_max_retries": 1,
                        "retry_backoff_base_seconds": 0,
                        "retry_backoff_max_seconds": 0.5,
                        "enable_llm_checker": False,
                        "command_confirmation_policy": "low_risk_auto",
                        "workspace_root": "workspace",
                        "react_executor_log_path": "tmp/react.log",
                        "event_stream_enabled": False,
                        "log_full_prompt": True,
                        "max_model_observation_chars": 1234,
                        "max_recent_observations": 2,
                    }
                ),
                encoding="utf-8",
            )

            config = load_react_executor_config(root)

            self.assertEqual(config.max_execution_turns, 9)
            self.assertEqual(config.max_step_turns, 4)
            self.assertEqual(config.max_action_packet_repair_attempts, 2)
            self.assertEqual(config.default_tool_max_retries, 1)
            self.assertEqual(config.retry_backoff_base_seconds, 0.0)
            self.assertEqual(config.retry_backoff_max_seconds, 0.5)
            self.assertFalse(config.enable_llm_checker)
            self.assertEqual(config.command_confirmation_policy, "low_risk_auto")
            self.assertEqual(config.workspace_root, (root / "workspace").resolve())
            self.assertEqual(config.react_executor_log_path, (root / "tmp" / "react.log").resolve())
            self.assertFalse(config.event_stream_enabled)
            self.assertTrue(config.log_full_prompt)
            self.assertEqual(config.max_model_observation_chars, 1234)
            self.assertEqual(config.max_recent_observations, 2)

    def test_invalid_values_are_normalized(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_dir = root / "config" / "react_executor"
            config_dir.mkdir(parents=True)
            (config_dir / "react_executor_config.json").write_text(
                json.dumps(
                    {
                        "max_execution_turns": 0,
                        "max_step_turns": -2,
                        "max_action_packet_repair_attempts": -1,
                        "default_tool_max_retries": -3,
                        "retry_backoff_base_seconds": -1,
                        "retry_backoff_max_seconds": -5,
                        "command_confirmation_policy": "unsafe",
                        "max_model_observation_chars": 20,
                        "max_recent_observations": -1,
                    }
                ),
                encoding="utf-8",
            )

            config = load_react_executor_config(root)

            self.assertEqual(config.max_execution_turns, 1)
            self.assertEqual(config.max_step_turns, 1)
            self.assertEqual(config.max_action_packet_repair_attempts, 0)
            self.assertEqual(config.default_tool_max_retries, 0)
            self.assertEqual(config.retry_backoff_base_seconds, 0.0)
            self.assertEqual(config.retry_backoff_max_seconds, 0.0)
            self.assertEqual(config.command_confirmation_policy, "ask")
            self.assertEqual(config.max_model_observation_chars, 100)
            self.assertEqual(config.max_recent_observations, 0)

    def test_default_config_file_uses_supported_command_policy(self):
        config = load_react_executor_config()

        self.assertIn(config.command_confirmation_policy, COMMAND_CONFIRMATION_POLICIES)
        self.assertFalse(config.log_full_prompt)
        self.assertTrue(config.to_dict()["event_stream_enabled"])
        self.assertEqual(config.to_dict()["max_model_observation_chars"], 2000)
        self.assertEqual(config.to_dict()["max_recent_observations"], 5)


if __name__ == "__main__":
    unittest.main()
