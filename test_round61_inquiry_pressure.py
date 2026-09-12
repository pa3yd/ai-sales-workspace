# -*- coding: utf-8 -*-
"""Post ROUND 6.1 pressure test: inquiry -> CRM -> sidebar -> pipeline consistency."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workbench"))

import main  # noqa: E402
import db  # noqa: E402
import pipeline_ui  # noqa: E402
import queue_ui as ui  # noqa: E402
import workflow as wf  # noqa: E402

NORD_OLD = """
Dear team,
We are looking to place an order for 5,000 pcs of Wireless ANC Earbuds.
Please send quotation for Germany.
Best regards,
Michael Weber
NordHaus Electronics GmbH
"""

NORD_REVISION = """
Dear team,
Please send us your updated quotation.
We would like to revise the first order quantity to 3,000 pcs instead of 5,000 pcs.
The product is Wireless ANC Earbuds with Bluetooth 5.4, ANC, minimum 40 hours battery life,
custom logo and custom retail packaging. Destination Hamburg, Germany. Trade term FOB.
Target FOB price USD 8.80/set. Please include CE / RoHS documentation.
Michael Weber
NordHaus Electronics GmbH
"""

NORD_NEW_PRODUCT = """
Dear team,
We need 1,000 pcs USB-C Travel Charger for Germany. Please quote with CE certificate.
Michael Weber
NordHaus Electronics GmbH
"""

BRIGHTPROMO = """
Hello,
This is Sophie from BrightPromo BV in Netherlands.
We need Stainless Steel Water Bottles, 10,000 pcs, with logo printing.
Please send your quotation and confirm if you can supply. Target price is USD 3.20.
Regards,
Sophie
BrightPromo BV
"""


def _queue_rows():
    rows = []
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
        rows.append(item)
    return rows


def test_round61_full_inquiry_pressure_consistency():
    old_db = db.DB_PATH
    with tempfile.TemporaryDirectory() as td:
        db.DB_PATH = os.path.join(td, "pressure.db")
        try:
            db.init_db()
            extractor, matcher, client = main.build_engine("rule")
            reports = []
            for text in [NORD_OLD, NORD_REVISION, NORD_NEW_PRODUCT, BRIGHTPROMO]:
                report = main.analyze(text, extractor, matcher, client)
                reports.append(report)
                db.save_inquiry(text, report)

            latest = reports[1]["extracted"]
            assert latest["product_query"] == "Wireless ANC Earbuds"
            assert latest["quantity_revision"]["current"] == 3000
            assert latest["quantity_revision"]["previous"] == 5000
            assert latest["quantity_revision"]["conflict"] is False

            travel = reports[2]["extracted"]
            assert travel["product_query"] == "USB-C Travel Charger"

            bright = reports[3]
            bright_info = bright["extracted"]
            bright_pm = (bright.get("insight") or {}).get("product_match") or {}
            assert bright_info["product_query"] == "Stainless Steel Water Bottles"
            assert bright_pm["customer_product_state"] == "CUSTOMER_PRODUCT_KNOWN"
            assert bright_pm["product_match_status"] in {"NO_MATCH", "UNRESOLVED"}
            assert bright_pm["supplier_capability_status"] in {"UNKNOWN", "NEEDS_CHECK"}

            opps = db.list_opportunities()
            by_product = {(o.get("company"), o.get("product")): o for o in opps}
            assert ("NordHaus Electronics GmbH", "Wireless ANC Earbuds") in by_product
            assert ("NordHaus Electronics GmbH", "USB-C Travel Charger") in by_product
            assert ("BrightPromo BV", "Stainless Steel Water Bottles") in by_product
            assert all(o.get("product") != "耳塞鼻夹套装" for o in opps)

            threads = pipeline_ui.resolve_pipeline_deal_threads(opps)
            assert len(threads) == 3
            nord = next(t for t in threads if t.get("company") == "NordHaus Electronics GmbH" and t.get("product") == "Wireless ANC Earbuds")
            # ROUND 6.9 §4：5,000 → 3,000 是同一次商业机会的数量修订，
            # 由 Deal 创建/解析源逻辑直接收敛成一条记录（不是前端去重），
            # 所以 Pipeline 线程内只有 1 条商机记录，而不是 2 条并列记录。
            assert nord["raw_count"] == 1
            assert nord["message_count"] == 1
            assert "2条记录" not in pipeline_ui._card_label(nord)
            assert "Wireless ANC Earbuds" in pipeline_ui._card_label(nord)

            groups = ui.group_deal_threads(_queue_rows(), {o["inquiry_id"]: o["stage"] for o in opps})
            nord_group = next(g for g in groups if g.get("key") and "wireless anc earbuds" in str(g.get("key")).lower())
            work = ui.deal_work_item(nord_group, opp_by_inq={o["inquiry_id"]: o for o in opps}, tasks_by_opp={})
            req = work["resolvedState"]["resolvedRequirement"]
            assert work["product"] == "Wireless ANC Earbuds"
            assert work["quantity"] == "约 3,000 pcs"
            assert req["previousQuantity"] == 5000
            assert req["currentQuantity"] == 3000
            assert req["quantityConflict"] is False
        finally:
            db.DB_PATH = old_db


if __name__ == "__main__":
    test_round61_full_inquiry_pressure_consistency()
    print("round 6.1 full inquiry pressure test passed")
