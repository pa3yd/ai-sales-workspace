# -*- coding: utf-8 -*-
"""ROUND 4 · CRM governance logic regression tests."""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "workbench"))

import sales_crm as crm  # noqa: E402


def _deal(**patch):
    base = {
        "id": 1,
        "customer_id": 10,
        "company": "NordHaus Electronics GmbH",
        "title": "NordHaus Earbuds",
        "product": "Wireless ANC Earbuds",
        "stage": "REQUIREMENT_CONFIRMED",
        "amount": 45000,
        "currency": "USD",
        "probability": 35,
        "owner": "销售",
        "next_action": "",
        "next_action_at": "",
        "last_activity_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "details": {
            "quantity": "5000 pcs",
            "specification": "Bluetooth 5.4, ANC",
            "product_match_status": "MATCHED",
            "supplier_capability_status": "CAPABLE",
        },
    }
    base.update(patch)
    return base


def test_draft_quote_is_not_stage_gate_for_quoted():
    deal = _deal()
    draft_quotes = [(1, 1, 45000, "USD", "2026-10-10", "DRAFT", "2026-09-10")]

    assert crm.has_sent_quote(draft_quotes) is False
    errors = crm.transition_errors(
        deal["stage"], "QUOTED", deal, has_quote=crm.has_sent_quote(draft_quotes)
    )
    assert "已发送报价" in errors


def test_sent_quote_allows_quoted_stage():
    deal = _deal()
    sent_quotes = [(1, 1, 45000, "USD", "2026-10-10", "SENT", "2026-09-10")]

    assert crm.has_sent_quote(sent_quotes) is True
    errors = crm.transition_errors(
        deal["stage"], "QUOTED", deal, has_quote=crm.has_sent_quote(sent_quotes)
    )
    assert "已发送报价" not in errors


def test_quote_lifecycle_and_risk_engine():
    quoted_without_sent = _deal(stage="QUOTED")
    draft_quotes = [(1, 1, 45000, "USD", "2026-10-10", "DRAFT", "2026-09-10")]

    qsum = crm.quote_lifecycle_summary(draft_quotes)
    risks = crm.deal_risks(quoted_without_sent, draft_quotes, [], ["QUOTATION_CREATED"])

    assert qsum["status"] == "DRAFT"
    assert qsum["has_sent"] is False
    assert any(r["type"] == "STAGE_MISMATCH_RISK" for r in risks)
    assert any(r["type"] == "QUOTE_NO_FOLLOWUP_RISK" for r in risks)


def test_data_quality_score_is_separate_from_priority():
    deal = _deal(next_action="发送报价")
    sent_quotes = [(1, 1, 45000, "USD", "2026-10-10", "SENT", "2026-09-10")]
    dq = crm.data_quality_score(deal, sent_quotes, [{"fu_status": "PENDING"}], ["QUOTATION_SENT"])

    assert dq["score"] >= 80
    assert "客户产品" not in dq["missing"]
    assert "供应能力" not in dq["missing"]


if __name__ == "__main__":
    test_draft_quote_is_not_stage_gate_for_quoted()
    test_sent_quote_allows_quoted_stage()
    test_quote_lifecycle_and_risk_engine()
    test_data_quality_score_is_separate_from_priority()
    print("round4 crm governance tests passed")
