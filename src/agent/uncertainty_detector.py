from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict


@dataclass
class UncertaintyResult:
    uncertain: bool
    score: float
    reason: str = ""


class UncertaintyDetector:
    """Detect low-confidence classifier outputs."""

    def __init__(self, low_confidence: float = 0.6, margin_threshold: float = 0.2, entropy_threshold: float = 0.7):
        self.low_confidence = low_confidence
        self.margin_threshold = margin_threshold
        self.entropy_threshold = entropy_threshold

    def detect(self, probabilities: Dict[str, float]) -> UncertaintyResult:
        if not probabilities:
            return UncertaintyResult(True, 1.0, "no_probabilities")

        values = sorted(probabilities.values(), reverse=True)
        top1 = values[0]
        top2 = values[1] if len(values) > 1 else 0.0
        entropy = self._normalized_entropy(values)

        if top1 < self.low_confidence:
            return UncertaintyResult(True, entropy, "low_confidence")
        if top1 - top2 <= self.margin_threshold:
            return UncertaintyResult(True, entropy, "small_margin")
        if entropy >= self.entropy_threshold:
            return UncertaintyResult(True, entropy, "high_entropy")
        return UncertaintyResult(False, entropy, "confident")

    def _normalized_entropy(self, values: list[float]) -> float:
        total = sum(values)
        if total <= 0 or len(values) <= 1:
            return 0.0
        entropy = 0.0
        for value in values:
            probability = value / total
            if probability > 0:
                entropy -= probability * math.log(probability, 2)
        return entropy / math.log(len(values), 2)
