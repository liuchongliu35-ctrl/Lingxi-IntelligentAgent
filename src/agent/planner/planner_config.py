from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class PlannerConfig:
    root: Path
    planner_config: Dict[str, Any]
    rule_templates: Dict[str, Any]
    llm_planner_prompt: Dict[str, Any]

    @property
    def max_plan_steps(self) -> int:
        return int(self.planner_config.get("max_plan_steps", 20))

    @property
    def max_task_units(self) -> int:
        return int(self.planner_config.get("max_task_units", 6))

    @property
    def max_llm_repair_attempts(self) -> int:
        return int(self.planner_config.get("max_llm_repair_attempts", 3))

    @property
    def default_step_max_retries(self) -> int:
        return int(self.planner_config.get("default_step_max_retries", 3))

    @property
    def enable_llm_planner(self) -> bool:
        return bool(self.planner_config.get("enable_llm_planner", True))

    @property
    def enable_shell_fallback_plan(self) -> bool:
        return bool(self.planner_config.get("enable_shell_fallback_plan", False))

    @property
    def planner_log_path(self) -> Path:
        return (self.root / self.planner_config.get("planner_log_path", "logs/planner.log")).resolve()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


@lru_cache(maxsize=1)
def load_planner_config(root: str | Path | None = None) -> PlannerConfig:
    project_root = Path(root or Path.cwd()).resolve()
    config_dir = project_root / "config" / "planner"
    return PlannerConfig(
        root=project_root,
        planner_config=_read_json(config_dir / "planner_config.json", {}),
        rule_templates=_read_json(config_dir / "rule_templates.json", {}),
        llm_planner_prompt=_read_json(config_dir / "llm_planner_prompt.json", {}),
    )
