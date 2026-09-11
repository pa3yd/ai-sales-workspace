# -*- coding: utf-8 -*-
"""ROUND 5 · Re-inquiry and resolved requirement consistency tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "workbench"))

import queue_ui as ui  # noqa: E402


def _item(iid, created, qty, product="Wireless ANC Earbuds", biz="READY_TO_REPLY"):
    return {
        "id": iid,
        "created": created,
        "company": "NordHaus Electronics GmbH",
        "contact": "Michael Weber",
        "cust_id": 1,
        "status": "待处理",
        "biz": biz,
        "need": {
            "product_query": product,
            "qty": qty,
            "readiness": "PRELIMINARY_QUOTE_READY",
            "blockers": [],
        },
        "action": {"type": "REPLY", "label": "查看并发送回复"},
        "pts": 80,
    }


def test_product_signature_keeps_close_product_types_apart():
    assert ui.product_signature("Stainless Steel Water Bottles") != ui.product_signature(
        "Stainless Steel Water Mugs"
    )
    assert ui.product_signature(
        "Wireless ANC Earbuds with Bluetooth 5.4, 40 hours battery"
    ) == ui.product_signature(
        "Wireless ANC Earbuds with Bluetooth 5.4, 40h battery"
    )


def test_resolved_requirement_uses_latest_quantity_and_keeps_history():
    group = {
        "key": ("deal", ("cid", 1), "wireless anc earbuds", "open"),
        "items": [
            _item(1, "2026-09-09 10:00", "约 5,000 pcs"),
            _item(2, "2026-09-10 10:00", "约 3,000 pcs"),
        ],
        "lead": _item(1, "2026-09-09 10:00", "约 5,000 pcs"),
        "count": 2,
    }
    req = ui.resolved_requirement(group)
    assert req["quantity"] == "约 3,000 pcs"
    assert req["quantityHistory"] == ["约 5,000 pcs", "约 3,000 pcs"]
    assert req["quantityChanged"] is True
    assert "quantity" in req["changedFields"]


def test_quantity_change_changes_next_action_to_confirm_or_requote():
    group = {
        "key": ("deal", ("cid", 1), "wireless anc earbuds", "open"),
        "items": [
            _item(1, "2026-09-09 10:00", "约 5,000 pcs"),
            _item(2, "2026-09-10 10:00", "约 3,000 pcs"),
        ],
        "lead": _item(2, "2026-09-10 10:00", "约 3,000 pcs"),
        "count": 2,
    }
    opp = {
        "id": -9999,
        "stage": "QUOTED",
        "product": "Wireless ANC Earbuds",
        "details": {
            "product_match_status": "MATCHED",
            "supplier_capability_status": "CAPABLE",
            "quotation_readiness": "DRAFT_CREATED",
        },
    }
    w = ui.deal_work_item(group, opp=opp, opp_tasks=[])
    state = w["resolvedState"]

    assert w["quantity"] == "约 3,000 pcs"
    assert state["resolvedRequirement"]["quantityChanged"] is True
    assert state["amountStale"] is False
    assert state["needsRequote"] is True
    assert state["nextBestAction"]["type"] in (
        "UPDATE_QUOTATION",
        "PREPARE_UPDATED_QUOTATION",
        "CHECK_INTERNAL_QUOTATION_PREREQUISITES",
    )
    assert state["primaryCta"] in ("更新报价", "创建更新报价", "检查报价条件")


if __name__ == "__main__":
    test_product_signature_keeps_close_product_types_apart()
    test_resolved_requirement_uses_latest_quantity_and_keeps_history()
    test_quantity_change_changes_next_action_to_confirm_or_requote()
    print("round5 re-inquiry consistency tests passed")
