from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from lingxing_mcp.config import load_config
from lingxing_mcp.errors import ServiceError, public_error
from lingxing_mcp.lingxing import (
    PACKING_DETAIL,
    PACKING_LIST,
    SHIPMENT_DETAIL_LIST,
    SHIPMENT_LIST,
    LingxingClient,
)
from lingxing_mcp.signature import sign_parameters

CONFIG = load_config(
    {
        "LINGXING_APP_ID": "0123456789abcdef",
        "LINGXING_APP_SECRET": "test-only-secret",
        "LINGXING_ALLOWED_SIDS": "54",
    }
)


def response(request: httpx.Request, value: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=value, request=request)


def token_data(
    access: str = "test-only-access",
    refresh: str = "test-only-refresh",
) -> dict[str, Any]:
    return {
        "code": "200",
        "data": {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 120,
        },
    }


@pytest.mark.asyncio
async def test_credentials_signing_single_flight_and_per_path_serialization() -> None:
    auth_count = 0
    active = 0
    max_active = 0
    bodies: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal auth_count, active, max_active
        assert request.url.host == "openapi.lingxing.com"
        if "oauth" in request.url.path:
            auth_count += 1
            assert "authorization" not in request.headers
            assert request.headers["content-type"].startswith("multipart/form-data")
            content = request.content.decode()
            assert CONFIG.app_id in content
            assert CONFIG.app_secret in content
            return response(request, token_data())

        active += 1
        max_active = max(max_active, active)
        body = json.loads(request.content)
        bodies.append(body)
        common = {
            "access_token": "test-only-access",
            "app_key": CONFIG.app_id,
            "timestamp": "1792713600",
        }
        assert request.url.params["sign"] == sign_parameters(
            {**common, **body},
            CONFIG.app_id,
        )
        assert request.headers.get_list("authorization") == ["Bearer test-only-access"]
        assert request.url.params["app_key"] == CONFIG.app_id
        assert request.url.params["timestamp"] == "1792713600"
        assert "access_token" not in request.url.params
        assert "appSecret" not in request.url.params
        await asyncio.sleep(0)
        active -= 1
        return response(request, {"code": 0, "data": {"list": [], "total": 0}})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LingxingClient(CONFIG, http_client, lambda: 1_792_713_600_000)
    body = {
        "offset": 0,
        "length": 100,
        "relateSnType": ["2"],
        "startDate": "2026-10-01",
        "endDate": "2026-10-23",
    }
    await asyncio.gather(
        client.read(PACKING_LIST, body),
        client.read(PACKING_LIST, body),
    )
    assert auth_count == 1
    assert max_active == 1
    assert bodies == [body, body]
    await client.read(SHIPMENT_LIST, {"status": 0})
    await client.read(SHIPMENT_DETAIL_LIST, {"shipment_sn_arr": ["SP261023001"]})
    with pytest.raises(ServiceError, match="只开放"):
        await client.read("/dangerous/write", {})
    await http_client.aclose()


@pytest.mark.asyncio
async def test_near_expiry_calls_refresh_once_and_rotate_single_use_token() -> None:
    now = 0.0
    refresh_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_count
        if request.url.path.endswith("access-token"):
            return response(request, token_data())
        if request.url.path.endswith("refresh"):
            refresh_count += 1
            content = request.content.decode()
            assert "test-only-refresh" in content
            return response(request, token_data("test-only-access-2", "test-only-refresh-2"))
        return response(request, {"code": 0, "data": {"list": [], "total": 0}})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LingxingClient(CONFIG, http_client, lambda: now)
    await client.read(PACKING_LIST, {})
    now = 100_000
    await asyncio.gather(
        client.read(PACKING_LIST, {}),
        client.read(PACKING_DETAIL, {}),
    )
    assert refresh_count == 1
    await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rate", "network", "malformed"])
async def test_errors_never_become_empty_success_or_echo_secrets(failure: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in request.url.path:
            return response(request, token_data())
        if failure == "network":
            raise httpx.ConnectError(
                f"https://host/?access_token={CONFIG.app_secret}",
                request=request,
            )
        if failure == "malformed":
            return httpx.Response(200, text="not-json", request=request)
        return response(request, {"code": 3001008, "message": CONFIG.app_secret})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LingxingClient(CONFIG, http_client)
    with pytest.raises(ServiceError) as caught:
        await client.read(PACKING_LIST, {})
    safe = public_error(caught.value)
    assert CONFIG.app_secret not in json.dumps(safe)
    assert safe["retryable"] is (failure != "malformed")
    await http_client.aclose()


@pytest.mark.asyncio
async def test_missing_credentials_fails_without_fetching() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"must not fetch {request.url}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LingxingClient(load_config({}), http_client)
    with pytest.raises(ServiceError, match="LINGXING_APP_ID"):
        await client.read(PACKING_LIST, {})
    await http_client.aclose()
