# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
WB = os.path.join(ROOT, "workbench")
if WB not in sys.path:
    sys.path.insert(0, WB)

import db
import pipeline_ui
import sales_crm as crm


class PipelineRound61DedupeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "round61.db")
        db.init_db()
        self.customer_id = db.upsert_customer({
            "company": "NordHaus Electronics GmbH",
            "country": "Germany",
            "contact_name": "Michael Weber",
            "email": "michael@nordhaus.example",
        }, grade="P1", score=86)

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_same_customer_product_is_one_pipeline_thread(self):
        first = db.create_opportunity(self.customer_id, "NordHaus · ANC Earbuds #1", "Wireless ANC Earbuds", details={"quantity": "5,000 pcs", "ai_score": 86})
        second = db.create_opportunity(self.customer_id, "NordHaus · ANC Earbuds #2", "Wireless ANC Earbuds", details={"quantity": "3,000 pcs", "ai_score": 86})
        third = db.create_opportunity(self.customer_id, "NordHaus · Travel Charger", "USB-C Travel Charger", details={"quantity": "1,000 pcs", "ai_score": 63})
        db.update_opportunity(first, {"amount": 45000, "currency": "USD", "next_action": "查看并发送回复"})
        db.update_opportunity(second, {"amount": 30000, "currency": "USD", "next_action": "创建报价"})
        db.create_quote(second, 30000, "USD", "2099-12-31", "SENT")

        threads = pipeline_ui.resolve_pipeline_deal_threads(db.list_opportunities())
        self.assertEqual(len(threads), 2)
        earbuds = next(x for x in threads if x.get("product") == "Wireless ANC Earbuds")
        self.assertEqual(earbuds["raw_count"], 2)
        self.assertEqual(earbuds["id"], second)
        self.assertEqual(earbuds["quote_summary"]["status"], "SENT")
        self.assertIn(first, earbuds["opportunity_ids"])
        self.assertIn(second, earbuds["opportunity_ids"])

        label = pipeline_ui._card_label(earbuds)
        self.assertIn("NordHaus Electronics GmbH", label)
        self.assertIn("Wireless ANC Earbuds", label)
        self.assertIn("2条记录", label)
        self.assertIn("报价已发送", label)
        self.assertNotIn("UNKNOWN", label)

        metrics = crm.pipeline_metrics(threads)
        self.assertEqual(metrics["active_count"], 2)
        self.assertEqual(metrics["by_currency"]["USD"]["pipeline"], 30000)


if __name__ == "__main__":
    unittest.main()
