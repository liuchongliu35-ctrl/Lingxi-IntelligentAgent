from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List


@dataclass(frozen=True)
class AnalyzerConfig:
    root: Path
    analyzer_config: Dict[str, Any]
    intents: List[str]
    intent_keywords: Dict[str, List[str]]
    risk_rules: Dict[str, Any]
    complexity: Dict[str, Any]
    tech_stacks: Dict[str, List[str]]
    tool_mapping: Dict[str, List[str]]

    @property
    def log_path(self) -> Path:
        return (self.root / self.analyzer_config.get("log_path", "logs/analyzer.log")).resolve()

    @property
    def pending_intents_path(self) -> Path:
        return (self.root / self.analyzer_config.get("pending_intents_path", "storage/analyzer/pending_intents.json")).resolve()

    @property
    def agent_mode(self) -> str:
        return str(self.analyzer_config.get("agent_mode", "solo")).strip().lower() or "solo"

    @property
    def max_intents(self) -> int:
        return int(self.analyzer_config.get("max_intents", 4))

    @property
    def intent_score_threshold(self) -> float:
        return float(self.analyzer_config.get("intent_score_threshold", 50))

    @property
    def pending_intent_threshold(self) -> float:
        return float(self.analyzer_config.get("pending_intent_threshold", 0.65))

    @property
    def supported_file_types(self) -> List[str]:
        return list(self.analyzer_config.get("supported_file_types", []))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


@lru_cache(maxsize=1)
def load_analyzer_config(root: str | Path | None = None) -> AnalyzerConfig:
    project_root = Path(root or Path.cwd()).resolve()
    config_dir = project_root / "config" / "analyzer"
    return AnalyzerConfig(
        root=project_root,
        analyzer_config=_read_json(config_dir / "analyzer_config.json", {}),
        intents=_read_json(config_dir / "intents.json", []),
        intent_keywords=_read_json(config_dir / "intent_keywords.json", {}),
        risk_rules=_read_json(config_dir / "risk_rules.json", {}),
        complexity=_read_json(config_dir / "complexity_weights.json", {}),
        tech_stacks=_read_json(config_dir / "tech_stacks.json", {}),
        tool_mapping=_read_json(config_dir / "tool_mapping.json", {}),
    )
