from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class IntentPrediction:
    intents: List[str] = field(default_factory=list)
    probabilities: Dict[str, float] = field(default_factory=dict)
    source: str = "classifier"
    not_ready: bool = False


class IntentClassifier:
    """Placeholder interface for the future trained intent classifier."""

    def predict_single(self, text: str, tokens: List[str]) -> IntentPrediction:
        return IntentPrediction(not_ready=True, source="classifier_stub")

    def predict_multi(self, text: str, tokens: List[str]) -> IntentPrediction:
        return IntentPrediction(not_ready=True, source="classifier_stub")
