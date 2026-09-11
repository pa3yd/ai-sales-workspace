# -*- coding: utf-8 -*-
"""ROUND 5.3 · Sidebar Deal Thread Cleanup.

Checks that the sidebar shows one current Deal card per active deal, while
historical messages are compressed into lightweight timeline rows and no longer
repeat current Next Action copy.
"""

import os
import sys
import shutil
import tempfile
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workbench"))

import db  # noqa: E402


def _report(product, qty):
    return {
        "extracted": {
            "company": "NordHaus Electronics GmbH",
            "country": "Germany",
            "contact_name": "Michael Weber",
            "quantity": qty,
            "quantity_unit": "pcs",
            "product_query": product,
            "intent": "updated quotation request",
        },
        "lead": {"grade": "A", "score": 86},
        "matches": [],
        "insight": {},
        "score": 86,
        "draft": "",
    }


def test_sidebar_deal_history_is_compact_and_non_actionable():
    old_db = db.DB_PATH
    tmp = tempfile.mkdtemp()
    db.DB_PATH = os.path.join(tmp, "workbench.db")
    try:
        db.init_db()
        ids = [
            db.save_inquiry(
                "Previous quote request for 5,000 pcs Wireless ANC Earbuds.",
                _report("Wireless ANC Earbuds", 5000),
            ),
            db.save_inquiry(
                "Updated quote request for 3,000 pcs Wireless ANC Earbuds.",
                _report("Wireless ANC Earbuds", 3000),
            ),
            db.save_inquiry(
                "Follow-up for 3,000 pcs Wireless ANC Earbuds.",
                _report("Wireless ANC Earbuds", 3000),
            ),
        ]
        db.save_inquiry(
            "New request for Wireless ANC Headphones, 1,000 pcs.",
            _report("Wireless ANC Headphones", 1000),
        )

        at = AppTest.from_file(str(ROOT / "workbench" / "app.py"),
                               default_timeout=90).run()
        assert not at.exception, [getattr(e, "value", str(e)) for e in at.exception]

        buttons = [(str(b.key), str(b.label)) for b in at.sidebar.button]
        open_deal = [x for x in buttons if x[0].startswith("open_deal_")]
        history_toggles = [x for x in buttons if x[0].startswith("sd_open_")]
        assert len(open_deal) == 2, buttons
        assert len(history_toggles) == 1, buttons

        md = "\n".join(str(x.value) for x in at.sidebar.markdown)
        assert "3 条往来" in md
        assert "Wireless ANC Earbuds" in md
        assert "Wireless ANC Headphones" in md

        # Open the history for the earbuds deal.
        toggle = next(b for b in at.sidebar.button
                      if str(b.key).startswith("sd_open_"))
        toggle.click().run()
        assert not at.exception, [getattr(e, "value", str(e)) for e in at.exception]

        md2 = "\n".join(str(x.value) for x in at.sidebar.markdown)
        buttons2 = [(str(b.key), str(b.label)) for b in at.sidebar.button]
        hist_buttons = [x for x in buttons2 if x[0].startswith("open_hist_")]

        assert hist_buttons, buttons2
        assert all(label == "查看" for _, label in hist_buttons), hist_buttons
        assert "打开这条历史" not in "\n".join(label for _, label in buttons2)
        assert "下一步：查看并发送回复" not in md2
        assert "历史往来 · 当前动作以主卡为准" in md2 or "历史数量：" in md2
        # Same product + same quantity historical rows are visually compressed.
        assert len(hist_buttons) < len(ids), hist_buttons
    finally:
        db.DB_PATH = old_db
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_sidebar_deal_history_is_compact_and_non_actionable()
    print("sidebar round 5.3 cleanup tests passed")
