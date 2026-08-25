"""Backward-compatible import path for Analyzer configuration."""

from .analyzer.analyzer_config import AnalyzerConfig, load_analyzer_config

__all__ = ["AnalyzerConfig", "load_analyzer_config"]
