# -*- coding: utf-8 -*-
"""ROUND 6 · Pipeline & Quote Execution Closure tests."""

import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench"))

import db  # noqa: E402
import sales_crm as crm  # noqa: E402


def _setup_deal():
    db.init_db()
    cid = db.upsert_customer({"company": "NordHaus Electronics GmbH",
                              "country": "Germany",
                              "contact_name": "Michael Weber"}, "A", 86)
    oid = db.create_opportunity(
        cid, "NordHaus · Wireless ANC Earbuds",
        product="Wireless ANC Earbuds", owner="销售",
        details={"quantity": "3,000 pcs",
                 "specification": "Bluetooth 5.4, ANC, 40h battery",
                 "customization": "Logo and retail packaging"})
    return oid


def test_quote_sent_moves_pipeline_and_creates_followup():
    old = db.DB_PATH
    tmp = tempfile.mkdtemp()
    db.DB_PATH = os.path.join(tmp, "workbench.db")
    try:
        oid = _setup_deal()
        version = db.create_quote(oid, 26400, "USD", "2026-09-30", "SENT")
        assert version == 1
        deal = db.get_opportunity(oid)
        assert deal["stage"] == "QUOTED"
        quotes = db.list_quotes(oid)
        assert crm.quote_lifecycle_summary(quotes)["status"] == "SENT"
        tasks = db.list_followup_tasks(opportunity_id=oid)
        assert any(t["reason"] == "QUOTE_SENT_NO_REPLY" for t in tasks)
        assert deal["next_action"] and "报价" in deal["next_action"]
    finally:
        db.DB_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_quote_revision_accept_reject_and_expire_paths():
    old = db.DB_PATH
    tmp = tempfile.mkdtemp()
    db.DB_PATH = os.path.join(tmp, "workbench.db")
    try:
        oid = _setup_deal()
        db.create_quote(oid, 26400, "USD", "2026-09-30", "DRAFT")
        q1 = db.list_quotes(oid)[0]
        assert db.update_quote_status(q1[0], "SENT")
        assert db.get_opportunity(oid)["stage"] == "QUOTED"

        db.create_quote(oid, 25800, "USD", "2026-10-15", "REVISED")
        latest = db.list_quotes(oid)[0]
        assert latest[1] == 2 and latest[5] == "REVISED"
        assert crm.quote_lifecycle_summary(db.list_quotes(oid))["status"] == "REVISED"

        assert db.update_quote_status(latest[0], "REJECTED")
        deal = db.get_opportunity(oid)
        assert deal["stage"] == "QUOTED"
        assert "异议" in (deal["next_action"] or "")

        db.create_quote(oid, 25500, "USD", "2026-10-30", "SENT")
        accepted = db.list_quotes(oid)[0]
        assert db.update_quote_status(accepted[0], "ACCEPTED")
        assert db.get_opportunity(oid)["stage"] == "PO_PENDING"
        assert crm.quote_lifecycle_summary(db.list_quotes(oid))["status"] == "ACCEPTED"

        db.create_quote(oid, 25000, "USD", "2020-01-01", "SENT")
        assert crm.quote_lifecycle_summary(db.list_quotes(oid))["status"] == "EXPIRED"
    finally:
        db.DB_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_quote_sent_moves_pipeline_and_creates_followup()
    test_quote_revision_accept_reject_and_expire_paths()
    print("round6 quote execution tests passed")
