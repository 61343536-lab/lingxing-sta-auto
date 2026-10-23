from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        upstream_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.upstream_code = upstream_code


def public_error(error: BaseException) -> dict[str, Any]:
    if isinstance(error, ServiceError):
        result: dict[str, Any] = {
            "code": error.code,
            "message": str(error),
            "retryable": error.retryable,
        }
        if error.upstream_code:
            result["upstreamCode"] = error.upstream_code
        return result
    return {
        "code": "INTERNAL_ERROR",
        "message": "处理失败，请联系服务管理员。",
        "retryable": False,
    }
