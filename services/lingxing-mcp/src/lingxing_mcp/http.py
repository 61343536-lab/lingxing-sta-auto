from __future__ import annotations

import hmac
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Config


def _hostname(host_header: str) -> str:
    if host_header.startswith("["):
        end = host_header.find("]")
        return host_header[1:end] if end >= 0 else host_header
    host, separator, port = host_header.rpartition(":")
    return host if separator and port.isdigit() else host_header


class HttpBoundary:
    def __init__(self, app: ASGIApp, config: Config) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        host_header = headers.get("host", "")
        host = _hostname(host_header)
        if not host or (
            host not in self.config.allowed_hosts and host_header not in self.config.allowed_hosts
        ):
            await Response(status_code=421)(scope, receive, send)
            return

        origin = headers.get("origin")
        if origin and origin not in self.config.allowed_origins:
            await Response(status_code=403)(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        if path == "/health" and method == "GET":
            await JSONResponse({"status": "ok"})(scope, receive, send)
            return
        if path != "/mcp":
            await Response(status_code=404)(scope, receive, send)
            return

        supplied = headers.get("authorization", "")
        expected = f"Bearer {self.config.bearer_token}"
        if not self.config.bearer_token or not hmac.compare_digest(supplied, expected):
            await Response(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        if method != "POST":
            await Response(status_code=405, headers={"Allow": "POST"})(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_http_app(config: Config, server: MCPServer[Any]) -> ASGIApp:
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host=config.host,
    )
    return HttpBoundary(app, config)
