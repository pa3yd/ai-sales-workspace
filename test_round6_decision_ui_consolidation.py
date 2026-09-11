# -*- coding: utf-8 -*-
"""ROUND 6.6 CRM UX reduction static guards."""
from pathlib import Path

APP = Path(__file__).resolve().parent / "workbench" / "app.py"
SRC = APP.read_text(encoding="utf-8")
START = SRC.index("def _render_customer_workspace(ctx):")
WORKSPACE = SRC[START:SRC.index("    return", START)]
DEFAULT = WORKSPACE.split('with st.expander("查看完整分析"', 1)[0]


def test_deal_detail_has_one_unified_ai_sales_assistant():
    assert "<div class='assistant-title'>AI销售助手</div>" in DEFAULT
    assert DEFAULT.count("assistant-title") == 1
    assert "<div class='nba'><div class='lb'>下一步</div>" in DEFAULT
    assert "<div class='lbl'>AI 商机判断</div>" not in DEFAULT
    assert "AI分析结果" not in DEFAULT
    assert "AI Priority = Σ" not in DEFAULT
    assert "🤖 AI 销售助手" not in DEFAULT
    assert "deal-action-grid" not in DEFAULT
    assert "<div class='cell'><div class='lb'>优先级</div>" not in DEFAULT
    assert "<div class='cell'><div class='lb'>阻塞项</div>" not in DEFAULT
    assert "mini-stage" not in DEFAULT
    assert "recent-activity" not in DEFAULT


def test_l2_l3_are_collapsed_inside_unified_assistant_flow():
    assert 'with st.expander("商机判断", expanded=False):' not in WORKSPACE
    assert 'with st.expander("完整AI分析"' not in WORKSPACE
    assert 'with st.expander("查看完整分析"' in WORKSPACE
    assert 'with st.expander("更多"' in WORKSPACE
    assert "商机优先级" in WORKSPACE
    assert "需求完整度" in WORKSPACE
    assert "报价准备度" in WORKSPACE
    assert "开发者调试详情" in WORKSPACE
    assert '_render_ai_basis(lead, insight' in WORKSPACE


def test_customer_requirements_and_timeline_remain_default_business_sections():
    assert "_render_requirement_compact" in DEFAULT
    assert "_render_timeline_compact" in DEFAULT
    assert '"Product"' in SRC
    assert '"Quantity"' in SRC
    assert '"Specification"' in SRC
    assert '"Target"' in SRC
    assert '"Incoterm"' in SRC
    assert '"Lead Time"' in SRC
    assert '"Samples"' in SRC
    assert '"Customization"' in SRC
    assert '"Recommendation Request"' in SRC


def test_render_report_stops_after_workspace_for_saved_deal():
    render_start = SRC.index("def render_report(report: dict, text: str, id_=None):")
    call = SRC.index("_render_customer_workspace(dict(", render_start)
    after_call = SRC[call:SRC.index("# ============ Level 1 · Next Action", call)]
    assert "\n        return\n" in after_call


def test_home_has_single_primary_sales_queue():
    assert "今日焦点" not in SRC
    assert "today-focus" not in SRC
    assert "focus-card" not in SRC
    assert "销售工作队列" in SRC


if __name__ == "__main__":
    test_deal_detail_has_one_unified_ai_sales_assistant()
    test_l2_l3_are_collapsed_inside_unified_assistant_flow()
    test_customer_requirements_and_timeline_remain_default_business_sections()
    test_render_report_stops_after_workspace_for_saved_deal()
    test_home_has_single_primary_sales_queue()
    print("round 6.6 crm ux reduction tests passed")
