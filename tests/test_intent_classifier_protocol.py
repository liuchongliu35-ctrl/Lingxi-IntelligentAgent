from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.agent.analyzer_config import load_analyzer_config
from src.agent.complexity_analyzer import ComplexityAnalyzer
from src.agent.intent_classifier import IntentClassifier, IntentPrediction
from src.agent.uncertainty_detector import UncertaintyDetector


class ReadySingleClassifier(IntentClassifier):
    @property
    def is_ready(self) -> bool:
        return True

    def predict_single(self, text: str, tokens: list[str]) -> IntentPrediction:
        return IntentPrediction.from_probabilities(
            {"search": 0.91, "chat": 0.05, "unknown_intent": 0.99},
            source="test_classifier",
            model_version="test_v1",
        )


class ReadyMultiClassifier(IntentClassifier):
    @property
    def is_ready(self) -> bool:
        return True

    def predict_multi(self, text: str, tokens: list[str]) -> IntentPrediction:
        return IntentPrediction.from_probabilities(
            {"search": 0.91, "summarize": 0.84, "chat": 0.2},
            source="test_classifier",
            model_version="test_v1",
            multi_label=True,
        )


class UncertainClassifier(IntentClassifier):
    @property
    def is_ready(self) -> bool:
        return True

    def predict_single(self, text: str, tokens: list[str]) -> IntentPrediction:
        return IntentPrediction.from_probabilities(
            {"search": 0.55, "chat": 0.45},
            source="test_classifier",
            model_version="test_v1",
        )


class IntentClassifierProtocolTest(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self._copy_analyzer_config()
        load_analyzer_config.cache_clear()
        self.config = load_analyzer_config(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()
        load_analyzer_config.cache_clear()

    def _copy_analyzer_config(self):
        source_dir = self.repo_root / "config" / "analyzer"
        target_dir = self.root / "config" / "analyzer"
        target_dir.mkdir(parents=True)
        for source_path in source_dir.glob("*.json"):
            shutil.copyfile(source_path, target_dir / source_path.name)

        analyzer_config_path = target_dir / "analyzer_config.json"
        analyzer_config = json.loads(analyzer_config_path.read_text(encoding="utf-8"))
        analyzer_config["log_path"] = "logs/analyzer.log"
        analyzer_config["pending_intents_path"] = "storage/analyzer/pending_intents.json"
        analyzer_config_path.write_text(json.dumps(analyzer_config, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_stub_classifier_reports_not_ready(self):
        classifier = IntentClassifier()
        prediction = classifier.predict_single("hello", list("hello"))

        self.assertFalse(classifier.is_ready)
        self.assertFalse(prediction.ready)
        self.assertTrue(prediction.not_ready)
        self.assertEqual(prediction.error, "classifier_not_ready")
        self.assertFalse(classifier.status()["ready"])

    def test_prediction_normalizes_probability_distribution(self):
        prediction = IntentPrediction.from_probabilities(
            {"search": 1.2, "chat": -0.4, "bad": "x", "summarize": 0.7}
        ).normalized(known_intents={"search", "summarize", "chat"})

        self.assertEqual(prediction.probabilities, {"search": 1.0, "chat": 0.0, "summarize": 0.7})
        self.assertEqual(prediction.ordered_intents(), ["search", "summarize", "chat"])
        self.assertEqual(prediction.top_intent, "search")
        self.assertEqual(prediction.top_probability, 1.0)

    def test_analyzer_consumes_ready_single_classifier_distribution(self):
        analyzer = ComplexityAnalyzer(analyzer_config=self.config, intent_classifier=ReadySingleClassifier())
        result = analyzer.analyze("alpha beta gamma")

        self.assertEqual(result.intent_sequence, ["search"])
        self.assertEqual(result.intent_source, "test_classifier")
        self.assertEqual(result.classifier_confidence, 0.91)
        self.assertEqual(result.uncertainty_reason, "confident")
        self.assertNotIn("unknown_intent", result.intent_sequence)

    def test_analyzer_consumes_ready_multi_classifier_distribution(self):
        analyzer = ComplexityAnalyzer(analyzer_config=self.config, intent_classifier=ReadyMultiClassifier())
        result = analyzer.analyze("alpha 然后 beta")

        self.assertEqual(result.intent_sequence, ["search", "summarize"])
        self.assertEqual(result.intent_source, "test_classifier")
        self.assertEqual(result.uncertainty_reason, "confident_multi_label")

    def test_analyzer_degrades_when_classifier_is_uncertain(self):
        analyzer = ComplexityAnalyzer(analyzer_config=self.config, intent_classifier=UncertainClassifier())
        result = analyzer.analyze("alpha beta gamma")

        self.assertEqual(result.intent_sequence, ["chat"])
        self.assertEqual(result.intent_source, "fallback")
        self.assertEqual(result.uncertainty_reason, "low_confidence")

    def test_uncertainty_detector_boundaries(self):
        detector = UncertaintyDetector(low_confidence=0.6, margin_threshold=0.2, entropy_threshold=0.7)

        self.assertEqual(detector.detect({}).reason, "no_probabilities")
        self.assertEqual(detector.detect({"search": 0.55, "chat": 0.2}).reason, "low_confidence")
        self.assertEqual(detector.detect({"search": 0.7, "chat": 0.55}).reason, "small_margin")
        self.assertEqual(detector.detect({"search": 0.92, "chat": 0.05}).reason, "confident")
        self.assertEqual(
            detector.detect({"search": 0.88, "summarize": 0.8}, multi_label=True).reason,
            "confident_multi_label",
        )


if __name__ == "__main__":
    unittest.main()
