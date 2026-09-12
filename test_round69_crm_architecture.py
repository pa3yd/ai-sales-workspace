# -*- coding: utf-8 -*-
"""ROUND 6.9 · CRM Architecture Finalization 回归。

覆盖用户验收清单：
  A · Pipeline 首屏就是 Deal（不是筛选器）；进阶筛选收进折叠「筛选」；
      常驻右侧 Deal 面板、5 张金额 KPI、拖拽看板已移除
  B · 跟进台卡片默认只回答 5 件事（客户 / 产品·数量 / 到期逾期 / 下一步 / 主 CTA），
      队列可以快速扫读
  C · 客户档案像 CRM 记录（公司 / 主要联系人 / 活跃商机 / 最近联系 / 下一次活动），
      A/B/C/D 不再是页面结构
  D · NordHaus 5,000 → 3,000 是同一个 Deal（源逻辑收敛，不是前端去重）
  E · 所有工作区打开同一个共享 Deal Detail（只有一套详情实现）

运行：
    .venv/Scripts/python.exe test_round69_crm_architecture.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workbench"))

import db  # noqa: E402
import queue_ui as ui  # noqa: E402
import sales_crm as crm  # noqa: E402

PIPE_SRC = (ROOT / "workbench" / "pipeline_ui.py").read_text(encoding="utf-8")
APP_SRC = (ROOT / "workbench" / "app.py").read_text(encoding="utf-8")

_PASS, _FAIL = [], []


def check(name, ok, detail=""):
    (_PASS if ok else _FAIL).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name + (f"　{detail}" if detail else ""))
    return ok


def _report(company, contact, product, qty, country="Germany"):
    return {
        "score": 86,
        "intent": "RFQ",
        "lead": {"grade": "A", "score": 86},
        "draft": "",
        "extracted": {
            "company": company, "contact_name": contact, "country": country,
            "product_query": product, "quantity": qty, "source": "TEST",
        },
        "matches": [],
        "insight": {
            "product_match": {"product_match_status": "NO_MATCH",
                              "supplier_capability_status": "UNKNOWN",
                              "opportunity_type": "NEW_PRODUCT"},
            "requirement_completeness": {"level": "HIGH"},
        },
    }


def _seed_temp_db(path):
    old = db.DB_PATH
    db.DB_PATH = path
    db.init_db()
    return old


# ======================= A · Pipeline 首屏是 Deal =======================
def test_a_pipeline_default_view_is_deals_not_filters():
    print("【A · Pipeline 默认视图】")
    # 5 张金额 KPI 卡 → 压成 1 行说明
    check("A1 · 金额 KPI 卡已删除（不再有 st.metric 堆叠）",
          "st.metric(" not in PIPE_SRC)
    check("A2 · 改为 1 行 Pipeline 摘要", "def _summary(" in PIPE_SRC)
    # 默认区 = 搜索 + 快捷视图；进阶筛选只在折叠里
    adv = PIPE_SRC.split("def _advanced_filters")[1].split("def _filtered")[0]
    check("A3 · 进阶筛选收进折叠「筛选」",
          'st.expander("筛选", expanded=False)' in adv)
    default_zone = PIPE_SRC.split("def _filtered")[1].split("return result")[0]
    leaked = [k for k in ("pipe_stages", "pipe_owners", "pipe_health",
                          "pipe_country", "pipe_source", "pipe_score")
              if f'key="{k}"' in default_zone]
    check("A4 · 默认区不再出现 AI Score / 国家 / 来源 / 负责人 / 健康度 / 阶段筛选",
          not leaked, str(leaked))
    # ROUND 7.1 §12 把「需处理」提到默认位（4 个：全部 / 需处理 / 已逾期 / 高价值）；
    # 仍要求"少而稳"：单处定义 + 上限 4 个，不得退化成筛选面板。
    _qv = PIPE_SRC.split("PIPE_QUICK_VIEWS = [")[1].split("]")[0].split(",")
    check("A5 · 快捷视图保留 3–4 个（ROUND 7.1 §12 新增「需处理」）",
          PIPE_SRC.count("PIPE_QUICK_VIEWS = [") == 1 and 3 <= len(_qv) <= 4,
          str(len(_qv)))
    check("A5b · 快捷视图含「需处理」（§12 执行视图）",
          "需处理" in PIPE_SRC.split("PIPE_QUICK_VIEWS = [")[1].split("]")[0])
    # 常驻右栏 / 拖拽看板 / 列表视图
    check("A6 · 常驻右侧 Deal 面板已移除",
          "main, side = st.columns" not in PIPE_SRC)
    check("A7 · 拖拽排序看板已移除（卡片改为可点击）",
          "sort_items" not in PIPE_SRC and "pipeline_pending = {" not in PIPE_SRC.split("def _detail")[0])
    check("A8 · 列表视图已删除（DELETE > HIDE）", "_list_view" not in PIPE_SRC)
    check("A9 · 新建商机入口保留", "＋ 新建商机" in PIPE_SRC)
    check("A10 · 阶段列 + Deal 卡保留",
          "_pcard_html" in PIPE_SRC and "crm.STAGES" in PIPE_SRC)
    # 首屏顺序：摘要 → 新建 → 过滤 → 看板（只在 render_pipeline_page 体内比较）
    page = PIPE_SRC.split("def render_pipeline_page")[1].split("def _default_open")[0]
    order = [page.index(x) for x in
             ('st.markdown("## Deal Pipeline")', "_summary(deals)", "_new_deal()",
              "_filtered(deals)", "_board(shown")]
    check("A11 · 渲染顺序 = 摘要 → 新建 → 筛选 → 看板", order == sorted(order), str(order))


# ======================= B · 跟进台卡片可扫读 =======================
def test_b_followup_cards_are_scannable():
    print("【B · 跟进台卡片】")
    seg = APP_SRC.split("def _render_followup_workspace")[1]
    card = seg.split("with st.container(border=True):", 1)[1].split("_fu_render_detail", 1)[0]
    check("B1 · 卡片使用紧凑样式 .fucard", "fucard" in card)
    check("B2 · 默认只有 3 行（客户 / 产品·数量 / 到期·下一步）",
          card.count("<div class='r") == 3, str(card.count("<div class='r")))
    check("B3 · 原因说明已移出默认卡", "REASON_WHY" not in card)
    check("B4 · 商机阶段已移出默认卡", "阶段 " not in card)
    check("B5 · 卡片带唯一主 CTA", "fu_cta_" in card)
    detail = APP_SRC.split("def _fu_render_detail")[1].split("def _qblockers_of")[0]
    moved = [k for k in ("fu_genbtn_", "fu_sent_", "fu_done_", "fu_snooze_",
                         "fu_re_", "fu_view_") if k not in detail]
    check("B6 · 邮件 / 发送 / 完成 / 稍后 / 改期 / 详情都在抽屉里", not moved, str(moved))
    check("B7 · 抽屉默认收起（expanded 由 session_state 控制）",
          "expanded=bool(st.session_state.get(_open_key))" in seg)


# ======================= C · 客户档案像 CRM 记录 =======================
def test_c_customer_archive_is_crm_records():
    print("【C · 客户档案】")
    seg = APP_SRC.split("# ---------- Tab4: 客户档案 ----------")[1].split("# ---------- Tab5")[0]
    check("C1 · A/B/C/D 等级 KPI 卡已删除", 'm1.metric("A 级客户"' not in seg)
    check("C2 · 「按等级筛选」不再是页面结构", "按等级筛选" not in seg)
    for label in ("公司", "主要联系人", "活跃商机", "最近联系", "下一次活动", "等级"):
        check(f"C3 · 默认行包含「{label}」", label in seg)
    check("C4 · 等级降为次要属性（行尾 chip + 详情内归档）",
          'st.selectbox("客户等级归档"' in seg and "次要属性" in seg)
    # 纯函数口径
    row = (1, "NordHaus Electronics GmbH", "Germany", "michael@nordhaus.example",
           "Michael Weber", 8, "A", 86, "2026-09-10 11:28", None, "",
           '[{"name": "Michael Weber"}, {"name": "Anna Keller"}]')
    opps = [{"id": 1, "stage": "QUOTED"}, {"id": 2, "stage": "WON"}]
    tasks = [{"id": 9, "status": "OPEN", "due_at": "2026-09-12 18:00",
              "next_action": "跟进报价反馈"}]
    rec = crm.customer_record(row, opps, tasks)
    check("C5 · 公司 = 客户主语", rec["company"] == "NordHaus Electronics GmbH")
    check("C6 · 主要联系人取联系人档案第一位",
          rec["contact"] == "Michael Weber" and rec["contact_count"] == 2)
    check("C7 · 活跃商机只数非终态", rec["active_deals"] == 1, str(rec["active_deals"]))
    check("C8 · 最近联系 = 客户档案最后互动时间",
          str(rec["last_contact"]).startswith("2026-09-10"))
    check("C9 · 下一次活动 = 最近一条 OPEN 跟进",
          rec["next_activity"]["action"] == "跟进报价反馈")
    check("C10 · 等级是被动属性（A 来自线索评级兜底）", rec["grade"] == "A")


# ======================= D · Deal 身份（P0） =======================
def test_d_nordhaus_revision_is_one_deal():
    print("【D · Deal 身份：5,000 → 3,000 同一个 Deal】")
    old = db.DB_PATH
    tmp = tempfile.mkdtemp()
    db.DB_PATH = os.path.join(tmp, "round69_deal.db")
    try:
        db.init_db()
        cid = db.upsert_customer({"company": "NordHaus Electronics GmbH",
                                  "country": "Germany",
                                  "contact_name": "Michael Weber",
                                  "email": "michael@nordhaus.example"}, grade="P1", score=86)
        first = db.save_inquiry(
            "First RFQ 5,000 pcs Wireless ANC Earbuds",
            _report("NordHaus Electronics GmbH", "Michael Weber",
                    "Wireless ANC Earbuds", "约 5,000 pcs"))
        opp_after_first = db.list_opportunities()
        second = db.save_inquiry(
            "Revision 3,000 pcs Wireless ANC Earbuds",
            _report("NordHaus Electronics GmbH", "Michael Weber",
                    "Wireless ANC Earbuds", "约 3,000 pcs"))
        db.reconcile_duplicate_opportunities()
        opps = db.list_opportunities()

        check("D1 · 一次客户 + 一个产品 = 一个 Deal", len(opps) == 1, str(len(opps)))
        opp = opps[0]
        check("D2 · Deal 身份稳定（第二封修订没有新建 Deal）",
              opp["id"] == opp_after_first[0]["id"])
        check("D3 · 当前数量 = 3,000（以最新修订为准）",
              str(opp["details"].get("quantity") or "").replace(" ", "") == "约3,000pcs",
              str(opp["details"].get("quantity")))
        history = opp["details"].get("quantity_history") or []
        check("D4 · 历史数量 5,000 仍被保留（不丢事实）",
              any("5,000" in str(x) for x in history), str(history))
        check("D5 · 两封往来都挂在同一个 Deal 上",
              {first, second} <= set(opp["details"].get("related_inquiry_ids") or []))
        # 源逻辑：再次解析同一修订必须返回同一个 Deal（而不是前端去重）
        again = db.create_opportunity_from_inquiry(
            opp["customer_id"], second,
            _report("NordHaus Electronics GmbH", "Michael Weber",
                    "Wireless ANC Earbuds", "约 3,000 pcs"))
        check("D6 · 收敛发生在 Deal 创建/解析源逻辑，而非前端去重",
              again == opp["id"] and len(db.list_opportunities()) == 1)

        # 线程层口径：同一 Deal 只出现一次
        groups = ui.group_deal_threads([], {})
        stage_of = {o["inquiry_id"]: o["stage"] for o in opps}
        rows = []
        for iid in (first, second):
            src, rep = db.get_inquiry(iid)
            info = rep.get("extracted") or {}
            rows.append({"id": iid, "created": "2026-09-09 10:00", "company": info.get("company"),
                         "contact": info.get("contact_name"), "country": info.get("country"),
                         "status": "待处理", "cust_id": cid,
                         "need": {"product_query": info.get("product_query"),
                                  "qty": info.get("quantity")}, "wf": {},
                         "action": {"type": "REPLY", "label": "查看并发送回复"}})
        groups = ui.group_deal_threads(rows, stage_of)
        check("D7 · 线程层同样只聚合成 1 个 Deal（前后端口径一致）",
              len(groups) == 1, str(len(groups)))
        # 不同产品仍是不同 Deal
        db.save_inquiry("New product USB-C Travel Charger 1,000 pcs",
                        _report("NordHaus Electronics GmbH", "Michael Weber",
                                "USB-C Travel Charger", "约 1,000 pcs"))
        db.reconcile_duplicate_opportunities()
        check("D8 · 同客户不同产品仍是两个 Deal（不是按公司名去重）",
              len(db.list_opportunities()) == 2, str(len(db.list_opportunities())))
    finally:
        db.DB_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)


# ======================= E · 共享 Deal Detail =======================
def test_e_single_shared_deal_detail():
    print("【E · 共享 Deal Detail】")
    check("E1 · 全站只有一套 Deal Detail 实现",
          APP_SRC.count("def _render_customer_workspace(") == 1)
    check("E2 · Pipeline 不再自带一套详情渲染",
          "def _render_customer_workspace(" not in PIPE_SRC)
    seg = APP_SRC.split("def _open_deal_from_pipeline")[1].split("\ndef ")[0]
    check("E3 · Pipeline 卡片跳转复用同一个询盘/详情入口",
          "_open_inquiry(inquiry_id)" in seg and "pipeline_opened_id" in seg)
    check("E4 · Pipeline 页注入共享跳转回调",
          "render_pipeline_page(open_deal=_open_deal_from_pipeline)" in APP_SRC)
    check("E5 · 四类工作区仍共用同一批 Deal 事实（Customer != Deal != Inquiry != Activity）",
          all(x in APP_SRC for x in ("_opp_by_inq", "_tasks_by_opp", "_home_all_deals")))


# ======================= UI 端到端（AppTest） =======================
def test_ui_round69_apptest():
    print("【UI · AppTest 端到端】")
    from streamlit.testing.v1 import AppTest
    old = db.DB_PATH
    tmp = tempfile.mkdtemp()
    db.DB_PATH = os.path.join(tmp, "workbench.db")
    try:
        db.init_db()
        cid = db.upsert_customer({"company": "NordHaus Electronics GmbH",
                                  "country": "Germany",
                                  "contact_name": "Michael Weber",
                                  "email": "michael@nordhaus.example"}, grade="P1", score=86)
        i1 = db.save_inquiry("5,000 pcs Wireless ANC Earbuds",
                             _report("NordHaus Electronics GmbH", "Michael Weber",
                                     "Wireless ANC Earbuds", "约 5,000 pcs"))
        i2 = db.save_inquiry("3,000 pcs Wireless ANC Earbuds",
                             _report("NordHaus Electronics GmbH", "Michael Weber",
                                     "Wireless ANC Earbuds", "约 3,000 pcs"))
        db.save_inquiry("8,000 pcs Insulated Food Container",
                        _report("Alpine Outdoor GmbH", "Anna Keller",
                                "Insulated Food Container", "约 8,000 pcs",
                                country="Netherlands"))
        opps = db.list_opportunities()
        nord = next(o for o in opps if "Earbuds" in (o.get("product") or ""))
        db.create_followup_task(nord["id"], "QUOTE_SENT_NO_REPLY",
                                due_at="2026-01-05 18:00",
                                next_action="跟进报价反馈", actor="销售")

        at = AppTest.from_file(str(ROOT / "workbench" / "app.py"),
                               default_timeout=120).run()
        errs = [getattr(e, "value", str(e)) for e in at.exception]
        check("U1 · 应用无异常启动", not errs, str(errs[:2]))
        if errs:
            return
        keys = [str(b.key) for b in at.button]
        md = "\n".join(str(m.value) for m in at.markdown)
        exp_labels = [str(e.label) for e in at.expander]

        # A · Pipeline 卡片可点击
        check("U2 · Pipeline 每个 Deal 一张可点击卡",
              f"pipe_open_{nord['id']}" in keys
              and any(k.startswith("pipe_open_") for k in keys), str([k for k in keys if "pipe_open_" in k]))
        # B · 跟进卡 + 主 CTA
        check("U3 · 跟进队列出现主 CTA（生成客户邮件）",
              any(k.startswith("fu_cta_") for k in keys),
              str([k for k in keys if k.startswith("fu_cta_")][:3]))
        check("U4 · 跟进卡抽屉已就位",
              any("处理（邮件" in x for x in exp_labels), str(exp_labels)[:120])
        # C · 客户档案行
        check("U5 · 客户档案默认行含 CRM 字段",
              all(x in md for x in ("主要联系人", "活跃商机", "最近联系", "下一次活动")))
        check("U6 · 客户档案不再以等级看板开场", "A 级客户" not in md)
        check("U7 · 客户记录含两家客户",
              "NordHaus Electronics GmbH" in md and "Alpine Outdoor GmbH" in md)
        # E · 点击 Pipeline 卡片 → 打开共享 Deal Detail
        before = "\n".join(str(m.value) for m in at.markdown)
        at.button(key=f"pipe_open_{nord['id']}").click().run()
        after = "\n".join(str(m.value) for m in at.markdown)
        state = {}
        try:
            state = dict(at.session_state.filtered_state)
        except Exception:
            pass
        check("U8 · 点 Pipeline 卡片 → 打开共享 Deal Detail（同一套 AI销售助手 详情）",
              ("AI销售助手" in after) and
              (not state or state.get("selected_id") in {i1, i2}),
              f"selected_id={state.get('selected_id')}")
        # D · Pipeline 打开后写入最近访问；同一 Deal 的 5,000/3,000 往来只保留一张卡。
        recent_keys = [str(b.key) for b in at.sidebar.button
                       if str(b.key).startswith("open_recent_")]
        check("U9 · Pipeline 打开 NordHaus 后最近访问只保留一张 canonical Deal 卡",
              len(recent_keys) == 1 and recent_keys[0] == f"open_recent_{nord['inquiry_id']}",
              str(recent_keys))
    finally:
        db.DB_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 68)
    print("ROUND 6.9 · CRM Architecture Finalization 回归")
    print("=" * 68)
    test_a_pipeline_default_view_is_deals_not_filters()
    test_b_followup_cards_are_scannable()
    test_c_customer_archive_is_crm_records()
    test_d_nordhaus_revision_is_one_deal()
    test_e_single_shared_deal_detail()
    test_ui_round69_apptest()
    print("-" * 68)
    print(f"结果：{len(_PASS)} 通过 / {len(_FAIL)} 失败")
    if _FAIL:
        print("失败项：" + "; ".join(_FAIL))
        return 1
    print("ROUND 6.9 全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
