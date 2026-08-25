"""Backward-compatible import path for Analyzer uncertainty detection."""

from .analyzer.uncertainty_detector import UncertaintyDetector, UncertaintyResult

__all__ = ["UncertaintyDetector", "UncertaintyResult"]
