from __future__ import annotations

import calendar
import datetime as dt


class TimeQuery:
    """Time and date helper."""

    def run(self, operation: str = "current", date: str | None = None) -> str:
        if operation == "current":
            return dt.datetime.now().strftime("Current time: %Y-%m-%d %H:%M:%S")
        if operation == "convert":
            if not date:
                return "Please provide a date in YYYY-MM-DD format."
            parsed = dt.datetime.strptime(date, "%Y-%m-%d")
            days_in_month = calendar.monthrange(parsed.year, parsed.month)[1]
            return (
                f"date={date}, weekday={parsed.strftime('%A')}, "
                f"day_of_year={parsed.timetuple().tm_yday}, days_in_month={days_in_month}"
            )
        return f"Unsupported time operation: {operation}"
