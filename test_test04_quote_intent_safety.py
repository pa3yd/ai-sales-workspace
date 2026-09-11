# -*- coding: utf-8 -*-
"""TEST04 · Quote intent, blocking logic and certification fact safety."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "workbench"))

import gapcheck  # noqa: E402
import email_unified as email  # noqa: E402
import queue_ui as ui  # noqa: E402


TEXT04 = """
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


INFO04 = {
    "company": "NordHaus Electronics GmbH",
    "contact_name": "Michael Weber",
    "country": "Germany",
    "product_query": "Wireless ANC Earbuds",
    "quantity": 3000,
    "quantity_unit": "pcs",
    "specification": "Bluetooth 5.4, ANC, minimum 40 hours battery life",
    "customization": "Black and White, custom logo, custom retail packaging",
    "target_price": "8.80",
    "target_price_currency": "USD",
    "target_price_unit": "set",
    "incoterm": "FOB",
    "certification": "CE / RoHS",
}


def _ctx(matches=None):
    gaps = gapcheck.detect_missing(TEXT04, INFO04, matches or [], known_email="m.weber@nordhaus-electronics.de")
    group = {
        "key": ("deal", ("cid", 1), "wireless anc earbuds", "open"),
        "items": [
            {"id": 1, "created": "2026-09-09 10:00", "company": "NordHaus Electronics GmbH",
             "contact": "Michael Weber", "cust_id": 1, "status": "已处理", "biz": "QUOTED",
             "need": {"product_query": "Wireless ANC Earbuds", "qty": "约 5,000 pcs",
                      "readiness": "PRELIMINARY_QUOTE_READY", "blockers": []},
             "action": {"type": "FOLLOW_UP", "label": "执行跟进"}, "pts": 70},
            {"id": 2, "created": "2026-09-10 10:00", "company": "NordHaus Electronics GmbH",
             "contact": "Michael Weber", "cust_id": 1, "status": "待处理", "biz": "READY_TO_REPLY",
             "need": {"product_query": "Wireless ANC Earbuds", "qty": "约 3,000 pcs",
                      "readiness": "PRELIMINARY_QUOTE_READY", "blockers": []},
             "action": {"type": "REPLY", "label": "查看并发送回复"}, "pts": 90},
        ],
        "lead": {"id": 2, "created": "2026-09-10 10:00", "company": "NordHaus Electronics GmbH",
                 "contact": "Michael Weber", "cust_id": 1, "status": "待处理", "biz": "READY_TO_REPLY",
                 "need": {"product_query": "Wireless ANC Earbuds", "qty": "约 3,000 pcs",
                          "readiness": "PRELIMINARY_QUOTE_READY", "blockers": []},
                 "action": {"type": "REPLY", "label": "查看并发送回复"}, "pts": 90},
        "count": 2,
    }
    opp = {"id": -10001, "stage": "QUOTED", "product": "Wireless ANC Earbuds",
           "details": {"requirement_completeness": "HIGH",
                       "product_match_status": "MATCHED",
                       "supplier_capability_status": "UNKNOWN",
                       "quotation_readiness": "PRELIMINARY_READY"}}
    w = ui.deal_work_item(group, opp=opp, opp_tasks=[])
    return {
        "text": TEXT04,
        "info": INFO04,
        "matches": matches or [],
        "gaps": gaps,
        "stored_draft": "",
        "seller_company": "",
        "biz": "READY_TO_REPLY",
        "quotation_ready": False,
        "customer_product": "Wireless ANC Earbuds",
        "known": ["email", "company"],
        "resolved_state": w["resolvedState"],
    }, gaps, w


def test_requirement_complete_and_reference_optional():
    ctx, gaps, w = _ctx()
    buckets = email.requirement_buckets(ctx)

    assert buckets["requirementCompleteness"] in {"HIGH", "COMPLETE"}
    assert not buckets["customerBlockingItems"]
    assert all("reference" not in str(x.get("question", "")).lower()
               for x in buckets["customerBlockingItems"])
    assert w["resolvedState"]["customerRequestedOutcome"] == "REQUEST_UPDATED_QUOTATION"


def test_next_best_action_is_quotation_related():
    ctx, _, w = _ctx()
    nba = w["resolvedState"]["nextBestAction"]

    assert nba["type"] in {
        "CHECK_INTERNAL_QUOTATION_PREREQUISITES",
        "PREPARE_UPDATED_QUOTATION",
    }
    assert w["resolvedState"]["primaryCta"] in {"检查报价条件", "创建更新报价"}


def test_email_for_updated_quote_is_safe_and_no_questions():
    ctx, _, _ = _ctx()
    res = email.generate_customer_email(ctx, intent="AUTO")
    body = res["body"]
    low = body.lower()

    assert res["intent"] == email.UPDATED_QUOTATION_REPLY
    assert "revised first-order quantity of" in low
    assert "initial quantity of" not in low
    assert "initial order quantity of" not in low
    assert "reference model" not in low
    assert "reference photo" not in low
    assert "a photo" not in low
    assert "what quantity" not in low
    assert "your target fob price of usd 8.80/set" in low
    assert "our fob price is usd 8.80" not in low
    assert "available ce and rohs documentation" not in low
    assert "verify the relevant ce/rohs documentation" in low
    assert "samples are available" not in low
    assert "not currently represented in our standard catalog" not in low
    assert res["question_count"] == 0


def test_incomplete_inquiry_can_still_ask_blocking_questions():
    text = "Hello, please quote best price for a promotional item. Need it soon."
    info = {"company": "Acme", "contact_name": "Ana"}
    gaps = gapcheck.detect_missing(text, info, [], known_email="ana@example.com")
    ctx = {"text": text, "info": info, "matches": [], "gaps": gaps,
           "customer_product": "", "known": ["email"]}
    buckets = email.requirement_buckets(ctx)
    res = email.generate_customer_email(ctx, intent="AUTO")

    assert buckets["customerBlockingItems"]
    assert res["intent"] == email.CLARIFY_REQUIREMENT
    assert res["question_count"] >= 1


if __name__ == "__main__":
    test_requirement_complete_and_reference_optional()
    test_next_best_action_is_quotation_related()
    test_email_for_updated_quote_is_safe_and_no_questions()
    test_incomplete_inquiry_can_still_ask_blocking_questions()
    print("test04 quote intent safety tests passed")
