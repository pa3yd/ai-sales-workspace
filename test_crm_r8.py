# -*- coding: utf-8 -*-
"""CRM V1: Deal identity plus Sidebar Recent Access navigation."""
import os, shutil, sys, tempfile
from pathlib import Path
from streamlit.testing.v1 import AppTest
ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "workbench")]
import db, queue_ui

def report(company, product, qty):
    return {"score":70,"lead":{"grade":"B","score":70},"matches":[],
      "extracted":{"company":company,"country":"Germany","contact_name":"Anna","product_query":product,"quantity":qty,"source":"TEST"},
      "insight":{"product_match":{"product_match_status":"NO_MATCH"},"requirement_completeness":{"level":"HIGH"}}}

def test_customer_and_deal_identity_with_recent_access():
    old, tmp = db.DB_PATH, tempfile.mkdtemp(); db.DB_PATH = os.path.join(tmp,"workbench.db")
    try:
        db.init_db()
        a1=db.save_inquiry("A first",report("Agg Retail Ltd","Steel Bottle","5,000 pcs"))
        a2=db.save_inquiry("A revision",report("Agg Retail Ltd","Steel Bottle","3,000 pcs"))
        b1=db.save_inquiry("B new",report("Agg Retail Ltd","Office Desk Lamp","1,000 pcs"))
        deals=db.list_opportunities(); assert len(deals)==2
        a=next(d for d in deals if d["product"]=="Steel Bottle"); b=next(d for d in deals if d["product"]=="Office Desk Lamp")
        assert {a1,a2} <= set(a["details"]["related_inquiry_ids"])
        assert a["details"]["quantity"]=="3,000 pcs" and {"5,000 pcs","3,000 pcs"} <= set(a["details"]["quantity_history"])
        assert a["id"] != b["id"] and b1 in b["details"]["related_inquiry_ids"]
        at=AppTest.from_file(str(ROOT/"workbench"/"app.py"),default_timeout=90).run(); assert not at.exception
        md="\n".join(str(x.value) for x in at.sidebar.markdown)
        assert "最近访问" in md and "打开商机后会显示在这里" in md
        assert not [x for x in at.sidebar.button if str(x.key).startswith("open_recent_")]
        # 今日行动按当前代表询盘渲染；不能从 Deal 的历史 source inquiry_id 推断。
        representative_id = str(a["details"].get("current_inquiry_id"))
        homepage_keys = [str(x.key) for x in at.button if str(x.key).startswith("mq_open_")]
        homepage_key = next(key for key in homepage_keys if key.rsplit("_", 1)[-1] == representative_id)
        at.button(key=homepage_key).click().run()
        recent=[str(x.key) for x in at.sidebar.button if str(x.key).startswith("open_recent_")]
        assert len(recent) == 1
        at.sidebar.button(key=recent[0]).click().run()
        assert [str(x.key) for x in at.sidebar.button if str(x.key).startswith("open_recent_")]==recent
        pipeline_keys = [str(x.key) for x in at.button if str(x.key).startswith("pipe_open_")]
        pipeline_key = next(key for key in pipeline_keys if key.rsplit("_", 1)[-1] == str(a["id"]))
        at.button(key=pipeline_key).click().run()
        assert [str(x.key) for x in at.sidebar.button if str(x.key).startswith("open_recent_")]==recent
        at.button(key=f"mq_open_{b['inquiry_id']}").click().run()
        recent=[str(x.key) for x in at.sidebar.button if str(x.key).startswith("open_recent_")]
        assert set(recent)=={f"open_recent_{a['inquiry_id']}",f"open_recent_{b['inquiry_id']}"}
    finally:
        db.DB_PATH=old; shutil.rmtree(tmp,ignore_errors=True)

if __name__=="__main__":
    test_customer_and_deal_identity_with_recent_access(); print("CRM R8 Recent Access regression PASS")
