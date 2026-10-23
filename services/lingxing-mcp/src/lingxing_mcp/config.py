from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ServiceError

DateField = Literal["create", "done"]
Transport = Literal["stdio", "http"]


@dataclass(frozen=True, slots=True)
class Config:
    app_id: str
    app_secret: str
    allowed_sids: frozenset[str]
    timezone: str
    date_field: DateField
    timeout_ms: int
    transport: Transport
    host: str
    port: int
    bearer_token: str
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]


def _csv(value: str = "") -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def load_config(env: Mapping[str, str] | None = None) -> Config:
    values = os.environ if env is None else env
    timezone_name = values.get("BUSINESS_TIMEZONE", "") or "Asia/Shanghai"
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ServiceError("CONFIG_ERROR", "BUSINESS_TIMEZONE 不是有效时区。") from exc

    date_field_value = values.get("PACKING_DATE_FIELD", "") or "create"
    transport_value = values.get("MCP_TRANSPORT", "") or "stdio"
    timeout_text = values.get("LINGXING_TIMEOUT_MS", "") or "20000"
    port_text = values.get("MCP_PORT", "") or "3100"
    allowed_sids = frozenset(_csv(values.get("LINGXING_ALLOWED_SIDS", "")))

    try:
        timeout_ms = int(timeout_text)
        port = int(port_text)
    except ValueError as exc:
        raise ServiceError(
            "CONFIG_ERROR",
            "检查日期字段、传输方式、超时、端口和授权店铺配置。",
        ) from exc

    if (
        date_field_value not in {"create", "done"}
        or transport_value not in {"stdio", "http"}
        or not 100 <= timeout_ms <= 25_000
        or not 1 <= port <= 65_535
        or any(re.fullmatch(r"[1-9]\d*", sid) is None for sid in allowed_sids)
    ):
        raise ServiceError(
            "CONFIG_ERROR",
            "检查日期字段、传输方式、超时、端口和授权店铺配置。",
        )

    bearer_token = values.get("MCP_BEARER_TOKEN", "")
    if transport_value == "http" and len(bearer_token) < 32:
        raise ServiceError("CONFIG_ERROR", "HTTP 模式需要至少 32 字符的独立 MCP_BEARER_TOKEN。")

    return Config(
        app_id=values.get("LINGXING_APP_ID", ""),
        app_secret=values.get("LINGXING_APP_SECRET", ""),
        allowed_sids=allowed_sids,
        timezone=timezone_name,
        date_field=cast(DateField, date_field_value),
        timeout_ms=timeout_ms,
        transport=cast(Transport, transport_value),
        host=values.get("MCP_HOST", "") or "127.0.0.1",
        port=port,
        bearer_token=bearer_token,
        allowed_hosts=_csv(values.get("MCP_ALLOWED_HOSTS", "") or "localhost,127.0.0.1"),
        allowed_origins=_csv(values.get("MCP_ALLOWED_ORIGINS", "")),
    )
