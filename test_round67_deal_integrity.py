# -*- coding: utf-8 -*-
"""ROUND 6.7 regression: Deal identity and resolved state integrity."""
import os
import tempfile
import importlib

from workbench import db, queue_ui
from workbench.email_unified import customer_requested_outcome


def _report(company, contact, product, qty, text=""):
    return {
        "score": 61,
        "intent": "RFQ",
        "extracted": {
            "company": company,
            "contact_name": contact,
            "country": "Germany" if company.startswith("NordHaus") else "Netherlands",
            "product_query": product,
            "quantity": qty,
            "source": "TEST",
            "raw_text": text or f"We need {qty} pcs {product}",
        },
        "matches": [],
        "insight": {
            "product_match": {
                "product_match_status": "NO_MATCH",
                "supplier_capability_status": "UNKNOWN",
                "opportunity_type": "NEW_PRODUCT",
            },
            "requirement_completeness": {"level": "HIGH"},
        },
    }


def test_nordhaus_revision_stays_one_deal():
    old_path = db.DB_PATH
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db.DB_PATH = path
        db.init_db()
        first = db.save_inquiry("First RFQ 5,000 pcs", _report(
            "NordHaus Electronics GmbH", "Michael Weber", "Wireless ANC Earbuds", "约 5,000 pcs"))
        second = db.save_inquiry("Revision 3,000 pcs", _report(
            "NordHaus Electronics GmbH", "Michael Weber", "Wireless ANC Earbuds", "约 3,000 pcs"))
        db.reconcile_duplicate_opportunities()
        opps = db.list_opportunities()
        assert len(opps) == 1, opps
        opp = opps[0]
        details = opp["details"]
        assert opp["id"] == db.create_opportunity_from_inquiry(
            opp["customer_id"], second, _report("NordHaus Electronics GmbH", "Michael Weber", "Wireless ANC Earbuds", "约 3,000 pcs"))
        assert details["quantity"] == "约 3,000 pcs"
        assert "约 5,000 pcs" in details["quantity_history"]
        assert "约 3,000 pcs" in details["quantity_history"]
        assert set(details["related_inquiry_ids"]) >= {first, second}
    finally:
        db.DB_PATH = old_path
        try:
            os.remove(path)
        except OSError:
            pass


def test_test05_product_and_lid_are_separate_dimensions():
    raw = "We need an insulated food container and would like your recommendation between screw lid or a lid with an integrated spoon."
    item = {
        "id": 1,
        "status": "待处理",
        "company": "Alpine Outdoor GmbH",
        "contact": "Anna Keller",
        "country": "Netherlands",
        "created": "2026-09-09 10:00",
        "pts": 1,
        "need": {
            "product_query": "Insulated Food Container",
            "qty": "约 8,000 pcs",
            "raw_text": raw,
            "customer_requested_outcome": customer_requested_outcome({"info": {"raw_text": raw}}),
            "blockers": ["product", "lid"],
        },
        "action": {"type": "SEND_REPLY", "label": "查看并发送回复"},
        "biz": "READY_TO_REPLY",
    }
    opp = {
        "id": 10,
        "inquiry_id": 1,
        "stage": "NEW",
        "product": "Insulated Food Container",
        "details": {
            "product_query": "Insulated Food Container",
            "product_match_status": "NO_MATCH",
            "supplier_capability_status": "UNKNOWN",
            "requirement_completeness": "HIGH",
        },
    }
    w = queue_ui.deal_work_item({"lead": item, "items": [item], "count": 1}, opp=opp)
    resolved = w["resolvedState"]
    assert resolved["customerProductRequirement"] == "Insulated Food Container"
    assert resolved["productMatchStatus"] in {"NO_MATCH", "UNRESOLVED"}
    assert resolved["lidOptionStatus"] == "RECOMMENDATION_REQUESTED"
    blockers = {str(x.get("key") if isinstance(x, dict) else x).lower()
                for x in resolved.get("customerBlockingItems") or []}
    assert "product" not in blockers
    assert "lid" not in blockers
    action = (resolved.get("nextBestAction") or {}).get("type")
    assert action in {"CHECK_SUPPLIER_CAPABILITY", "CHECK_PRODUCT_OPTIONS", "MATCH_PRODUCT"}


if __name__ == "__main__":
    test_nordhaus_revision_stays_one_deal()
    test_test05_product_and_lid_are_separate_dimensions()
    print("ROUND 6.7 regression PASS")
