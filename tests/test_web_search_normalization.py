from __future__ import annotations

import json
import unittest

from src.tools import (
    WebSearchData,
    WebSearchResult,
    build_web_search_observation_views,
    normalize_url_for_dedup,
    normalize_web_search_data,
    normalize_web_search_evidence,
)


class WebSearchNormalizationTest(unittest.TestCase):
    def test_url_normalization_removes_fragment_and_default_port(self):
        self.assertEqual(
            normalize_url_for_dedup("HTTPS://Example.TEST:443/path?q=1#fragment"),
            "https://example.test/path?q=1",
        )
        self.assertEqual(
            normalize_url_for_dedup("https://example.test/path?q=1"),
            "https://example.test/path?q=1",
        )

    def test_duplicate_urls_merge_without_concatenating_content(self):
        data = WebSearchData(
            query="agent",
            provider="tavily",
            provider_type="search_api",
            results=[
                WebSearchResult(
                    title="First",
                    url="https://Example.TEST:443/article#one",
                    snippet="short",
                    score=0.4,
                    rank=3,
                ),
                WebSearchResult(
                    title="",
                    url="https://example.test/article#two",
                    content="longer content",
                    source="example.test",
                    score=0.9,
                    rank=1,
                ),
                WebSearchResult(
                    title="No URL",
                    snippet="kept separately",
                ),
            ],
        )

        normalized = normalize_web_search_evidence(data)

        self.assertEqual(normalized.result_count, 2)
        self.assertEqual(normalized.results[0].url, "https://example.test/article")
        self.assertEqual(normalized.results[0].title, "First")
        self.assertEqual(normalized.results[0].content, "longer content")
        self.assertEqual(normalized.results[0].score, 0.9)
        self.assertEqual(normalized.results[1].title, "No URL")
        self.assertIn("duplicate_urls_removed", normalized.warnings)
        self.assertEqual(normalized.metadata["normalization"]["duplicates_removed"], 1)

    def test_model_builtin_urls_remain_model_reported_and_partial(self):
        data = WebSearchData(
            query="model",
            provider="model_builtin",
            provider_type="model_builtin",
            results=[
                {
                    "title": "Model source",
                    "url": "https://example.test/source",
                    "evidence_level": "url_verified",
                }
            ],
            evidence_level="url_verified",
            source_quality="verified_sources",
        )

        normalized = normalize_web_search_evidence(data)

        self.assertEqual(normalized.evidence_level, "model_reported")
        self.assertEqual(normalized.source_quality, "partial_sources")
        self.assertEqual(normalized.results[0].evidence_level, "model_reported")

    def test_standard_and_full_views_exclude_raw_content(self):
        data = WebSearchData(
            query="views",
            provider="tavily",
            provider_type="search_api",
            summary="summary",
            results=[
                {
                    "title": "A",
                    "url": "https://example.test/a",
                    "snippet": "snippet",
                    "content": "content",
                    "raw_content": "PRIVATE RAW CONTENT",
                }
            ],
        )

        views = build_web_search_observation_views(data, max_chars=5000)
        encoded = json.dumps(views, ensure_ascii=False)

        self.assertIn("snippet", views["standard_data"]["results"][0])
        self.assertNotIn("raw_content", encoded)
        self.assertNotIn("PRIVATE RAW CONTENT", encoded)
        self.assertIn("content", views["full_data"]["results"][0])

    def test_single_and_overall_output_limits_mark_truncation(self):
        data = WebSearchData(
            query="large",
            provider="fake",
            provider_type="fake",
            summary="S" * 200,
            results=[
                {
                    "title": "T" * 1000,
                    "url": "https://example.test/large",
                    "snippet": "N" * 5000,
                    "content": "C" * 5000,
                },
                {
                    "title": "second",
                    "url": "https://example.test/second",
                },
            ],
        )

        normalized = normalize_web_search_evidence(
            data,
            max_output_chars=700,
            max_observation_chars=400,
        )

        self.assertTrue(normalized.truncated)
        self.assertIn("result_fields_truncated", normalized.warnings)
        self.assertTrue(
            normalized.metadata["observation_views"]["standard_data"].get("truncated")
            or normalized.metadata["observation_views"]["full_data"].get("truncated")
        )

    def test_cache_fields_are_reserved_and_passed_through_without_cache_behavior(self):
        data = normalize_web_search_data(
            {
                "query": "cache",
                "provider": "fake",
                "provider_type": "fake",
                "results": [],
                "cache_key": "web:v1:cache",
                "cache_hit": False,
                "cache_age_seconds": 0,
            }
        )

        self.assertEqual(data.cache_key, "web:v1:cache")
        self.assertFalse(data.cache_hit)
        self.assertEqual(data.cache_age_seconds, 0.0)
        self.assertNotIn("cache_hit", data.metadata)


if __name__ == "__main__":
    unittest.main()
