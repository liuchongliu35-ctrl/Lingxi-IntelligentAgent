from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List


@dataclass
class IntentPrediction:
    """Classifier output contract for Analyzer.

    Future trained classifiers should return a probability distribution whose
    keys are Analyzer intent names and whose values are confidence scores in
    [0.0, 1.0]. `intents` is the ordered candidate list. If omitted, it is
    derived from probabilities in descending order.
    """

    intents: List[str] = field(default_factory=list)
    probabilities: Dict[str, float] = field(default_factory=dict)
    source: str = "classifier"
    model_version: str | None = None
    not_ready: bool = False
    multi_label: bool = False
    error: str | None = None
    raw_output: Any | None = None

    @property
    def ready(self) -> bool:
        return not self.not_ready and self.error is None and bool(self.probabilities)

    @property
    def top_intent(self) -> str | None:
        return self.ordered_intents(limit=1)[0] if self.probabilities else None

    @property
    def top_probability(self) -> float | None:
        if not self.probabilities:
            return None
        return max(self.probabilities.values())

    @classmethod
    def unavailable(cls, source: str = "classifier_stub", error: str = "classifier_not_ready") -> "IntentPrediction":
        return cls(source=source, not_ready=True, error=error)

    @classmethod
    def from_probabilities(
        cls,
        probabilities: Dict[str, float],
        *,
        source: str = "classifier",
        model_version: str | None = None,
        intents: List[str] | None = None,
        raw_output: Any | None = None,
        multi_label: bool = False,
    ) -> "IntentPrediction":
        prediction = cls(
            intents=list(intents or []),
            probabilities=dict(probabilities),
            source=source,
            model_version=model_version,
            multi_label=multi_label,
            raw_output=raw_output,
        )
        return prediction.normalized()

    def normalized(
        self,
        *,
        known_intents: Iterable[str] | None = None,
        max_intents: int | None = None,
        min_probability: float = 0.0,
    ) -> "IntentPrediction":
        known = set(known_intents) if known_intents is not None else None
        probabilities: Dict[str, float] = {}
        for name, value in self.probabilities.items():
            intent = str(name).strip()
            if not intent or (known is not None and intent not in known):
                continue
            try:
                probability = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(probability):
                continue
            probability = min(1.0, max(0.0, probability))
            if probability >= min_probability:
                probabilities[intent] = probability

        ordered = self._ordered_names(probabilities)
        explicit_order = [intent for intent in self.intents if intent in probabilities]
        if explicit_order:
            ordered = explicit_order + [intent for intent in ordered if intent not in explicit_order]
        if max_intents is not None:
            ordered = ordered[:max_intents]

        return IntentPrediction(
            intents=ordered,
            probabilities=probabilities,
            source=self.source,
            model_version=self.model_version,
            not_ready=self.not_ready,
            multi_label=self.multi_label,
            error=self.error,
            raw_output=self.raw_output,
        )

    def ordered_intents(self, limit: int | None = None) -> List[str]:
        ordered = self._ordered_names(self.probabilities)
        if limit is not None:
            return ordered[:limit]
        return ordered

    def _ordered_names(self, probabilities: Dict[str, float]) -> List[str]:
        return [name for name, _ in sorted(probabilities.items(), key=lambda item: (-item[1], item[0]))]


class IntentClassifier:
    """Placeholder interface for the future trained intent classifier."""

    model_version = "classifier_stub_v1"

    @property
    def is_ready(self) -> bool:
        return False

    def status(self) -> Dict[str, Any]:
        return {
            "ready": self.is_ready,
            "source": "classifier_stub",
            "model_version": self.model_version,
        }

    def predict_single(self, text: str, tokens: List[str]) -> IntentPrediction:
        return IntentPrediction.unavailable(source="classifier_stub")

    def predict_multi(self, text: str, tokens: List[str]) -> IntentPrediction:
        return IntentPrediction.unavailable(source="classifier_stub")
