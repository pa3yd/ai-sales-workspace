# -*- coding: utf-8 -*-
"""CRM Sidebar Deal Integrity & Density regression.

Sidebar is navigation + recent Deal access. It should not behave as a second
full sales queue with separate open/history controls.
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


def _report(product, qty, company="NordHaus Electronics GmbH"):
    return {
        "extracted": {
            "company": company,
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


def test_sidebar_is_navigation_and_quick_access_only():
    old_db = db.DB_PATH
    tmp = tempfile.mkdtemp()
    db.DB_PATH = os.path.join(tmp, "workbench.db")
    try:
        db.init_db()
        for text, product, qty in [
            ("Previous quote request for 5,000 pcs Wireless ANC Earbuds.", "Wireless ANC Earbuds", 5000),
            ("Updated quote request for 3,000 pcs Wireless ANC Earbuds.", "Wireless ANC Earbuds", 3000),
            ("Follow-up for 3,000 pcs Wireless ANC Earbuds.", "Wireless ANC Earbuds", 3000),
            ("New request for Wireless ANC Headphones, 1,000 pcs.", "Wireless ANC Headphones", 1000),
        ]:
            db.save_inquiry(text, _report(product, qty))

        at = AppTest.from_file(str(ROOT / "workbench" / "app.py"), default_timeout=90).run()
        assert not at.exception, [getattr(e, "value", str(e)) for e in at.exception]

        buttons = [(str(b.key), str(b.label)) for b in at.sidebar.button]
        labels = "\n".join(label for _, label in buttons)
        keys = [key for key, _ in buttons]
        md = "\n".join(str(x.value) for x in at.sidebar.markdown)

        assert "exec_start_side" not in keys, buttons
        assert "开始处理" not in labels
        assert not any(k.startswith("sd_open_") or k.startswith("sd_close_") for k in keys), buttons
        assert not any(k.startswith("open_hist_") for k in keys), buttons
        assert "打开商机" not in labels and "历史" not in labels, labels

        for name in ("优先处理", "待回复", "待报价", "今日跟进", "已逾期", "新询盘", "全部商机"):
            assert f"sv_{name}" in keys, buttons
        assert any(str(e.label) == "筛选与视图" for e in at.sidebar.expander), [str(e.label) for e in at.sidebar.expander]
        assert "最近访问" in md
        assert "打开商机后会显示在这里" in md
        # Sidebar 默认不是第二个 Deal 队列；从首页行动打开后才出现最近访问。
        action_key = next(str(b.key) for b in at.button if str(b.key).startswith("mq_open_"))
        at.button(key=action_key).click().run()
        recent = [str(b.key) for b in at.sidebar.button if str(b.key).startswith("open_recent_")]
        assert len(recent) == 1, recent
        at.sidebar.button(key=recent[0]).click().run()
        repeat = [str(b.key) for b in at.sidebar.button if str(b.key).startswith("open_recent_")]
        assert repeat == recent, repeat
        quick_md = "\n".join(str(x.value) for x in at.sidebar.markdown).split("最近访问", 1)[-1]
        assert "#" not in quick_md and "P1" not in quick_md and "分" not in quick_md, quick_md

        # 待报价是低频入口，仍可访问但零数量不显示成显眼徽标。
        quote_labels = [label for key, label in buttons if key == "sv_待报价"]
        assert quote_labels == ["💰 待报价"], quote_labels
        assert "sidebar_all_deals" not in keys, keys
    finally:
        db.DB_PATH = old_db
        shutil.rmtree(tmp, ignore_errors=True)


def test_quick_access_is_limited_to_five_deals():
    """Quick Access 只用于快速进入重点 Deal，不能随队列长度无限增长。"""
    old_db = db.DB_PATH
    tmp = tempfile.mkdtemp()
    db.DB_PATH = os.path.join(tmp, "workbench.db")
    try:
        db.init_db()
        for index in range(7):
            product = f"Product Variant {index + 1}"
            db.save_inquiry(
                f"Request for {product}, {index + 1}000 pcs.",
                _report(product, (index + 1) * 1000, company=f"Buyer {index + 1} GmbH"),
            )

        at = AppTest.from_file(str(ROOT / "workbench" / "app.py"), default_timeout=90).run()
        assert not at.exception, [getattr(e, "value", str(e)) for e in at.exception]
        assert not [str(button.key) for button in at.sidebar.button
                    if str(button.key).startswith("open_recent_")]
        # Recent Access 的上限是 3，且只能由实际访问产生。
        action_keys = [str(button.key) for button in at.button if str(button.key).startswith("mq_open_")]
        for key in action_keys[:4]:
            at.button(key=key).click().run()
        recent = [str(button.key) for button in at.sidebar.button
                  if str(button.key).startswith("open_recent_")]
        assert 1 <= len(recent) <= 3, recent
    finally:
        db.DB_PATH = old_db
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_sidebar_is_navigation_and_quick_access_only()
    test_quick_access_is_limited_to_five_deals()
    print("sidebar round 6.8 simplification tests passed")
