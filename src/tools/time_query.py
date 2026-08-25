from __future__ import annotations

import calendar
import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode


class TimeQuery:
    """Deterministic time and date helper."""

    def run(
        self,
        operation: str = "current",
        date: str | None = None,
        timezone: str = "UTC",
    ) -> ToolResult:
        normalized_operation = str(operation or "current").strip().lower()
        zone_result = _zone(timezone)
        if isinstance(zone_result, ToolResult):
            return zone_result
        zone = zone_result

        if normalized_operation == "current":
            now = dt.datetime.now(zone)
            data = {
                "operation": "current",
                "timezone": str(timezone or "UTC"),
                "iso": now.isoformat(),
                "date": now.date().isoformat(),
                "time": now.time().isoformat(timespec="seconds"),
                "utc_offset": now.strftime("%z"),
            }
            return ToolResult.ok(data=data, message=data["iso"])

        if normalized_operation in {"date_info", "convert"}:
            if not isinstance(date, str) or not date.strip():
                return ToolResult.fail(
                    "date is required in YYYY-MM-DD format.",
                    code=ToolErrorCode.MISSING_REQUIRED_PARAM.value,
                )
            try:
                parsed = dt.datetime.strptime(date.strip(), "%Y-%m-%d").date()
            except ValueError as exc:
                return ToolResult.fail(
                    f"Invalid date: {date}",
                    code=ToolErrorCode.INVALID_ARGS.value,
                    data={"date": date, "parse_error": str(exc)},
                )
            data = {
                "operation": "date_info",
                "timezone": str(timezone or "UTC"),
                "date": parsed.isoformat(),
                "weekday": parsed.strftime("%A"),
                "day_of_year": parsed.timetuple().tm_yday,
                "days_in_month": calendar.monthrange(parsed.year, parsed.month)[1],
            }
            return ToolResult.ok(data=data, message=f"{parsed.isoformat()} {data['weekday']}")

        return ToolResult.fail(
            f"Unsupported time operation: {operation}",
            code=ToolErrorCode.INVALID_ARGS.value,
            data={"operation": operation},
        )


_FIXED_TIMEZONES = {
    "UTC": dt.timezone.utc,
    "Etc/UTC": dt.timezone.utc,
    "Asia/Shanghai": dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai"),
}


def _zone(timezone: str | None) -> dt.tzinfo | ToolResult:
    name = str(timezone or "UTC").strip() or "UTC"
    if name in _FIXED_TIMEZONES:
        return _FIXED_TIMEZONES[name]
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ToolResult.fail(
            f"Invalid timezone: {name}",
            code=ToolErrorCode.INVALID_ARGS.value,
            data={"timezone": name},
        )


__all__ = ["TimeQuery"]
