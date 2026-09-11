# -*- coding: utf-8 -*-
"""ROUND 5.2 · Test04 actual runtime decision pipeline.

This test follows the real application path as closely as possible without
opening the Streamlit UI:
main.analyze -> db.save_inquiry -> db.list_inquiries/list_opportunities ->
queue_ui.group_deal_threads/deal_work_item -> email_unified.generate_customer_email
"""

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
import email_unified as email  # noqa: E402
import workflow as wf  # noqa: E402


OLD_TEXT = """
Dear team,

We are looking to place an order for 5,000 pcs of Wireless ANC Earbuds.
Please send quotation for Germany.

Best regards,
Michael Weber
NordHaus Electronics GmbH
"""


LATEST_TEXT = """
Dear team,

Thank you for the previous quotation for 5,000 pcs.
Please send us your updated quotation as soon as possible.

We would like to revise the first order quantity to 3,000 pcs instead of 5,000 pcs.
The product is Wireless ANC Earbuds with Bluetooth 5.4, Active Noise Cancellation,
minimum 40 hours battery life including charging case, Black and White colors,
custom logo on earbuds and charging case, and custom retail packaging.
Destination is Hamburg, Germany. Trade term: FOB. Our target FOB price is USD 8.80/set.
Please also include 2 samples and CE / RoHS documentation for the proposed model.

Best regards,
Michael Weber
NordHaus Electronics GmbH
"""


def test_test04_runtime_pipeline_uses_resolved_state():
    old_db = db.DB_PATH
    with tempfile.TemporaryDirectory() as td:
        db.DB_PATH = os.path.join(td, "workbench.db")
        try:
            db.init_db()
            extractor, matcher, client = main.build_engine("rule")
            old_report = main.analyze(OLD_TEXT, extractor, matcher, client)
            old_id = db.save_inquiry(OLD_TEXT, old_report)
            latest_report = main.analyze(LATEST_TEXT, extractor, matcher, client)
            latest_id = db.save_inquiry(LATEST_TEXT, latest_report)

            rows = _load_queue_like_app()
            opps = db.list_opportunities()
            opp_by_inq = {o["inquiry_id"]: o for o in opps}
            stage_of = {old_id: "QUOTED", latest_id: "QUOTED"}
            groups = ui.group_deal_threads(rows, stage_of)
            group = next(
                g for g in groups
                if any(x.get("id") == latest_id for x in g.get("items") or [])
            )
            work_item = ui.deal_work_item(group, opp_by_inq=opp_by_inq,
                                          tasks_by_opp={})
            state = work_item["resolvedState"]
            req = state["resolvedRequirement"]

            assert work_item["company"] == "NordHaus Electronics GmbH"
            assert work_item["product"] == "Wireless ANC Earbuds"
            assert work_item["quantity"] == "约 3,000 pcs"
            assert req["currentQuantity"] == 3000
            assert req["previousQuantity"] == 5000
            assert req["quantityEvolution"] == "REVISION"
            assert req["quantityConflict"] is False
            assert req["quantityHistory"] == ["约 5,000 pcs", "约 3,000 pcs"]
            assert state["requirementCompleteness"] in {"HIGH", "COMPLETE"}
            assert state["customerBlockingItems"] == []
            assert state["customerRequestedOutcome"] == "REQUEST_UPDATED_QUOTATION"
            assert state["nextBestAction"]["type"] in {
                "CHECK_INTERNAL_QUOTATION_PREREQUISITES",
                "PREPARE_UPDATED_QUOTATION",
            }

            ctx = {
                "text": LATEST_TEXT,
                "info": latest_report["extracted"],
                "matches": latest_report["matches"],
                "gaps": latest_report["gaps"],
                "stored_draft": latest_report.get("draft") or "",
                "seller_company": "",
                "biz": "READY_TO_REPLY",
                "quotation_ready": False,
                "customer_product": "Wireless ANC Earbuds",
                "known": ["email", "company"],
                "resolved_state": state,
            }
            mail = email.generate_customer_email(ctx, intent="AUTO")
            body = mail["body"].lower()

            assert mail["question_count"] == 0
            assert "3,000 and 5,000" not in body
            assert "confirm which quantity" not in body
            assert "reference model" not in body
            assert "photo" not in body
            assert "not currently represented in our standard catalog" not in body
            assert "available ce and rohs documentation" not in body
            assert "samples are available" not in body
        finally:
            db.DB_PATH = old_db


def _load_queue_like_app():
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
        item["fu_state"] = wf.followup_state(wfd.get("follow_up_at"),
                                             wfd.get("follow_up_done"))
        items.append(item)
    items.sort(key=lambda x: (-x["pts"], -x["id"]))
    return items


if __name__ == "__main__":
    test_test04_runtime_pipeline_uses_resolved_state()
    print("test04 runtime pipeline tests passed")
