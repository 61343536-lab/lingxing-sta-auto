from __future__ import annotations

from typing import Any

from lingxing_mcp.shipment import (
    normalize_shipment_detail,
    normalize_shipment_list_item,
    shipment_in_scope,
    summarize_shipments,
)


def shipment_list_row(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": 250692382241271800,
        "shipment_sn": "SP261023001",
        "status": 0,
        "status_name": "待发货",
        "wid": 8,
        "wname": "华南二仓",
        "gmt_create": "2026-10-20 09:00:00",
        "update_time": "2026-10-23 10:00:00",
        "is_delete": 0,
        "destination_fulfillment_center_id": "ONT8,LGB8",
        "relate_list": [
            {
                "sid": 54,
                "shipment_id": "",
                "sta_shipment_id": "",
                "sta_inbound_plan_id": "",
                "shipment_order_list": [],
            }
        ],
        "not_relate_list": [],
    }
    value.update(overrides)
    return value


def shipment_detail(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": 250692382241271800,
        "shipment_sn": "SP261023001",
        "status": 0,
        "status_name": "待发货",
        "wid": 8,
        "wname": "华南二仓",
        "gmt_create": "2026-10-20 09:00:00",
        "gmt_modified": "2026-10-23 10:00:00",
        "is_delete": 0,
        "msku_count": 2,
        "quantity_total": 30,
        "destination_fulfillment_center_id": "ONT8",
        "items": [
            {
                "id": 101,
                "product_id": 201,
                "sid": 54,
                "sname": "US Store",
                "nation": "美国",
                "sku": "SKU-A",
                "msku": "MSKU-A",
                "fnsku": "FNSKU-A",
                "fulfillment_network_sku": "FNSKU-A",
                "product_name": "Product A",
                "pic_url": "https://example.test/a.jpg",
                "num": 10,
                "shipment_plan_quantity": 10,
                "apply_num": 0,
                "shipment_id": "",
                "wid": 8,
                "wname": "华南二仓",
            },
            {
                "id": 102,
                "product_id": 202,
                "sid": 54,
                "sname": "US Store",
                "nation": "美国",
                "sku": "SKU-B",
                "msku": "MSKU-B",
                "fnsku": "FNSKU-B",
                "num": 20,
                "shipment_id": "",
                "wid": 8,
                "wname": "华南二仓",
            },
        ],
        "box_list": [],
    }
    value.update(overrides)
    return value


def test_pending_list_normalization_classifies_existing_relations() -> None:
    candidate = normalize_shipment_list_item(shipment_list_row())
    assert candidate.orderId == "250692382241271800"
    assert candidate.destinationCenters == ["LGB8", "ONT8"]
    assert candidate.eligibility == "candidate"
    assert candidate.reason == "PENDING_NO_SHIPMENT_OR_STA"

    unlinked = normalize_shipment_list_item(
        shipment_list_row(
            relate_list=[],
            not_relate_list=[
                {
                    "sid": 54,
                    "shipment_id": "",
                    "sta_shipment_id": "",
                    "sta_inbound_plan_id": "",
                }
            ],
        )
    )
    assert unlinked.storeIds == ["54"]
    assert unlinked.eligibility == "candidate"

    with_fba = normalize_shipment_list_item(
        shipment_list_row(
            relate_list=[
                {
                    "sid": 54,
                    "shipment_id": "FBA123",
                    "sta_shipment_id": "",
                    "sta_inbound_plan_id": "",
                }
            ]
        )
    )
    assert with_fba.fbaShipmentIds == ["FBA123"]
    assert with_fba.reason == "HAS_SHIPMENT"

    with_sta = normalize_shipment_list_item(
        shipment_list_row(
            relate_list=[
                {
                    "sid": 54,
                    "shipment_id": "",
                    "sta_shipment_id": "sh-existing",
                    "sta_inbound_plan_id": "wf-existing",
                }
            ]
        )
    )
    assert with_sta.eligibility == "needs_review"
    assert with_sta.reason == "EXISTING_STA_RELATION"

    missing_relations = shipment_list_row()
    del missing_relations["relate_list"]
    assert (
        normalize_shipment_list_item(missing_relations).reason
        == "SHIPMENT_RELATION_UNKNOWN"
    )


def test_detail_normalization_keeps_all_order_items_and_scope() -> None:
    order = normalize_shipment_detail(shipment_detail())
    assert order.eligibility == "candidate"
    assert order.storeIds == ["54"]
    assert len(order.items) == 2
    assert order.items[0].sku == "SKU-A"
    assert order.items[1].quantity == 20
    assert shipment_in_scope(order, {"54"}) is True
    assert shipment_in_scope(order, {"99"}) is False
    assert shipment_in_scope(order, set(), all_stores=True) is True

    changed = shipment_detail()
    changed["items"][0]["shipment_id"] = "FBA-NOW"
    with_fba = normalize_shipment_detail(changed)
    assert with_fba.reason == "HAS_SHIPMENT"
    summary = summarize_shipments([order, with_fba])
    assert summary["candidateShipmentSns"] == ["SP261023001"]
    assert summary["candidateCount"] == 1
    assert summary["excluded"] == [
        {"shipmentSn": "SP261023001", "reason": "HAS_SHIPMENT"}
    ]


def test_detail_without_items_requires_review() -> None:
    missing = shipment_detail()
    del missing["items"]
    order = normalize_shipment_detail(missing)
    assert order.eligibility == "needs_review"
    assert order.reason == "SHIPMENT_RELATION_UNKNOWN"
