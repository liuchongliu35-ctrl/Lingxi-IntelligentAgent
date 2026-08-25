from __future__ import annotations

from typing import Any, Mapping

from ..base import ToolResult
from ..errors import ToolErrorCode
from ..protocol import ToolCallContext, ToolCallOptions
from .protocol import WebSearchContext, WebSearchRequest
from .router import WebSearchRouter, WebSearchRouterConfig


class WebSearchTool:
    """Unified web_search tool entry for ReActExecutor ToolRuntime calls."""

    def __init__(
        self,
        providers_config: Mapping[str, Any] | None = None,
        *,
        router: WebSearchRouter | None = None,
        model_manager: Any | None = None,
    ) -> None:
        self.router = router or WebSearchRouter(
            providers_config,
            model_manager=model_manager,
        )
        self.config: WebSearchRouterConfig = self.router.config

    def run(
        self,
        query: str,
        max_results: int | None = None,
        topic: str | None = None,
        search_depth: str | None = None,
        time_range: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        include_answer: bool = False,
        include_raw_content: bool = False,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        provider: str = "auto",
        observation_mode: str | None = None,
        *,
        timeout_seconds: int | None = None,
        tool_call_context: ToolCallContext | None = None,
        tool_call_options: ToolCallOptions | None = None,
    ) -> ToolResult:
        effective_provider = self._effective_provider(provider)
        try:
            request = WebSearchRequest(
                query=query,
                max_results=(
                    max_results
                    if max_results is not None
                    else self._default_max_results(effective_provider)
                ),
                topic=topic or self._default_topic(effective_provider),
                search_depth=search_depth or self._default_search_depth(effective_provider),
                time_range=time_range,
                start_date=start_date,
                end_date=end_date,
                include_answer=include_answer,
                include_raw_content=include_raw_content,
                include_domains=include_domains or [],
                exclude_domains=exclude_domains or [],
                provider=provider,
                observation_mode=observation_mode,
            )
        except ValueError as exc:
            return ToolResult.fail(
                str(exc),
                code=ToolErrorCode.INVALID_ARGS.value,
                data={"query": query, "provider": provider, "max_results": max_results},
            )
        if len(request.query) > self.config.max_query_chars:
            return ToolResult.fail(
                f"query exceeds max length: {self.config.max_query_chars}",
                code=ToolErrorCode.INVALID_ARGS.value,
                data={
                    "query_chars": len(request.query),
                    "max_query_chars": self.config.max_query_chars,
                },
            )

        context = _web_search_context(
            tool_call_context=tool_call_context,
            tool_call_options=tool_call_options,
            timeout_seconds=timeout_seconds or self.config.timeout_seconds,
            observation_mode=observation_mode,
        )
        if context.dry_run:
            return self.router.dry_run(request, context)
        return self.router.search(request, context)

    def _effective_provider(self, provider: str) -> str:
        if provider != "auto":
            return provider
        return str(self.config.provider or "auto")

    def _default_max_results(self, provider: str) -> int:
        if provider == "search_api":
            search_api = self.config.provider_config("search_api")
            value = search_api.get("max_results")
            if value is not None:
                try:
                    return max(int(value), 1)
                except (TypeError, ValueError):
                    pass
        return max(int(self.config.max_results), 1)

    def _default_search_depth(self, provider: str) -> str:
        if provider == "search_api":
            search_api = self.config.provider_config("search_api")
            value = search_api.get("default_search_depth")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "basic"

    def _default_topic(self, provider: str) -> str:
        if provider == "search_api":
            search_api = self.config.provider_config("search_api")
            value = search_api.get("default_topic")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "general"


def _web_search_context(
    *,
    tool_call_context: ToolCallContext | None,
    tool_call_options: ToolCallOptions | None,
    timeout_seconds: int,
    observation_mode: str | None,
) -> WebSearchContext:
    options = tool_call_options or ToolCallOptions()
    context = tool_call_context or ToolCallContext()
    return WebSearchContext(
        trace_id=context.trace_id,
        execution_id=context.execution_id,
        step_id=context.step_id,
        workspace_root=context.workspace_root,
        allow_network=options.allow_network,
        dry_run=options.dry_run,
        timeout_seconds=options.timeout_seconds or timeout_seconds,
        observation_mode=options.observation_mode or observation_mode,
        max_output_chars=options.max_output_chars,
        max_observation_chars=options.max_observation_chars,
    )
__all__ = ["WebSearchTool"]
