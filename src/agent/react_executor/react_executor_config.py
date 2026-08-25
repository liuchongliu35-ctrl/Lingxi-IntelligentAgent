from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict


COMMAND_CONFIRMATION_POLICIES = {"ask", "low_risk_auto", "session", "always"}

DEFAULT_REACT_EXECUTOR_CONFIG: Dict[str, Any] = {
    "max_execution_turns": 20,
    "max_step_turns": 5,
    "max_action_packet_repair_attempts": 5,
    "default_tool_max_retries": 3,
    "retry_backoff_base_seconds": 0.2,
    "retry_backoff_max_seconds": 2.0,
    "enable_llm_reasoning": True,
    "enable_llm_checker": True,
    "enable_command_tool": True,
    "command_confirmation_policy": "ask",
    "workspace_root": ".",
    "react_executor_log_path": "logs/react_executor.log",
    "event_stream_enabled": True,
    "log_full_prompt": False,
    "max_model_observation_chars": 2000,
    "max_recent_observations": 5,
}


@dataclass(frozen=True)
class ReActExecutorConfig:
    root: Path
    react_executor_config: Dict[str, Any]

    @property
    def max_execution_turns(self) -> int:
        return self._int_at_least("max_execution_turns", 20, minimum=1)

    @property
    def max_step_turns(self) -> int:
        return self._int_at_least("max_step_turns", 5, minimum=1)

    @property
    def max_action_packet_repair_attempts(self) -> int:
        return self._int_at_least("max_action_packet_repair_attempts", 5, minimum=0)

    @property
    def default_tool_max_retries(self) -> int:
        return self._int_at_least("default_tool_max_retries", 3, minimum=0)

    @property
    def retry_backoff_base_seconds(self) -> float:
        return self._float_at_least("retry_backoff_base_seconds", 0.2, minimum=0.0)

    @property
    def retry_backoff_max_seconds(self) -> float:
        return self._float_at_least("retry_backoff_max_seconds", 2.0, minimum=0.0)

    @property
    def enable_llm_reasoning(self) -> bool:
        return self._bool("enable_llm_reasoning", True)

    @property
    def enable_llm_checker(self) -> bool:
        return self._bool("enable_llm_checker", True)

    @property
    def enable_command_tool(self) -> bool:
        return self._bool("enable_command_tool", True)

    @property
    def command_confirmation_policy(self) -> str:
        policy = str(self.react_executor_config.get("command_confirmation_policy", "ask"))
        if policy not in COMMAND_CONFIRMATION_POLICIES:
            return "ask"
        return policy

    @property
    def workspace_root(self) -> Path:
        value = self.react_executor_config.get("workspace_root", ".")
        path = Path(str(value))
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    @property
    def react_executor_log_path(self) -> Path:
        value = self.react_executor_config.get("react_executor_log_path", "logs/react_executor.log")
        path = Path(str(value))
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    @property
    def event_stream_enabled(self) -> bool:
        return self._bool("event_stream_enabled", True)

    @property
    def log_full_prompt(self) -> bool:
        return self._bool("log_full_prompt", False)

    @property
    def max_model_observation_chars(self) -> int:
        return self._int_at_least("max_model_observation_chars", 2000, minimum=100)

    @property
    def max_recent_observations(self) -> int:
        return self._int_at_least("max_recent_observations", 5, minimum=0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_execution_turns": self.max_execution_turns,
            "max_step_turns": self.max_step_turns,
            "max_action_packet_repair_attempts": self.max_action_packet_repair_attempts,
            "default_tool_max_retries": self.default_tool_max_retries,
            "retry_backoff_base_seconds": self.retry_backoff_base_seconds,
            "retry_backoff_max_seconds": self.retry_backoff_max_seconds,
            "enable_llm_reasoning": self.enable_llm_reasoning,
            "enable_llm_checker": self.enable_llm_checker,
            "enable_command_tool": self.enable_command_tool,
            "command_confirmation_policy": self.command_confirmation_policy,
            "workspace_root": str(self.workspace_root),
            "react_executor_log_path": str(self.react_executor_log_path),
            "event_stream_enabled": self.event_stream_enabled,
            "log_full_prompt": self.log_full_prompt,
            "max_model_observation_chars": self.max_model_observation_chars,
            "max_recent_observations": self.max_recent_observations,
        }

    def _int_at_least(self, key: str, default: int, *, minimum: int) -> int:
        try:
            value = int(self.react_executor_config.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(value, minimum)

    def _float_at_least(self, key: str, default: float, *, minimum: float) -> float:
        try:
            value = float(self.react_executor_config.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(value, minimum)

    def _bool(self, key: str, default: bool) -> bool:
        value = self.react_executor_config.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    with path.open("r", encoding="utf-8-sig") as file:
        loaded = json.load(file)
    if not isinstance(loaded, dict):
        return dict(default)
    merged = dict(default)
    merged.update(loaded)
    return merged


@lru_cache(maxsize=16)
def load_react_executor_config(root: str | Path | None = None) -> ReActExecutorConfig:
    project_root = Path(root or Path.cwd()).resolve()
    config_dir = project_root / "config" / "react_executor"
    return ReActExecutorConfig(
        root=project_root,
        react_executor_config=_read_json(
            config_dir / "react_executor_config.json",
            DEFAULT_REACT_EXECUTOR_CONFIG,
        ),
    )
