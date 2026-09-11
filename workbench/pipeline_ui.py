# -*- coding: utf-8 -*-
"""CRM Sales Pipeline 页面。业务规则在 sales_crm，持久化在 db。"""
from __future__ import annotations

import datetime as dt
import re

import streamlit as st
from streamlit_sortables import sort_items

import db
import queue_ui as q_ui
import sales_crm as crm

HEALTH_ICON = {"healthy": "🟢", "attention": "🟡", "at_risk": "🔴", "overdue": "🔴", "closed": "⚪"}
ACTIVITY_TYPES = ["EMAIL_SENT", "EMAIL_RECEIVED", "CALL", "MEETING", "FOLLOW_UP",
                  "SAMPLE_REQUEST", "SAMPLE_SENT", "SAMPLE_APPROVED", "NEGOTIATION", "NOTE"]


def _money(value, currency="USD"):
    return f"{currency} {float(value):,.0f}" if value else "金额待确认"


def _quote_status_cn(status: str) -> str:
    return {
        "DRAFT": "草稿",
        "SENT": "已发送",
        "REVISED": "已修订",
        "ACCEPTED": "已接受",
        "REJECTED": "已拒绝",
        "EXPIRED": "已过期",
    }.get(str(status or "").upper(), str(status or ""))


def _quote_action_buttons(quote, deal_id: int):
    status = str(quote[5] or "").upper()
    qid, version = quote[0], quote[1]
    cols = st.columns(4)
    if status == "DRAFT":
        if cols[0].button("标记已发送", key=f"quote_sent_{qid}"):
            db.update_quote_status(qid, "SENT")
            st.toast(f"报价 v{version} 已发送，并已创建跟进任务")
            st.rerun()
    if status in ("SENT", "REVISED"):
        if cols[0].button("客户接受", key=f"quote_accept_{qid}"):
            db.update_quote_status(qid, "ACCEPTED")
            st.toast("报价已接受，商机推进到 PO Pending")
            st.rerun()
        if cols[1].button("客户拒绝", key=f"quote_reject_{qid}"):
            db.update_quote_status(qid, "REJECTED")
            st.toast("已记录报价被拒，下一步改为处理异议/修订报价")
            st.rerun()
        if cols[2].button("标记过期", key=f"quote_expire_{qid}"):
            db.update_quote_status(qid, "EXPIRED")
            st.toast("报价已标记过期")
            st.rerun()
    if status in ("SENT", "REVISED", "REJECTED", "EXPIRED"):
        if cols[3].button("创建修订版", key=f"quote_revise_{qid}"):
            st.session_state[f"revise_quote_from_{deal_id}"] = qid
            st.toast("请在上方录入修订金额并保存新版本")



def _norm_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _closed_bucket(stage: str) -> str:
    return "closed" if stage in crm.TERMINAL else "open"


def _stage_rank(stage: str) -> int:
    try:
        return crm.STAGES.index(stage)
    except ValueError:
        return -1


def _deal_thread_key(deal: dict) -> tuple:
    """同一客户 + 同一客户产品 + 开启/关闭桶 = 一个可执行 Deal Thread。"""
    details = deal.get("details") or {}
    customer = deal.get("customer_id") or _norm_text("|".join([
        str(deal.get("company") or ""), str(deal.get("contact") or ""), str(deal.get("email") or "")]))
    product_text = (deal.get("product") or details.get("product_query") or
                    details.get("customerProductRequirement") or deal.get("title") or "")
    signature = q_ui.product_signature(product_text) if product_text else ""
    if not signature:
        signature = f"opp-{deal.get('id')}"
    return (customer, signature, _closed_bucket(deal.get("stage")))


def _parse_dt(value):
    try:
        return dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return dt.datetime.min


def _representative_deal(items: list[dict]) -> dict:
    """选择线程里当前最能代表执行状态的一条 opportunity。"""
    def sort_key(x):
        terminal_penalty = -100 if x.get("stage") in crm.TERMINAL else 0
        return (terminal_penalty + _stage_rank(x.get("stage")),
                _parse_dt(x.get("updated_at") or x.get("created_at")),
                int(x.get("id") or 0))
    return max(items, key=sort_key)


def _latest_followup(tasks: list[dict]) -> dict | None:
    active = [t for t in tasks if str(t.get("fu_status") or "").upper() in {"PENDING", "WAITING_CUSTOMER", "SNOOZED"}]
    if not active:
        return None
    return sorted(active, key=lambda t: (str(t.get("due_at") or "9999-12-31"), int(t.get("id") or 0)))[0]


def _decorate_deal_thread(rep: dict, items: list[dict]) -> dict:
    deal = dict(rep)
    ids = [int(x["id"]) for x in items if x.get("id")]
    quotes_by_deal = {oid: db.list_quotes(oid) for oid in ids}
    tasks_by_deal = {oid: db.list_followup_tasks(opportunity_id=oid) for oid in ids}
    activities_by_deal = {oid: db.list_deal_activity(oid) for oid in ids}
    all_quotes = [q for rows in quotes_by_deal.values() for q in rows]
    all_tasks = [t for rows in tasks_by_deal.values() for t in rows]
    all_activities = [a for rows in activities_by_deal.values() for a in rows]
    qsum = crm.quote_lifecycle_summary(all_quotes)
    health = crm.health_of(deal)
    risks = crm.deal_risks(deal, all_quotes, all_tasks, [a["type"] for a in all_activities])
    dq = crm.data_quality_score(deal, all_quotes, all_tasks, [a["type"] for a in all_activities])
    latest_quote = all_quotes[0] if all_quotes else None
    if all_quotes:
        latest_quote = sorted(all_quotes, key=lambda q: (int(q[1] or 0), str(q[6] or "")), reverse=True)[0]
    latest_task = _latest_followup(all_tasks)
    # Pipeline 金额按线程只计算一次，优先采用代表记录；代表记录无金额时取线程内最新非零金额。
    if not deal.get("amount"):
        nonzero = [x for x in items if x.get("amount")]
        if nonzero:
            priced = max(nonzero, key=lambda x: (_parse_dt(x.get("updated_at") or x.get("created_at")), int(x.get("id") or 0)))
            deal["amount"] = priced.get("amount")
            deal["currency"] = priced.get("currency") or deal.get("currency")
    if latest_task and not deal.get("next_action_at"):
        deal["next_action_at"] = latest_task.get("due_at")
    deal.update({
        "thread_key": _deal_thread_key(rep),
        "thread_opportunities": sorted(items, key=lambda x: int(x.get("id") or 0), reverse=True),
        "opportunity_ids": ids,
        "raw_count": len(items),
        "message_count": len({x.get("inquiry_id") for x in items if x.get("inquiry_id")}) or len(items),
        "quote_summary": qsum,
        "latest_quote": latest_quote,
        "latest_followup": latest_task,
        "thread_health": health,
        "thread_risks": risks,
        "thread_data_quality": dq,
    })
    return deal


def resolve_pipeline_deal_threads(raw_deals: list[dict]) -> list[dict]:
    """Pipeline 页面唯一入口：把原始机会聚合为业务员可执行的 Deal Thread。"""
    buckets: dict[tuple, list[dict]] = {}
    for deal in raw_deals or []:
        buckets.setdefault(_deal_thread_key(deal), []).append(deal)
    threads = [_decorate_deal_thread(_representative_deal(items), items) for items in buckets.values()]
    return sorted(threads, key=lambda x: (_parse_dt(x.get("updated_at") or x.get("created_at")), int(x.get("id") or 0)), reverse=True)

def _filtered(deals):
    a, b, c, d = st.columns([2.2, 1.1, 1.1, 1.1])
    query = a.text_input("搜索", placeholder="客户、产品、联系人", key="pipe_search")
    stages = b.multiselect("阶段", crm.STAGES, format_func=lambda x: crm.STAGE_CN[x], key="pipe_stages")
    owners = c.multiselect("负责人", sorted({x.get("owner") or "未分配" for x in deals}), key="pipe_owners")
    healths = d.multiselect("健康度", ["healthy", "attention", "at_risk", "overdue"],
                            format_func=lambda x: {"healthy":"健康","attention":"需关注","at_risk":"有风险","overdue":"已逾期"}[x], key="pipe_health")
    e, f, g, h = st.columns(4)
    countries = e.multiselect("国家", sorted({x.get("country") for x in deals if x.get("country")}), key="pipe_country")
    sources = f.multiselect("来源", sorted({x.get("source") or "未知" for x in deals}), key="pipe_source")
    min_score = g.slider("最低 AI Score", 0, 100, 0, key="pipe_score")
    quick = h.selectbox("快速筛选", ["全部", "我的商机", "本月预计成交", "已逾期", "高价值"], key="pipe_quick")
    result = []
    month = dt.datetime.now().strftime("%Y-%m")
    for deal in deals:
        health = crm.health_of(deal)
        haystack = " ".join(str(deal.get(k) or "") for k in ("company", "contact", "product", "title")).lower()
        if query and query.lower() not in haystack: continue
        if stages and deal.get("stage") not in stages: continue
        if owners and (deal.get("owner") or "未分配") not in owners: continue
        if healths and health["status"] not in healths: continue
        if countries and deal.get("country") not in countries: continue
        if sources and (deal.get("source") or "未知") not in sources: continue
        if float(deal.get("ai_score") or 0) < min_score: continue
        if quick == "我的商机" and (deal.get("owner") or "销售") != "销售": continue
        if quick == "本月预计成交" and not str(deal.get("expected_close") or "").startswith(month): continue
        if quick == "已逾期" and health["status"] != "overdue": continue
        if quick == "高价值" and float(deal.get("amount") or 0) < 10000: continue
        result.append(deal)
    return result


def _kpis(deals):
    metrics = crm.pipeline_metrics(deals)
    primary_currency = "USD" if "USD" in metrics["by_currency"] else next(iter(metrics["by_currency"]), "USD")
    bucket = metrics["by_currency"].get(primary_currency, {})
    overdue = sum(crm.health_of(x)["status"] == "overdue" for x in deals if x.get("stage") not in crm.TERMINAL)
    cols = st.columns(5)
    cols[0].metric("Pipeline 总金额", _money(bucket.get("pipeline"), primary_currency))
    cols[1].metric("加权 Pipeline", _money(bucket.get("weighted"), primary_currency))
    cols[2].metric("进行中商机", metrics["active_count"])
    cols[3].metric("本月预计成交", _money(bucket.get("closing_month"), primary_currency))
    cols[4].metric("逾期跟进", overdue)
    if len(metrics["by_currency"]) > 1:
        st.caption("金额 KPI 默认显示 USD；其他币种分别统计：" + " · ".join(
            f"{cur} {v['pipeline']:,.0f}" for cur, v in metrics["by_currency"].items() if cur != primary_currency))


def _new_deal():
    with st.expander("＋ 新建商机"):
        customers = db.list_customers()
        choices = {f"{x[1] or x[3] or '未知客户'} · #{x[0]}": x[0] for x in customers}
        if not choices:
            st.info("请先从询盘建立客户档案，再创建独立商机。")
            return
        with st.form("new_pipeline_deal"):
            customer = st.selectbox("客户", list(choices))
            title = st.text_input("商机名称")
            product = st.text_input("产品")
            quantity = st.text_input("数量")
            owner = st.text_input("负责人", "销售")
            if st.form_submit_button("创建商机", type="primary"):
                if not title.strip(): st.error("请填写商机名称")
                else:
                    oid = db.create_opportunity(choices[customer], title.strip(), product.strip(), owner.strip(), details={"quantity": quantity})
                    st.session_state.pipeline_selected = oid
                    st.rerun()


def _card_label(deal):
    health = deal.get("thread_health") or crm.health_of(deal)
    qsum = deal.get("quote_summary") or crm.quote_lifecycle_summary(db.list_quotes(deal["id"]))
    quote_text = qsum.get("label") or "暂无报价"
    if qsum.get("latest_version"):
        quote_text = f"{quote_text} V{qsum['latest_version']}"
    next_text = deal.get("next_action") or "未安排下一步"
    count_text = f" · {deal.get('raw_count', 1)}条记录" if deal.get("raw_count", 1) > 1 else ""
    return (f"#{deal['id']} · {HEALTH_ICON[health['status']]} {deal.get('company') or deal['title']}\n"
            f"{deal.get('product') or '产品待补充'} · {_money(deal.get('amount'), deal.get('currency'))}\n"
            f"{quote_text} · {health['label']}{count_text}\n"
            f"Next: {next_text}")


def _board(deals):
    active_stages = [s for s in crm.STAGES if s not in crm.TERMINAL]
    containers = [{"header": f"{crm.STAGE_CN[s]} · {sum(x.get('stage') == s for x in deals)}",
                   "items": [_card_label(x) for x in deals if x.get("stage") == s]} for s in active_stages]
    original = {x["id"]: x["stage"] for x in deals}
    sorted_containers = sort_items(containers, multi_containers=True, direction="horizontal",
        custom_style=""".sortable-component{background:#f3f5f8;border-radius:10px;padding:8px;min-width:245px}
        .sortable-container-header{font-weight:700;color:#26344d;padding:6px}
        .sortable-item{white-space:pre-line;background:white;color:#172033;border:1px solid #e7eaf0;border-radius:9px;
        box-shadow:0 1px 3px rgba(20,32,60,.08);padding:10px;margin:7px 0;font-size:12px;line-height:1.55;min-height:96px}""")
    for index, container in enumerate(sorted_containers):
        target = active_stages[index]
        for label in container.get("items", []):
            match = re.match(r"#(\d+)", label)
            if match and original.get(int(match.group(1))) != target:
                st.session_state.pipeline_pending = {"id": int(match.group(1)), "target": target}
                st.rerun()


def _list_view(deals):
    rows = []
    for x in deals:
        health = x.get("thread_health") or crm.health_of(x)
        dq = x.get("thread_data_quality") or {"score": "—"}
        qsum = x.get("quote_summary") or crm.quote_lifecycle_summary(db.list_quotes(x["id"]))
        latest_task = x.get("latest_followup") or {}
        rows.append({"客户": x.get("company") or x["title"], "产品": x.get("product"),
            "阶段": crm.STAGE_CN.get(x.get("stage"), x.get("stage")),
            "金额": _money(x.get("amount"), x.get("currency")),
            "报价": (f"{qsum.get('label')} V{qsum.get('latest_version')}" if qsum.get("latest_version") else qsum.get("label")),
            "往来": x.get("raw_count", 1), "负责人": x.get("owner"),
            "健康": health["label"], "数据质量": dq.get("score"),
            "风险": len(x.get("thread_risks") or []),
            "下次跟进": latest_task.get("due_at") or x.get("next_action_at") or "—",
            "Next Action": x.get("next_action") or "—"})
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _transition_dialog(deals):
    pending = st.session_state.get("pipeline_pending")
    if not pending: return
    deal = next((x for x in deals if x["id"] == pending["id"]), db.get_opportunity(pending["id"]))
    target = pending["target"]
    st.warning(f"确认阶段变更：{deal['title']} · {crm.STAGE_CN[deal['stage']]} → {crm.STAGE_CN[target]}")
    flags = {}
    if target == "SAMPLE": flags["skip_sample"] = st.checkbox("本商机无需样品，确认跳过")
    if target == "NEGOTIATION": flags["negotiation_evidence"] = st.checkbox("已与客户进入价格或条款谈判")
    if target == "PO_PENDING": flags["po_signal"] = st.checkbox("客户已表达明确下单意向")
    if target == "WON":
        flags["manual_confirm"] = st.checkbox("我确认该商机已经成交")
        final = st.number_input("最终成交金额", min_value=0.0, value=float(deal.get("amount") or 0))
        po = st.text_input("PO Number（可选）")
        flags["po_received"] = st.checkbox("已收到 PO")
        flags["order_confirmed"] = st.checkbox("订单已由双方确认")
        deal = dict(deal, final_value=final, po_number=po)
    if target == "LOST":
        reason = st.selectbox("输单原因", crm.LOST_REASONS)
        note = st.text_area("补充说明")
        flags["lost_reason"] = reason
        deal = dict(deal, lost_reason=reason)
    activities = db.list_deal_activity(deal["id"])
    quotes = db.list_quotes(deal["id"])
    errors = crm.transition_errors(deal["stage"], target, deal, has_quote=crm.has_sent_quote(quotes),
                                   activity_types=[x["type"] for x in activities], flags=flags)
    if errors: st.error("推进前需补齐：" + "、".join(errors))
    c1, c2, c3 = st.columns([1, 1, 4])
    if c1.button("确认变更", type="primary", disabled=bool(errors)):
        if target == "WON": db.mark_opportunity_won(deal["id"], final, po, "PO 已收到" if flags["po_received"] else "订单已确认")
        elif target == "LOST": db.mark_opportunity_lost(deal["id"], reason, note)
        else: db.move_opportunity_stage(deal["id"], target)
        st.session_state.pipeline_pending = None; st.rerun()
    if errors and target not in crm.TERMINAL and c2.button("人工覆盖"):
        db.move_opportunity_stage(deal["id"], target, reason="人工覆盖阶段准入", manual_override=True)
        st.session_state.pipeline_pending = None; st.rerun()
    if c3.button("取消"): st.session_state.pipeline_pending = None; st.rerun()


def _detail(deal):
    st.markdown(f"### {deal.get('company') or deal['title']}")
    health = deal.get("thread_health") or crm.health_of(deal)
    raw_count = deal.get("raw_count", 1)
    thread_note = f" · 合并 {raw_count} 条往来记录" if raw_count > 1 else ""
    st.caption(f"{HEALTH_ICON[health['status']]} {health['label']} · {crm.STAGE_CN[deal['stage']]} · 负责人 {deal.get('owner') or '未分配'}{thread_note}")
    _quotes = db.list_quotes(deal["id"])
    _activities = db.list_deal_activity(deal["id"])
    _tasks = db.list_followup_tasks(opportunity_id=deal["id"])
    _qsum = deal.get("quote_summary") or crm.quote_lifecycle_summary(_quotes)
    _dq = deal.get("thread_data_quality") or crm.data_quality_score(deal, _quotes, _tasks, [a["type"] for a in _activities])
    _risks = deal.get("thread_risks") or crm.deal_risks(deal, _quotes, _tasks, [a["type"] for a in _activities])
    latest_fu = deal.get("latest_followup")
    follow_text = f" · 下次跟进：{latest_fu.get('due_at') or '未设时间'}" if latest_fu else ""
    st.caption(f"资料完整度 { _dq['score'] }/100 · {_dq['label']} · 报价状态：{_qsum['label']} · 风险 {len(_risks)} 项{follow_text}")
    if _risks:
        st.warning("；".join(f"{r['message']} → {r['fix']}" for r in _risks[:3]))
    section = st.radio("详情", ["概览", "联系人", "需求", "活动", "AI 建议"], horizontal=True, label_visibility="collapsed")
    if section == "概览":
        with st.form(f"deal_edit_{deal['id']}"):
            title = st.text_input("商机名称", deal["title"]); product = st.text_input("产品", deal.get("product") or "")
            amount = st.number_input("金额", min_value=0.0, value=float(deal.get("amount") or 0)); currency = st.selectbox("币种", ["USD","EUR","CNY","GBP"], index=["USD","EUR","CNY","GBP"].index(deal.get("currency") if deal.get("currency") in ["USD","EUR","CNY","GBP"] else "USD"))
            close = st.text_input("预计成交日", deal.get("expected_close") or "", placeholder="YYYY-MM-DD")
            owner = st.text_input("负责人", deal.get("owner") or "销售"); notes = st.text_area("备注", deal.get("notes") or "")
            if st.form_submit_button("保存"):
                db.update_opportunity(deal["id"], {"title":title,"product":product,"amount":amount or None,"currency":currency,"expected_close":close,"owner":owner,"notes":notes}); st.rerun()
        target = st.selectbox("推进到阶段", crm.STAGES, index=crm.STAGES.index(deal["stage"]),
                              format_func=lambda x: f"{crm.STAGE_CN[x]} · {crm.PROBABILITY[x]}%",
                              key=f"detail_stage_{deal['id']}")
        if st.button("验证并推进", type="primary", disabled=target == deal["stage"]):
            st.session_state.pipeline_pending = {"id": deal["id"], "target": target}; st.rerun()
        if deal.get("raw_count", 1) > 1:
            with st.expander("相关历史商机记录", expanded=False):
                for item in deal.get("thread_opportunities", [])[1:8]:
                    st.caption(f"#{item['id']} · {crm.STAGE_CN.get(item.get('stage'), item.get('stage'))} · {item.get('product') or '产品待补充'} · {item.get('updated_at') or item.get('created_at')}")
        with st.expander("报价版本"):
            revise_from = st.session_state.get(f"revise_quote_from_{deal['id']}")
            revise_quote = next((q for q in _quotes if q[0] == revise_from), None)
            with st.form(f"quote_{deal['id']}"):
                default_amount = float((revise_quote[2] if revise_quote else deal.get("amount")) or 0)
                quote_amount = st.number_input("报价金额", min_value=0.0, value=default_amount)
                valid = st.text_input("有效期", value=(revise_quote[4] if revise_quote else "") or "",
                                      placeholder="YYYY-MM-DD")
                sent = st.checkbox("已发送给客户", value=bool(revise_quote))
                if st.form_submit_button("保存新报价版本"):
                    if quote_amount <= 0: st.error("报价金额必须大于 0")
                    else:
                        status = "REVISED" if revise_quote and sent else ("SENT" if sent else "DRAFT")
                        db.create_quote(deal["id"], quote_amount, deal.get("currency") or "USD", valid, status)
                        st.session_state.pop(f"revise_quote_from_{deal['id']}", None)
                        st.rerun()
            for quote in db.list_quotes(deal["id"])[:5]:
                st.caption(f"V{quote[1]} · {quote[3]} {quote[2]:,.0f} · {_quote_status_cn(quote[5])} · 有效至 {quote[4] or '—'}")
                _quote_action_buttons(quote, deal["id"])
    elif section == "联系人":
        st.write(f"联系人：{deal.get('contact') or '—'}"); st.write(f"邮箱：{deal.get('email') or '—'}"); st.write(f"国家：{deal.get('country') or '—'}")
    elif section == "需求":
        keys = [("quantity","数量"),("specification","规格"),("customization","定制要求"),("certification","认证"),("moq","MOQ"),("target_price","目标价"),("incoterm","Incoterm"),("destination","目的地"),("payment_term","付款条款")]
        with st.form(f"requirements_{deal['id']}"):
            values = {k: st.text_input(label, str(deal.get(k) or "")) for k, label in keys}
            if st.form_submit_button("保存需求"): db.update_opportunity(deal["id"], {"details": values}); st.rerun()
    elif section == "活动":
        with st.form(f"activity_{deal['id']}"):
            kind = st.selectbox("活动类型", ACTIVITY_TYPES); note = st.text_area("活动结果")
            if st.form_submit_button("记录活动"):
                db.record_deal_activity(deal["id"], kind, note); st.rerun()
        with st.form(f"next_{deal['id']}"):
            action = st.text_input("Next Activity", deal.get("next_action") or ""); due = st.text_input("时间", deal.get("next_action_at") or "", placeholder="YYYY-MM-DD HH:MM")
            if st.form_submit_button("创建跟进") and action.strip(): db.create_crm_task(deal["id"], action.strip(), due); st.rerun()
        for task in db.list_crm_tasks(deal["id"], open_only=True):
            c1, c2 = st.columns([4, 1]); c1.caption(f"□ {task[2]} · {task[3] or '未设时间'}")
            if c2.button("完成", key=f"pipeline_task_{task[0]}"): db.complete_crm_task(task[0]); st.rerun()
        for item in _activities[:12]: st.caption(f"{item['ts']} · {item['type']} · {item['description']}")
    else:
        nba = crm.recommended_action(deal, health)
        st.info(f"**Recommended Action**\n\n{nba['action']}\n\n**Reason**\n\n{nba['reason']}\n\n**Follow up**: {nba['follow_up']}")
        if st.button("采用建议并创建跟进", type="primary"):
            due = dt.datetime.now().strftime("%Y-%m-%d 17:00")
            db.create_crm_task(deal["id"], nba["action"], due); st.rerun()
        c1, c2 = st.columns(2)
        if c1.button("生成回复", disabled=not bool(deal.get("inquiry_id")),
                     help="打开关联询盘后复用现有 AI 回复区"):
            st.session_state.selected_id = deal["inquiry_id"]
            st.toast("已选中关联询盘，请打开「分析询盘」生成回复")
        c2.button("发送邮件", disabled=True, help="当前项目尚未接入邮箱发送 API")


def render_pipeline_page():
    st.markdown("## Deal Pipeline")
    st.caption("外贸销售机会 · 从询盘验证、需求确认、报价与样品，到谈判、PO 和成交")
    raw_deals = db.list_opportunities()
    deals = resolve_pipeline_deal_threads(raw_deals)
    _kpis(deals); _new_deal()
    raw_total = len(raw_deals)
    if raw_total > len(deals):
        st.caption(f"Pipeline 已按商机聚合：{len(deals)} 个可执行商机 · {raw_total} 条历史记录。")
    shown = _filtered(deals)
    _transition_dialog(deals)
    view = st.segmented_control("视图", ["看板", "列表"], default="看板", key="pipeline_view")
    main, side = st.columns([3.25, 1.15], gap="large")
    with main:
        if not shown: st.info("当前筛选条件下暂无商机。")
        elif view == "看板": _board(shown)
        else: _list_view(shown)
        st.caption("拖动卡片后需确认阶段条件；Won / Lost 始终要求人工确认。")
    with side:
        if shown:
            labels = {f"#{x['id']} · {x.get('company') or x['title']} · {x.get('raw_count', 1)}条记录": x for x in shown}
            selected_label = st.selectbox("打开商机", labels, key="pipeline_deal_picker")
            _detail(labels[selected_label])
        else: st.caption("选择或新建商机后查看详情。")
