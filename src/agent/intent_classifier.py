"""Backward-compatible import path for Analyzer intent classification."""

from .analyzer.intent_classifier import (
    IntentClassifier,
    IntentPrediction,
)

__all__ = [
    "IntentClassifier",
    "IntentPrediction",
]
