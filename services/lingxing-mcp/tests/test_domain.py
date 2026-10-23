from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from lingxing_mcp.dates import current_month_range, validate_range
from lingxing_mcp.errors import ServiceError
from lingxing_mcp.packing import in_scope, normalize_task, read_page, summarize_tasks
from lingxing_mcp.signature import canonical_parameters, sign_parameters


def packing_row(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "taskSn": "PL261023001",
        "ptId": "250692382241271800",
        "relateSn": "SP261023001",
        "sid": 54,
        "taskStatus": 3,
        "relateSnType": 2,
        "relateShipment": [],
        "itemList": [{"sid": 54, "msku": "SKU-A", "shipmentId": ""}],
        "gmtCreate": "2026-10-23 09:00:00",
    }
    value.update(overrides)
    return value


def test_current_month_uses_business_timezone_and_includes_today() -> None:
    assert current_month_range(
        "Asia/Shanghai",
        datetime(2026, 10, 22, 16, 30, tzinfo=UTC),
    ) == {
        "timezone": "Asia/Shanghai",
        "resolvedAt": "2026-10-22T16:30:00.000Z",
        "today": "2026-10-23",
        "startDate": "2026-10-01",
        "endDate": "2026-10-23",
        "boundary": "inclusive",
    }
    assert (
        current_month_range(
            "Asia/Shanghai",
            datetime(2026, 10, 31, 16, tzinfo=UTC),
        )["startDate"]
        == "2026-11-01"
    )
    assert (
        current_month_range(
            "Asia/Shanghai",
            datetime(2026, 12, 31, 16, tzinfo=UTC),
        )["endDate"]
        == "2027-01-01"
    )
    assert (
        current_month_range(
            "America/Los_Angeles",
            datetime(2026, 11, 1, 1, tzinfo=UTC),
        )["endDate"]
        == "2026-10-31"
    )
    with pytest.raises(ServiceError):
        validate_range("2026-02-29", "2026-03-01")
    with pytest.raises(ServiceError):
        validate_range("2026-10-23", "2026-10-01")
    validate_range("2028-02-01", "2028-02-29")


def test_signature_matches_existing_canonical_and_aes_rules() -> None:
    params = {
        "z": "",
        "c": {"value": "中 文"},
        "b": ["2", "3"],
        "n": None,
        "a": 0,
    }
    canonical = 'a=0&b=["2","3"]&c={"value":"中 文"}&n=null'
    assert canonical_parameters(params) == canonical

    key = "0123456789abcdef"
    encrypted = sign_parameters(params, key)
    decryptor = Cipher(algorithms.AES(key.encode()), modes.ECB()).decryptor()
    padded = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    recovered = unpadder.update(padded) + unpadder.finalize()
    assert (
        recovered.decode()
        == hashlib.md5(canonical.encode(), usedforsecurity=False).hexdigest().upper()
    )

    with pytest.raises(ServiceError):
        sign_parameters(params, "invalid-key")


def test_python_json_preserves_unsafe_integer_id_digits() -> None:
    parsed = json.loads('{"code":0,"data":{"ptId":250692382241271800,"sid":54}}')
    assert str(parsed["data"]["ptId"]) == "250692382241271800"
    assert parsed["data"]["sid"] == 54


def test_status_source_type_and_shipment_fields_drive_filtering() -> None:
    assert normalize_task(packing_row()).eligibility == "candidate"
    assert normalize_task(packing_row(taskStatus=2, packingCon=3)).reason == "NOT_COMPLETED"
    assert normalize_task(packing_row(relateSnType=1)).reason == "NOT_SP_SOURCE"
    assert (
        normalize_task(packing_row(relateShipment=[{"shipmentId": " FBA123 "}])).reason
        == "HAS_SHIPMENT"
    )
    assert (
        normalize_task(packing_row(itemList=[{"sid": 54, "shipmentId": "FBA456"}])).reason
        == "HAS_SHIPMENT"
    )
    assert normalize_task(packing_row(relateShipment=None)).eligibility == "needs_review"
    missing = packing_row()
    del missing["relateShipment"]
    assert normalize_task(missing).eligibility == "needs_review"
    assert normalize_task(packing_row(relateShipment=[{}])).eligibility == "needs_review"
    assert (
        normalize_task(packing_row(relateShipment=[{"shipmentId": 0}])).eligibility
        == "needs_review"
    )
    assert (
        normalize_task(packing_row(relateShipment=[{"shipmentId": "  "}])).eligibility
        == "candidate"
    )


def test_existing_sta_remains_visible_without_granting_creation_permission() -> None:
    task = normalize_task(
        packing_row(relateShipment=[{"shipmentId": "", "staInboundPlanId": "wf-existing"}])
    )
    assert task.reason == "NO_SHIPMENT_EXISTING_STA"
    assert summarize_tasks([task])["candidates"][0]["packingTasks"][0]["staIds"] == ["wf-existing"]


def test_detail_resolves_unknown_associations_and_scope_requires_all_stores() -> None:
    detail = normalize_task(
        {
            "taskInfo": packing_row(sid=0, itemList=None),
            "productItems": [{"sid": 54, "shipmentId": ""}],
        }
    )
    assert detail.sid == "54"
    assert detail.eligibility == "candidate"

    mixed = normalize_task(packing_row(itemList=[{"sid": 99}]))
    assert in_scope(mixed, {"54"}) is False
    assert in_scope(mixed, {"54", "99"}) is True
    assert mixed.eligibility == "needs_review"


def test_pagination_accepts_documented_totals_and_rejects_inconsistency() -> None:
    _, first = read_page(
        {"code": 0, "data": {"list": [packing_row(), packing_row()], "total": 3}},
        0,
        2,
    )
    assert first["nextOffset"] == 2
    _, last = read_page({"code": 0, "data": {"list": [packing_row()]}, "total": 3}, 2, 2)
    assert last["hasMore"] is False
    assert read_page({"data": {"list": []}, "total": 0}, 0, 100)[1]["hasMore"] is False
    assert read_page({"data": {"list": [packing_row()]}}, 0, 1)[1]["hasMore"] is True
    with pytest.raises(ServiceError):
        read_page({"data": {"items": []}}, 0, 100)
    with pytest.raises(ServiceError):
        read_page({"data": {"list": [], "total": 10}}, 0, 100)
    with pytest.raises(ServiceError):
        read_page({"data": {"list": [], "total": 0}, "total": 10}, 0, 100)


def test_grouping_preserves_pls_store_boundaries_and_conflicts() -> None:
    one = normalize_task(packing_row())
    two = normalize_task(packing_row(taskSn="PL261023002"))
    another_store = normalize_task(
        packing_row(sid=99, itemList=[{"sid": 99}], taskSn="PL261023003")
    )
    summary = summarize_tasks([one, one, two, another_store])
    assert summary["candidateSpCount"] == 2
    assert summary["candidateTaskCount"] == 3
    assert summary["relatedSpNumbers"] == ["SP261023001"]
    store_54 = next(item for item in summary["candidates"] if item["sid"] == "54")
    assert len(store_54["packingTasks"]) == 2

    conflicting = one.model_copy(
        update={
            "shipmentIds": ["FBA-now"],
            "eligibility": "excluded",
            "reason": "HAS_SHIPMENT",
        }
    )
    conflict_result = summarize_tasks([one, conflicting])
    assert conflict_result["candidateSpCount"] == 0
    assert conflict_result["needsReview"][0]["reason"] == "CONFLICTING_SNAPSHOTS"
