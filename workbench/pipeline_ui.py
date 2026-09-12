# -*- coding: utf-8 -*-
"""CRM Sales Pipeline 页面。业务规则在 sales_crm，持久化在 db。"""
from __future__ import annotations

import datetime as dt
import re

import streamlit as st

import db
import progression as prog
import queue_ui as q_ui
import sales_crm as crm

HEALTH_ICON = {"healthy": "🟢", "attention": "🟡", "at_risk": "🔴", "overdue": "🔴", "closed": "⚪"}
HEALTH_CN = {"healthy": "健康", "attention": "需关注", "at_risk": "有风险", "overdue": "已逾期"}

# ROUND 7.1 · Pipeline 只消费 Deal Progression Engine 的输出（§2）。
#   健康度 icon/文案统一来自 progression（三态 HEALTHY/ATTENTION/AT_RISK），
#   不再在 Pipeline 内自算 healthy/overdue 两套口径。
PROG_ICON = prog.HEALTH_ICON
PROG_CN = prog.HEALTH_CN

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
    """同一客户 + 同一 Deal 产品 + 开启/关闭桶 = 一个可执行 Deal Thread。

    ROUND 6.9 §4：直接复用 queue_ui.deal_identity_of —— 商机页与
    侧栏/首页/Deal Detail 必须用同一把 Deal 身份键，不再各自定义口径。
    """
    key = q_ui.deal_identity_of(deal)
    if key:
        return key
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


# ROUND 7.0 §3 · Pipeline 与 Sidebar / 销售工作队列共用同一「代表 Deal 选择」口径
#   排序键：OPEN 跟进任务优先 > 阶段推进最靠前 > 最近更新 > 新 ID（与
#   queue_ui.representative_opp 同口径：见 ROUND 6.9 §4-5 / R23 §3）。
#   这保证三处点击同一个 Deal 卡时打开的是同一个 lead_id —— Deal 身份
#   唯一权威 = 商机层（deal_identity_of），代表 inquiry_id = 商机主关联
#   inquiry_id；Sidebar / 销售队列另外用 representative_opp 做相同事。
_DEAL_REP_TASKS_KEY = "_pipeline_rep_tasks"


def _representative_deal(items: list[dict]) -> dict:
    """选择线程里当前最能代表执行状态的一条 opportunity。

    ROUND 7.0 §3：排序键与 queue_ui.representative_opp 对齐：
      1. 有 OPEN 跟进任务者优先（任何 deal 挂的最近一条任务未完成即可）
      2. 阶段推进最靠前（避开终态惩罚）
      3. 最近更新
      4. 新 ID（断平）
    与 Sidebar / 销售工作队列返回同一 lead inquiry —— 三处点击 Deal 卡
    一定打开同一个 Deal Detail。
    """
    # 汇总线程内全部机会的跟进任务作为排序信号；同一进程内复用一份缓存
    # 避免每次 stage_rank / datetime.strptime 重复 IO（机会列表上限百级）。
    cached = st.session_state.get(_DEAL_REP_TASKS_KEY)
    if cached is None:
        tasks_by_opp = {}
        for x in items or []:
            try:
                tasks_by_opp[x["id"]] = db.list_followup_tasks(opportunity_id=x["id"]) or []
            except Exception:
                tasks_by_opp[x["id"]] = []
        cached = tasks_by_opp
        st.session_state[_DEAL_REP_TASKS_KEY] = tasks_by_opp

    def _has_open_task(x):
        for t in cached.get(x.get("id"), []) or []:
            if str(t.get("status") or "").upper() == "OPEN":
                return True
        return False

    def sort_key(x):
        terminal_penalty = -100 if x.get("stage") in crm.TERMINAL else 0
        return (
            1 if _has_open_task(x) else 0,
            terminal_penalty + _stage_rank(x.get("stage")),
            _parse_dt(x.get("updated_at") or x.get("created_at")),
            int(x.get("id") or 0),
        )
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
    # ROUND 7.1 §2：Pipeline **不**自算进度 —— 一律读 domain 层 progression。
    #   同一 Deal 在 Pipeline / Deal Detail / AI 助手 / Sidebar 看到完全一致的
    #   health / primary_reason / next_activity / recommended_transition。
    try:
        progression = prog.resolve_progression(
            deal, tasks=all_tasks, quotes=all_quotes, activities=all_activities,
            history=db.list_stage_history(rep.get("id")) or [], now=None)
    except Exception:
        progression = {}
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
        "progression": progression,
        # 便捷字段（卡片/排序直接读，避免组件再推导）
        "prog_health": (progression or {}).get("health") or "",
        "prog_health_cn": (progression or {}).get("health_cn") or "",
        "prog_health_icon": (progression or {}).get("health_icon") or "",
        "prog_reason": (progression or {}).get("primary_reason") or "",
        "prog_next": (progression or {}).get("card_next") or "",
        "prog_na_status": (progression or {}).get("next_activity_status") or "",
        "prog_stage_age": (progression or {}).get("stage_age_text") or "",
        "prog_transition": (progression or {}).get("recommended_transition") or "",
        "prog_transition_reason": (progression or {}).get("transition_reason") or "",
        "prog_needs_action": bool((progression or {}).get("needs_action")),
        "prog_exec_key": prog.exec_priority(progression or {}),
    })
    return deal


def resolve_pipeline_deal_threads(raw_deals: list[dict]) -> list[dict]:
    """Pipeline 页面唯一入口：把原始机会聚合为业务员可执行的 Deal Thread。

    ROUND 7.1 §13：默认排序 = 执行优先级（OVERDUE > DUE TODAY > AT_RISK >
    MISSING NEXT ACTIVITY > ATTENTION > WAITING CUSTOMER > SCHEDULED >
    NO ACTION），**不再**只用 updated_at。同一档内用 updated_at / id 断平。
    """
    buckets: dict[tuple, list[dict]] = {}
    for deal in raw_deals or []:
        buckets.setdefault(_deal_thread_key(deal), []).append(deal)
    threads = [_decorate_deal_thread(_representative_deal(items), items)
               for items in buckets.values()]

    def _sort_key(x):
        exec_key = x.get("prog_exec_key") or (7, 0, 0, 0)
        return (exec_key[0],
                -_parse_dt(x.get("updated_at") or x.get("created_at")).toordinal(),
                -int(x.get("id") or 0))
    return sorted(threads, key=_sort_key, reverse=False)

# ==========================================================================
# ROUND 6.9 §1 · Pipeline 默认视图极简化
#   默认只回答一个问题：现在有哪些 Deal、各在什么阶段。
#     阶段列 + Deal 卡 + 搜索 + 快捷视图 + 新建商机
#   AI Score / 国家 / 来源 / 负责人 / 健康度 / 阶段 等进阶筛选
#     全部收进折叠「筛选」，默认不占版面。
#   5 张金额 KPI 卡 → 压成 1 行说明（金额仍可查，只是不再抢占首屏）。
#   点击 Deal 卡 → 共享 Deal Detail（与其它工作区同一个详情）。
#
# ROUND 7.1 §12 · 快捷视图加入「需处理」
#   不重做筛选系统，只把最常用的执行视图提到默认位：
#     全部 / 需处理 / 已逾期 / 高价值
#   「需处理」= ATTENTION / AT_RISK / MISSING next / DUE TODAY / OVERDUE /
#               内部动作待办（由 progression.needs_action 统一判定）。
#   高价值**不删除**（其它地方仍可能依赖）—— 保留在快捷视图里，
#   但不再作为默认推荐位的第一选择；金额不可靠时不会显示误导性加权值。
# ==========================================================================
PIPE_QUICK_VIEWS = ["全部", "需处理", "已逾期", "高价值"]


def _summary(deals):
    """1 行替代 5 张 KPI 卡：进行中 Deal 数 + 加权金额 + 逾期数 + 需处理数。

    ROUND 7.1 §12：金额不可靠（无任何商机填过 amount）时**不显示**加权值，
    避免把"客户目标价 / 估算值"当成本方报价误导销售。改用「N 个需处理」。
    """
    metrics = crm.pipeline_metrics(deals)
    primary = "USD" if "USD" in metrics["by_currency"] else next(iter(metrics["by_currency"]), "USD")
    weighted = (metrics["by_currency"].get(primary) or {}).get("weighted") or 0
    has_amount = any(float(x.get("amount") or 0) > 0
                     for x in deals if x.get("stage") not in crm.TERMINAL)
    overdue = sum((x.get("prog_na_status") or "") == prog.NA_OVERDUE
                  for x in deals if x.get("stage") not in crm.TERMINAL)
    needs = sum(1 for x in deals
                if x.get("prog_needs_action") and x.get("stage") not in crm.TERMINAL)
    parts = [f"{metrics['active_count']} 个进行中 Deal"]
    if has_amount:
        parts.append(f"加权 {primary} {weighted:,.0f}")
    parts.append(f"{needs} 个需处理")
    parts.append(f"逾期跟进 {overdue}")
    st.caption("　·　".join(parts))


def _advanced_filters(deals):
    """进阶筛选：默认折叠，需要时才展开。"""
    with st.expander("筛选", expanded=False):
        c1, c2, c3 = st.columns(3)
        stages = c1.multiselect("阶段", crm.STAGES, format_func=lambda x: crm.STAGE_CN[x],
                                key="pipe_stages")
        owners = c2.multiselect("负责人", sorted({x.get("owner") or "未分配" for x in deals}),
                                key="pipe_owners")
        healths = c3.multiselect("健康度", list(HEALTH_CN), format_func=lambda x: HEALTH_CN[x],
                                 key="pipe_health")
        c4, c5, c6 = st.columns(3)
        countries = c4.multiselect("国家", sorted({x.get("country") for x in deals if x.get("country")}),
                                   key="pipe_country")
        sources = c5.multiselect("来源", sorted({x.get("source") or "未知" for x in deals}),
                                 key="pipe_source")
        min_score = c6.slider("最低 AI Score", 0, 100, 0, key="pipe_score")
    return stages, owners, healths, countries, sources, min_score


def _filtered(deals):
    """默认版面：搜索 + 快捷视图；其余筛选折叠在「筛选」里。"""
    c1, c2 = st.columns([2.1, 2.4])
    with c1:
        query = st.text_input("搜索", placeholder="🔍 客户 / 产品 / 联系人",
                              key="pipe_search", label_visibility="collapsed")
    with c2:
        quick = st.segmented_control("快捷视图", PIPE_QUICK_VIEWS, default="全部",
                                     key="pipe_quick", label_visibility="collapsed") or "全部"
    stages, owners, healths, countries, sources, min_score = _advanced_filters(deals)
    result = []
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
        # ROUND 7.1 §12：快捷视图统一读 progression（不在 UI 里重算）
        if quick == "需处理" and not deal.get("prog_needs_action"): continue
        if quick == "已逾期" and (deal.get("prog_na_status") or "") != prog.NA_OVERDUE: continue
        if quick == "高价值" and float(deal.get("amount") or 0) < 10000: continue
        result.append(deal)
    return result


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


def _pcard_html(deal, health):
    """Pipeline Deal 卡（只读概览）。

    ROUND 7.1 §11 最大层级（不新增模块、不加分数/图表/进度条）：
        客户
        产品 · 数量
        Health + Primary Reason（一句业务原因）
        下一步 · 到期
    健康/原因/下一步全部来自 progression —— 组件不自己推导。
    """
    import html as _h
    p = deal.get("progression") or {}
    hl = p.get("health") or ""
    icon = PROG_ICON.get(hl, "") or HEALTH_ICON.get((health or {}).get("status"), "")
    hl_cn = p.get("health_cn") or (health or {}).get("label") or ""
    reason = p.get("primary_reason") or ""
    na = p.get("next_activity") or ""
    na_status = p.get("next_activity_status") or ""
    next_txt = p.get("card_next") or "未安排下一步"
    qty = str(p.get("resolved_state", {}).get("resolvedRequirement", {}).get("quantity")
              or deal.get("quantity") or "").strip()
    company = _h.escape(str(deal.get("company") or deal.get("title") or ""))
    product = _h.escape(str(deal.get("product") or "产品待补充"))
    # 阶段停留（§5.2：只给紧凑文本，详细说明进 Deal Detail）
    age = p.get("stage_age_text") or ""
    age_txt = f" · {_h.escape(age)}" if age and age != "今天" else ""
    # 推荐阶段迁移（§9：只提示，不自动执行）
    tr = p.get("recommended_transition_cn") or ""
    tr_html = (f"<div class='nx tr'>建议推进至 {_h.escape(tr)}</div>"
               if tr else "")
    na_cls = " nx" + (" od" if na_status == prog.NA_OVERDUE
                      else (" miss" if na_status == prog.NA_MISSING else ""))
    # 鼠标悬停显示完整挂牌信息（与 _card_label 同一口径，供快速核对）
    tip = (_h.escape(_card_label(deal)).replace("\n", " · ")
           .replace("'", "&#39;").replace('"', "&quot;"))
    return (f"<div class='pcard clickable {hl.lower() or (health or {}).get('status','')}' title='{tip}'>"
            f"<div class='co'>{company}</div>"
            f"<div class='pd'>{product}{(' · ' + _h.escape(qty)) if qty else ''}</div>"
            f"<div class='amt'>{_money(deal.get('amount'), deal.get('currency'))}</div>"
            f"<div class='meta'>{icon} {_h.escape(str(hl_cn))}{age_txt}</div>"
            f"<div class='rsn'>{_h.escape(reason)}</div>"
            f"<div class='nx{na_cls[3:]}'>下一步：{_h.escape(next_txt)}</div>"
            + tr_html +
            "</div>")


def _board(deals, open_deal):
    """阶段看板：左→右 = 推进顺序；点卡片进 Deal Detail。

    ROUND 6.9 §1：卡片可点击（复用全局 .clickable 覆盖层），保留
    拖拽排序会让卡片无法点击，因此拖动改阶段的能力移除 ——
    阶段推进统一在 Deal Detail / 「商机管理」折叠区完成。
    """
    active_stages = [s for s in crm.STAGES if s not in crm.TERMINAL]
    cols = st.columns(len(active_stages), gap="small")
    for col, stage in zip(cols, active_stages):
        rows = [x for x in deals if x.get("stage") == stage]
        col.markdown(f"<div class='phead'>{crm.STAGE_CN[stage]}"
                     f"<span>{len(rows)}</span></div>", unsafe_allow_html=True)
        if not rows:
            col.caption("—")
        for deal in rows[:6]:
            health = deal.get("thread_health") or crm.health_of(deal)
            col.markdown(_pcard_html(deal, health), unsafe_allow_html=True)
            col.button("", key=f"pipe_open_{deal['id']}", use_container_width=True,
                       on_click=open_deal, args=(deal.get("inquiry_id"),),
                       help=f"打开 #{deal['id']} · {deal.get('company') or deal['title']} 的 Deal Detail")
        if len(rows) > 6:
            col.caption(f"…另有 {len(rows) - 6} 个")


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


def render_pipeline_page(open_deal=None):
    """Pipeline 页（ROUND 6.9 §1：默认视图 = Deal，不是筛选器）。

    首屏 = 阶段列 + Deal 卡 + 搜索 + 快捷视图 + 新建商机；
    进阶筛选折叠；报价版本 / 阶段推进折叠在「商机管理」里。
    """
    st.markdown("## Deal Pipeline")
    st.caption("外贸销售机会 · 从询盘验证、需求确认、报价与样品，到谈判、PO 和成交")
    raw_deals = db.list_opportunities()
    deals = resolve_pipeline_deal_threads(raw_deals)
    _summary(deals)
    _new_deal()
    if len(raw_deals) > len(deals):
        st.caption(f"已按 Deal 聚合：{len(deals)} 个可执行商机 · {len(raw_deals)} 条历史记录。")
    shown = _filtered(deals)
    if not shown:
        st.info("当前条件下暂无商机。")
    else:
        _board(shown, open_deal or _default_open)
        st.caption("点卡片打开 Deal Detail（与其它工作区同一个详情）。")
    _transition_dialog(deals)
    with st.expander("🛠 商机管理（报价版本 · 阶段推进）", expanded=False):
        if shown:
            labels = {f"#{x['id']} · {x.get('company') or x['title']} · {x.get('raw_count', 1)}条记录": x
                      for x in shown}
            selected_label = st.selectbox("打开商机", labels, key="pipeline_deal_picker")
            _detail(labels[selected_label])
        else:
            st.caption("选择或新建商机后查看详情。")


def _default_open(inquiry_id):
    """未注入跳转回调时的兜底：至少把该询盘选中，供其它区域读取。"""
    if inquiry_id:
        st.session_state.selected_id = inquiry_id
