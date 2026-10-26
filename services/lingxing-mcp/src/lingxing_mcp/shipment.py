from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Eligibility = Literal["candidate", "excluded", "needs_review"]
MAX_SAFE_INTEGER = 9_007_199_254_740_991


class ShipmentItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    itemId: str
    productId: str
    sid: str
    storeName: str
    country: str
    sku: str
    msku: str
    fnsku: str
    fulfillmentNetworkSku: str
    productName: str
    imageUrl: str
    quantity: int | None
    plannedQuantity: int | None
    appliedQuantity: int | None
    boxCount: int | None
    unitsPerBox: int | None
    packingType: int | None
    shipmentId: str
    warehouseId: str
    warehouseName: str


class ShipmentOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")

    orderId: str
    shipmentSn: str
    status: int | None
    statusName: str
    warehouseId: str
    warehouseName: str
    createdAt: str
    modifiedAt: str
    remark: str
    destinationCenters: list[str]
    storeIds: list[str]
    fbaShipmentIds: list[str]
    staShipmentIds: list[str]
    staTaskIds: list[str]
    skuCount: int | None
    quantityTotal: int | None
    boxType: str
    boxRemark: str
    items: list[ShipmentItem]
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


def _texts(values: list[Any]) -> list[str]:
    return sorted({_text(value) for value in values if _text(value)})


def _centers(value: Any) -> list[str]:
    if isinstance(value, list):
        return _texts(value)
    return sorted(part.strip() for part in _text(value).split(",") if part.strip())


def _nested_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [nested for row in rows for nested in _rows(row.get(key))]


def _normalize_item(value: Any) -> ShipmentItem:
    item = _row(value)
    return ShipmentItem(
        itemId=_text(item.get("id")),
        productId=_text(item.get("product_id")),
        sid=_store_id(item.get("sid")),
        storeName=_text(item.get("sname")),
        country=_text(item.get("nation")),
        sku=_text(item.get("sku")),
        msku=_text(item.get("msku")),
        fnsku=_text(item.get("fnsku")),
        fulfillmentNetworkSku=_text(item.get("fulfillment_network_sku")),
        productName=_text(item.get("product_name")),
        imageUrl=_text(item.get("pic_url")),
        quantity=_integer(item.get("num")),
        plannedQuantity=_integer(item.get("shipment_plan_quantity")),
        appliedQuantity=_integer(item.get("apply_num")),
        boxCount=_integer(item.get("box_num")),
        unitsPerBox=_integer(item.get("quantity_in_case")),
        packingType=_integer(item.get("packing_type")),
        shipmentId=_text(item.get("shipment_id")),
        warehouseId=_text(item.get("wid")),
        warehouseName=_text(item.get("wname")),
    )


def _eligibility(
    *,
    shipment_sn: str,
    status: int | None,
    is_deleted: int | None,
    store_ids: list[str],
    fba_shipment_ids: list[str],
    sta_shipment_ids: list[str],
    sta_task_ids: list[str],
    relation_known: bool,
) -> tuple[Eligibility, str]:
    if status is not None and status != 0:
        return "excluded", "NOT_PENDING"
    if is_deleted not in {None, 0}:
        return "excluded", "DELETED"
    if fba_shipment_ids:
        return "excluded", "HAS_SHIPMENT"
    if sta_shipment_ids or sta_task_ids:
        return "needs_review", "EXISTING_STA_RELATION"
    if not relation_known:
        return "needs_review", "SHIPMENT_RELATION_UNKNOWN"
    if re.fullmatch(r"SP[A-Za-z0-9_-]+", shipment_sn) is None or not store_ids or status != 0:
        return "needs_review", "INVALID_SOURCE_OR_STORE"
    return "candidate", "PENDING_NO_SHIPMENT_OR_STA"


def normalize_shipment_list_item(value: Any) -> ShipmentOrder:
    source = _row(value)
    relations_value = source.get("relate_list")
    unlinked_value = source.get("not_relate_list")
    relations = _rows(relations_value)
    unlinked = _rows(unlinked_value)
    all_lines = [*relations, *unlinked]
    relation_orders = _nested_rows(all_lines, "shipment_order_list")
    store_ids = sorted(
        {
            sid
            for sid in (_store_id(item.get("sid")) for item in all_lines)
            if sid
        }
    )
    fba_shipment_ids = _texts(
        [
            source.get("shipment_id"),
            *[item.get("shipment_id") for item in all_lines],
            *[item.get("shipment_mws_sn") for item in relation_orders],
        ]
    )
    sta_shipment_ids = _texts([item.get("sta_shipment_id") for item in all_lines])
    sta_task_ids = _texts([item.get("sta_inbound_plan_id") for item in all_lines])
    status = _integer(source.get("status"))
    relation_known = isinstance(relations_value, list) and isinstance(unlinked_value, list)
    eligibility, reason = _eligibility(
        shipment_sn=_text(source.get("shipment_sn")),
        status=status,
        is_deleted=_integer(source.get("is_delete")),
        store_ids=store_ids,
        fba_shipment_ids=fba_shipment_ids,
        sta_shipment_ids=sta_shipment_ids,
        sta_task_ids=sta_task_ids,
        relation_known=relation_known,
    )
    return ShipmentOrder(
        orderId=_text(source.get("id")),
        shipmentSn=_text(source.get("shipment_sn")),
        status=status,
        statusName=_text(source.get("status_name")),
        warehouseId=_text(source.get("wid")),
        warehouseName=_text(source.get("wname")),
        createdAt=_text(source.get("gmt_create") or source.get("create_time")),
        modifiedAt=_text(source.get("update_time") or source.get("gmt_modified")),
        remark=_text(source.get("remark")),
        destinationCenters=_centers(source.get("destination_fulfillment_center_id")),
        storeIds=store_ids,
        fbaShipmentIds=fba_shipment_ids,
        staShipmentIds=sta_shipment_ids,
        staTaskIds=sta_task_ids,
        skuCount=_integer(source.get("msku_count")),
        quantityTotal=_integer(source.get("quantity_total")),
        boxType=_text(source.get("box_type")),
        boxRemark=_text(source.get("box_remark")),
        items=[],
        relationKnown=relation_known,
        eligibility=eligibility,
        reason=reason,
    )


def normalize_shipment_detail(value: Any) -> ShipmentOrder:
    source = _row(value)
    items_value = source.get("items")
    items = [_normalize_item(item) for item in _rows(items_value)]
    box_rows = _rows(source.get("box_list"))
    box_items = _nested_rows(box_rows, "box_skus")
    store_ids = sorted({item.sid for item in items if item.sid})
    fba_shipment_ids = _texts(
        [
            *[item.shipmentId for item in items],
            *[item.get("shipment_id") for item in box_items],
        ]
    )
    sta_shipment_ids = _texts([source.get("sta_shipment_id")])
    sta_task_ids = _texts([source.get("sta_inbound_plan_id")])
    status = _integer(source.get("status"))
    relation_known = isinstance(items_value, list) and bool(items)
    eligibility, reason = _eligibility(
        shipment_sn=_text(source.get("shipment_sn")),
        status=status,
        is_deleted=_integer(source.get("is_delete")),
        store_ids=store_ids,
        fba_shipment_ids=fba_shipment_ids,
        sta_shipment_ids=sta_shipment_ids,
        sta_task_ids=sta_task_ids,
        relation_known=relation_known,
    )
    return ShipmentOrder(
        orderId=_text(source.get("id")),
        shipmentSn=_text(source.get("shipment_sn")),
        status=status,
        statusName=_text(source.get("status_name")),
        warehouseId=_text(source.get("wid")),
        warehouseName=_text(source.get("wname") or source.get("ware_house_bak_name")),
        createdAt=_text(source.get("gmt_create")),
        modifiedAt=_text(source.get("gmt_modified") or source.get("last_update_time")),
        remark=_text(source.get("remark")),
        destinationCenters=_centers(source.get("destination_fulfillment_center_id")),
        storeIds=store_ids,
        fbaShipmentIds=fba_shipment_ids,
        staShipmentIds=sta_shipment_ids,
        staTaskIds=sta_task_ids,
        skuCount=_integer(source.get("msku_count")),
        quantityTotal=_integer(source.get("quantity_total")),
        boxType=_text(source.get("box_type")),
        boxRemark=_text(source.get("box_remark")),
        items=items,
        relationKnown=relation_known,
        eligibility=eligibility,
        reason=reason,
    )


def shipment_in_scope(
    order: ShipmentOrder,
    allowed_sids: frozenset[str] | set[str],
    all_stores: bool = False,
) -> bool:
    if all_stores:
        return True
    return bool(order.storeIds) and all(sid in allowed_sids for sid in order.storeIds)


def summarize_shipments(orders: list[ShipmentOrder]) -> dict[str, Any]:
    candidates = [
        {"shipmentSn": order.shipmentSn, "storeIds": order.storeIds}
        for order in orders
        if order.eligibility == "candidate"
    ]
    excluded = [
        {"shipmentSn": order.shipmentSn, "reason": order.reason}
        for order in orders
        if order.eligibility == "excluded"
    ]
    needs_review = [
        {"shipmentSn": order.shipmentSn, "reason": order.reason}
        for order in orders
        if order.eligibility == "needs_review"
    ]
    return {
        "candidateShipmentSns": list(
            dict.fromkeys(item["shipmentSn"] for item in candidates if item["shipmentSn"])
        ),
        "candidateCount": len(candidates),
        "excluded": excluded,
        "needsReview": needs_review,
        "note": (
            "候选仅表示待发货且未发现已有货件或STA关系；创建货件前仍需再次核验实时状态。"
        ),
    }
