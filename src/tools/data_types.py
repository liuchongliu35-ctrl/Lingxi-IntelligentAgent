from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from .base import _json_safe


WEB_SEARCH_EVIDENCE_LEVELS = frozenset(
    {
        "url_verified",
        "provider_reported",
        "model_reported",
        "no_url_summary",
    }
)
WEB_SEARCH_SOURCE_QUALITIES = frozenset(
    {
        "verified_sources",
        "partial_sources",
        "summary_only",
        "empty",
    }
)


class ToolDataMixin:
    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class FileReadData(ToolDataMixin):
    path: str = ""
    encoding: str = "utf-8"
    size_bytes: int = 0
    line_count: int = 0
    content: str | None = None
    content_preview: str | None = None
    content_truncated: bool = False
    content_hash: str | None = None
    is_sensitive: bool = False


@dataclass
class FileWriteData(ToolDataMixin):
    path: str = ""
    write_mode: str = "overwrite"
    created: bool = False
    overwritten: bool = False
    appended: bool = False
    bytes_written: int = 0
    content_hash_before: str | None = None
    content_hash_after: str | None = None
    content_preview: str | None = None
    content_truncated: bool = False


@dataclass
class FilePatchData(ToolDataMixin):
    path: str = ""
    patch_count: int = 0
    applied_count: int = 0
    changed_lines: int = 0
    diff_preview: str | None = None
    content_hash_before: str | None = None
    content_hash_after: str | None = None
    patch_results: list[Any] = field(default_factory=list)


@dataclass
class FileDeleteData(ToolDataMixin):
    paths: list[str] = field(default_factory=list)
    deleted_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    total_count: int = 0
    total_size_bytes: int = 0


@dataclass
class CommandExecutionData(ToolDataMixin):
    command: str = ""
    program: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    purpose: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_preview: str | None = None
    stderr_preview: str | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    timeout_seconds: float | None = None
    duration_ms: int | None = None


@dataclass
class DocumentParseData(ToolDataMixin):
    path: str = ""
    file_type: str = ""
    title: str | None = None
    page_count: int | None = None
    sheet_count: int | None = None
    text: str | None = None
    text_preview: str | None = None
    text_truncated: bool = False
    tables: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    parser: str | None = None


@dataclass
class WebSearchResult(ToolDataMixin):
    title: str = ""
    url: str | None = None
    snippet: str | None = None
    content: str | None = None
    score: float | None = None
    rank: int | None = None
    source: str | None = None
    published_at: str | None = None
    favicon: str | None = None
    images: list[Any] = field(default_factory=list)
    raw_content: str | None = None
    evidence_level: str | None = None

    def __post_init__(self) -> None:
        self.title = str(self.title or "")
        self.url = _optional_str(self.url)
        self.snippet = _optional_str(self.snippet)
        self.content = _optional_str(self.content)
        self.source = _optional_str(self.source)
        self.published_at = _optional_str(self.published_at)
        self.favicon = _optional_str(self.favicon)
        self.images = list(self.images or [])
        self.raw_content = _optional_str(self.raw_content)
        if self.score is not None:
            self.score = float(self.score)
        if self.rank is not None:
            self.rank = max(int(self.rank), 1)
        self.evidence_level = normalize_web_search_evidence_level(
            self.evidence_level,
            default="url_verified" if self.url else "provider_reported",
        )


@dataclass
class WebSearchData(ToolDataMixin):
    query: str = ""
    provider: str = ""
    provider_type: str = ""
    mode: str = ""
    provider_request_id: str | None = None
    retrieved_at: str | None = None
    schema_version: str = "v1"
    search_depth: str | None = None
    topic: str | None = None
    answer: str | None = None
    summary: str | None = None
    results: list[WebSearchResult] = field(default_factory=list)
    result_count: int = 0
    evidence_level: str | None = None
    source_quality: str | None = None
    response_time_ms: int | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw_content_included: bool = False
    truncated: bool = False
    cache_key: str | None = None
    cache_hit: bool | None = None
    cache_age_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.results = [_coerce_web_search_result(result) for result in self.results]
        self.result_count = len(self.results)
        self.evidence_level = normalize_web_search_evidence_level(
            self.evidence_level,
            default=infer_web_search_evidence_level(
                self.results,
                summary=self.summary or self.answer,
                provider_type=self.provider_type,
            ),
        )
        self.source_quality = normalize_web_search_source_quality(
            self.source_quality,
            default=infer_web_search_source_quality(
                self.results,
                summary=self.summary or self.answer,
                provider_type=self.provider_type,
            ),
        )
        if self.evidence_level == "no_url_summary" and self.source_quality == "verified_sources":
            self.source_quality = "summary_only"
        self.usage = dict(self.usage)
        self.metadata = dict(self.metadata)
        self.cache_key = _optional_str(self.cache_key)
        if self.cache_hit is not None:
            self.cache_hit = bool(self.cache_hit)
        if self.cache_age_seconds is not None:
            self.cache_age_seconds = max(float(self.cache_age_seconds), 0.0)
        self.warnings = [str(item) for item in self.warnings]
        self.raw_content_included = bool(self.raw_content_included)
        self.truncated = bool(self.truncated)


def normalize_web_search_evidence_level(value: Any, *, default: str = "provider_reported") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in WEB_SEARCH_EVIDENCE_LEVELS:
        return normalized
    return default if default in WEB_SEARCH_EVIDENCE_LEVELS else "provider_reported"


def normalize_web_search_source_quality(value: Any, *, default: str = "empty") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in WEB_SEARCH_SOURCE_QUALITIES:
        return normalized
    return default if default in WEB_SEARCH_SOURCE_QUALITIES else "empty"


def infer_web_search_evidence_level(
    results: list[WebSearchResult],
    *,
    summary: str | None = None,
    provider_type: str | None = None,
) -> str:
    if results and any(result.url for result in results):
        if str(provider_type or "").strip().lower() == "model_builtin":
            return "model_reported"
        return "url_verified"
    if results:
        return "provider_reported"
    if summary:
        return "no_url_summary"
    return "provider_reported"


def infer_web_search_source_quality(
    results: list[WebSearchResult],
    *,
    summary: str | None = None,
    provider_type: str | None = None,
) -> str:
    if str(provider_type or "").strip().lower() == "model_builtin" and results:
        return "partial_sources"
    if results and all(result.url for result in results):
        return "verified_sources"
    if results:
        return "partial_sources"
    if summary:
        return "summary_only"
    return "empty"


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


_WEB_SEARCH_RESULT_FIELDS = {
    field_info.name for field_info in fields(WebSearchResult)
}


def _coerce_web_search_result(value: Any) -> WebSearchResult:
    if isinstance(value, WebSearchResult):
        return value
    if isinstance(value, dict):
        filtered = {
            key: item
            for key, item in value.items()
            if key in _WEB_SEARCH_RESULT_FIELDS
        }
        return WebSearchResult(**filtered)
    return WebSearchResult(title=str(value or ""))


@dataclass
class MCPToolData(ToolDataMixin):
    source_type: str = "mcp"
    server_id: str = ""
    remote_tool_name: str = ""
    content: list[Any] = field(default_factory=list)
    structured_content: Any = None
    resource_links: list[Any] = field(default_factory=list)
    is_error: bool = False
    stderr_preview: str | None = None
    output_truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source_type = "mcp"
        self.server_id = str(self.server_id or "")
        self.remote_tool_name = str(self.remote_tool_name or "")
        self.content = list(self.content or [])
        self.resource_links = list(self.resource_links or [])
        self.is_error = bool(self.is_error)
        self.stderr_preview = _optional_str(self.stderr_preview)
        self.output_truncated = bool(self.output_truncated)
        self.metadata = dict(self.metadata or {})
