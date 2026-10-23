from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .errors import ServiceError

Eligibility = Literal["candidate", "excluded", "needs_review"]
MAX_SAFE_INTEGER = 9_007_199_254_740_991


class PackingTask(BaseModel):
    model_config = ConfigDict(extra="ignore")

    taskSn: str
    ptId: str
    relateSn: str
    sid: str
    storeIds: list[str]
    taskStatus: int | None
    relateSnType: int | None
    createdAt: str
    completedAt: str
    shipmentIds: list[str]
    staIds: list[str]
    relationKnown: bool
    eligibility: Eligibility
    reason: str


def _row(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [_row(item) for item in value] if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _store_id(value: Any) -> str:
    candidate = _text(value)
    return candidate if re.fullmatch(r"[1-9]\d*", candidate) else ""


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    candidate = _text(value)
    if not candidate:
        return None
    try:
        number = Decimal(candidate)
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    result = int(number)
    return result if abs(result) <= MAX_SAFE_INTEGER else None


def normalize_task(value: Any) -> PackingTask:
    outer = _row(value)
    task_info = outer.get("taskInfo")
    task = _row(task_info) if isinstance(task_info, Mapping) else outer
    products = [
        *_rows(outer.get("itemList")),
        *_rows(outer.get("productItems")),
        *[
            product
            for box in _rows(outer.get("boxList"))
            for product in _rows(box.get("boxProduct"))
        ],
    ]
    relation_value = task.get("relateShipment")
    relation_rows = _rows(relation_value)

    store_ids = sorted(
        {
            sid
            for sid in (
                _store_id(item)
                for item in [task.get("sid"), *[product.get("sid") for product in products]]
            )
            if sid
        }
    )
    shipment_values = [
        task.get("shipmentId"),
        *[relation.get("shipmentId") for relation in relation_rows],
        *[product.get("shipmentId") for product in products],
    ]
    shipment_ids = sorted(
        {_text(item) for item in shipment_values if isinstance(item, str) and _text(item)}
    )
    invalid_shipment = any(
        item is not None and not isinstance(item, str) for item in shipment_values
    )
    relation_known = isinstance(relation_value, list) and all(
        isinstance(item, Mapping) and "shipmentId" in item for item in relation_value
    )

    task_status = _integer(task.get("taskStatus"))
    relate_sn_type = _integer(task.get("relateSnType"))
    result = PackingTask(
        taskSn=_text(task.get("taskSn")),
        ptId=_text(task.get("ptId")),
        relateSn=_text(task.get("relateSn") or outer.get("relateSn")),
        sid=store_ids[0] if len(store_ids) == 1 else "",
        storeIds=store_ids,
        taskStatus=task_status,
        relateSnType=relate_sn_type,
        createdAt=_text(task.get("gmtCreate")),
        completedAt=_text(task.get("finishTime")),
        shipmentIds=shipment_ids,
        staIds=sorted(
            {
                sta_id
                for sta_id in (_text(item.get("staInboundPlanId")) for item in relation_rows)
                if sta_id
            }
        ),
        relationKnown=relation_known,
        eligibility="needs_review",
        reason="INCOMPLETE_DATA",
    )

    if result.taskStatus is not None and result.taskStatus != 3:
        result.eligibility = "excluded"
        result.reason = "NOT_COMPLETED"
    elif result.shipmentIds:
        result.eligibility = "excluded"
        result.reason = "HAS_SHIPMENT"
    elif result.relateSnType is not None and result.relateSnType != 2:
        result.eligibility = "excluded"
        result.reason = "NOT_SP_SOURCE"
    elif invalid_shipment:
        result.reason = "INVALID_SHIPMENT_FIELD"
    elif not result.relationKnown:
        result.reason = "SHIPMENT_RELATION_UNKNOWN"
    elif (
        not result.taskSn
        or re.fullmatch(r"SP[A-Za-z0-9_-]+", result.relateSn) is None
        or not result.sid
        or result.taskStatus != 3
        or result.relateSnType != 2
    ):
        result.reason = "INVALID_SOURCE_OR_STORE"
    else:
        result.eligibility = "candidate"
        result.reason = "NO_SHIPMENT_EXISTING_STA" if result.staIds else "COMPLETED_NO_SHIPMENT"
    return result


def in_scope(task: PackingTask, allowed_sids: frozenset[str] | set[str]) -> bool:
    return bool(task.storeIds) and all(sid in allowed_sids for sid in task.storeIds)


def read_page(
    envelope: Mapping[str, Any],
    offset: int,
    length: int,
) -> tuple[list[Any], dict[str, Any]]:
    data = _row(envelope.get("data"))
    source_tasks = data.get("list")
    if not isinstance(source_tasks, list):
        raise ServiceError("INVALID_PAGE", "领星未返回 data.list，不能将本页视为空结果。")

    data_total = _integer(data.get("total")) if "total" in data else None
    root_total = _integer(envelope.get("total")) if "total" in envelope else None
    if (
        ("total" in data and (data_total is None or data_total < 0))
        or ("total" in envelope and (root_total is None or root_total < 0))
        or (data_total is not None and root_total is not None and data_total != root_total)
    ):
        raise ServiceError(
            "INVALID_PAGE_TOTAL",
            "领星分页总数缺损或互相矛盾，请核验真实响应。",
        )

    total = data_total if data_total is not None else root_total
    received = len(source_tasks)
    if (
        received > length
        or (received == 0 and total is not None and offset < total)
        or (total is not None and received > 0 and offset + received > total)
    ):
        raise ServiceError(
            "INCONSISTENT_PAGE",
            "领星分页数据与总数不一致，请重新查询本次范围。",
            True,
        )
    has_more = received == length if total is None else offset + received < total
    return source_tasks, {
        "offset": offset,
        "length": length,
        "received": received,
        "total": total,
        "hasMore": has_more,
        "nextOffset": offset + received if has_more else None,
    }


def summarize_tasks(tasks: list[PackingTask]) -> dict[str, Any]:
    unique: dict[str, PackingTask] = {}
    conflicts: set[str] = set()
    for task in tasks:
        key = f"{task.sid}:{task.taskSn}"
        old = unique.get(key)
        if old is not None and old.model_dump() != task.model_dump():
            conflicts.add(key)
        else:
            unique[key] = task

    review: list[dict[str, str]] = []
    groups: dict[str, dict[str, Any]] = {}
    excluded_count = 0
    for key, task in unique.items():
        if key in conflicts or task.eligibility == "needs_review":
            review.append(
                {
                    "taskSn": task.taskSn,
                    "relateSn": task.relateSn,
                    "reason": "CONFLICTING_SNAPSHOTS" if key in conflicts else task.reason,
                }
            )
            continue
        if task.eligibility == "excluded":
            excluded_count += 1
            continue
        group_key = f"{task.sid}:{task.relateSn}"
        group = groups.setdefault(
            group_key,
            {"sid": task.sid, "shipmentSn": task.relateSn, "packingTasks": []},
        )
        group["packingTasks"].append(
            {"taskSn": task.taskSn, "ptId": task.ptId, "staIds": task.staIds}
        )

    candidates = [groups[key] for key in sorted(groups)]
    related_sp_numbers = list(dict.fromkeys(candidate["shipmentSn"] for candidate in candidates))
    return {
        "candidates": candidates,
        "relatedSpNumbers": related_sp_numbers,
        "candidateSpCount": len(candidates),
        "candidateTaskCount": sum(len(candidate["packingTasks"]) for candidate in candidates),
        "excludedCount": excluded_count,
        "needsReview": review,
        "note": (
            "这是已完成且未发现货件的装箱任务所关联 SP，尚未核验 SP 待发货状态，"
            "也不代表允许直接创建 STA。"
        ),
    }
