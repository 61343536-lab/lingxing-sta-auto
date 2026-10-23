from __future__ import annotations

import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from .errors import ServiceError


def current_month_range(timezone_name: str, now: datetime | None = None) -> dict[str, str]:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    local = instant.astimezone(ZoneInfo(timezone_name))
    resolved_at = instant.astimezone(UTC).isoformat(timespec="milliseconds")
    return {
        "timezone": timezone_name,
        "resolvedAt": resolved_at.replace("+00:00", "Z"),
        "today": local.date().isoformat(),
        "startDate": local.date().replace(day=1).isoformat(),
        "endDate": local.date().isoformat(),
        "boundary": "inclusive",
    }


def is_calendar_date(value: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_range(start_date: str, end_date: str) -> None:
    if not is_calendar_date(start_date) or not is_calendar_date(end_date) or start_date > end_date:
        raise ServiceError(
            "INVALID_DATE_RANGE",
            "必须提供有效日期，且开始日期不得晚于结束日期。",
        )
