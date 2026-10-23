from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import AfterValidator, Field

from .config import Config
from .dates import current_month_range, is_calendar_date, validate_range
from .errors import ServiceError, public_error
from .lingxing import PACKING_DETAIL, PACKING_LIST, LingxingClient
from .packing import PackingTask, in_scope, normalize_task, read_page, summarize_tasks

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
