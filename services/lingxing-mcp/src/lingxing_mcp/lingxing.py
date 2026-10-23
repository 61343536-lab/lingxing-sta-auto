from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from .config import Config
from .errors import ServiceError
from .signature import sign_parameters

PACKING_LIST = "/basicOpen/packingTask/list"
PACKING_DETAIL = "/basicOpen/packingTask/taskDetail"
API_ORIGIN = "https://openapi.lingxing.com"
MAX_RESPONSE_CHARS = 8_000_000


class LingxingClient:
    def __init__(
        self,
        config: Config,
        http_client: httpx.AsyncClient | None = None,
        now_ms: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._http_client = http_client or httpx.AsyncClient(follow_redirects=False)
        self._owns_http_client = http_client is None
        self._now_ms = now_ms or (lambda: time.time() * 1000)
        self._token: dict[str, str | float] | None = None
        self._renewing: asyncio.Task[str] | None = None
        self._lanes: dict[str, asyncio.Lock] = {}

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    async def _request(
        self,
        path: str,
        *,
        method: str,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        multipart: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        files = None
        if multipart is not None:
            files = {key: (None, value) for key, value in multipart.items()}
        try:
            response = await self._http_client.request(
                method,
                f"{API_ORIGIN}{path}",
                params=params,
                json=json_body,
                files=files,
                timeout=self.config.timeout_ms / 1000,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                "UPSTREAM_UNAVAILABLE",
                "领星连接失败或超时，可重试本次只读查询。",
                True,
            ) from exc

        if not response.is_success:
            raise ServiceError(
                "UPSTREAM_HTTP_ERROR",
                "领星请求未成功，请检查连接、授权或服务状态。",
                response.status_code == 429 or response.status_code >= 500,
                str(response.status_code),
            )

        try:
            text = response.text
            if len(text) > MAX_RESPONSE_CHARS:
                raise ValueError("response too large")
            envelope = json.loads(text)
            if not isinstance(envelope, dict):
                raise ValueError("invalid response envelope")
        except (UnicodeError, ValueError) as exc:
            raise ServiceError(
                "INVALID_UPSTREAM_RESPONSE",
                "领星响应格式异常，不能视为查询成功。",
            ) from exc
        return envelope

    async def _authorize(self, refresh: str | None = None) -> str:
        form = {
            "appId": self.config.app_id,
            "refreshToken" if refresh else "appSecret": refresh or self.config.app_secret,
        }
        result = await self._request(
            "/api/auth-server/oauth/refresh" if refresh else "/api/auth-server/oauth/access-token",
            method="POST",
            multipart=form,
        )
        if str(result.get("code")) != "200":
            raise ServiceError(
                "LINGXING_AUTH_ERROR",
                "领星鉴权失败，请检查应用凭据、授权及 IP 白名单。",
                False,
                _safe_code(result.get("code")),
            )

        data = result.get("data")
        if not isinstance(data, dict):
            raise ServiceError("INVALID_TOKEN_RESPONSE", "领星未返回有效的令牌信息。")
        access = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_value = data.get("expires_in")
        if isinstance(expires_value, bool) or not isinstance(expires_value, (str, int, float)):
            expires = 0.0
        else:
            try:
                expires = float(expires_value)
            except ValueError:
                expires = 0.0
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(refresh_token, str)
            or not refresh_token
            or expires <= 0
        ):
            raise ServiceError("INVALID_TOKEN_RESPONSE", "领星未返回有效的令牌信息。")

        self._token = {
            "access": access,
            "refresh": refresh_token,
            "expires_at": self._now_ms() + expires * 1000,
        }
        return access

    async def _renew_access_token(self) -> str:
        token = self._token
        if token is not None and float(token["expires_at"]) > self._now_ms():
            try:
                return await self._authorize(str(token["refresh"]))
            except ServiceError as exc:
                if exc.code != "LINGXING_AUTH_ERROR":
                    raise
        return await self._authorize()

    async def _access_token(self) -> str:
        if not self.config.app_id or not self.config.app_secret:
            raise ServiceError(
                "MISSING_CREDENTIALS",
                "请在服务端配置 LINGXING_APP_ID 和 LINGXING_APP_SECRET。",
            )
        token = self._token
        if token is not None and float(token["expires_at"]) > self._now_ms() + 30_000:
            return str(token["access"])

        if self._renewing is None:
            self._renewing = asyncio.create_task(self._renew_access_token())
        renewing = self._renewing
        try:
            return await asyncio.shield(renewing)
        finally:
            if self._renewing is renewing and renewing.done():
                self._renewing = None

    async def read(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        if path not in {PACKING_LIST, PACKING_DETAIL}:
            raise ServiceError("UNSUPPORTED_API", "当前服务只开放装箱任务列表和详情查询。")

        lane = self._lanes.setdefault(path, asyncio.Lock())
        async with lane:
            access = await self._access_token()
            common = {
                "access_token": access,
                "app_key": self.config.app_id,
                "timestamp": str(int(self._now_ms() // 1000)),
            }
            params = {
                **common,
                "sign": sign_parameters({**common, **body}, self.config.app_id),
            }
            result = await self._request(
                path,
                method="POST",
                params=params,
                json_body=body,
            )
            if str(result.get("code")) != "0":
                code = str(result.get("code"))
                raise ServiceError(
                    "LINGXING_API_ERROR",
                    "领星业务查询失败；请依据错误码检查接口权限、白名单、参数或令牌状态。",
                    code == "3001008",
                    _safe_code(result.get("code")),
                )
            return result


def _safe_code(value: Any) -> str:
    code = str(value)
    return code if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code) else "UNKNOWN"
