# -*- coding: utf-8 -*-
"""ROUND 6.3: TEST05 semantics and sidebar deal-thread aggregation."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workbench"))

import main  # noqa: E402
import db  # noqa: E402
import queue_ui as ui  # noqa: E402
import workflow as wf  # noqa: E402


TEST05 = """
Dear Sales Team,

This is Anna Keller from Alpine Outdoor GmbH in Germany.

We are developing a new outdoor lunch set and would like to request your quotation and product recommendation.

Product: Insulated Food Container
Material: Stainless Steel
Capacity: 750ml
Quantity: approximately 8,000 pcs
Colors: Dark Green and Black
Customization: logo printing and retail packaging
Target price: USD 5.50 / pc
Incoterm: FOB
Destination: Hamburg, Germany
Lead time: within 30 days
Samples: 3 pcs

For the lid, we are currently considering either a standard screw lid or a lid with an integrated spoon, but we have not decided which option would be better for our market.

Could you please recommend the suitable lid option and send us your quotation?

Best regards,
Anna Keller
Alpine Outdoor GmbH
"""


def _queue_items():
    items = []
    for row in db.list_inquiries():
        (id_, created, grade, score, country, company, contact, stt, cg,
         _clg, urgency) = row[:11]
        need = row[11] if len(row) > 11 else {}
        cust_id = row[12] if len(row) > 12 else None
        wfd = row[13] if len(row) > 13 else {}
        pri, pts = db.calc_priority(cg, grade, score, urgency, stt)
        item = dict(id=id_, created=created, grade=grade, score=score,
                    country=country, company=company, contact=contact,
                    status=stt, cust_grade=cg, pri=pri, pts=pts,
                    urgency=urgency, need=need or {}, cust_id=cust_id, wf=wfd)
        item["biz"] = wf.derive_biz(stt, (need or {}).get("blockers") or [],
                                     (need or {}).get("readiness") or "",
                                     wfd.get("biz_status"),
                                     bool((need or {}).get("has_draft")))
        item["action"] = wf.next_action(
            stt, item["biz"], (need or {}).get("blockers") or [],
            (need or {}).get("readiness") or "",
            has_draft=bool((need or {}).get("has_draft")),
            follow_up_at=wfd.get("follow_up_at"),
            follow_up_done=bool(wfd.get("follow_up_done")),
            intent=(need or {}).get("intent") or "")
        items.append(item)
    return items


def test_test05_product_and_lid_are_not_customer_blockers():
    old_db = db.DB_PATH
    with tempfile.TemporaryDirectory() as td:
        db.DB_PATH = os.path.join(td, "round63.db")
        try:
            db.init_db()
            extractor, matcher, client = main.build_engine("rule")
            report = main.analyze(TEST05, extractor, matcher, client)
            db.save_inquiry(TEST05, report)

            opp = db.list_opportunities()[0]
            assert opp["product"] == "Insulated Food Container"

            groups = ui.group_deal_threads(
                _queue_items(), {opp["inquiry_id"]: opp["stage"]})
            work = ui.deal_work_item(
                groups[0],
                opp_by_inq={opp["inquiry_id"]: opp},
                tasks_by_opp={})
            state = work["resolvedState"]

            assert state["customerProductRequirement"] == "Insulated Food Container"
            assert state["productRequirementStatus"] == "KNOWN"
            assert state["productMatchStatus"] in {"NO_MATCH", "UNRESOLVED"}
            assert state["customerBlockingItems"] == []
            assert state["lidOptionStatus"] == "RECOMMENDATION_REQUESTED"
            assert state["nextBestAction"]["type"] in {
                "CHECK_PRODUCT_OPTIONS",
                "CHECK_SUPPLIER_CAPABILITY",
                "PREPARE_PRODUCT_RECOMMENDATION",
                "PREPARE_QUOTATION",
            }
        finally:
            db.DB_PATH = old_db


def test_duplicate_alpine_inquiries_group_into_one_deal_thread():
    old_db = db.DB_PATH
    with tempfile.TemporaryDirectory() as td:
        db.DB_PATH = os.path.join(td, "round63_dup.db")
        try:
            db.init_db()
            extractor, matcher, client = main.build_engine("rule")
            report1 = main.analyze(TEST05, extractor, matcher, client)
            report2 = main.analyze(TEST05, extractor, matcher, client)
            db.save_inquiry(TEST05, report1)
            db.save_inquiry(TEST05, report2)

            opps = db.list_opportunities()
            stage_of = {o["inquiry_id"]: o["stage"] for o in opps}
            groups = ui.group_deal_threads(_queue_items(), stage_of)
            alpine = [g for g in groups if "Alpine Outdoor" in g["lead"].get("company", "")]

            assert len(alpine) == 1
            assert alpine[0]["count"] == 2
        finally:
            db.DB_PATH = old_db


def test_duplicate_company_with_different_customer_ids_still_one_deal_thread():
    rows = []
    for idx, cust_id in enumerate([101, 202, 303], 1):
        rows.append({
            "id": idx,
            "created": f"2026-09-11 0{idx}:00",
            "company": "Alpine Outdoor GmbH",
            "contact": "Anna Keller",
            "country": "Germany",
            "status": "待处理",
            "cust_id": cust_id,
            "biz": "READY_TO_REPLY",
            "pri": "正常处理",
            "pts": 50,
            "need": {
                "intent": "OEM/贴牌定制",
                "qty": "约 8,000 pcs",
            },
            "wf": {},
            "action": {"type": "SEND_REPLY", "label": "查看并发送回复"},
        })

    groups = ui.group_deal_threads(rows, {})

    assert len(groups) == 1
    assert groups[0]["count"] == 3
    assert groups[0]["key"][1] == ("co", "alpine outdoor gmbh")


if __name__ == "__main__":
    test_test05_product_and_lid_are_not_customer_blockers()
    test_duplicate_alpine_inquiries_group_into_one_deal_thread()
    test_duplicate_company_with_different_customer_ids_still_one_deal_thread()
    print("round 6.3 sales ui simplification tests passed")
