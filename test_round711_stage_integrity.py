# -*- coding: utf-8 -*-
"""ROUND 7.1.1 · Deal stage integrity at inquiry ingestion boundaries."""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workbench"))
from workbench import db


def _report(company, contact, product, quantity, country="Germany"):
    return {
        "score": 80,
        "intent": "RFQ",
        "lead": {"grade": "A", "score": 80},
        "extracted": {
            "company": company,
            "contact_name": contact,
            "country": country,
            "product_query": product,
            "quantity": quantity,
            "source": "TEST",
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


class _TemporaryDb:
    def __enter__(self):
        self.old_path = db.DB_PATH
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db.DB_PATH = self.path
        db.init_db()
        return self

    def __exit__(self, *_):
        db.DB_PATH = self.old_path
        try:
            os.remove(self.path)
        except OSError:
            pass


def _seed(company="NordHaus Electronics GmbH", product="Wireless ANC Earbuds", quantity="约 5,000 pcs"):
    inquiry_id = db.save_inquiry("Initial RFQ", _report(company, "Michael Weber", product, quantity))
    opportunity = db.list_opportunities()[0]
    return inquiry_id, opportunity


def _reply(company, product, quantity):
    return db.save_inquiry("Customer reply / revised requirement", _report(company, "Michael Weber", product, quantity))


def test_existing_quoted_reply_and_revision_preserve_stage_and_history():
    with _TemporaryDb():
        first, opp = _seed()
        db.move_opportunity_stage(opp["id"], "QUOTED", reason="报价已发送")
        second = _reply("NordHaus Electronics GmbH", "Wireless ANC Earbuds", "约 3,000 pcs")

        opportunities = db.list_opportunities()
        assert len(opportunities) == 1
        resolved = opportunities[0]
        assert resolved["id"] == opp["id"]
        assert resolved["stage"] == "QUOTED"
        assert resolved["details"]["quantity"] == "约 3,000 pcs"
        assert {"约 5,000 pcs", "约 3,000 pcs"} <= set(resolved["details"]["quantity_history"])
        assert {first, second} <= set(resolved["details"]["related_inquiry_ids"])


def test_same_message_reingestion_keeps_one_deal_and_stage():
    with _TemporaryDb():
        _, opp = _seed()
        db.move_opportunity_stage(opp["id"], "QUOTED", reason="已报价")
        _reply("NordHaus Electronics GmbH", "Wireless ANC Earbuds", "约 3,000 pcs")
        _reply("NordHaus Electronics GmbH", "Wireless ANC Earbuds", "约 3,000 pcs")

        opportunities = db.list_opportunities()
        assert len(opportunities) == 1
        assert opportunities[0]["stage"] == "QUOTED"


def test_existing_sample_and_negotiation_preserve_stage_on_reply():
    for stage in ("SAMPLE", "NEGOTIATION"):
        with _TemporaryDb():
            _, opp = _seed()
            db.move_opportunity_stage(opp["id"], stage, reason="existing stage")
            _reply("NordHaus Electronics GmbH", "Wireless ANC Earbuds", "约 3,000 pcs")
            opportunities = db.list_opportunities()
            assert len(opportunities) == 1
            assert opportunities[0]["stage"] == stage


def test_new_product_and_new_customer_initialize_new_stage():
    with _TemporaryDb():
        _, existing = _seed()
        db.move_opportunity_stage(existing["id"], "QUOTED", reason="已报价")
        _reply("NordHaus Electronics GmbH", "USB-C Travel Charger", "约 1,000 pcs")
        _reply("BrightPromo BV", "Stainless Steel Water Bottles", "约 10,000 pcs")

        opportunities = db.list_opportunities()
        by_product = {opp["product"]: opp for opp in opportunities}
        assert by_product["Wireless ANC Earbuds"]["stage"] == "QUOTED"
        assert by_product["USB-C Travel Charger"]["stage"] == "NEW"
        assert by_product["Stainless Steel Water Bottles"]["stage"] == "NEW"


def test_legacy_duplicate_reconciliation_preserves_most_advanced_stage():
    with _TemporaryDb() as temp:
        first, original = _seed()
        db.move_opportunity_stage(original["id"], "QUOTED", reason="已报价")
        second = _reply("NordHaus Electronics GmbH", "Wireless ANC Earbuds", "约 3,000 pcs")
        conn = sqlite3.connect(temp.path)
        row = conn.execute("SELECT * FROM opportunities WHERE id=?", (original["id"],)).fetchone()
        current = db.get_opportunity(original["id"])
        details = dict(current["details"])
        details.update({"current_inquiry_id": second, "related_inquiry_ids": [second], "quantity": "约 3,000 pcs"})
        conn.execute(
            """INSERT INTO opportunities
               (customer_id, inquiry_id, title, product, stage, probability, details_json, last_activity_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (row[1], second, row[3], row[4], "NEW", 10, json.dumps(details),
             current["last_activity_at"], current["created_at"], "9999-12-31 23:59"),
        )
        conn.commit()
        conn.close()

        assert db.reconcile_duplicate_opportunities() == 1
        opportunities = db.list_opportunities()
        assert len(opportunities) == 1
        assert opportunities[0]["stage"] == "QUOTED"


if __name__ == "__main__":
    test_existing_quoted_reply_and_revision_preserve_stage_and_history()
    test_same_message_reingestion_keeps_one_deal_and_stage()
    test_existing_sample_and_negotiation_preserve_stage_on_reply()
    test_new_product_and_new_customer_initialize_new_stage()
    test_legacy_duplicate_reconciliation_preserves_most_advanced_stage()
    print("ROUND 7.1.1 stage integrity PASS")
