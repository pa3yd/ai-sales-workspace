# -*- coding: utf-8 -*-
"""ROUND 6.1 TEST05: recommendation intent is internal work, not customer blocker."""
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
import email_unified as email  # noqa: E402

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


def test_test05_recommendation_intent_internal_action():
    old_db = db.DB_PATH
    with tempfile.TemporaryDirectory() as td:
        db.DB_PATH = os.path.join(td, "test05.db")
        try:
            db.init_db()
            extractor, matcher, client = main.build_engine("rule")
            report = main.analyze(TEST05, extractor, matcher, client)
            db.save_inquiry(TEST05, report)

            info = report["extracted"]
            assert info["company"] == "Alpine Outdoor GmbH"
            assert info["contact_name"] == "Anna Keller"
            assert info["product_query"] == "Insulated Food Container"
            assert info["material"] == "Stainless Steel"
            assert info["capacity"] == "750ml"
            assert info["quantity"] == 8000
            assert info["target_price"] == 5.5
            assert info["incoterm"] == "FOB"

            opps = db.list_opportunities()
            assert len(opps) == 1
            assert opps[0]["product"] == "Insulated Food Container"
            assert opps[0]["details"]["product_match_status"] in {"NO_MATCH", "UNRESOLVED"}

            stage_of = {o["inquiry_id"]: o["stage"] for o in opps}
            group = ui.group_deal_threads(_queue_items(), stage_of)[0]
            work = ui.deal_work_item(group, opp_by_inq={o["inquiry_id"]: o for o in opps}, tasks_by_opp={})
            state = work["resolvedState"]

            assert state["customerProductRequirement"] == "Insulated Food Container"
            assert state["productMatchStatus"] in {"NO_MATCH", "UNRESOLVED"}
            assert state["customerBlockingItems"] == []
            assert "REQUEST_PRODUCT_RECOMMENDATION" in state["customerRequestedOutcome"]
            assert "REQUEST_QUOTATION" in state["customerRequestedOutcome"]
            assert state["actionState"] == "WAITING_INTERNAL"
            assert state["lidOptionStatus"] == "RECOMMENDATION_REQUESTED"
            assert state["nextBestAction"]["type"] in {
                "CHECK_PRODUCT_OPTIONS", "PREPARE_PRODUCT_RECOMMENDATION", "PREPARE_QUOTATION"
            }
            keys = {x["key"] for x in state["internalPrerequisites"]}
            assert {"CHECK_PRODUCT_OPTIONS", "CHECK_LID_OPTIONS", "CHECK_COST",
                    "CHECK_SAMPLE_AVAILABILITY", "CHECK_SAMPLE_COST", "CHECK_LEAD_TIME",
                    "CHECK_CUSTOMIZATION_COST", "CHECK_COMPLIANCE_DOCUMENTS"} <= keys

            mail = email.generate_customer_email({
                "text": TEST05, "info": info, "matches": report["matches"],
                "gaps": report["gaps"], "stored_draft": report.get("draft") or "",
                "seller_company": "", "customer_product": info.get("product_query") or "",
                "quotation_ready": False, "known": [k for k, v in info.items() if v],
                "resolved_state": state,
            }, intent="AUTO")
            body = mail["body"].lower()
            assert mail["question_count"] == 0
            assert "insulated food container" in body
            assert "8,000 pcs" in body
            assert "usd 5.5" in body
            assert "standard screw lid" in body
            assert "integrated spoon" in body
            assert "recommendation and quotation" in body
            forbidden = [
                "could you confirm which lid", "which lid do you prefer",
                "could you share a reference model", "could you share a photo",
                "product was not specified", "product you are looking for",
            ]
            assert not any(x in body for x in forbidden), body
        finally:
            db.DB_PATH = old_db


if __name__ == "__main__":
    test_test05_recommendation_intent_internal_action()
    print("round 6.1 test05 recommendation intent passed")
