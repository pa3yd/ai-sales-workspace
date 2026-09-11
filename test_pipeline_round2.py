"""Round 2 Pipeline scenarios A-H. Uses an isolated SQLite database."""
import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "workbench"))
import db
import sales_crm as crm


class PipelineScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(cls.tmp.name, "pipeline.db")
        db.init_db()
        conn = db.sqlite3.connect(db.DB_PATH)
        conn.execute("INSERT INTO customers(ckey,company,country,email,contact_name,inquiry_count,last_seen) VALUES(?,?,?,?,?,?,?)",
                     ("email:a@example.com", "Nordic Retail", "Sweden", "a@example.com", "Anna", 1, "2026-09-09 09:00"))
        conn.commit(); cls.customer_id = conn.execute("SELECT id FROM customers").fetchone()[0]; conn.close()

    def new_deal(self, details=None):
        return db.create_opportunity(self.customer_id, "Nordic Earbuds", "Wireless Earbuds", details=details or {
            "quantity": "5000 pcs", "specification": "BT 5.3", "customization": "Logo"})

    def test_a_new_inquiry_creates_new_pipeline_deal(self):
        oid = db.create_opportunity_from_inquiry(self.customer_id, 9001,
            {"extracted": {"company": "Nordic Retail", "quantity": "5000 pcs"},
             "matches": [{"name_cn": "无线耳机"}]})
        self.assertEqual(db.get_opportunity(oid)["stage"], "NEW")

    def test_b_required_fields_allow_qualified_and_missing_quantity_blocks(self):
        deal = {"stage": "NEW", "customer_id": self.customer_id, "product": "Earbuds", "quantity": ""}
        self.assertIn("数量", crm.transition_errors("NEW", "QUALIFIED", deal))
        oid = self.new_deal(); complete = db.get_opportunity(oid)
        self.assertEqual(crm.transition_errors("NEW", "QUALIFIED", complete), [])
        db.move_opportunity_stage(oid, "QUALIFIED")
        self.assertEqual(db.get_opportunity(oid)["stage"], "QUALIFIED")

    def test_c_completed_quote_allows_quoted(self):
        oid = self.new_deal(); db.create_quote(oid, 12000, "USD", "2026-10-01", "SENT")
        db.move_opportunity_stage(oid, "QUOTED")
        self.assertEqual(db.get_opportunity(oid)["stage"], "QUOTED")
        self.assertTrue(db.list_quotes(oid))

    def test_d_follow_up_syncs_deal_next_activity(self):
        oid = self.new_deal(); db.record_deal_activity(oid, "EMAIL_SENT", "Sent specifications")
        due = (dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        db.create_crm_task(oid, "Call customer", due)
        deal = db.get_opportunity(oid)
        self.assertEqual(crm.health_of(deal)["status"], "healthy")
        self.assertEqual(db.list_deal_activity(oid)[0]["type"], "FOLLOW_UP_CREATED")

    def test_e_overdue_next_activity(self):
        oid = self.new_deal(); past = (dt.datetime.now() - dt.timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        db.update_opportunity(oid, {"next_action": "Follow up", "next_action_at": past})
        self.assertEqual(crm.health_of(db.get_opportunity(oid))["status"], "overdue")

    def test_health_stale_deal_at_risk(self):
        oid = self.new_deal(); old = (dt.datetime.now() - dt.timedelta(days=8)).strftime("%Y-%m-%d %H:%M")
        conn = db.sqlite3.connect(db.DB_PATH); conn.execute("UPDATE opportunities SET last_activity_at=?,next_action='Email' WHERE id=?", (old, oid)); conn.commit(); conn.close()
        self.assertEqual(crm.health_of(db.get_opportunity(oid))["status"], "at_risk")

    def test_f_won_requires_manual_confirmation_and_order_fact(self):
        oid = self.new_deal(); deal = db.get_opportunity(oid); deal["amount"] = 12000
        errors = crm.transition_errors("PO_PENDING", "WON", deal, has_quote=True)
        self.assertIn("人工确认赢单", errors); self.assertIn("PO 已收到或订单已确认", errors)
        flags = {"manual_confirm": True, "po_received": True}
        self.assertEqual(crm.transition_errors("PO_PENDING", "WON", deal, has_quote=True, flags=flags), [])

    def test_g_lost_requires_reason_and_is_saved(self):
        oid = self.new_deal(); deal = db.get_opportunity(oid)
        self.assertIn("输单原因", crm.transition_errors("NEW", "LOST", deal))
        db.mark_opportunity_lost(oid, "价格", "预算不足")
        saved = db.get_opportunity(oid)
        self.assertEqual((saved["stage"], saved["lost_reason"]), ("LOST", "价格"))

    def test_h_metrics_and_stage_filter_use_persisted_state(self):
        oid = self.new_deal(); db.update_opportunity(oid, {"amount": 10000, "currency": "USD"})
        db.move_opportunity_stage(oid, "QUALIFIED")
        deals = db.list_opportunities(stage="QUALIFIED")
        self.assertIn(oid, [x["id"] for x in deals])
        metrics = crm.pipeline_metrics(db.list_opportunities())
        self.assertGreaterEqual(metrics["by_currency"]["USD"]["weighted"], 2000)


if __name__ == "__main__":
    unittest.main()
