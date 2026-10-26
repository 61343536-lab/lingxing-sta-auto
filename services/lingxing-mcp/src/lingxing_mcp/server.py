from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import AfterValidator, Field

from .config import Config
from .dates import current_month_range, is_calendar_date, recent_months_range, validate_range
from .errors import ServiceError, public_error
from .lingxing import (
    PACKING_DETAIL,
    PACKING_LIST,
    SHIPMENT_DETAIL_LIST,
    SHIPMENT_LIST,
    LingxingClient,
)
from .packing import PackingTask, in_scope, normalize_task, read_page, summarize_tasks
from .shipment import (
    normalize_shipment_detail,
    normalize_shipment_list_item,
    shipment_in_scope,
    summarize_shipments,
)

ReadOnlyAnnotations = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
ClosedWorldAnnotations = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def _calendar_date(value: str) -> str:
    if not is_calendar_date(value):
        raise ValueError("格式为有效的 YYYY-MM-DD 日期")
    return value


CalendarDate = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    AfterValidator(_calendar_date),
]
StoreId = Annotated[str, Field(pattern=r"^[1-9]\d*$")]
ShipmentNumber = Annotated[
    str,
    Field(pattern=r"^SP[A-Za-z0-9_-]+$", max_length=100),
]
RelateType = Literal["1", "2", "3", "4"]
ToolValue = dict[str, Any]
ToolFactory = Callable[[], ToolValue | Awaitable[ToolValue]]


def _result(value: ToolValue, *, is_error: bool = False) -> CallToolResult:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return CallToolResult(
        is_error=is_error,
        content=[TextContent(type="text", text=text)],
        structured_content=value,
    )


async def _guarded(factory: ToolFactory) -> CallToolResult:
    try:
        value = factory()
        if inspect.isawaitable(value):
            value = await value
        return _result({"ok": True, **value})
    except Exception as exc:
        value = {"ok": False, "error": public_error(exc)}
        return _result(value, is_error=True)


def _require_scope(config: Config) -> None:
    if not config.all_stores and not config.allowed_sids:
        raise ServiceError(
            "MISSING_STORE_SCOPE",
            "服务端需配置 LINGXING_ALLOWED_SIDS，或显式启用 LINGXING_ALL_STORES=true。",
        )


def _validate_recent_range(start_date: str, end_date: str) -> None:
    validate_range(start_date, end_date)
    if (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days > 100:
        raise ServiceError(
            "DATE_RANGE_TOO_WIDE",
            "发货单创建时间范围不能超过最近三个月。",
        )


def create_mcp_server(
    config: Config,
    client: LingxingClient | None = None,
    now: Callable[[], datetime] | None = None,
) -> MCPServer[Any]:
    lingxing = client or LingxingClient(config)
    owns_client = client is None
    clock = now or (lambda: datetime.now(UTC))

    @asynccontextmanager
    async def lifespan(_server: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            if owns_client:
                await lingxing.aclose()

    server: MCPServer[Any] = MCPServer(
        name="lingxing-packing",
        version="0.1.0",
        lifespan=lifespan,
    )

    @server.tool(
        name="get_current_month_range",
        description=(
            "读取服务运行时当前日期，按公司业务时区返回当月1日至当天的日期范围。"
            "用户说“处理这个月的建单业务”或“处理这个月所有店铺的建单业务”时"
            "首先调用；不要使用模型记忆中的日期。"
        ),
        annotations=ClosedWorldAnnotations,
        structured_output=False,
    )
    async def get_current_month_range() -> CallToolResult:
        return await _guarded(
            lambda: {
                **current_month_range(config.timezone, clock()),
                "searchFieldTime": config.date_field,
                "storeScope": "all" if config.all_stores else "allow_list",
                "allowedSids": [] if config.all_stores else sorted(config.allowed_sids),
            }
        )

    @server.tool(
        name="get_recent_three_month_range",
        description=(
            "读取服务运行时当前日期，按公司业务时区返回当天向前推三个自然月的"
            "闭区间。查询第一阶段SP对应的待发货单时调用，不要由模型估算日期。"
        ),
        annotations=ClosedWorldAnnotations,
        structured_output=False,
    )
    async def get_recent_three_month_range() -> CallToolResult:
        return await _guarded(
            lambda: {
                **recent_months_range(config.timezone, 3, clock()),
                "timeType": 3,
                "timeField": "create",
            }
        )

    @server.tool(
        name="list_packing_tasks",
        description=(
            "对应领星装箱任务列表 API，只查询一页。按服务配置返回全部店铺或白名单"
            "店铺的任务摘要和筛选判断。按照 nextOffset 继续；必须保留日期范围和日期字段。"
            "候选是 taskStatus=3、来源为SP且明确无货件的任务。"
        ),
        annotations=ReadOnlyAnnotations,
        structured_output=False,
    )
    async def list_packing_tasks(
        startDate: CalendarDate,
        endDate: CalendarDate,
        searchFieldTime: Literal["create", "done"] = config.date_field,
        offset: Annotated[int, Field(ge=0)] = 0,
        length: Annotated[int, Field(ge=1, le=200)] = 100,
        wid: list[StoreId] | None = None,
        relateSnType: Annotated[tuple[RelateType, ...], Field(min_length=1)] = ("2",),
    ) -> CallToolResult:
        async def execute() -> ToolValue:
            _require_scope(config)
            validate_range(startDate, endDate)
            body: dict[str, Any] = {
                "startDate": startDate,
                "endDate": endDate,
                "searchFieldTime": searchFieldTime,
                "offset": offset,
                "length": length,
                "relateSnType": list(relateSnType),
            }
            if wid is not None:
                body["wid"] = wid
            result = await lingxing.read(PACKING_LIST, body)
            source_tasks, pagination = read_page(result, offset, length)
            all_tasks = [normalize_task(task) for task in source_tasks]
            tasks = [
                task
                for task in all_tasks
                if in_scope(task, config.allowed_sids, config.all_stores)
            ]
            return {
                "range": {
                    "startDate": startDate,
                    "endDate": endDate,
                    "searchFieldTime": searchFieldTime,
                },
                "pagination": pagination,
                "tasks": [task.model_dump() for task in tasks],
                "hiddenOrUnresolvedStoreCount": len(all_tasks) - len(tasks),
                "pageSummary": summarize_tasks(tasks),
                "note": (
                    "分页基于领星原始记录数；空的范围内结果不等于分页结束。"
                    "needs_review 需查询详情，不能当成货件号为空。"
                ),
            }

        return await _guarded(execute)

    @server.tool(
        name="get_packing_task",
        description=(
            "对应领星装箱任务详情 API。用前一工具返回的 taskSn（PL号）核验状态和"
            "货件关联，返回同形摘要供替换列表记录；不创建或修改任务。"
        ),
        annotations=ReadOnlyAnnotations,
        structured_output=False,
    )
    async def get_packing_task(
        taskSn: Annotated[str, Field(pattern=r"^PL[A-Za-z0-9_-]+$", max_length=100)],
    ) -> CallToolResult:
        async def execute() -> ToolValue:
            _require_scope(config)
            result = await lingxing.read(PACKING_DETAIL, {"taskSn": taskSn})
            task = normalize_task(result.get("data"))
            if task.taskSn != taskSn:
                raise ServiceError("MISMATCHED_TASK", "详情与请求的 PL 不一致。")
            if not in_scope(task, config.allowed_sids, config.all_stores):
                raise ServiceError(
                    "STORE_SCOPE_UNRESOLVED",
                    "该任务不在授权店铺范围内，或无法确定店铺归属。",
                )
            return {"task": task.model_dump(), "summary": summarize_tasks([task])}

        return await _guarded(execute)

    @server.tool(
        name="list_pending_shipments",
        description=(
            "对应领星发货单待发货列表，只查询一页。把第一阶段得到的SP作为发货单号"
            "精确搜索值，固定按创建时间查询最近三个月、状态待发货且未删除。"
            "按照nextOffset继续；每批最多50个SP。"
        ),
        annotations=ReadOnlyAnnotations,
        structured_output=False,
    )
    async def list_pending_shipments(
        shipmentSns: Annotated[
            list[ShipmentNumber],
            Field(min_length=1, max_length=50),
        ],
        startDate: CalendarDate,
        endDate: CalendarDate,
        offset: Annotated[int, Field(ge=0)] = 0,
        length: Annotated[int, Field(ge=1, le=200)] = 100,
    ) -> CallToolResult:
        async def execute() -> ToolValue:
            _require_scope(config)
            _validate_recent_range(startDate, endDate)
            requested = list(dict.fromkeys(shipmentSns))
            body: dict[str, Any] = {
                "status": 0,
                "time_type": 3,
                "start_date": startDate,
                "end_date": endDate,
                "offset": offset,
                "length": length,
                "is_delete": 0,
                "senior_search_list": [
                    {
                        "search_field": "shipment_sn",
                        "search_value": requested,
                    }
                ],
            }
            if not config.all_stores:
                body["sids"] = ",".join(sorted(config.allowed_sids))
            result = await lingxing.read(SHIPMENT_LIST, body)
            source_orders, pagination = read_page(result, offset, length)
            all_orders = [normalize_shipment_list_item(order) for order in source_orders]
            unexpected = sorted(
                {
                    order.shipmentSn
                    for order in all_orders
                    if order.shipmentSn and order.shipmentSn not in requested
                }
            )
            if unexpected:
                raise ServiceError(
                    "MISMATCHED_SHIPMENT_RESULTS",
                    "领星返回了未请求的发货单，不能继续本次汇总。",
                )
            orders = [
                order
                for order in all_orders
                if shipment_in_scope(order, config.allowed_sids, config.all_stores)
            ]
            return {
                "range": {
                    "startDate": startDate,
                    "endDate": endDate,
                    "timeType": 3,
                    "timeField": "create",
                },
                "requestedShipmentSns": requested,
                "pagination": pagination,
                "shipments": [order.model_dump() for order in orders],
                "hiddenOrUnresolvedStoreCount": len(all_orders) - len(orders),
                "pageSummary": summarize_shipments(orders),
                "note": (
                    "列表中的蓝色SP是可点击的发货单号；使用详情工具读取其全部商品明细。"
                ),
            }

        return await _guarded(execute)

    @server.tool(
        name="get_pending_shipment_details",
        description=(
            "批量读取待发货单详情，相当于在领星页面逐个点击蓝色SP发货单号。"
            "一次最多50个SP，返回后续建单所需的订单和全部商品明细；只读，不创建货件。"
        ),
        annotations=ReadOnlyAnnotations,
        structured_output=False,
    )
    async def get_pending_shipment_details(
        shipmentSns: Annotated[
            list[ShipmentNumber],
            Field(min_length=1, max_length=50),
        ],
    ) -> CallToolResult:
        async def execute() -> ToolValue:
            _require_scope(config)
            requested = list(dict.fromkeys(shipmentSns))
            result = await lingxing.read(
                SHIPMENT_DETAIL_LIST,
                {"shipment_sn_arr": requested, "return_deleted": False},
            )
            source_orders = result.get("data")
            if not isinstance(source_orders, list):
                raise ServiceError(
                    "INVALID_SHIPMENT_DETAILS",
                    "领星未返回有效的发货单详情数组。",
                )
            all_orders = [normalize_shipment_detail(order) for order in source_orders]
            returned = {order.shipmentSn for order in all_orders if order.shipmentSn}
            if any(shipment_sn not in requested for shipment_sn in returned):
                raise ServiceError(
                    "MISMATCHED_SHIPMENT_RESULTS",
                    "领星返回了未请求的发货单详情，不能继续本次汇总。",
                )
            orders = [
                order
                for order in all_orders
                if shipment_in_scope(order, config.allowed_sids, config.all_stores)
            ]
            return {
                "requestedShipmentSns": requested,
                "returnedShipmentSns": sorted(returned),
                "missingShipmentSns": [
                    shipment_sn for shipment_sn in requested if shipment_sn not in returned
                ],
                "orders": [order.model_dump() for order in orders],
                "hiddenOrUnresolvedStoreCount": len(all_orders) - len(orders),
                "summary": summarize_shipments(orders),
                "note": (
                    "详情为实时只读结果；候选仅供下一阶段准备，不能据此直接授权创建货件。"
                ),
            }

        return await _guarded(execute)

    @server.tool(
        name="summarize_sp_candidates",
        description=(
            "确定性汇总辅助工具，不访问领星。接收所有已取页的任务摘要；用详情替换"
            "待核验记录后再调用。按店铺+SP去重，保留全部PL；不得仅传候选而省略"
            "待核验和排除记录。"
        ),
        annotations=ClosedWorldAnnotations,
        structured_output=False,
    )
    async def summarize_sp_candidates(
        tasks: Annotated[list[PackingTask], Field(max_length=10_000)],
        paginationComplete: bool,
    ) -> CallToolResult:
        def execute() -> ToolValue:
            _require_scope(config)
            if any(
                not in_scope(task, config.allowed_sids, config.all_stores) for task in tasks
            ):
                raise ServiceError(
                    "STORE_SCOPE_UNRESOLVED",
                    "汇总包含未授权或店铺归属不明的任务。",
                )

            checked: list[PackingTask] = []
            for task in tasks:
                raw: dict[str, Any] = {
                    "taskSn": task.taskSn,
                    "ptId": task.ptId,
                    "relateSn": task.relateSn,
                    "sid": task.sid,
                    "itemList": [
                        *[{"sid": sid} for sid in task.storeIds],
                        *[{"shipmentId": shipment_id} for shipment_id in task.shipmentIds],
                    ],
                    "taskStatus": task.taskStatus,
                    "relateSnType": task.relateSnType,
                    "gmtCreate": task.createdAt,
                    "finishTime": task.completedAt,
                }
                if task.relationKnown:
                    raw["relateShipment"] = [
                        *[{"shipmentId": item} for item in task.shipmentIds],
                        *[{"shipmentId": "", "staInboundPlanId": item} for item in task.staIds],
                    ]
                normalized = normalize_task(raw)
                if task.reason == "INVALID_SHIPMENT_FIELD":
                    normalized.eligibility = "needs_review"
                    normalized.reason = task.reason
                checked.append(normalized)

            return {
                **summarize_tasks(checked),
                "complete": paginationComplete,
                "source": "caller_provided_tool_results",
                "limitation": (
                    "分页完整性由 Agent 提供；仅当每一页均成功且 hasMore=false 时才能报告完整结果。"
                ),
            }

        return await _guarded(execute)

    return server
