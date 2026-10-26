from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from lingxing_mcp.config import load_config
from lingxing_mcp.http import create_http_app
from lingxing_mcp.lingxing import (
    PACKING_DETAIL,
    PACKING_LIST,
    SHIPMENT_DETAIL_LIST,
    SHIPMENT_LIST,
    LingxingClient,
)
from lingxing_mcp.server import create_mcp_server

CONFIG = load_config(
    {
        "MCP_TRANSPORT": "http",
        "MCP_BEARER_TOKEN": "test-only-mcp-bearer-000000000000000",
        "LINGXING_APP_ID": "0123456789abcdef",
        "LINGXING_APP_SECRET": "test-only-secret",
        "LINGXING_ALLOWED_SIDS": "54",
    }
)
ALL_STORES_CONFIG = load_config(
    {
        "MCP_TRANSPORT": "http",
        "MCP_BEARER_TOKEN": "test-only-all-stores-bearer-000000000",
        "LINGXING_APP_ID": "0123456789abcdef",
        "LINGXING_APP_SECRET": "test-only-secret",
        "LINGXING_ALL_STORES": "true",
    }
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def packing(task_sn: str, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "taskSn": task_sn,
        "ptId": task_sn,
        "relateSn": "SP261023001",
        "sid": 54,
        "taskStatus": 3,
        "relateSnType": 2,
        "relateShipment": [],
        "itemList": [{"sid": 54}],
    }
    value.update(overrides)
    return value


def shipment_list(shipment_sn: str, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": shipment_sn,
        "shipment_sn": shipment_sn,
        "status": 0,
        "status_name": "待发货",
        "wid": 8,
        "wname": "华南二仓",
        "gmt_create": "2026-10-20 09:00:00",
        "is_delete": 0,
        "relate_list": [
            {
                "sid": 54,
                "shipment_id": "",
                "sta_shipment_id": "",
                "sta_inbound_plan_id": "",
            }
        ],
        "not_relate_list": [],
    }
    value.update(overrides)
    return value


def shipment_detail(shipment_sn: str, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": shipment_sn,
        "shipment_sn": shipment_sn,
        "status": 0,
        "status_name": "待发货",
        "wid": 8,
        "wname": "华南二仓",
        "gmt_create": "2026-10-20 09:00:00",
        "gmt_modified": "2026-10-23 10:00:00",
        "is_delete": 0,
        "msku_count": 1,
        "quantity_total": 10,
        "items": [
            {
                "id": f"{shipment_sn}-item",
                "product_id": 201,
                "sid": 54,
                "sname": "US Store",
                "nation": "美国",
                "sku": f"{shipment_sn}-SKU",
                "msku": f"{shipment_sn}-MSKU",
                "fnsku": f"{shipment_sn}-FNSKU",
                "product_name": "Product",
                "num": 10,
                "shipment_id": "",
                "wid": 8,
                "wname": "华南二仓",
            }
        ],
        "box_list": [],
    }
    value.update(overrides)
    return value


def response(request: httpx.Request, value: Any) -> httpx.Response:
    return httpx.Response(200, json=value, request=request)


def content(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error is False, result.model_dump_json()
    assert isinstance(result.structured_content, dict)
    return result.structured_content


@asynccontextmanager
async def running_app(app: Any) -> AsyncIterator[str]:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    else:
        server.should_exit = True
        await task
        raise RuntimeError("test HTTP server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
        sock.close()


@pytest.mark.asyncio
async def test_http_executes_full_monthly_candidate_workflow() -> None:
    tasks = [
        packing("PL261023001"),
        packing("PL261023002", taskStatus=2),
        packing("PL261023003", sid=99, itemList=[{"sid": 99}]),
        packing("PL261023004", relateShipment=None),
        packing("PL261023005"),
        packing("PL261023006", relateShipment=[{"shipmentId": "FBA-ALREADY"}]),
        packing(
            "PL261023007",
            relateSn="SP261023002",
            relateShipment=[{"shipmentId": "", "staInboundPlanId": "wf-existing"}],
        ),
    ]
    list_calls: list[dict[str, Any]] = []
    shipment_list_calls: list[dict[str, Any]] = []
    shipment_detail_calls: list[dict[str, Any]] = []
    pending_shipments = [
        shipment_list("SP261023001"),
        shipment_list(
            "SP261023002",
            relate_list=[
                {
                    "sid": 54,
                    "shipment_id": "",
                    "sta_shipment_id": "sh-existing",
                    "sta_inbound_plan_id": "wf-existing",
                }
            ],
        ),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in request.url.path:
            return response(
                request,
                {
                    "code": "200",
                    "data": {
                        "access_token": "mock-access",
                        "refresh_token": "mock-refresh",
                        "expires_in": 7199,
                    },
                },
            )
        body = json.loads(request.content)
        if request.url.path == SHIPMENT_LIST:
            shipment_list_calls.append(body)
            return response(
                request,
                {
                    "code": 0,
                    "data": {
                        "list": pending_shipments[
                            body["offset"] : body["offset"] + body["length"]
                        ],
                        "total": len(pending_shipments),
                    },
                },
            )
        if request.url.path == SHIPMENT_DETAIL_LIST:
            shipment_detail_calls.append(body)
            second = shipment_detail("SP261023002")
            second["items"][0]["shipment_id"] = "FBA-NOW"
            return response(
                request,
                {
                    "code": 0,
                    "data": [
                        shipment_detail("SP261023001"),
                        second,
                    ],
                },
            )
        if request.url.path == PACKING_LIST:
            list_calls.append(body)
            return response(
                request,
                {
                    "code": 0,
                    "data": {
                        "list": tasks[body["offset"] : body["offset"] + body["length"]],
                        "total": len(tasks),
                    },
                },
            )
        assert request.url.path == PACKING_DETAIL
        assert body["taskSn"] == "PL261023004"
        return response(
            request,
            {"code": 0, "data": {"taskInfo": packing("PL261023004")}},
        )

    upstream_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    lingxing = LingxingClient(CONFIG, upstream_http)
    mcp_server = create_mcp_server(
        CONFIG,
        lingxing,
        lambda: datetime(2026, 10, 23, 4, tzinfo=UTC),
    )
    app = create_http_app(CONFIG, mcp_server)

    async with running_app(app) as base:
        async with httpx.AsyncClient() as raw:
            assert (await raw.post(f"{base}/mcp")).status_code == 401
            assert (
                await raw.post(
                    f"{base}/mcp",
                    headers={
                        "Authorization": f"Bearer {CONFIG.bearer_token}",
                        "Origin": "https://untrusted.example",
                    },
                )
            ).status_code == 403
            assert (await raw.get(f"{base}/health")).json() == {"status": "ok"}

        mcp_http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {CONFIG.bearer_token}"})
        async with mcp_http:
            async with streamable_http_client(
                f"{base}/mcp",
                http_client=mcp_http,
                terminate_on_close=False,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tool_result = await session.list_tools()
                    assert sorted(tool.name for tool in tool_result.tools) == [
                        "get_current_month_range",
                        "get_packing_task",
                        "get_pending_shipment_details",
                        "get_recent_three_month_range",
                        "list_packing_tasks",
                        "list_pending_shipments",
                        "summarize_sp_candidates",
                    ]
                    month = content(await session.call_tool("get_current_month_range", {}))
                    assert month["searchFieldTime"] == "create"
                    assert month["storeScope"] == "allow_list"
                    assert month["allowedSids"] == ["54"]
                    assert month["startDate"][:7] == month["endDate"][:7]
                    recent = content(
                        await session.call_tool("get_recent_three_month_range", {})
                    )
                    assert recent["startDate"] == "2026-07-23"
                    assert recent["endDate"] == "2026-10-23"
                    assert recent["timeType"] == 3

                    collected: list[dict[str, Any]] = []
                    offset: int | None = 0
                    hidden = 0
                    while offset is not None:
                        page = content(
                            await session.call_tool(
                                "list_packing_tasks",
                                {
                                    "startDate": "2026-10-01",
                                    "endDate": "2026-10-23",
                                    "offset": offset,
                                    "length": 2,
                                },
                            )
                        )
                        collected.extend(page["tasks"])
                        hidden += page["hiddenOrUnresolvedStoreCount"]
                        offset = page["pagination"]["nextOffset"]

                    assert hidden == 1
                    assert [item["offset"] for item in list_calls] == [0, 2, 4, 6]
                    assert all(
                        item["searchFieldTime"] == "create" and item["endDate"] == "2026-10-23"
                        for item in list_calls
                    )
                    assert all(task["sid"] == "54" for task in collected)
                    unresolved = next(
                        index
                        for index, task in enumerate(collected)
                        if task["taskSn"] == "PL261023004"
                    )
                    assert collected[unresolved]["eligibility"] == "needs_review"
                    detail = content(
                        await session.call_tool(
                            "get_packing_task",
                            {"taskSn": "PL261023004"},
                        )
                    )
                    collected[unresolved] = detail["task"]
                    summary = content(
                        await session.call_tool(
                            "summarize_sp_candidates",
                            {"tasks": collected, "paginationComplete": True},
                        )
                    )
                    assert summary["relatedSpNumbers"] == [
                        "SP261023001",
                        "SP261023002",
                    ]
                    assert summary["candidateTaskCount"] == 4
                    assert summary["complete"] is True
                    assert summary["excludedCount"] == 2
                    assert len(summary["candidates"][0]["packingTasks"]) == 3
                    assert summary["candidates"][1]["packingTasks"][0]["staIds"] == ["wf-existing"]

                    partial = content(
                        await session.call_tool(
                            "summarize_sp_candidates",
                            {"tasks": collected, "paginationComplete": False},
                        )
                    )
                    assert partial["complete"] is False

                    pending: list[dict[str, Any]] = []
                    shipment_offset: int | None = 0
                    while shipment_offset is not None:
                        shipment_page = content(
                            await session.call_tool(
                                "list_pending_shipments",
                                {
                                    "shipmentSns": summary["relatedSpNumbers"],
                                    "startDate": recent["startDate"],
                                    "endDate": recent["endDate"],
                                    "offset": shipment_offset,
                                    "length": 1,
                                },
                            )
                        )
                        pending.extend(shipment_page["shipments"])
                        shipment_offset = shipment_page["pagination"]["nextOffset"]

                    assert [item["offset"] for item in shipment_list_calls] == [0, 1]
                    assert all(item["status"] == 0 for item in shipment_list_calls)
                    assert all(item["time_type"] == 3 for item in shipment_list_calls)
                    assert all(item["is_delete"] == 0 for item in shipment_list_calls)
                    assert all(item["sids"] == "54" for item in shipment_list_calls)
                    assert all(
                        item["senior_search_list"]
                        == [
                            {
                                "search_field": "shipment_sn",
                                "search_value": ["SP261023001", "SP261023002"],
                            }
                        ]
                        for item in shipment_list_calls
                    )
                    assert len(pending) == 2
                    assert pending[0]["eligibility"] == "candidate"
                    assert pending[1]["reason"] == "EXISTING_STA_RELATION"

                    details = content(
                        await session.call_tool(
                            "get_pending_shipment_details",
                            {"shipmentSns": [item["shipmentSn"] for item in pending]},
                        )
                    )
                    assert shipment_detail_calls == [
                        {
                            "shipment_sn_arr": ["SP261023001", "SP261023002"],
                            "return_deleted": False,
                        }
                    ]
                    assert details["missingShipmentSns"] == []
                    assert len(details["orders"]) == 2
                    assert len(details["orders"][0]["items"]) == 1
                    assert details["summary"]["candidateShipmentSns"] == ["SP261023001"]
                    assert details["summary"]["excluded"] == [
                        {"shipmentSn": "SP261023002", "reason": "HAS_SHIPMENT"}
                    ]

                    invalid = await session.call_tool(
                        "list_packing_tasks",
                        {
                            "startDate": "2026-10-23",
                            "endDate": "2026-10-01",
                        },
                    )
                    assert invalid.is_error is True
                    assert len(list_calls) == 4

    await upstream_http.aclose()


@pytest.mark.asyncio
async def test_http_all_stores_mode_keeps_every_store_and_reviews_unknown_store() -> None:
    tasks = [
        packing("PL261023101", sid=54, itemList=[{"sid": 54}]),
        packing("PL261023102", sid=99, itemList=[{"sid": 99}]),
        packing("PL261023103", sid=0, itemList=[]),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in request.url.path:
            return response(
                request,
                {
                    "code": "200",
                    "data": {
                        "access_token": "mock-access",
                        "refresh_token": "mock-refresh",
                        "expires_in": 7199,
                    },
                },
            )
        assert request.url.path == PACKING_LIST
        return response(
            request,
            {"code": 0, "data": {"list": tasks, "total": len(tasks)}},
        )

    upstream_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    lingxing = LingxingClient(ALL_STORES_CONFIG, upstream_http)
    mcp_server = create_mcp_server(ALL_STORES_CONFIG, lingxing)
    app = create_http_app(ALL_STORES_CONFIG, mcp_server)

    async with running_app(app) as base:
        mcp_http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {ALL_STORES_CONFIG.bearer_token}"}
        )
        async with mcp_http:
            async with streamable_http_client(
                f"{base}/mcp",
                http_client=mcp_http,
                terminate_on_close=False,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    month = content(await session.call_tool("get_current_month_range", {}))
                    assert month["storeScope"] == "all"
                    assert month["allowedSids"] == []

                    page = content(
                        await session.call_tool(
                            "list_packing_tasks",
                            {
                                "startDate": "2026-10-01",
                                "endDate": "2026-10-23",
                                "length": 100,
                            },
                        )
                    )
                    assert len(page["tasks"]) == 3
                    assert page["hiddenOrUnresolvedStoreCount"] == 0
                    assert {task["sid"] for task in page["tasks"]} == {"", "54", "99"}

                    summary = content(
                        await session.call_tool(
                            "summarize_sp_candidates",
                            {"tasks": page["tasks"], "paginationComplete": True},
                        )
                    )
                    assert summary["candidateSpCount"] == 2
                    assert summary["candidateTaskCount"] == 2
                    assert len(summary["needsReview"]) == 1
                    assert summary["needsReview"][0]["reason"] == "INVALID_SOURCE_OR_STORE"

    await upstream_http.aclose()


@pytest.mark.asyncio
async def test_stdio_starts_without_credentials_and_reports_missing_config() -> None:
    environment = {
        **os.environ,
        "MCP_TRANSPORT": "stdio",
        "LINGXING_ALLOWED_SIDS": "54",
        "LINGXING_APP_ID": "",
        "LINGXING_APP_SECRET": "",
        "BUSINESS_TIMEZONE": "Asia/Shanghai",
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lingxing_mcp"],
        cwd=PROJECT_ROOT,
        env=environment,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            month = content(await session.call_tool("get_current_month_range", {}))
            assert month["timezone"] == "Asia/Shanghai"
            result = await session.call_tool(
                "list_packing_tasks",
                {"startDate": "2026-10-01", "endDate": "2026-10-23"},
            )
            assert result.is_error is True
            assert isinstance(result.structured_content, dict)
            assert result.structured_content["error"]["code"] == "MISSING_CREDENTIALS"


@pytest.mark.asyncio
async def test_stdio_allow_list_mode_fails_closed_without_store_scope() -> None:
    environment = {
        **os.environ,
        "MCP_TRANSPORT": "stdio",
        "LINGXING_ALL_STORES": "false",
        "LINGXING_ALLOWED_SIDS": "",
        "LINGXING_APP_ID": "",
        "LINGXING_APP_SECRET": "",
        "BUSINESS_TIMEZONE": "Asia/Shanghai",
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lingxing_mcp"],
        cwd=PROJECT_ROOT,
        env=environment,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            month = content(await session.call_tool("get_current_month_range", {}))
            assert month["storeScope"] == "allow_list"
            assert month["allowedSids"] == []
            result = await session.call_tool(
                "list_packing_tasks",
                {"startDate": "2026-10-01", "endDate": "2026-10-23"},
            )
            assert result.is_error is True
            assert isinstance(result.structured_content, dict)
            assert result.structured_content["error"]["code"] == "MISSING_STORE_SCOPE"
