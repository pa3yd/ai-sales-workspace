# -*- coding: utf-8 -*-
"""
AI 外贸询盘分析 · 工作台（Streamlit 版）
把原来的命令行 Agent 套上一个网页界面 + SQLite 数据库，
实现：粘贴询盘 -> 调用已有引擎分析 -> 自动存库 -> 历史/客户/产品/导出 全管理。

复用关系：
  app.py 只负责"界面 + 存储"，分析大脑完全来自 ../main.py 的
  build_engine() 和 analyze()，不重写一行分析逻辑。
"""
import sys
import os
import datetime
from io import BytesIO

# 把父目录(AI询盘agent)加入搜索路径，这样能 import main 和 agent.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import db as _db_mod
# 自愈式 reload（同 queue_ui）：Streamlit 只热重载 app.py，旧进程内存里的 db
# 模块可能是旧版本（例如第十轮给 list_customers 加了 contacts 列 → 12 列），
# 直接 from db import 会拿到旧 11 列版导致 ValueError: not enough values to unpack。
# 除功能探测外，还检查版本标记 R10_CONTACTS（db.py 每轮加列时同步升级）。
if not (hasattr(_db_mod, "record_activity") and hasattr(_db_mod, "update_draft")
        and hasattr(_db_mod, "R16_DEAL_PIPELINE") and hasattr(_db_mod, "record_deal_activity")
        and hasattr(_db_mod, "R18_FOLLOWUP") and hasattr(_db_mod, "update_followup_task")
        and hasattr(_db_mod, "reconcile_duplicate_opportunities")):
    try:
        import importlib as _il_d
        _db_mod = _il_d.reload(_db_mod)
    except Exception:
        pass
from db import (    init_db, save_inquiry, list_inquiries, get_inquiry, delete_inquiry,
    list_customers, customer_inquiries, delete_customer, dump_inquiries,
    update_inquiry_note, get_inquiry_note, update_inquiry_info,
    update_customer_grade, update_customer_note,
    STATUS_TODO, STATUS_DONE,
    update_inquiry_status, get_inquiry_status, get_inquiry_priority_inputs,
    get_customer_email_by_inquiry,
    count_by_status, calc_priority, GRADE_POINT,
    PRI_HIGH, PRI_MID, PRI_LOW, PRI_DONE,
    # 第七轮：销售执行闭环
    record_activity, list_activity, get_workflow,
    record_activity_once,
    mark_replied, set_follow_up, complete_follow_up, set_deal, update_biz_status,
    # Phase 2：草稿保存（REPLY_EDITED 事件由调用方记录）
    update_draft,
    # CRM v1：独立商机、报价版本、阶段历史与任务
    list_opportunities, get_opportunity, update_opportunity, move_opportunity_stage,
    list_stage_history, list_quotes, create_quote, list_crm_tasks,
    create_crm_task, complete_crm_task, backfill_opportunities, reconcile_duplicate_opportunities,
)
import catalog
import gapcheck
import queue_ui as _ui
import email_unified as _emu
# 自愈式 reload：Streamlit 只热重载 app.py，旧进程里缓存的 queue_ui 模块可能
# 缺少最新轮次新增的函数（第十七轮 group_work_items / priority_tier，
# 第六轮 group_customers / wait / customer_key），页面会报 AttributeError。
# 发现缺函数就从磁盘重新加载一次，旧进程无需重启。
if not (hasattr(_ui, "group_work_items") and hasattr(_ui, "group_customers")):
    try:
        import importlib as _il_q
        _ui = _il_q.reload(_ui)
    except Exception:
        pass
# 第七轮：销售执行闭环（业务状态机 + Next Action）
import workflow as _wf
if not hasattr(_wf, "next_action"):
    try:
        import importlib as _il_w
        _wf = _il_w.reload(_wf)
    except Exception:
        pass

# 第十轮：客户级商机（Opportunity）派生视图（纯函数，不写任何业务数据）
import crm as _crm
if not hasattr(_crm, "opportunity_of"):
    try:
        import importlib as _il_c
        _crm = _il_c.reload(_crm)
    except Exception:
        pass

import sales_crm as _crm_core
# Streamlit 热重载 app.py 时可能继续持有旧阶段配置；主动探测并刷新。
if not (hasattr(_crm_core, "PIPELINE_STAGES")
        and "REQUIREMENT_CONFIRMED" in getattr(_crm_core, "STAGE_CN", {})):
    try:
        import importlib as _il_crm_core
        _crm_core = _il_crm_core.reload(_crm_core)
    except Exception:
        pass
import pipeline_ui as _pipeline_ui
if not (hasattr(_pipeline_ui, "render_pipeline_page")
        and getattr(_pipeline_ui, "crm", None) is _crm_core):
    try:
        import importlib as _il_pipeline
        _pipeline_ui = _il_pipeline.reload(_pipeline_ui)
    except Exception:
        pass

# ROUND 7.1：Deal Progression Engine（领域层，无 Streamlit 依赖）
import progression as _pg
if not (hasattr(_pg, "resolve_progression") and hasattr(_pg, "progression_of")):
    try:
        import importlib as _il_pg
        _pg = _il_pg.reload(_pg)
    except Exception:
        pass

# Phase 2：Customer + Opportunity Workspace 纯逻辑层（无 Streamlit 依赖）
import workspace as _wsx
if not hasattr(_wsx, "build_timeline"):
    try:
        import importlib as _il_x
        _wsx = _il_x.reload(_wsx)
    except Exception:
        pass

# Phase 3：AI Priority + Next Best Action 纯逻辑层（无 Streamlit 依赖）
import priority3 as _p3
if not hasattr(_p3, "analyze"):
    try:
        import importlib as _il_p
        _p3 = _il_p.reload(_p3)
    except Exception:
        pass

# Phase 4：Sales Execution Queue 纯逻辑层（队列构建 / 动作路由 / 去重判断）
import execution as _ex
if not hasattr(_ex, "build_exec_queue"):
    try:
        import importlib as _il_e
        _ex = _il_e.reload(_ex)
    except Exception:
        pass

# —— agent.insight 常量统一在此导入一次 ——
# 注意：Streamlit 只热重载 app.py，不会重载已 import 的模块。如果运行中 agent/insight.py
# 被更新，旧进程里的模块对象会缺新常量（如 CERT_STATUS_CN），页面会报 ImportError。
# 这里做一次"自愈式"检查：发现缺常量就 reload 模块，保证旧进程也能拿到最新定义。
import importlib as _il
import agent.insight as _insight_mod
if not hasattr(_insight_mod, "CERT_STATUS_CN"):
    try:
        _insight_mod = _il.reload(_insight_mod)
    except Exception:
        pass
STATUS_DESC = getattr(_insight_mod, "STATUS_DESC", {})
ACTION_CN = getattr(_insight_mod, "ACTION_CN", {})
CERT_STATUS_CN = getattr(_insight_mod, "CERT_STATUS_CN", {})
SOURCE_CN = getattr(_insight_mod, "SOURCE_CN", {})
# 第五轮第二次补丁 03：需求语义五态标签（旧进程缺常量时给空表兜底）
REQ_STATE_CN = getattr(_insight_mod, "REQ_STATE_CN", {})
_scn = getattr(_insight_mod, "STATUS_CN", {})
_ns = getattr(_insight_mod, "normalize_status", None) or (lambda s: s)
_ACN = ACTION_CN

# 客户关键信息：工作台的核心字段（顺序即展示顺序）
KEY_FIELDS = [
    ("country", "🌍 客户国家/地区"),
    ("company", "🏢 客户公司"),
    ("website", "🔗 公司网址"),
    ("email", "📧 联系邮箱"),
    ("contact_name", "👤 联系人"),
]

# Phase 2：blocker 字段英文 key → 中文（展示 Pipeline「缺失信息」用；无映射原样显示）
FIELD_CN = {
    "product": "产品/品类", "product_category": "产品类别", "product_model": "产品型号",
    "reference_model": "参考型号", "product_spec": "产品规格", "spec": "规格",
    "quantity": "数量", "customization": "定制要求", "destination": "目的地",
    "certification": "认证", "delivery": "交期", "packaging": "包装",
    "payment": "付款方式", "incoterm": "贸易术语", "email": "邮箱",
    "company": "公司信息", "contact": "联系人", "logo": "Logo/贴牌",
}


def _field_cn(field: str) -> str:
    """blocker 字段 → 中文（无映射原样返回，不编造）。"""
    return FIELD_CN.get(str(field or ""), str(field or ""))

# ---- 处理状态与优先级（计算逻辑在 db.py，界面只管显示）----
STATUS_ICON = {STATUS_TODO: "🔵", STATUS_DONE: "✅"}


def _row_has_draft(row) -> bool:
    """该行报告里是否已有回复草稿（第十轮修复：db._derive_need 现已派生
    has_draft；旧格式行无 need 字段时回落 False，不炸）。"""
    try:
        need = row[11] if len(row) > 11 else {}
        return bool((need or {}).get("has_draft"))
    except Exception:
        return False


# —— ROUND 7.2：load_queue 轮次级缓存 ——
# app.py 顶层每次 rerun 重跑，同一轮里 load_queue(None) 会被多处重复调用。
# 缓存键含「数据版本号」，任何写路径 bump 版本即失效，不会读到陈旧数据。
_LOAD_QUEUE_CACHE: dict = {}
_QUEUE_VERSION = {"v": 0}
# ROUND 7.2：客户档案页的「客户 id → 跟进任务」映射（只依赖商机与任务，
# 与搜索词无关）。同轮 rerun 内复用，写路径 bump 版本号即失效。
_FU_OF_CUST_CACHE: dict = {}
# ROUND 7.2：活动流缓存（Inquiry / Deal 两种键），返回**副本**，
# 避免调用方就地修改污染缓存（调用方会做 acts += [...]）。
_ACTIVITY_CACHE: dict = {}


def _cached_activity(dbmod, kind, obj_id):
    """按 (kind, obj_id, 数据版本) 复用活动流查询；未命中则查询并缓存副本。"""
    if obj_id is None:
        return []
    key = (kind, obj_id, _queue_version())
    hit = _ACTIVITY_CACHE.get(key)
    if hit is not None:
        return list(hit)
    if kind == "deal":
        fresh = list(dbmod.list_deal_activity(obj_id) or [])
    else:
        fresh = list(dbmod.list_activity(obj_id) or [])
    _ACTIVITY_CACHE[key] = list(fresh)
    return list(fresh)


def _queue_version() -> int:
    return _QUEUE_VERSION["v"]


def _bump_queue_version():
    """数据可能已变化时调用（新建/回复/任务/阶段变更后）。"""
    _QUEUE_VERSION["v"] += 1
    _LOAD_QUEUE_CACHE.clear()
    _FU_OF_CUST_CACHE.clear()
    _ACTIVITY_CACHE.clear()


def load_queue(status=None):
    """取出询盘并按优先级排好队，返回字典列表。
    ROUND 7.2 性能：同一轮 rerun 内 `load_queue(None)` 会被侧栏 / 首页 /
    跟进台 / 详情等多处各调一次，每次都全量重算 need 派生 + AI Priority
    （可测的侧栏切换卡顿来源之一）。这里加**轮次级缓存**：以
    (status, 数据版本号) 为键，同一轮内重复调用直接复用同一份结果。
    任何写操作（init_db / 新建询盘 / 回复 / 任务变更）都会 bump 版本号，
    因此不会读到陈旧数据。
    """
    key = (status, _queue_version())
    cached = _LOAD_QUEUE_CACHE.get(key)
    if cached is not None:
        return cached
    rows = list_inquiries(status)
    items = []
    for row in rows:
        # 兼容新旧两种返回格式（11 列 = 旧库函数 / 12 列 = 含 need 派生字段），
        # 避免 Streamlit 长驻进程未重载 db 模块时解包报错
        (id_, created, grade, score, country, company, contact, stt, cg,
         clg, urgency) = row[:11]
        need = row[11] if len(row) > 11 else {}
        # customer_id 追加在末尾（第 13 位），旧格式库/旧进程无此列时为 None，
        # 客户聚合自动降级为「规范化公司名」口径
        cust_id = row[12] if len(row) > 12 else None
        # 第七轮：闭环字段（biz_status/last_replied_at/follow_up_at/…），
        # 旧格式库无此列时为空 dict → 业务状态自动从现有数据派生
        wf = row[13] if len(row) > 13 else {}
        pri, pts = calc_priority(cg, grade, score, urgency, stt)
        it = dict(id=id_, created=created, grade=grade, score=score,
                  country=country, company=company, contact=contact,
                  status=stt, cust_grade=cg, pri=pri, pts=pts,
                  urgency=urgency, need=need or {}, cust_id=cust_id, wf=wf)
        # 业务状态 + Next Action（spec 三/四）：统一在此计算，队列/详情/建议共用同一口径
        it["biz"] = _wf.derive_biz(stt, (need or {}).get("blockers") or [],
                                   (need or {}).get("readiness") or "",
                                   wf.get("biz_status"),
                                   _row_has_draft(row))
        _act = _wf.next_action(
            stt, it["biz"], (need or {}).get("blockers") or [],
            (need or {}).get("readiness") or "",
            has_draft=_row_has_draft(row), follow_up_at=wf.get("follow_up_at"),
            follow_up_done=bool(wf.get("follow_up_done")),
            intent=(need or {}).get("intent") or "")
        it["action"] = _act
        it["fu_state"] = _wf.followup_state(wf.get("follow_up_at"),
                                            wf.get("follow_up_done"))
        # Phase 3：AI Priority + Queue Score（纯读派生，展示/排序用；never 回写）
        try:
            _p3r = _p3.analyze(it, _act)
            it["aip3"] = _p3r["aip"]
            it["qs3"] = _p3r["qs"]
        except Exception:
            it["aip3"], it["qs3"] = None, None
        items.append(it)
    items.sort(key=lambda x: (-x["pts"], -x["id"]))
    _LOAD_QUEUE_CACHE.clear()          # 只保留最近一个版本，避免无界增长
    _LOAD_QUEUE_CACHE[key] = items
    return items

init_db()
# —— ROUND 7.2：启动期数据修复只在每个进程跑一次 ——
# app.py 在模块顶层执行，Streamlit 每次 rerun（含每次侧栏点击）都会重跑
# 整段脚本。backfill / reconcile 属于**一次性数据迁移**，不是渲染逻辑：
#   · backfill_opportunities 旧版会把 10 条已吸附到 Deal 的往来永远判为
#     「未关联」，每次 rerun 重跑 ~85ms（可测到的侧栏切换卡顿来源），
#     并反复重写 details_json / updated_at；
#   · reconcile_duplicate_opportunities 每次 rerun 全表扫描 + 分组。
# 用模块级哨兵把这两步收敛到「本进程首次运行时」，行为对已有数据完全等价
# （两次调用都幂等），但不再随每次交互重复付出成本。
try:
    if not globals().get("_STARTUP_MAINTENANCE_DONE"):
        backfill_opportunities()
        reconcile_duplicate_opportunities()
        globals()["_STARTUP_MAINTENANCE_DONE"] = True
except Exception:
    # 数据维护失败不应阻断工作台渲染（与原行为一致：原代码也没有兜底）。
    pass


def _deal_identity_map(opportunities=None) -> dict:
    """询盘 → Deal 权威身份键（来自商机记录）。

    ROUND 6.9 §4：Deal 身份由源逻辑（商机层）决定。客户把同一个产品写成
    不同短语、或把 5,000 修订成 3,000 时，商机层已经收敛为一条记录 ——
    侧栏 / 首页 / 详情必须沿用同一把键，不允许再按每封询盘的产品文本各自
    分组（那等于前端二次去重，同一个 Deal 会裂成多行）。

    返回 {inquiry_id: ("deal", 客户键, 产品签名, "open"/"closed")}。
    """
    mapping = {}
    opps = opportunities if opportunities is not None else (list_opportunities() or [])
    for opp in opps:
        key = _ui.deal_identity_of(opp)
        if not key:
            continue
        details = opp.get("details") or {}
        ids = [opp.get("inquiry_id"), details.get("current_inquiry_id")]
        ids += list(details.get("related_inquiry_ids") or [])
        for iid in ids:
            if iid:
                mapping[iid] = key
    return mapping


# 工具函数：供产品库按钮的 on_click 使用（必须在调用前定义）
def _set_edit(pid):
    st.session_state.edit_pid = pid


def _del_product(pid):
    catalog.delete_product(pid)
    get_engine.clear()
    if st.session_state.get("edit_pid") == pid:
        st.session_state.edit_pid = None


def _set_status(iid, status):
    """切换某条询盘的处理状态（on_click 回调，执行后 Streamlit 自动重跑页面）"""
    update_inquiry_status(iid, status)


# —— 第七轮：销售执行闭环回调 ——
FU_OPTIONS = ["24小时", "48小时", "3天", "7天", "自定义"]


def _fu_time_from_choice(iid) -> str | None:
    """把跟进时间选择换算成具体时间字符串；自定义解析失败返回 None。"""
    import datetime as _dt
    pick = st.session_state.get(f"fu_pick_{iid}") or "48小时"
    now = _dt.datetime.now()
    if pick == "自定义":
        raw = (st.session_state.get(f"fu_custom_{iid}") or "").strip()
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return _dt.datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
            except Exception:
                continue
        return None
    hours = {"24小时": 24, "48小时": 48, "3天": 72, "7天": 168}.get(pick, 48)
    return (now + _dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")


def _mark_replied(iid):
    """「标记为已发送」：业务状态 → REPLIED + 记录时间与事件（不发真实邮件）"""
    mark_replied(iid)
    _opp = _opportunity_for_inquiry(iid)
    if _opp:
        _db_mod.record_deal_activity(
            _opp["id"], "CUSTOMER_EMAIL_SENT", "客户邮件已确认发送",
            actor="销售", metadata={"inquiry_id": iid})


def _do_set_followup(iid):
    at = _fu_time_from_choice(iid)
    if at:
        set_follow_up(iid, at)
        _opp = _opportunity_for_inquiry(iid)
        if _opp:
            try:
                _db_mod.create_followup_task(
                    _opp["id"], "MANUAL_FOLLOWUP", due_at=at,
                    title="跟进客户", next_action="按计划跟进客户",
                    actor="销售", reuse=True)
            except Exception:
                _db_mod.record_deal_activity(
                    _opp["id"], "FOLLOW_UP_CREATED",
                    f"计划跟进：{at}", actor="销售",
                    metadata={"inquiry_id": iid, "due_at": at})
        st.session_state[f"fu_msg_{iid}"] = f"跟进已设：{at}"
    else:
        st.session_state[f"fu_msg_{iid}"] = "自定义时间格式应为 2026-09-08 10:00"


def _do_complete_followup(iid):
    complete_follow_up(iid)
    _opp = _opportunity_for_inquiry(iid)
    if _opp:
        _tasks = _db_mod.list_followup_tasks(
            opportunity_id=_opp["id"], active_only=True) or []
        if _tasks:
            for _t in _tasks:
                _db_mod.update_followup_task(
                    _t["id"], fu_status="COMPLETED", actor="销售")
        else:
            _db_mod.record_deal_activity(
                _opp["id"], "FOLLOW_UP_COMPLETED", "跟进完成",
                actor="销售", metadata={"inquiry_id": iid})


def _do_mark_deal(iid, result):
    """成交 / 丢单（UI 已做人工二次确认，这里只落库）"""
    set_deal(iid, result)
    _opp = _opportunity_for_inquiry(iid)
    if _opp:
        if result == "WON":
            try:
                _db_mod.mark_opportunity_won(
                    _opp["id"], _opp.get("amount") or 0,
                    confirmation="人工确认成交", actor="销售")
            except Exception:
                move_opportunity_stage(_opp["id"], "WON",
                                       actor="销售", reason="人工确认成交",
                                       manual_override=True)
        else:
            try:
                _db_mod.mark_opportunity_lost(
                    _opp["id"], "人工确认丢单", actor="销售")
            except Exception:
                move_opportunity_stage(_opp["id"], "LOST",
                                       actor="销售", reason="人工确认丢单",
                                       manual_override=True)


def _opportunity_for_inquiry(iid):
    """询盘 → 当前商机。只读查找，不创建新记录。

    合并后的 Deal 会把历史消息保存在 details.related_inquiry_ids；
    任一历史询盘执行回复/报价/跟进，都必须落到同一个 Deal。
    """
    try:
        for _o in list_opportunities() or []:
            _details = _o.get("details") or {}
            _ids = {_o.get("inquiry_id"), _details.get("current_inquiry_id")}
            _ids.update(_details.get("related_inquiry_ids") or [])
            if iid in _ids:
                return _o
    except Exception:
        return None
    return None


def _quote_amount_from_context(info: dict, matches: list) -> float:
    """从现有提取字段和产品库匹配估算报价金额；无法估算则为 0。"""
    try:
        qty = float((info or {}).get("quantity") or 0)
    except Exception:
        qty = 0.0
    unit = 0.0
    if matches:
        try:
            lo, hi = (matches[0].get("price_range") or [0, 0])[:2]
            unit = (float(lo or 0) + float(hi or 0)) / 2
        except Exception:
            unit = 0.0
    return round(qty * unit, 2) if qty and unit else 0.0


def _record_quote_draft(iid, info: dict, matches: list, summary: str,
                        qty_txt: str, valid_until: str = ""):
    """创建报价草稿闭环：Quote(DRAFT) + Deal Timeline + Inquiry Timeline。

    草稿不是已发送报价，因此不自动推进 Pipeline 到 QUOTED。
    """
    amount = _quote_amount_from_context(info, matches)
    currency = (info or {}).get("target_price_currency") or "USD"
    _opp = _opportunity_for_inquiry(iid)
    version = None
    if _opp:
        try:
            amount_for_quote = amount if amount > 0 else float(_opp.get("amount") or 0)
        except Exception:
            amount_for_quote = amount
        version = create_quote(_opp["id"], amount_for_quote, currency, valid_until, "DRAFT")
        _db_mod.update_opportunity(
            _opp["id"],
            {"next_action": "发送报价",
             "details": {"quotation_readiness": "DRAFT_CREATED"}})
    record_activity(
        iid, "QUOTE_CREATED",
        f"创建报价草稿：{summary} · {qty_txt}",
        actor="销售",
        result=(f"报价草稿 v{version} 已保存" if version else "报价草稿已保存"))
    return version


def _execute_primary_action(iid, resolved_action: str, section_key: str = None):
    """主 CTA 执行入口：记录动作意图并打开既有工作区，不改变布局。"""
    _opp = _opportunity_for_inquiry(iid)
    if _opp:
        labels = {
            "MATCH_PRODUCT": "打开产品匹配",
            "CHECK_SUPPLIER_CAPABILITY": "检查供应能力",
            "CHECK_PRODUCT_OPTIONS": "检查产品方案",
            "PREPARE_PRODUCT_RECOMMENDATION": "准备产品推荐",
            "PREPARE_QUOTATION": "准备创建报价",
            "UPDATE_QUOTATION": "更新报价",
            "CONFIRM_REQUIREMENT_CHANGE": "确认需求变更",
            "CHECK_INTERNAL_QUOTATION_PREREQUISITES": "检查报价条件",
            "PREPARE_UPDATED_QUOTATION": "创建更新报价",
            "FOLLOW_UP": "执行跟进",
            "SEND_REPLY": "准备客户邮件",
            "CLARIFY_REQUIREMENT": "确认客户需求",
        }
        _db_mod.record_deal_activity(
            _opp["id"], "NEXT_ACTION_OPENED",
            labels.get(resolved_action, "执行下一步"),
            actor="销售",
            metadata={"inquiry_id": iid, "action": resolved_action})
        if resolved_action in ("CHECK_SUPPLIER_CAPABILITY", "CHECK_PRODUCT_OPTIONS", "PREPARE_PRODUCT_RECOMMENDATION"):
            try:
                _db_mod.create_crm_task(
                    _opp["id"], labels.get(resolved_action, "检查产品方案"), due_at="",
                    owner="销售")
                _db_mod.update_opportunity(
                    _opp["id"],
                    {"next_action": labels.get(resolved_action, "检查产品方案"),
                     "details": {"supplier_capability_status": "NEEDS_CHECK"}})
            except Exception:
                pass
    if section_key:
        st.session_state[section_key] = True
    elif resolved_action in ("MATCH_PRODUCT", "CHECK_SUPPLIER_CAPABILITY", "CHECK_PRODUCT_OPTIONS", "PREPARE_PRODUCT_RECOMMENDATION"):
        _goto_products()


def _do_set_stage(iid, dst):
    """第十一轮（spec 五）：销售阶段手动修改——AI 建议为默认值，人工可改。
    只允许合法转换（workflow.can_transition）；写入后记录 STATUS_CHANGE 事件，
    Timeline 不伪造历史。"""
    try:
        _src = (get_workflow(iid) or {}).get("biz_status") or ""
    except Exception:
        _src = ""
    cn = _wf.BIZ_CN.get(dst, dst)
    if _src and _src != dst and not _wf.can_transition(_src, dst):
        st.session_state[f"stage_msg_{iid}"] = (
            f"状态机不允许 {_wf.BIZ_CN.get(_src, _src)} → {cn}；"
            "请按销售流程逐步推进（或先转为暂缓）")
        return
    update_biz_status(iid, dst)
    record_activity(iid, "STATUS_CHANGE", f"手动调整销售阶段：{cn}")
    st.session_state[f"stage_msg_{iid}"] = f"已更新销售阶段：{cn}"


def _stage_change(iid):
    """selectbox on_change 包装：回调时 key 已注册，安全读取新值。"""
    dst = st.session_state.get(f"stage_pick_{iid}")
    if dst:
        _do_set_stage(iid, dst)


def _at_cn(t: str) -> str:
    """业务事件类型 → 中文名（Timeline 展示用，不伪造未发生的事件）。"""
    return {"ANALYZED": "AI完成询盘分析", "REPLY_GENERATED": "生成客户回复",
            "REPLY_EDITED": "销售修改回复", "REPLIED": "标记为已发送",
            "FOLLOW_UP_CREATED": "创建跟进任务", "FOLLOW_UP_COMPLETED": "完成跟进",
            "QUOTE_CREATED": "创建报价", "QUOTE_SENT": "报价已发送",
            "STATUS_CHANGE": "销售阶段变更",
            "WON": "成交 🏆", "LOST": "丢单",
            "FOLLOW_UP_SNOOZED": "稍后提醒", "FOLLOW_UP_RESCHEDULED": "跟进改期",
            "FOLLOW_UP_WAITING": "等待客户回复", "FOLLOW_UP_REOPENED": "重开跟进",
            "FOLLOW_UP_EMAIL_DRAFTED": "生成跟进邮件", "FOLLOW_UP_SENT": "跟进已发送",
            "FOLLOW_UP_CANCELLED": "取消跟进", "CUSTOMER_REPLIED": "客户回复",
            "DEAL_CREATED": "新建商机"}.get(t, t)


def _set_filter(v):
    """切换侧边栏询盘队列的筛选（纯 UI 状态，供「AI 今日建议」按钮复用）"""
    st.session_state.queue_filter = v


def _set_saved_view(name: str):
    """Sales Inbox 的保存视图仅改变展示筛选，不修改任何业务数据。"""
    st.session_state.sales_saved_view = name


def _filter_rows(items, flt):
    """队列筛选口径（第十轮抽出共用：侧边栏 chips / AI 行动队列同一套规则）。"""
    if flt == "待回复":
        return [x for x in items if x.get("biz") == _wf.READY_TO_REPLY]
    if flt == "待补关键信息":
        return [x for x in items if x.get("biz") == _wf.NEEDS_INFO]
    if flt == "高优先级":
        return [x for x in items
                if x["status"] == STATUS_TODO and x["pri"] == PRI_HIGH]
    if flt == "已回复":
        return [x for x in items if x.get("biz") == _wf.REPLIED]
    if flt == "待跟进":
        return [x for x in items
                if x.get("biz") == _wf.FOLLOW_UP
                or (x.get("action") or {}).get("type")
                in ("FOLLOW_UP_CUSTOMER", "CREATE_FOLLOW_UP")]
    if flt == "待报价":
        return [x for x in items
                if x["status"] == STATUS_TODO and x.get("biz") == _wf.READY_FOR_QUOTE]
    if flt == "超48小时":
        _sline = (datetime.datetime.now()
                  - datetime.timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
        return [x for x in items
                if x["status"] == STATUS_TODO and str(x.get("created") or "") <= _sline]
    return list(items)


# —— 第十轮（spec 八）：AI 建议行动队列（Action Queue）——
# Phase 4 升级：Sales Execution Queue 闭环——
# AI Priority → Next Best Action → Execute → Activity → Update State
# → Recalculate → Rebuild Queue → Next Customer
def _enter_actq(flt):
    """开始处理 → 按当前 Filter + Sort + Status 生成 Current Queue（spec 一）。
    flt=None 表示沿用侧边栏当前筛选。"""
    st.session_state.pop("exec_error", None)
    st.session_state.pop("actq_done_all", None)
    st.session_state["actq_entered"] = True
    st.session_state.actq_flt = flt or st.session_state.get("queue_filter") or "全部"
    st.session_state.queue_filter = st.session_state.actq_flt   # 侧栏筛选同步
    st.session_state.actq_sort = (st.session_state.get("queue_sort")
                                  or _ex.SORT_DEFAULT)
    try:
        _items = load_queue(None)
    except Exception:
        _items = []
    st.session_state.actq_ids = _ex.build_exec_queue(
        _items, st.session_state.actq_flt, st.session_state.actq_sort)
    st.session_state.actq_idx = 0


def _actq_nav(delta):
    """行动队列 上一个 / 下一个。"""
    st.session_state.actq_idx = max(
        0, min(st.session_state.get("actq_idx", 0) + delta,
               max(len(st.session_state.get("actq_ids") or []) - 1, 0)))


def _actq_skip():
    """稍后处理 → 跳到下一条（最后一条则留在原地）。"""
    _ids = st.session_state.get("actq_ids") or []
    _i = st.session_state.get("actq_idx", 0)
    if _i < len(_ids) - 1:
        st.session_state.actq_idx = _i + 1


def _actq_done(iid):
    """标记完成 → 该询盘转「已处理」（退出待办），并推进到下一条。"""
    update_inquiry_status(iid, STATUS_DONE)
    record_activity(iid, "NOTE", "行动队列：标记完成")
    _actq_skip()


def _actq_open(iid, section_key=None):
    """查看客户 / 生成回复 → 打开该询盘详情（可指定展开区域）。"""
    st.session_state.selected_id = iid
    st.session_state.analyzed = None
    if section_key:
        st.session_state[section_key] = True


def _actq_close():
    st.session_state.pop("actq_ids", None)
    st.session_state.pop("actq_idx", None)
    st.session_state.pop("exec_error", None)
    st.session_state.pop("actq_done_all", None)
    st.session_state.pop("actq_entered", None)


# —— Phase 4（spec 四）：执行完成 → 更新状态 → 重算 → 下一条 ——
# 完成语义按路由区分；全部复用既有闭环函数（mark_replied / complete_follow_up /
# update_inquiry_status），重复点击由「业务状态守卫 + record_activity_once 去重」防住。
_EXEC_TERMINAL_BIZ = ("REPLIED", "QUOTED", "NEGOTIATING", "WON", "LOST")


def _exec_do_complete(iid: int, route_key: str):
    """执行完成（spec 四.1-8）：保存 Activity、更新询盘/阶段/状态/Last Contact。
    动态经 db 模块取函数（测试可注入 API Error）。抛异常 = 操作失败。"""
    import db as _dbm
    _wfrow = get_workflow(iid) or {}
    _biz = _wfrow.get("biz_status") or ""
    if route_key in ("reply", "collect_info"):
        # 发送回复：REPLIED + last_replied_at + status 已处理（重复发送守卫）
        if _biz not in _EXEC_TERMINAL_BIZ:
            _dbm.mark_replied(iid)
    elif route_key in ("follow_up", "schedule_follow_up"):
        # 跟进完成：follow_up_done=1 + FOLLOW_UP_COMPLETED（重复完成守卫）
        if not _wfrow.get("follow_up_done"):
            _dbm.complete_follow_up(iid)
        _dbm.record_activity_once(iid, "NOTE", "执行队列：完成跟进",
                                  result="跟进已闭环")
    elif route_key == "mark_complete":
        if _biz not in ("WON", "LOST"):
            update_inquiry_status(iid, STATUS_DONE)
        _dbm.record_activity_once(iid, "NOTE", "执行队列：标记完成",
                                  result="退出待办")
    elif route_key == "negotiate":
        _dbm.record_activity_once(iid, "MEETING", "执行队列：谈判记录",
                                  result="谈判推进")
    # create_quote / match_product：完成动作发生在工作区
    # （创建报价 → Quote DRAFT + Timeline；产品匹配 → 引导产品库），
    # 此处只负责推进队列，不重复落库（防重复 Activity / 重复报价）。


def _exec_advance(iid: int, route_key: str):
    """完成当前条 → 重算 AI Priority（load_queue 全量重派生）→ 按进入时的
    Filter+Sort 重建队列 → 选下一条最高优先级 → 自动打开（spec 五）。
    任何一步失败：不跳下一条，记 exec_error 供 [重试]（spec 六）。"""
    st.session_state.pop("exec_error", None)          # 重试前清掉旧错误
    st.session_state["exec_busy"] = True
    try:
        try:
            _exec_do_complete(iid, route_key)
        except Exception as e:
            st.session_state["exec_error"] = {
                "iid": iid, "route": route_key, "msg": str(e)[:180]}
            return                       # spec 六：失败不跳到下一条
        flt = st.session_state.get("actq_flt") or "全部"
        srt = st.session_state.get("actq_sort") or _ex.SORT_DEFAULT
        try:
            items = load_queue(None)     # Recalculate AI Priority（重派生）
        except Exception as e:
            st.session_state["exec_error"] = {
                "iid": iid, "route": route_key, "msg": str(e)[:180]}
            return
        ids = _ex.build_exec_queue(items, flt, srt)   # Rebuild Queue
        nxt = next((x for x in ids if x != iid), None)
        if nxt is None:
            # 最后一条：退出执行模式，给出处理完毕提示
            st.session_state.pop("actq_ids", None)
            st.session_state.pop("actq_idx", None)
            st.session_state["actq_done_all"] = True
            st.toast("🎉 执行队列处理完毕")
        else:
            st.session_state.actq_ids = ids
            st.session_state.actq_idx = ids.index(nxt)
            _open_inquiry(nxt)           # 自动打开下一条
    finally:
        st.session_state["exec_busy"] = False


def _exec_retry():
    """[重试]：重新执行上一次失败的操作（含完成 + 推进）。"""
    err = st.session_state.get("exec_error") or {}
    if err.get("iid") is not None:
        _exec_advance(err["iid"], err.get("route") or "reply")


def _exec_save_negotiation(iid: int):
    """NEGOTIATE 路由内联表单：保存谈判记录（幂等）+ 已报价时可推进到谈判。"""
    txt = (st.session_state.get(f"exec_neg_{iid}") or "").strip()
    if not txt:
        st.session_state[f"exec_neg_msg_{iid}"] = "请先填写谈判内容"
        return
    try:
        record_activity_once(iid, "MEETING", f"谈判记录：{txt[:80]}",
                             result="谈判推进")
        _biz = (get_workflow(iid) or {}).get("biz_status") or ""
        if _biz == "QUOTED" and _wf.can_transition(_biz, "NEGOTIATING"):
            update_biz_status(iid, "NEGOTIATING")
            record_activity_once(iid, "STATUS_CHANGE", "进入谈判阶段")
        st.session_state[f"exec_neg_msg_{iid}"] = "✅ 谈判记录已保存"
    except Exception as e:
        st.session_state[f"exec_neg_msg_{iid}"] = ("操作失败，当前记录未完成。"
                                                   + str(e)[:80])


# —— Phase 2 · Workspace 操作回调 ——
def _ws_save_draft(iid):
    """「保存草稿修改」：把草稿文本框内容写回 report_json.draft，
    并记 REPLY_EDITED 活动（生成回复的修改也必须有迹可循）。"""
    txt = st.session_state.get(f"draft_box_{iid}", "")
    ok = update_draft(iid, txt)
    if ok:
        record_activity(iid, "REPLY_EDITED", "保存回复草稿修改",
                        result="草稿已更新")
        _opp = _opportunity_for_inquiry(iid)
        if _opp:
            _db_mod.record_deal_activity(
                _opp["id"], "EMAIL_DRAFT_SAVED",
                "客户邮件草稿已保存", actor="销售",
                metadata={"inquiry_id": iid})
        st.toast("草稿修改已保存并记入 Timeline ✏️")
        st.rerun()
    else:
        st.toast("保存失败：未找到该询盘")


def _open_inquiry(iid):
    """点击询盘卡片 → 主工作区切换到该询盘（只切换查看对象，不重新分析）"""
    st.session_state.selected_id = iid
    st.session_state.analyzed = None


def _toggle_group(gkey):
    """展开 / 收起同客户聚合组（纯 UI 状态）"""
    s = st.session_state.setdefault("sq_open", set())
    if gkey in s:
        s.discard(gkey)
    else:
        s.add(gkey)


def _toggle_deal(gkey):
    """展开 / 收起某张 Deal 主卡的历史往来（第二十二轮，纯 UI 状态）"""
    s = st.session_state.setdefault("sbdeal_open", set())
    if gkey in s:
        s.discard(gkey)
    else:
        s.add(gkey)


def _set_deal_open(gkey, opened):
    """明确设置 Deal 历史往来展开状态，避免 open/close 按钮重跑时互相翻转。"""
    s = st.session_state.setdefault("sbdeal_open", set())
    if opened:
        s.add(gkey)
    else:
        s.discard(gkey)


def _nav_queue(delta):
    """主工作区「上一个 / 下一个」：沿当前筛选+排序后的队列移动，不重新分析"""
    ids = st.session_state.get("queue_ids") or []
    cur = st.session_state.get("selected_id")
    if cur in ids:
        i = ids.index(cur) + delta
        if 0 <= i < len(ids):
            _open_inquiry(ids[i])


def _done_and_next(cur_id):
    """AI Sales Workspace Phase A（spec 十一）：「完成并进入下一条」完整事务——
    ① 按当前 NBA Action Type 落完成动作（保存 Activity + 更新状态/阶段/
       Last Contact，复用执行队列 _exec_do_complete，内部幂等守卫防重复点击）
    ② Recalculate：load_queue 全量重派生（AI Priority / Queue Score 刷新）
    ③ 沿当前队列进入下一条。
    任一步失败：不移动、置 ws_exec_error 供界面提示（不假成功）；
    失败后可直接重点按钮重试（防重复守卫保证幂等）。"""
    st.session_state.pop("ws_exec_error", None)
    st.session_state["exec_busy"] = True
    try:
        items = load_queue(None)
        it = next((x for x in items if x.get("id") == cur_id), None)
        route = _ex.route_of(((it or {}).get("action") or {}).get("type"))
        _exec_do_complete(cur_id, route)      # Activity + Status + Last Contact
        load_queue(None)                       # Recalculate AI Priority
    except Exception as e:
        st.session_state["ws_exec_error"] = {"iid": cur_id, "msg": str(e)[:180]}
        return                                 # 失败：停在当前客户，不进入下一条
    finally:
        st.session_state["exec_busy"] = False
    ids = st.session_state.get("queue_ids") or []
    if cur_id in ids:
        i = ids.index(cur_id) + 1
        if i < len(ids):
            _open_inquiry(ids[i])
    st.toast("✅ 已完成，正在进入下一条…")


# —— NBA Hero 主 CTA 文案（spec 四.6：主按钮文案随 Action Type 动态切换）——
# 值：(主按钮文案, 打开的工作区 section 模板)；None section = 特殊路由（见 hero 渲染）。
# 第二十一轮：email 类动作（reply/collect_info/follow_up）统一收敛为
# 「生成客户邮件」，不再让业务员在 追问/回复/跟进 三套之间做选择。
_NBA_CTA = {
    "reply": ("✉️ 生成客户邮件", "ui_mail_{id}"),
    "collect_info": ("✉️ 生成客户邮件", "ui_mail_{id}"),
    "follow_up": ("✉️ 生成客户邮件", "ui_mail_{id}"),
    "create_quote": ("💰 创建报价", "ui_quote_{id}"),
    "schedule_follow_up": ("⏰ 设置跟进时间", "ui_fuzone_{id}"),
    "negotiate": ("🤝 进入谈判处理", "ui_quote_{id}"),
    # match_product → 引导产品库（_goto_products）；mark_complete → 直接完成推进
}


def _clear_search():
    """清空队列搜索框（空状态里的「清除搜索」按钮）"""
    st.session_state.inbox_search = ""


def _goto_products():
    """提示前往产品库标签页（Streamlit 的 st.tabs 无法编程切换，只做引导）"""
    st.toast("请切换到下方「📦 产品库」标签页查看")


def _biz_status(status: str, blockers: list, readiness: str, wf: dict | None = None):
    """业务状态 Badge（第七轮升级为 workflow 口径，不改两态数据模型）：
    人工保存过的业务状态（REPLIED/QUOTED/WON…）优先，其余从现有数据派生。
    返回 (css色调, 状态文字, 状态emoji)
    """
    biz = _wf.derive_biz(status, blockers or [], readiness or "",
                         (wf or {}).get("biz_status"))
    tone, emoji = _wf.BIZ_STYLE.get(biz, ("blue", "🔵"))
    return tone, _wf.BIZ_CN.get(biz, biz), emoji


def _get_llm_client():
    """按需取一个 LLM client（用于 AI 润色追问邮件）。没 Key 就返回 None。"""
    if st.session_state.get("llm_client") is not None:
        return st.session_state.llm_client
    try:
        from agent.llm_client import load_api_key
        key, _ = load_api_key()
        if not key:
            return None
        _, _, cl = get_engine("auto")   # auto 模式：无 Key 会降级为规则，不会中断
        st.session_state.llm_client = cl
        return cl
    except Exception:
        return None


URGENCY_CN = {"high": "高", "medium": "中", "low": "低"}


def _aip_band(aip):
    """AI Priority 分段语义（V8.1 spec 五）：>=80 Danger / 50-79 Brand / <50 Neutral。
    返回 CSS band 类名；数字本身始终为主要信息（仅用于微色标与底衬）。"""
    try:
        v = int(aip)
    except (TypeError, ValueError):
        return ""
    return "band-hi" if v >= 80 else ("band-md" if v >= 50 else "band-lo")
GRADE_COLOR = {"A": "🔴", "B": "🟠", "C": "🟢", "D": "⚪"}
RESP_TIME = {"A": "4 小时内回复", "B": "24 小时内回复",
             "C": "48 小时内回复", "D": "可先归档培养"}
SELLER = {}
try:
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "config.json"), encoding="utf-8") as f:
        cfg = __import__("json").load(f)
        SELLER = cfg.get("SELLER", {})
except Exception:
    SELLER = {}


# ===================== 全局样式（专业 · 紧凑 · B2B CRM 观感） =====================
# 只调视觉，不改任何业务逻辑。颜色尽量走 Streamlit 主题变量，深浅色主题都成立。
PAGE_CSS = """
<style>
/* —— AI Sales Workspace 设计 Token（统一设计基座，全部组件逐步迁移到 var()）—— */
:root {
  --app-bg: #F5F7FA;
  --sidebar-bg: #F1F4F8;
  --surface-1: #FFFFFF;
  --surface-2: #F8FAFC;
  --surface-hover: #F1F6FF;
  --surface-selected: #EAF2FF;
  --text-primary: #172033;
  --text-secondary: #475569;
  --text-muted: #64748B;
  --text-disabled: #94A3B8;
  --border-default: #E2E8F0;
  --border-subtle: #EEF2F6;
  --border-focus: #93C5FD;
  --brand-500: #2563EB;
  --brand-600: #1D4ED8;
  --brand-700: #1E40AF;
  --ai-bg: #F4F8FF;
  --ai-bg-strong: #EAF2FF;
  --ai-border: #D7E5FF;
  --ai-text: #1E3A8A;
  --success: #16A34A;
  --success-bg: #F0FDF4;
  --warning: #D97706;
  --warning-bg: #FFFBEB;
  --danger: #DC2626;
  --danger-bg: #FEF2F2;
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --shadow-card: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 12px rgba(15, 23, 42, 0.04);
}
/* —— NBA Hero（Action-First：主 CTA 区，spec 四）—— */
.nba-hero { background: var(--ai-bg); border: 1px solid var(--ai-border);
  border-radius: var(--radius-lg); box-shadow: var(--shadow-card);
  padding: .62rem .9rem .58rem; margin: 0 0 .5rem; }
.nba-hero .ttl { font-size: .68rem; font-weight: 800; letter-spacing: .1em;
  color: var(--ai-text); text-transform: uppercase; opacity: .85; }
.nba-hero .what { font-size: 1.05rem; font-weight: 800;
  color: var(--text-primary); margin: .12rem 0 .08rem; }
.nba-hero .why { font-size: .78rem; color: var(--text-secondary); line-height: 1.45; }
/* —— 信息密度：压缩大面留白，拉宽工作区 —— */
.block-container { padding-top: .9rem; padding-bottom: 1.5rem; max-width: 1560px; }
[data-testid="stSidebar"] .block-container { padding-top: .7rem; }
h1 { font-size: 1.42rem !important; letter-spacing: .2px; margin-bottom: .15rem !important; }
h3 { font-size: 1.02rem !important; }
[data-testid="stMetric"] { padding: .1rem 0; }
[data-testid="stMetricLabel"] p { font-size: .72rem !important; opacity: .8; }
[data-testid="stMetricValue"] { font-size: 1.18rem; }
.stTabs [data-baseweb="tab"] { padding: .32rem 1.15rem; font-weight: 600; }
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 10px; }
div[data-testid="stExpander"] details { border-radius: 10px; }
[data-testid="stAlert"] { padding: .4rem .8rem; border-radius: 8px; }
.stButton button { font-weight: 600; }
.stDownloadButton button { font-weight: 600; }

/* —— 层级标签：业务结果/任务 > AI 明细 > 详情，权重递减 —— */
.zone-label {
  display: inline-block; font-size: .72rem; font-weight: 800;
  letter-spacing: .1em; opacity: .9; margin: .55rem 0 .3rem;
  padding: .24rem .6rem; border-radius: 6px;
  background: rgba(128,128,128,.12); border-left: 3px solid var(--primary-color,var(--brand-500));
}
.zone-label.lv1 { background: rgba(37,99,235,.14); border-left-color: var(--primary-color,var(--brand-500)); }
.zone-label.lv2 { background: rgba(217,119,6,.14); border-left-color: var(--warning); }
.zone-label.lv3 { background: rgba(22,163,74,.1); border-left-color: var(--success); opacity: .85; }
.zone-label.lv4 { background: rgba(100,116,139,.08); border-left-color: var(--text-muted); opacity: .9; }

/* 第五级：元数据 / 原始数据（弱化折叠区） */
.zone-label.lv5 { background: rgba(100,116,139,.04); border-left-color: var(--text-disabled); opacity: .65; font-weight: 500; }

/* —— 顶部 KPI 数字卡：数字 + 状态 + 一句说明 ——
   第十一轮（spec 三）：KPI 降权——高度压低、字号缩小，主要空间让给当前询盘 —— */
.kpi {
  border: 1px solid rgba(128,128,128,.22); border-radius: 8px;
  padding: .28rem .7rem .32rem; height: 100%;
  background: rgba(128,128,128,.04);
  box-shadow: none;
}
.kpi .t { font-size: .68rem; font-weight: 600; opacity: .72; letter-spacing: .02em; }
.kpi .n { font-size: .92rem; font-weight: 700; line-height: 1.15;
         font-variant-numeric: tabular-nums; margin-top: .02rem; }
.kpi .s { font-size: .68rem; opacity: .85; margin-top: .01rem; }
.kpi.red   { border-color: rgba(220,38,38,.45); box-shadow: inset 3px 0 0 rgba(220,38,38,.9); }
.kpi.red .n   { color: var(--danger); }
.kpi.amber { border-color: rgba(217,119,6,.4); box-shadow: inset 3px 0 0 var(--warning); }
.kpi.amber .n { color: var(--warning); }
.kpi.green { border-color: rgba(22,163,74,.4); box-shadow: inset 3px 0 0 var(--success); }
.kpi.green .n { color: var(--success); }
.kpi.blue  { box-shadow: inset 3px 0 0 var(--primary-color,var(--brand-500)); }
.kpi.blue .n  { color: var(--primary-color,var(--brand-500)); }

/* —— 业务结果结论卡 —— */
.card {
  border: 1px solid rgba(128,128,128,.18); border-radius: 10px;
  padding: .42rem .7rem .5rem; height: 100%;
  background: rgba(128,128,128,.05);
}
.card .t { font-size: .7rem; opacity: .68; letter-spacing: .03em; }
.card .v { font-size: 1.06rem; font-weight: 700; margin: .08rem 0 .05rem; line-height: 1.3; }
.card .s { font-size: .73rem; opacity: .85; }
.card.red .v   { color: var(--danger); }
.card.amber .v { color: var(--warning); }
.card.green .v { color: var(--success); }
.card.blue .v  { color: var(--primary-color,var(--brand-500)); }

/* —— 高优先级询盘：整条横幅压到最重 —— */
.pri-banner {
  border: 1px solid rgba(220,38,38,.45); border-left: 4px solid var(--danger);
  border-radius: 8px; background: rgba(220,38,38,.08);
  color: var(--danger); padding: .35rem .75rem; margin: .15rem 0 .3rem;
  font-weight: 700; font-size: .88rem;
}
.pri-banner small { font-weight: 500; opacity: .85; }

/* —— 客户关键信息小卡片（克制，绿=已识别/黄=缺失） —— */
.cust-chip {
  border: 1px solid rgba(128,128,128,.2); border-radius: 8px;
  padding: .3rem .55rem; height: 100%;
  background: rgba(128,128,128,.04);
}
.cust-chip .t { font-size: .66rem; opacity: .66; }
.cust-chip .v { font-size: .84rem; font-weight: 600; line-height: 1.28;
                word-break: break-all; margin-top: .05rem; }
.cust-chip.ok { border-color: rgba(22,163,74,.4); background: rgba(22,163,74,.06); }
.cust-chip.miss { border-style: dashed; border-color: rgba(217,119,6,.55);
                  background: rgba(217,119,6,.05); }
.cust-chip.miss .v { color: var(--warning); }

/* —— AI 摘要（放大突出：业务员一眼抓关键信息） —— */
.sum-hero {
  background: rgba(37,99,235,.05); border: 1px solid rgba(37,99,235,.28);
  border-left: 4px solid var(--brand-500);
  border-radius: 8px; padding: .55rem .9rem; margin: .42rem 0 .5rem;
  box-shadow: none;
}
.sum-hero .lbl { font-size: .68rem; font-weight: 700; color: var(--brand-500);
                 letter-spacing: .1em; margin-bottom: .15rem; text-transform: uppercase; }
.sum-hero .txt { font-size: 1.18rem; line-height: 1.65; font-weight: 600;
                 color: inherit; letter-spacing: -.005em; }
.sum-hero .hl { color: var(--danger); font-weight: 800; padding: 0 .08em; }
.sum-hero .conf { font-size: .74rem; opacity: .55; font-weight: 400;
                  margin-left: .45rem; }
.ok-tip { font-size: .8rem; opacity: .8; margin: .2rem 0; }

/* —— 跟进台队列卡：按优先级着色，高优一眼可见 —— */
.qc {
  border: 1px solid rgba(128,128,128,.2); border-radius: 10px;
  padding: .42rem .75rem .5rem; margin: .12rem 0 .1rem;
  background: rgba(128,128,128,.05);
}
.qc.high { border-color: rgba(220,38,38,.55); border-left: 5px solid var(--danger);
           background: rgba(220,38,38,.07); }
.qc.mid  { border-left: 5px solid var(--warning); }
.qc.low  { border-left: 5px solid var(--success); }
.qc.done { opacity: .62; border-left: 5px solid var(--text-disabled); }
.qc .row1 { display: flex; align-items: center; gap: .55rem; flex-wrap: wrap; }
.qc .badge { font-weight: 700; white-space: nowrap; font-size: .9rem; }
.qc.high .badge { color: var(--danger); }
.qc.mid .badge  { color: var(--warning); }
.qc.low .badge  { color: var(--success); }
.qc .who { font-weight: 700; font-size: .95rem; }
.qc .meta { font-size: .76rem; opacity: .72; margin-top: .12rem; }
.qc .row1 .meta { margin-top: 0; opacity: .8; font-size: .78rem; }

/* —— 顶栏右侧公司徽标 —— */
.brand-box { text-align: right; padding-top: .5rem; }
.brand-box .co { font-size: .88rem; font-weight: 700; opacity: .95; }
.brand-box .st { font-size: .72rem; opacity: .6; margin-top: .1rem; }

/* —— 综合评分块 —— */
.score-box {
  border: 1px solid rgba(128,128,128,.2); border-radius: 10px;
  padding: .45rem .7rem; height: 100%; background: rgba(128,128,128,.05);
  text-align: center;
}
.score-box .t { font-size: .7rem; opacity: .68; }
.score-box .n { font-size: .95rem; font-weight: 700; line-height: 1.2;
                font-variant-numeric: tabular-nums; }
.advice { background: rgba(128,128,128,.06); border-left: 3px solid
          var(--primary-color,var(--brand-500)); border-radius: 6px;
          padding: .4rem .7rem; font-size: .88rem; height: 100%; }

/* —— Level 1 询盘头区：chip 状态 + 客户名 + 判断/下一步，排版主导不用大卡片 —— */
.inq-head { padding: .05rem 0 .1rem; }
.inq-head .chips { display: flex; gap: .4rem; flex-wrap: wrap; margin-bottom: .3rem; }
.chip { display: inline-block; font-size: .74rem; font-weight: 700;
        padding: .13rem .6rem; border-radius: 999px;
        border: 1px solid rgba(128,128,128,.25); background: rgba(128,128,128,.07); }
.chip.high  { color: var(--danger); border-color: rgba(220,38,38,.45); background: rgba(220,38,38,.07); }
.chip.mid   { color: var(--warning); border-color: rgba(217,119,6,.4); background: rgba(217,119,6,.06); }
.chip.low   { color: var(--text-muted); }
.chip.amber { color: var(--warning); border-color: rgba(217,119,6,.4); background: rgba(217,119,6,.07); }
.chip.green { color: var(--success); border-color: rgba(22,163,74,.4); background: rgba(22,163,74,.07); }
.inq-head .cname { font-size: 1.55rem; font-weight: 750; line-height: 1.18;
                   letter-spacing: -.01em; margin-top: .05rem; }
.inq-head .cmeta { font-size: .8rem; opacity: .7; margin-top: .08rem; }
/* —— Level 1 · Next Action：业务员当前最该做的一件事，字重字号仅次于结论 —— */
.nextstep { font-size: 1.04rem; margin: .35rem 0 .3rem; padding: .45rem .75rem;
            background: rgba(37,99,235,.05); border-left: 4px solid var(--brand-500);
            border-radius: 6px; line-height: 1.55; font-weight: 600; }
.nextstep b { opacity: .68; font-weight: 700; color: var(--brand-600); font-size: .78rem;
              letter-spacing: .08em; margin-right: .3rem; }
/* 优先级 chip：优先级文字为主，等级/分数只作辅助（不让分数喧宾夺主） */
.chip.pri { font-size: .82rem; padding: .16rem .7rem; }
.chip.pri small.sc { font-weight: 500; opacity: .68; margin-left: .4rem; font-size: .7rem; }
.chip.blue { color: var(--primary-color,var(--brand-500)); border-color: rgba(37,99,235,.4);
             background: rgba(37,99,235,.06); }

/* —— 关键数字一行带：hairline 分隔，弱化为参考指标 —— */
.metric-strip {
  display: flex; flex-wrap: wrap; gap: .35rem 1.7rem;
  padding: .42rem .15rem; margin: .4rem 0 .5rem;
  border-top: 1px solid rgba(128,128,128,.16);
  border-bottom: 1px solid rgba(128,128,128,.16);
}
.metric-strip .mi { font-size: .8rem; }
.metric-strip .mi label { display: block; font-size: .66rem; opacity: .6; }
.metric-strip .mi b { font-size: .95rem; font-variant-numeric: tabular-nums; }
.metric-strip .mi small { opacity: .65; }

/* —— 缺失信息三级行：阻塞 > 建议确认 > 可选，颜色和重量递减 —— */
.gapline.blk {
  border-left: 4px solid var(--danger); background: rgba(220,38,38,.06);
  padding: .45rem .7rem .5rem; margin: .28rem 0;
  border-radius: 6px; font-weight: 700; font-size: .92rem; color: var(--danger);
  line-height: 1.55;
}
/* 阻塞块：标题行 + 逐项列出（每项独立一行，一眼数清缺什么） */
.gapline .hd { font-size: .76rem; font-weight: 700; opacity: .8;
               letter-spacing: .03em; margin-bottom: .05rem; }
.gapline ul.blk-list { list-style: none; margin: .1rem 0 0; padding: 0; }
.gapline ul.blk-list li { font-size: .96rem; font-weight: 700; line-height: 1.5;
                          padding: .14rem 0 .14rem .1rem; }
.gapline ul.blk-list li + li { border-top: 1px dashed rgba(220,38,38,.22); }
.gapline.sug {
  border-left: 3px solid var(--warning); background: rgba(217,119,6,.04);
  padding: .32rem .55rem; margin: .2rem 0;
  border-radius: 6px; font-size: .86rem; color: var(--warning);
  line-height: 1.5; font-weight: 500;
}
.gapline.opt {
  font-size: .78rem; opacity: .55; padding: .1rem 0 .1rem .55rem;
  margin: .08rem 0; font-weight: 400;
}

/* —— 侧边栏商机卡（询盘队列） ——
   信息层级：业务状态 > 客户 > 询盘主题·核心需求 > 国家·等待时长 > AI评分 */
.sq { border: 1px solid rgba(128,128,128,.18); border-left: 3px solid var(--text-disabled);
      border-radius: 8px; padding: .38rem .55rem .42rem;
      margin: .35rem 0 .1rem; background: rgba(128,128,128,.04); cursor: pointer; }
.sq .r1 { display: flex; gap: .4rem; align-items: center; min-width: 0; }
.sq .sdot { width: 8px; height: 8px; border-radius: 50%;
            flex: 0 0 auto; background: var(--text-disabled); }
.sq .stx { font-size: .72rem; font-weight: 800; white-space: nowrap; color:var(--text-muted); }
.sq .tier { font-size: .68rem; font-weight: 850; letter-spacing: .02em; margin-right: .3rem; }
.sq .co { display: block; font-weight: 700; font-size: .88rem; margin-top: .12rem;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sq .r2 { font-size: .73rem; margin-top: .16rem; overflow: hidden;
          text-overflow: ellipsis; white-space: nowrap; }
.sq .need { font-weight: 600; opacity: .85; }
.sq .meta { opacity: .6; font-weight: 400; }
.sq .next { opacity: .92; font-weight: 400; color: var(--brand-500); }
.sq .next b { font-weight: 700; }
.sq .iid { font-size: .68rem; opacity: .8; }
.sq .score { text-align: right; font-size: .7rem; opacity: .55; margin-top: .18rem; }
/* Phase 3：卡片 AI Priority / Queue Score 徽标 */
.sq .score .aip { display: inline-block; font-weight: 800; opacity: 1;
  color: var(--brand-500); background: rgba(37,99,235,.08); border-radius: 5px;
  padding: 0 .3rem; margin-right: .25rem; font-size: .74rem; }
.sq .score .qs { display: inline-block; font-weight: 700; opacity: .9;
  color: var(--brand-600); background: rgba(15,23,42,.08); border-radius: 5px;
  padding: 0 .3rem; font-size: .7rem; }
/* Phase 3：Header AI Priority 大数字 */
.ws-aip { font-size: 1.4rem; font-weight: 900; color: var(--brand-500); line-height: 1.1; }
.ws-aipu { opacity: .55; font-size: .72rem; margin-left: .1rem; }
.ws-aipg { margin-left: .4rem; font-size: .8rem; opacity: .8; font-weight: 700; }
/* Phase 3：AI Priority 评分解释卡 */
.aip-card { border: 1px solid rgba(37,99,235,.28); border-radius: 10px;
  padding: .42rem .62rem .34rem; margin: .25rem 0 .35rem;
  background: rgba(37,99,235,.05); }
.aip-card .hd { display: flex; align-items: baseline; gap: .5rem; }
.aip-card .hd .n { font-size: 1.28rem; font-weight: 900; color: var(--brand-500); }
.aip-card .hd .u { font-size: .72rem; opacity: .6; }
.aip-card .hd .lvl { font-size: .74rem; opacity: .9; }
.aip-lst { list-style: none; margin: .28rem 0 .12rem; padding: 0; font-size: .78rem; }
.aip-lst li { padding: .1rem 0; border-bottom: 1px dashed rgba(128,128,128,.12); }
.aip-lst li:last-child { border-bottom: none; }
.aip-lst .sgn { display: inline-block; min-width: 2.1em; font-weight: 800;
  font-variant-numeric: tabular-nums; }
.aip-lst .up { color: var(--success); }     /* 加分项（绿） */
.aip-lst .dn { color: var(--danger); }     /* 减分项（红） */
.aip-lst .na { color: var(--text-disabled); }
.aip-lst .dim { opacity: .55; font-size: .7rem; }
.aip-fml { font-size: .66rem; opacity: .62; margin-top: .14rem;
  word-break: break-all; }
.nba-chip { font-size: .8rem; margin: .15rem 0 .2rem; }
.nba-chip b { color: var(--brand-600); }
.nba-reason { font-size: .7rem; opacity: .72; margin-bottom: .25rem; }
.sales-brief { background: var(--surface-1); border: 1px solid var(--border-subtle);
  border-radius: 8px; padding: .62rem .72rem; margin: .35rem 0 .48rem;
  box-shadow: var(--shadow-card); }
.sales-brief .summary { font-size: .88rem; line-height: 1.45; color: var(--text-secondary);
  max-width: 92ch; margin-bottom: .48rem; }
.sales-grid { display: grid; grid-template-columns: 1.25fr 1.25fr 1fr 1fr 1.35fr;
  gap: .42rem; align-items: stretch; }
.sales-fact { border: 1px solid var(--border-subtle); border-radius: 7px;
  padding: .38rem .5rem; background: #fff; min-height: 64px; }
.sales-fact .lb { font-size: .62rem; text-transform: uppercase; font-weight: 800;
  color: var(--text-muted); letter-spacing: .04em; }
.sales-fact .v { font-size: .88rem; font-weight: 800; line-height: 1.25;
  margin-top: .1rem; color: var(--text-primary); word-break: break-word; }
.sales-fact .s { font-size: .68rem; color: var(--text-secondary); margin-top: .08rem; }
.status-row { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: .42rem;
  margin: .42rem 0; }
.status-tile { border: 1px solid var(--border-subtle); border-radius: 7px;
  padding: .38rem .5rem; background: #fff; }
.status-tile .lb { font-size: .62rem; color: var(--text-muted); font-weight: 800;
  text-transform: uppercase; }
.status-tile .v { font-size: .82rem; font-weight: 850; margin-top: .1rem; }
.status-tile.green .v { color: var(--success); }
.status-tile.amber .v { color: var(--warning); }
.status-tile.blue .v { color: var(--brand-600); }
.next-action-simple { border: 1px solid var(--ai-border); border-left: 3px solid var(--brand-500);
  background: #F7FAFF; border-radius: 8px; padding: .5rem .62rem; margin-top: .42rem; }
.next-action-simple .eyebrow { font-size: .62rem; letter-spacing: .08em; font-weight: 850;
  color: var(--brand-600); text-transform: uppercase; }
.next-action-simple .action { font-size: 1rem; font-weight: 850; margin: .08rem 0 .16rem; }
.next-action-simple .reason { font-size: .76rem; color: var(--text-secondary); line-height: 1.45; }
.missing-simple { display: flex; flex-wrap: wrap; gap: .3rem; margin: .42rem 0 .2rem; }
.missing-simple .tag { border: 1px solid rgba(217,119,6,.35); color: var(--warning);
  background: var(--warning-bg); border-radius: 999px; padding: .12rem .46rem;
  font-size: .72rem; font-weight: 750; }
.missing-simple .ok { border-color: rgba(22,163,74,.35); color: var(--success);
  background: var(--success-bg); }
.task-list { display: grid; grid-template-columns: repeat(auto-fit,minmax(190px,1fr));
  gap: .35rem; margin: .42rem 0 .15rem; }
.task-item { border: 1px solid var(--border-subtle); border-radius: 7px;
  padding: .34rem .48rem; background: #fff; font-size: .78rem; }
.task-item .t { font-weight: 800; }
.task-item .m { font-size: .68rem; color: var(--text-secondary); margin-top: .06rem; }
@media (max-width: 1000px) { .sales-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .status-row { grid-template-columns: 1fr; } }
/* 客户聚合：折叠组头 + 展开后的子卡缩进（同一客户多条询盘时才出现） */
.sq.ghead { border-style: dashed; opacity: .96; }
/* 第二十三轮 §6：历史（未选中的子卡）= 灰 —— 蓝只表达选中/活跃，
   红/橙只表达紧迫(逾期/风险/高优)，不混用 */
.sq.child { margin-left: 1rem; }
.sq.child:not(.sel) { border-left-color: var(--text-disabled);
  background: var(--surface-2); opacity: .92; }
.sq.child:not(.sel) .sdot { background: var(--text-disabled); }
.sq.child:not(.sel) .stx { color: var(--text-muted); }
.sq.sel.sq.child, .sq.child.sel {
  border-left: 4px solid var(--brand-500);
  background: rgba(37,99,235,.08); opacity: 1; }
.sq.sel.sq.child .stx, .sq.child.sel .stx { color: var(--brand-500); }
/* 状态语义色（低饱和，颜色只做提示） */
.sq.blue  { border-left-color: var(--brand-500); } .sq.blue .sdot { background:var(--brand-500); }
.sq.blue .stx { color:var(--brand-500); }
.sq.amber { border-left-color: var(--warning); } .sq.amber .sdot { background:var(--warning); }
.sq.amber .stx { color:var(--warning); }
.sq.green { border-left-color: var(--success); } .sq.green .sdot { background:var(--success); }
.sq.green .stx { color:var(--success); }
.sq.high  { border-left-color: var(--danger); background: rgba(220,38,38,.05); }
.sq.high .sdot { background:var(--danger); } .sq.high .stx { color:var(--danger); }
.sq.gray  { opacity: .55; border-left-color: var(--text-disabled); }
/* 当前选中：左侧 4px 竖线 + 轻背景 + 轻阴影（不做大色块） */
.sq.sel { border-left: 4px solid var(--brand-500); background: rgba(37,99,235,.08);
          box-shadow: 0 1px 5px rgba(0,0,0,.10); }
.sq.sel .co { font-weight: 800; }
.deal-card-actions { display:grid; grid-template-columns:1fr 1fr; gap:.28rem;
  margin:-.18rem 0 .36rem; }
.deal-card-actions.single { grid-template-columns:1fr; }
.deal-history-row { border-left:2px solid var(--border-default); margin:.22rem 0 .18rem .58rem;
  padding:.22rem .38rem; background:transparent; border-radius:7px; }
.deal-history-row .meta { font-size:.66rem; color:var(--text-muted); line-height:1.35; }
.deal-history-row .title { font-size:.74rem; color:var(--text-secondary); font-weight:700;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.deal-history-row .next { font-size:.68rem; color:var(--text-muted); margin-top:.04rem; }
.deal-history-row .delta { color:var(--warning); font-weight:800; }
/* 侧栏宽度：桌面端 292px（spec 十七：260–300px），窄屏自适应 */
@media (min-width: 901px) {
  [data-testid="stSidebar"] { width: 292px; min-width: 292px; }
}
/* ROUND 6.8：CRM Sidebar = navigation + quick access，约 244px；折叠态窄 icon rail */
@media (min-width: 901px) {
  [data-testid="stSidebar"] { width: 244px !important; min-width: 244px !important; }
}
.sidebar-collapsed [data-testid="stSidebar"] { width: 62px !important; min-width: 62px !important; }
.crm-side-title { font-size:.72rem; font-weight:850; color:var(--text-muted);
  text-transform:uppercase; letter-spacing:.06em; margin:.55rem 0 .2rem; }
.smart-view-row { display:flex; align-items:center; justify-content:space-between;
  gap:.35rem; padding:.28rem .42rem; border-radius:8px; margin:.05rem 0;
  font-size:.78rem; color:var(--text-secondary); }
.smart-view-row .name { font-weight:750; color:var(--text-primary); }
.smart-view-row .count { font-size:.68rem; font-weight:850; min-width:1.55rem;
  text-align:center; border-radius:999px; background:#fff; border:1px solid var(--border-subtle);
  padding:.02rem .32rem; color:var(--text-muted); }
.smart-view-row.active { background:#EEF5FF; color:var(--brand-700); }
.smart-view-row.active .name { color:var(--brand-700); }
/* CRM 侧栏的 Smart Views 是导航，不是任务卡：保持稳定入口，但收紧为单行。 */
[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="secondary"] {
  min-height:2rem; padding:.18rem .45rem; border-radius:8px; font-size:.78rem;
}
[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="secondary"] p {
  line-height:1.2;
}
/* Deal Quick Access：比旧卡更矮，隐藏 ID / message count / timestamp。 */
.sq.deal { padding:.28rem .46rem .3rem; margin:.2rem 0 .06rem; border-radius:9px; }
.sq.deal .co { font-size:.82rem; margin-top:.08rem; }
.sq.deal .r2 { font-size:.68rem; margin-top:.08rem; }
.sq.deal .meta { display:none; }
.sq.deal .next { color:var(--brand-600); font-weight:650; }
.quick-access-empty { color:var(--text-muted); font-size:.73rem; padding:.35rem .1rem; }
.icon-rail-note { text-align:center; color:var(--text-muted); font-size:.7rem; margin:.3rem 0; }

/* 队列列表区独立滚动的细节 */
.queue-scroll [data-testid="stVerticalBlockBorderWrapper"] { margin-top: 0; }

/* —— 第六轮：整卡可点击（修复版）——
   每张卡 = st.container(markdown卡片 + 透明覆盖按钮)。按钮 absolute 铺满卡片
   容器（position:relative 锚点），整卡任意位置点击即选中。
   修复：旧选择器 :has(.clickable) 会冒泡命中一切含 .clickable 的祖先块
   （KPI/AI建议在主区 → 主区根容器被误匹配 → 主区全部按钮被染成透明覆盖层，
   「开始分析」等按钮消失）。现改为：
   ① position:relative 与 hover 只命中「直接子级元素容器里含 .clickable」的块；
   ② 透明覆盖只作用于「.clickable 卡片的元素容器 紧邻的下一个元素容器」里的按钮，
      主区普通按钮（开始分析/清空/删除…）完全不受影响。 */
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .clickable) {
  position: relative;
}
/* 关键修复（展开按钮无反应的根因）：
   Streamlit 1.63 的 stElementContainer 自带 position:relative，
   透明按钮 absolute 时锚点落在「按钮自己的元素容器」上——
   该容器高度为 0 → 覆盖层实际大小 0×宽 → 点卡片=点文字，永远没反应。
   现改为：让覆盖按钮所在的「元素容器」本身 absolute 铺满整张卡，
   按钮再填满该容器。该容器原本在文档流里占 0 高，
   绝对定位后布局零变化（KPI 卡同理一并修复）。 */
div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:has(.clickable)
  + div[data-testid="stElementContainer"]:has(button) {
  position: absolute; inset: 0; margin: 0; z-index: 5;
}
div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:has(.clickable)
  + div[data-testid="stElementContainer"] button {
  position: absolute; inset: 0; width: 100%; height: 100%;
  opacity: 0; z-index: 5; cursor: pointer; border: none; box-shadow: none;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .clickable):hover {
  background: rgba(128,128,128,.05); border-radius: 8px;
}

/* —— 第九轮：业务事实层（Value / Source / Certainty 三元组展示）—— */
.factbox { background:var(--surface-2); border:1px solid var(--border-default); border-radius:10px;
  padding:.55rem .7rem; margin:.35rem 0 .55rem; }
.factbox .ftitle { font-size:.86rem; font-weight:700; color:var(--text-primary); }
.factbox .fsub { font-size:.72rem; font-weight:400; color:var(--text-muted); margin-left:.3rem; }
.factbox .frow { display:flex; align-items:center; gap:.5rem;
  padding:.3rem .1rem; border-top:1px dashed var(--border-default); }
.factbox .frow:first-of-type { border-top:none; }
.factbox .flab { width:6.2em; font-size:.74rem; color:var(--text-muted); flex:none; }
.factbox .fval { flex:1; font-size:.8rem; font-weight:600; color:var(--text-primary); }
.factbox .fval .fqr { font-size:.66rem; font-weight:500; color:var(--text-muted); }
.factbox .fsrc { font-size:.68rem; color:var(--text-secondary); background:var(--border-default);
  border-radius:8px; padding:.06rem .45rem; flex:none; }
.factbox .fcert { font-size:.68rem; font-weight:600; border:1px solid;
  border-radius:8px; padding:.06rem .45rem; flex:none; }
.factbox .fnote { font-size:.68rem; color:var(--text-disabled); padding:0 .1rem .15rem 6.9em; }
/* —— Phase 2 · Customer + Opportunity Workspace（CRM 详情工作区）—— */
.deal-action-view { background:#fff; border:1px solid var(--border-subtle);
  border-radius:12px; padding:.72rem .86rem; margin:.48rem 0 .55rem;
  box-shadow:0 2px 8px rgba(15,23,42,.05); }
.deal-action-view .assistant-title { font-size:.72rem; font-weight:900; color:var(--brand-600); letter-spacing:.08em; text-transform:uppercase; margin-bottom:.38rem; }
.deal-action-view .head { display:flex; justify-content:space-between; gap:.8rem;
  align-items:flex-start; border-bottom:1px solid rgba(148,163,184,.22);
  padding-bottom:.46rem; margin-bottom:.48rem; }
.deal-action-view .company { font-size:1.18rem; font-weight:850; line-height:1.18; }
.deal-action-view .sub { font-size:.78rem; color:var(--text-secondary); margin-top:.12rem; }
.deal-action-view .state { text-align:right; font-size:.72rem; color:var(--text-secondary); }
.deal-action-view .state b { display:block; font-size:.92rem; color:var(--text-primary); }
.deal-action-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:.44rem;
  margin:.4rem 0 .5rem; }
.deal-action-grid .cell { border-left:2px solid rgba(148,163,184,.34); padding-left:.44rem; }
.deal-action-grid .lb { font-size:.62rem; color:var(--text-muted); font-weight:850; text-transform:uppercase; }
.deal-action-grid .v { font-size:.82rem; font-weight:760; margin-top:.06rem; }
.deal-action-view .nba { background:#F7FAFF; border-left:3px solid var(--brand-500);
  border-radius:8px; padding:.46rem .56rem; margin:.42rem 0; }
.deal-action-view .nba .lb { font-size:.66rem; color:var(--brand-600); font-weight:850; }
.deal-action-view .nba .what { font-size:1rem; font-weight:850; margin:.08rem 0; }
.deal-action-view .nba .why { font-size:.78rem; color:var(--text-secondary); line-height:1.45; }
/* ROUND 7.1 §14：Deal Progression 只读展示行（Health / Aging / Next Activity / 建议迁移） */
.deal-action-view .prog { font-size:.76rem; color:var(--text-secondary);
  margin-top:.34rem; padding-top:.32rem; border-top:1px dashed rgba(148,163,184,.35); }
.deal-action-view .prog b { font-size:.66rem; color:var(--brand-600); font-weight:850;
  letter-spacing:.04em; margin-right:.3rem; }
.deal-action-view .prog.tr { border-top:none; margin-top:.16rem;
  padding:.3rem .5rem; border-radius:7px; background:rgba(59,130,246,.06);
  color:var(--text-primary); font-size:.78rem; font-weight:650; }
.deal-action-view .prog.tr .hint { font-size:.7rem; color:var(--text-tertiary); font-weight:500; }
.req-compact { background:#fff; border:1px solid var(--border-subtle); border-radius:12px;
  padding:.68rem .8rem; margin:.56rem 0 .38rem; box-shadow:var(--shadow-card); }
.req-compact .title, .timeline-compact .title { font-size:.78rem; font-weight:850; margin-bottom:.38rem; }
.req-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.36rem; }
.req-item { border:1px solid rgba(148,163,184,.28); border-radius:8px; padding:.34rem .48rem; background:#f8fafc; }
.req-item .k { font-size:.66rem; color:var(--text-secondary); font-weight:750; }
.req-item .v { font-size:.8rem; font-weight:750; margin-top:.08rem; word-break:break-word; }
.req-item.warn { background:#fff7ed; border-color:rgba(217,119,6,.32); }
.req-item.ok .v { color:#166534; }
.req-note { font-size:.75rem; color:var(--text-secondary); margin-top:.42rem; }
.timeline-compact { background:#fff; border:1px solid var(--border-subtle); border-radius:12px;
  padding:.62rem .8rem; margin:.45rem 0 .38rem; box-shadow:var(--shadow-card); }
.timeline-compact .row { display:grid; grid-template-columns:7.5rem 1fr 4rem; gap:.5rem;
  border-top:1px solid rgba(148,163,184,.2); padding:.32rem 0; font-size:.76rem; }
.timeline-compact .row:first-of-type { border-top:0; }
.timeline-compact .ts, .timeline-compact .actor { color:var(--text-secondary); }
@media (max-width: 900px) { .req-grid { grid-template-columns:1fr; } .timeline-compact .row { grid-template-columns:1fr; } }
.mini-stage, .recent-activity { font-size:.78rem; color:var(--text-secondary);
  border-top:1px solid rgba(148,163,184,.20); padding-top:.36rem; margin-top:.38rem; }
.mini-stage b, .recent-activity b { color:var(--text-primary); }
@media (max-width: 900px) {
  .deal-action-view .head { display:block; }
  .deal-action-view .state { text-align:left; margin-top:.35rem; }
  .deal-action-grid { grid-template-columns:1fr; }
}
/* Customer Header：公司大字 + 字段格 + 操作按钮行 */
.ws-hd { padding: .15rem 0 .3rem; }
.ws-hd .co { font-size: 1.42rem; font-weight: 800; line-height: 1.2;
             letter-spacing: -.01em; }
.ws-hd .sub { font-size: .8rem; opacity: .72; margin-top: .12rem; line-height: 1.5; }
.ws-hd .sub b { font-weight: 700; }
.ws-cells { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
            gap: .45rem; margin: .5rem 0 .15rem; }
.ws-cell { border: 1px solid rgba(128,128,128,.2); border-radius: 8px;
           padding: .32rem .55rem; background: rgba(128,128,128,.045); }
.ws-cell .lb { font-size: .64rem; opacity: .62; letter-spacing: .02em;
               text-transform: uppercase; }
.ws-cell .v { font-size: .9rem; font-weight: 700; margin-top: .06rem;
              line-height: 1.3; word-break: break-word; }
.ws-cell .s { font-size: .68rem; opacity: .72; margin-top: .04rem; }
.ws-cell .v.red { color: var(--danger); } .ws-cell .v.amber { color: var(--warning); }
.ws-cell .v.green { color: var(--success); } .ws-cell .v.blue { color: var(--primary-color,var(--brand-500)); }
/* Pipeline：七段 chips + 箭头 + 当前高亮 */
.pipe-line { display: flex; align-items: center; flex-wrap: wrap;
             gap: .3rem .25rem; margin: .35rem 0 .15rem; }
.pchip { display: inline-flex; align-items: center; gap: .28rem;
         font-size: .78rem; font-weight: 700; padding: .2rem .55rem;
         border-radius: 999px; border: 1px solid rgba(128,128,128,.28);
         background: rgba(128,128,128,.05); color: inherit; white-space: nowrap; }
.pchip.done { border-color: rgba(22,163,74,.5); background: rgba(22,163,74,.07);
              color: var(--success); }
.pchip.cur { border-color: var(--primary-color,var(--brand-500));
             background: rgba(37,99,235,.1); color: var(--primary-color,var(--brand-500));
             box-shadow: 0 0 0 1px var(--primary-color,var(--brand-500)); }
.pchip.won { border-color: rgba(22,163,74,.6); background: rgba(22,163,74,.1);
             color: var(--success); }
.pchip.lost { border-color: rgba(100,116,139,.5); background: rgba(100,116,139,.08);
              color: var(--text-secondary); opacity: .8; }
.pipe-arw { color: rgba(128,128,128,.45); font-size: .8rem; flex: 0 0 auto; }
/* Pipeline 阶段知识卡 */
.pipe-know { border: 1px dashed rgba(128,128,128,.35); border-radius: 8px;
             padding: .42rem .6rem; margin: .3rem 0 .15rem;
             background: rgba(128,128,128,.03); }
.pipe-know .pk-row { display: flex; gap: .5rem; font-size: .82rem;
                     line-height: 1.5; padding: .12rem 0; }
.pipe-know .pk-lb { flex: 0 0 5.4em; font-weight: 700; opacity: .68;
                    font-size: .72rem; padding-top: .1rem; }
.pipe-know .pk-lb.ai { color: var(--primary-color,var(--brand-500)); }
.pipe-know .pk-lb.gap { color: var(--warning); }
/* AI Sales Assistant：label 列 + 值列 */
.asst { display: grid; grid-template-columns: 6.2em 1fr; gap: .18rem .6rem;
        font-size: .86rem; line-height: 1.5; }
.asst .lb { font-size: .7rem; font-weight: 800; opacity: .65;
            letter-spacing: .05em; padding-top: .3rem; }
.asst .val { padding: .18rem 0; border-bottom: 1px dashed rgba(128,128,128,.15); }
.asst .val:last-child { border-bottom: none; }
.asst .why { color: inherit; opacity: .88; }
/* Activity Timeline */
.tl-item { display: flex; gap: .5rem; padding: .3rem 0;
           border-bottom: 1px dashed rgba(128,128,128,.14); font-size: .8rem; }
.tl-item:last-child { border-bottom: none; }
.tl-ic { flex: 0 0 1.3rem; text-align: center; font-size: .95rem; }
.tl-bd { flex: 1; min-width: 0; }
.tl-bd .t1 { font-weight: 700; }
.tl-bd .t2 { opacity: .8; margin-top: .02rem; word-break: break-word; }
.tl-bd .res { color: var(--success); font-size: .74rem; margin-top: .02rem; }
.tl-side { flex: 0 0 auto; text-align: right; font-size: .7rem; opacity: .62;
           white-space: nowrap; }
/* Related Records 四格 */
.rel-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(210px,1fr));
            gap: .45rem; margin-top: .2rem; }
.rel-box { border: 1px solid rgba(128,128,128,.2); border-radius: 8px;
           padding: .4rem .55rem; background: rgba(128,128,128,.04); }
.rel-box .t { font-size: .68rem; font-weight: 700; opacity: .7;
              letter-spacing: .03em; margin-bottom: .2rem; }
.rel-box .line { font-size: .8rem; line-height: 1.55; }
.rel-box .empty { font-size: .74rem; opacity: .5; }
/* =====================================================================
   V8.1 · Visual System（纯视觉覆盖层：不改任何业务逻辑 / API / 数据）
   Background → Sidebar → Surface → AI Surface → Primary Action → Status
   ===================================================================== */
/* —— ① 背景层级：App 灰 → Sidebar 更深灰 → 卡片白 → AI 淡蓝 —— */
[data-testid="stAppViewContainer"] { background: var(--app-bg); }
[data-testid="stHeader"] { background: transparent; }
section[data-testid="stSidebar"] { background: var(--sidebar-bg); }
section[data-testid="stSidebar"] > div { background: transparent; }
[data-testid="stSidebar"] .block-container { background: transparent; }
[data-testid="stAppViewContainer"] .main .block-container { background: transparent; }
div[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--surface-1);
  border: 1px solid var(--border-subtle) !important;
  box-shadow: var(--shadow-card);
}
details[data-testid="stExpander"] {
  background: var(--surface-1); border: 1px solid var(--border-subtle);
  box-shadow: var(--shadow-card); border-radius: var(--radius-md);
}
/* —— ② 按钮层级：Primary = Brand Blue（红只留给危险语义，spec 二）——
   Streamlit 默认 primary 为红 #FF4B4B；此处全局覆盖为品牌蓝。
   危险操作类按钮若需要红色，用单独语义类（当前产品无） */
button[kind="primary"], button[data-testid="stBaseButton-primary"] {
  background: var(--brand-500) !important;
  border: 1px solid var(--brand-500) !important;
  color: #ffffff !important;
}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {
  background: var(--brand-600) !important;
  border-color: var(--brand-600) !important;
}
button[kind="primary"]:active { background: var(--brand-700) !important; border-color: var(--brand-700) !important; }
button[kind="primary"]:focus-visible { box-shadow: 0 0 0 3px rgba(37,99,235,.25) !important; }
button[kind="primary"]:disabled { background: var(--border-focus) !important; border-color: var(--border-focus) !important; }
button[kind="secondary"] {
  background: var(--surface-1); border: 1px solid var(--border-default);
  color: var(--text-primary);
}
button[kind="secondary"]:hover {
  background: var(--surface-hover); border-color: var(--brand-500);
  color: var(--brand-600);
}
button[kind="tertiary"] { color: var(--text-secondary); }
button[kind="tertiary"]:hover { color: var(--brand-600); }
.stButton button, .stDownloadButton button, .stFormSubmitButton button { font-weight: 600; }
/* —— ③ Surface 层级：中性卡统一 白底 + 细边框 + 弱阴影，去掉灰蒙层 —— */
.kpi, .card, .score-box, .cust-chip, .qc, .rel-box, .ws-cell, .factbox {
  background: var(--surface-1);
  border: 1px solid var(--border-subtle);
  box-shadow: none;
}
.kpi { box-shadow: var(--shadow-card); }
.ws-cell .v.red { color: var(--danger); } .ws-cell .v.amber { color: var(--warning); }
.ws-cell .v.green { color: var(--success); }
/* 状态卡保留语义浅底（颜色只表达状态，spec 二） */
.kpi.red { border-color: rgba(220,38,38,.35); box-shadow: inset 3px 0 0 var(--danger); background: var(--danger-bg); }
.kpi.amber { border-color: rgba(217,119,6,.35); box-shadow: inset 3px 0 0 var(--warning); background: var(--warning-bg); }
.kpi.green { border-color: rgba(22,163,74,.35); box-shadow: inset 3px 0 0 var(--success); background: var(--success-bg); }
.qc.high { border-color: rgba(220,38,38,.45); border-left: 5px solid var(--danger); background: var(--danger-bg); }
.qc.mid { border-left: 5px solid var(--warning); background: var(--surface-1); }
.qc.low { border-left: 5px solid var(--success); background: var(--surface-1); }
.qc.done { opacity: .6; }
.qc.high .badge { color: var(--danger); } .qc.mid .badge { color: var(--warning); } .qc.low .badge { color: var(--success); }
/* —— ④ AI Surface（spec 四）：独立视觉层，仅 AI 区域允许极轻微渐变 —— */
.nba-hero {
  background: linear-gradient(135deg, #F7FAFF, #EEF5FF);
  border-color: var(--ai-border);
  box-shadow: 0 0 0 1px rgba(37,99,235,.08), 0 8px 24px rgba(37,99,235,.06);
}
.sum-hero { background: linear-gradient(135deg,#F7FAFF,#EEF5FF); border-color: var(--ai-border); border-left: 4px solid var(--brand-500); }
.aip-card { background: linear-gradient(135deg,#F7FAFF,#EEF5FF); border: 1px solid var(--ai-border); }
.aip-card .hd .n { color: var(--brand-600); }
.nextstep { background: var(--ai-bg); border-left-color: var(--brand-500); }
.nextstep b { color: var(--brand-600); }
/* —— ⑤ AI Priority：蓝色核心 + 分段语义底衬（spec 五）——
   数字本身始终为主信息；band 只提供微弱的语义底衬 */
.ws-aip {
  display: inline-block; border-radius: var(--radius-sm);
  padding: .02rem .3rem; font-size: 1.45rem; font-weight: 900;
  color: var(--brand-600); line-height: 1.1;
  background: #F5F9FF;
}
.ws-aip.band-hi { background: var(--danger-bg); color: var(--danger); }
.ws-aip.band-lo { background: var(--surface-2); color: var(--text-muted); }
.aip-card .hd .n.band-hi { color: var(--danger); }
.aip-card .hd .n.band-lo { color: var(--text-muted); }
.sq .score .aip { color: var(--brand-600); background: rgba(37,99,235,.09); }
.sq .score .qs { color: var(--text-secondary); background: rgba(15,23,42,.06); }
/* —— ⑥ Sidebar 客户卡（spec 六）：白卡 + 选中 = 浅蓝 + 3px 左线 —— */
.sq { background: var(--surface-1); border: 1px solid var(--border-subtle);
      box-shadow: var(--shadow-card); border-left: 3px solid var(--border-default); }
.sq.sel { border: 1px solid var(--border-focus); border-left: 3px solid var(--brand-500);
          background: var(--surface-selected); box-shadow: none; }
.sq.sel .co { font-weight: 800; color: var(--text-primary); }
/* —— ⑦ Pipeline 阶段（spec 八）：未完成浅灰 / 当前蓝 / 已完成绿 —— */
.pchip { border: 1px solid var(--border-default); background: var(--surface-2); color: var(--text-secondary); }
.pchip.cur { border: 1px solid var(--brand-500); background: #EFF6FF; color: var(--brand-700);
             box-shadow: 0 0 0 1px rgba(37,99,235,.35); }
.pchip.done { border-color: rgba(22,163,74,.4); background: #ECFDF5; color: var(--success); }
.pchip.won { border-color: rgba(22,163,74,.55); background: var(--success-bg); color: #047857; }
.pchip.lost { border-color: var(--border-default); background: var(--surface-2); color: var(--text-secondary); }
/* —— ⑧ 排版层级（spec 九）：Primary/Secondary/Muted，禁纯黑 —— */
.ws-hd .co { color: var(--text-primary); }
.ws-hd .sub, .ws-cell .s, .qc .meta, .tl-side { color: var(--text-secondary); }
.inq-head .cmeta, .factbox .fsub, .card .s { color: var(--text-secondary); }
/* —— ⑨ 留白收紧（spec 十）：首屏信息密度，减少滚动 —— */
.block-container { padding-top: .6rem; padding-bottom: 1.1rem; }
.nba-hero { margin-bottom: .42rem; padding-top: .5rem; padding-bottom: .5rem; }
.ws-cells { gap: .4rem; margin-top: .42rem; }
.sum-hero { margin-top: .3rem; padding: .5rem .85rem; }
/* —— ⑩ 文字级颜色（事实盒主文本禁 #0f172a 纯黑系偏黑） —— */
.factbox .ftitle, .factbox .fval { color: var(--text-primary); }
/* —— ⑪ 主题主色统一：pills / tabs / radio / focus 等 Streamlit 内建控件
      全部走 --primary-color（默认红 #FF4B4B），统一切到品牌蓝 —— */
:root, [data-testid="stAppViewContainer"] {
  --primary-color: #2563EB;
}
/* CRM Sales Workspace：首页采用任务优先的层级，避免所有模块都是同一种白卡。 */
[data-testid="stAppViewContainer"] { background: #F6F8FB; }
[data-testid="stSidebar"] { background: #EEF2F7; }
.workspace-section { margin: .8rem 0 .42rem; display:flex; align-items:baseline; gap:.55rem; }
.workspace-section .title { font-size: 1rem; font-weight: 800; color: var(--text-primary); }
.today-action-row { padding:.48rem 0 .52rem; border-bottom:1px solid var(--border-subtle); }
.today-action-row .action { font-size:.94rem; font-weight:800; color:var(--text-primary); line-height:1.28; }
.today-action-row .deal { font-size:.77rem; color:var(--text-secondary); margin-top:.12rem;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.today-action-row .why { font-size:.7rem; color:var(--text-muted); margin-top:.12rem; }
.workspace-section .note { font-size: .74rem; color: var(--text-muted); }
.workspace-section.compact { margin-top: .55rem; }
.workspace-kpi { background: var(--surface-1); border: 1px solid var(--border-subtle);
  border-radius: 10px; padding: .55rem .75rem; min-height: 82px; box-shadow: var(--shadow-card); }
.workspace-kpi .label { font-size:.7rem; color:var(--text-secondary); font-weight:700; }
.workspace-kpi .value { font-size:1.35rem; color:var(--text-primary); line-height:1.1; font-weight:800; margin:.18rem 0 .1rem; font-variant-numeric: tabular-nums; }
.workspace-kpi .sub { font-size:.68rem; color:var(--text-muted); }
.workspace-kpi.blue { border-top: 2px solid var(--brand-500); }
.workspace-kpi.amber { border-top: 2px solid var(--warning); }
.workspace-kpi.green { border-top: 2px solid var(--success); }
.workspace-kpi.neutral { border-top: 2px solid #94A3B8; }
.kpi-strip { display:flex; align-items:center; gap:.45rem; flex-wrap:wrap;
  background: rgba(255,255,255,.72); border:1px solid var(--border-subtle);
  border-radius: 10px; padding:.42rem .58rem; box-shadow:0 1px 4px rgba(15,23,42,.04);
  margin:.32rem 0 .5rem; }
.kpi-chip { display:flex; align-items:baseline; gap:.28rem; padding:.18rem .42rem;
  border-radius:999px; background:#fff; border:1px solid rgba(148,163,184,.22);
  font-size:.72rem; color:var(--text-secondary); }
.kpi-chip b { font-size:.9rem; color:var(--text-primary); font-variant-numeric: tabular-nums; }
.kpi-chip.blue b { color:var(--brand-600); }
.kpi-chip.amber b { color:var(--warning); }
.kpi-chip.red b { color:var(--danger); }
.kpi-chip.green b { color:var(--success); }
.queue-row { border-top:1px solid rgba(148,163,184,.28); padding:.34rem 0 .28rem; }
.queue-row:first-child { border-top:none; }
.task-card { background: var(--surface-1); border: 1px solid var(--border-subtle); border-radius: 10px;
  padding: .62rem .72rem; margin-bottom:.4rem; box-shadow: 0 1px 3px rgba(15,23,42,.035); }
.task-card.high { border-left: 3px solid var(--danger); }
.task-card.amber { border-left: 3px solid var(--warning); }
.task-card.green { border-left: 3px solid var(--success); }
.task-card .kind { font-size:.67rem; font-weight:800; letter-spacing:.05em; text-transform:uppercase; color:var(--text-muted); }
.task-card .who { font-size:.92rem; font-weight:800; margin:.08rem 0; }
.task-card .order { font-size:.76rem; color:var(--text-secondary); }
.task-card .state { font-size:.68rem; color:var(--text-secondary); margin-top:.1rem; }
.task-card .why { font-size:.72rem; color:var(--text-muted); margin-top:.32rem; line-height:1.4; }
.task-card .next { font-size:.76rem; margin-top:.28rem; color:var(--brand-700); font-weight:700; }
.nba-workspace { border: 1px solid #D7E5FF; background: #F7FAFF; border-radius:10px; padding:.72rem .85rem; height:100%; }
.nba-workspace .eyebrow { font-size:.67rem; letter-spacing:.08em; font-weight:800; color:var(--brand-600); }
.nba-workspace .action { font-size:1rem; font-weight:800; margin:.12rem 0 .3rem; }
.nba-workspace .detail { font-size:.74rem; color:var(--text-secondary); line-height:1.5; }
.pipeline-strip { display:flex; gap:.45rem; flex-wrap:wrap; padding:.55rem .1rem .15rem; }
.pipeline-pill { background:#FFF; border:1px solid var(--border-subtle); border-radius:7px; padding:.35rem .55rem; min-width:102px; }
.pipeline-pill .n { font-weight:800; font-size:.88rem; }.pipeline-pill .l { font-size:.65rem; color:var(--text-muted); }
.inbox-nav { font-size:.76rem; font-weight:800; color:var(--text-secondary); margin:.55rem 0 .18rem; letter-spacing:.03em; }
.saved-view { font-size:.7rem; color:var(--text-muted); padding:.18rem 0; }
/* —— ROUND 6.9 §2 · 跟进卡（默认 3 行：客户 / 产品·数量 / 到期·下一步）—— */
.fucard { line-height:1.45; }
.fucard .r1 { display:flex; justify-content:space-between; align-items:baseline; gap:.5rem; }
.fucard .r1 b { font-size:.9rem; }
.fucard .r1 .st { font-size:.7rem; font-weight:750; white-space:nowrap; }
.fucard .r2 { font-size:.78rem; color:var(--text-primary); margin-top:.1rem; }
.fucard .r3 { font-size:.72rem; color:var(--text-secondary); margin-top:.12rem; }
/* —— ROUND 6.9 §1 · Pipeline 概览卡（只读，点卡片进 Deal Detail）—— */
.phead { font-size:.74rem; font-weight:800; color:var(--text-secondary);
  padding:.3rem .1rem .35rem; border-bottom:1px solid var(--border-subtle);
  margin-bottom:.4rem; display:flex; justify-content:space-between; }
.phead span { color:var(--text-muted); font-weight:700; }
.pcard { border:1px solid var(--border-subtle); border-left:3px solid var(--text-disabled);
  border-radius:9px; padding:.45rem .55rem; margin-bottom:.4rem;
  background:var(--surface-2); cursor:pointer; }
.pcard.healthy { border-left-color:var(--success); }
.pcard.attention { border-left-color:var(--warning); }
.pcard.at_risk, .pcard.overdue { border-left-color:var(--danger); }
.pcard .co { font-size:.82rem; font-weight:750; line-height:1.3;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pcard .pd { font-size:.72rem; color:var(--text-secondary); margin-top:.12rem;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pcard .amt { font-size:.78rem; font-weight:700; margin-top:.22rem; }
.pcard .meta { font-size:.68rem; color:var(--text-muted); margin-top:.1rem; }
/* ROUND 7.1 §11 · Primary Reason 一行业务原因（比 meta 略重，但仍是次级） */
.pcard .rsn { font-size:.69rem; color:var(--text-secondary); margin-top:.14rem;
  line-height:1.35; display:-webkit-box; -webkit-line-clamp:2;
  -webkit-box-orient:vertical; overflow:hidden; }
.pcard .nx { font-size:.68rem; color:var(--brand-600); margin-top:.16rem;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
/* 逾期/缺失是执行例外，必须一眼可见（颜色语义与全站一致：红=紧迫） */
.pcard .nx.od { color:var(--danger); font-weight:700; }
.pcard .nx.miss { color:var(--warning); font-weight:650; }
.pcard .nx.tr { color:var(--text-muted); font-weight:600; }
@media (max-width: 900px) { .block-container { padding-left:.7rem; padding-right:.7rem; }
  .workspace-kpi { min-height:70px; padding:.42rem .55rem; }
}
</style>
"""


def _zone(label: str, lv: int = 0):
    """分层级的小标题（5 级 / 视觉降权）：
    lv=1 业务结果　lv=2 待办任务　lv=3 AI 明细　lv=4 客户与产品　lv=5 原始与元数据。
    """
    cls = "zone-label" + (f" lv{lv}" if lv in (1, 2, 3, 4, 5) else "")
    st.markdown(f"<div class='{cls}'>{label}</div>", unsafe_allow_html=True)


def _tone_grade(g: str) -> str:
    """询盘等级 → 卡片色调（A 红 / B 橙 / C 绿 / 其他默认）"""
    return {"A": "red", "B": "amber", "C": "green"}.get((g or "")[:1], "")


def _tone_urgency(u) -> str:
    """紧急度 → 卡片色调（高红 / 中橙 / 低绿）"""
    return {"high": "red", "medium": "amber", "low": "green"}.get(u or "", "")


def _kpi_card(col, label: str, value, sub: str, tone: str = ""):
    """顶部 KPI：左侧色条 + 标题 + 数字 + 一句说明（带 clickable 标记供整卡点击）。"""
    cls = "kpi clickable" + (f" {tone}" if tone in ("red", "amber", "green", "blue") else "")
    col.markdown(
        f"<div class='{cls}'>"
        f"<div class='t'>{label}</div>"
        f"<div class='n'>{value}</div>"
        f"<div class='s'>{sub}</div></div>",
        unsafe_allow_html=True)


def _workspace_kpi(col, label: str, value, sub: str, tone: str = "neutral"):
    """Sales Workspace KPI：保留事实计算，只提升首页视觉层级。"""
    import html as _html
    col.markdown(
        f"<div class='workspace-kpi {tone}'><div class='label'>{_html.escape(str(label))}</div>"
        f"<div class='value'>{_html.escape(str(value))}</div>"
        f"<div class='sub'>{sub}</div></div>", unsafe_allow_html=True)


def _workspace_reason(item: dict) -> list[str]:
    """从已提取/已评分事实生成任务原因，不凭空补造客户信息。"""
    need = item.get("need") or {}
    lines = []
    if need.get("qty"):
        lines.append(f"已识别数量：{need['qty']}")
    if need.get("product") or need.get("product_cat"):
        lines.append(f"已识别产品：{need.get('product') or need.get('product_cat')}")
    if need.get("match_top", {}).get("name"):
        lines.append("产品库已有匹配")
    blockers = need.get("blockers") or []
    if blockers:
        lines.append("待确认：" + "、".join(_field_cn(b) for b in blockers[:2]))
    if item.get("fu_state") in ("已逾期", "今日跟进"):
        lines.append(item["fu_state"] + "，需立即跟进")
    if not lines:
        lines.append("AI 已完成询盘价值与下一步判断")
    return lines[:3]


def _record_recent_deal_access(iid: int):
    """记录一次 Deal 访问；渲染层再按 canonical Deal identity 去重。"""
    if not iid:
        return
    recent = [x for x in st.session_state.get("recent_opened_inquiry_ids", [])
              if str(x) != str(iid)]
    st.session_state.recent_opened_inquiry_ids = [iid] + recent[:11]


def _home_select_inquiry(iid: int):
    """统一打开 Deal：切换详情并记录一次最近访问。

    最近访问只保存代表询盘 ID；侧栏渲染时再按 Deal identity 聚合，避免同一
    Deal 的多封往来在侧栏变成多张卡。该状态纯 UI，不写入 CRM 数据。
    """
    _open_inquiry(iid)
    st.session_state.home_opened_id = iid
    _record_recent_deal_access(iid)


def _home_execute_resolved_action(iid: int, action: str):
    """首页行动队列复用 Deal Detail 的既有主 CTA 路由。

    这里不派生新动作：action 与按钮文案都来自 ResolvedDealState。首页只负责
    把用户带到相同的执行区域，确保首页、详情与 Pipeline 不会出现不同 CTA。
    """
    _home_select_inquiry(iid)
    if action in ("PREPARE_QUOTATION", "UPDATE_QUOTATION",
                  "CHECK_INTERNAL_QUOTATION_PREREQUISITES",
                  "PREPARE_UPDATED_QUOTATION"):
        section_key = f"ui_quote_{iid}"
    elif action == "FOLLOW_UP":
        section_key = f"ui_fuzone_{iid}"
    elif action in ("MATCH_PRODUCT", "CHECK_SUPPLIER_CAPABILITY",
                    "CHECK_PRODUCT_OPTIONS", "PREPARE_PRODUCT_RECOMMENDATION"):
        section_key = None
    else:
        section_key = f"ui_mail_{iid}"
    _execute_primary_action(iid, action, section_key)


def _open_deal_from_pipeline(inquiry_id):
    """ROUND 6.9 §1：Pipeline 卡片 → 共享 Deal Detail。

    不再复制一套 Pipeline 专属详情面板：直接复用其它工作区同一个
    Deal Detail（选中该 Deal 的代表询盘），保证全站只有一个详情定义。
    """
    if not inquiry_id:
        st.toast("该商机尚未关联询盘，暂时无法打开 Deal Detail")
        return
    _open_inquiry(inquiry_id)
    _record_recent_deal_access(inquiry_id)
    st.session_state.home_opened_id = None
    st.session_state.pipeline_opened_id = inquiry_id


def _render_task_card(item: dict, label: str, tone: str, key_prefix: str):
    """今日任务卡（第二十三轮：输入 = 统一 DealWorkItem wv）。

    只回答 WHAT SHOULD I DO：公司 / 产品·数量 / 优先级 / Deal 当前状态 /
    下一步。不再重复「为什么现在」的长篇理由。
    卡点（阻塞项）仍单独一行，销售 5 秒看懂"这笔单差什么"。
    """
    import html as _html
    need = item.get("need") or {}
    action = item.get("nba") or ((item.get("lead") or {}).get("action") or {}) \
        .get("label") or "查看详情"
    who = item.get("company") or item.get("contact") or "未知客户"
    product = item.get("product") or need.get("product") \
        or need.get("intent") or "产品待识别"
    qty = item.get("quantity") or need.get("qty") or "数量待确认"
    order = " · ".join(x for x in (str(product), str(qty)) if x)
    _tier = item.get("tier") or "—"
    _badge = (item.get("emoji") or "") + " " + (item.get("badge") or "")
    _blockers = need.get("blockers") or []
    _gap = ("、".join(_field_cn(b) for b in _blockers[:3]) if _blockers
            else "暂无阻塞项，可直接推进")
    st.markdown(
        f"<div class='task-card {tone}'><div class='kind'>{_html.escape(label)}"
        f" · <b>{_html.escape(str(_tier))}</b></div>"
        f"<div class='who'>{_html.escape(str(who))}</div>"
        f"<div class='order'>{_html.escape(order)}</div>"
        f"<div class='state'>{_html.escape(_badge.strip())}</div>"
        f"<div class='why'><b>卡点</b>：{_html.escape(_gap)}</div>"
        f"<div class='next'>下一步：{_html.escape(str(action))}</div></div>",
        unsafe_allow_html=True)
    st.button("处理", key=f"{key_prefix}_{item.get('lead_id')}", type="primary",
              use_container_width=True, on_click=_home_select_inquiry,
              args=(item.get("lead_id"),),
              help="已选择该询盘；在“分析询盘”中可查看详情并执行下一步")


# =====================================================================
# 第十八轮（ROUND 3）· AI 跟进台 = ACTION CENTER（非客户库 / 非分析台）
# 三轴分离：InquiryStatus(沟通) / DealStage(商机) / FollowUpStatus(跟进任务)
# =====================================================================
import followup as _fu
from db import create_followup_task as _db_create_fu, \
    update_followup_task as _db_upd_fu, \
    list_followup_tasks as _db_list_fu, \
    mark_followup_sent as _db_fu_sent

_FU_SNOOZE_OPT = {"明天": 1, "3 天": 3, "下周": 7}


def _fu_due_in(days: int) -> str:
    return (datetime.datetime.now()
            + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")


def _fu_ensure_task(item, due_at=None, note="") -> int:
    """auto 建议项在用户执行动作时固化为任务（复用去重创建）。"""
    if item.get("id"):
        return item["id"]
    tid, _reused = _db_create_fu(
        item["deal_id"], item["reason"], due_at=due_at or item.get("due_at") or "",
        title=f"{_fu.REASON_CN.get(item['reason'], item['reason'])} · "
              f"{item.get('company') or ''}",
        next_action=item.get("nba") or "",
        note=note or item.get("note") or "", actor="销售")
    return tid


def _fu_load_items(now=None):
    """组装跟进队列 = 活跃跟进任务 + 尚无任务的 Deal 自动建议（不自动落库）。"""
    import db as _db
    now = now or datetime.datetime.now()
    qmap = {x["id"]: x for x in load_queue(None)}
    # —— 活跃任务 ——
    tasks = _db_list_fu(active_only=True)
    items = []
    covered_deals = set()
    for t in tasks:
        covered_deals.add(t["opportunity_id"])
        row = qmap.get(t.get("inquiry_id")) or {}
        need = row.get("need") or {}
        # 第十九轮（A3/D）：列表只显示产品短名；完整规格在 Deal/询盘详情。
        _pd = _ui.product_display(need)
        prod = _pd["title"] or need.get("intent") or t.get("product") or ""
        qty = need.get("qty") or ""
        items.append(dict(
            id=t["id"], deal_id=t["opportunity_id"], auto=False,
            reason=t["reason"], base_status=t["fu_status"] or "PENDING",
            due_at=t.get("due_at"), nba=t.get("next_action")
            or _fu.REASON_NBA.get(t["reason"], ("跟进", "manual"))[0],
            why=_fu.REASON_WHY.get(t["reason"], ""),
            company=t.get("company") or "", contact=t.get("contact") or "",
            country=t.get("country") or "", stage=t.get("stage") or "",
            deal_title=t.get("title") or "", product=prod, qty=qty,
            inquiry_id=t.get("inquiry_id"), pri=_ui.priority_tier(row),
            note=t.get("note") or "", owner=t.get("owner") or "",
            created=t.get("created_at") or "", fu_status=""))
    # —— 无活跃任务的活跃商机：规则建议 ——
    import db as _db2
    for deal in (_db2.list_opportunities() or []):
        if deal.get("stage") in (_crm_core.TERMINAL or ("WON", "LOST", "ON_HOLD")):
            continue
        if deal.get("id") in covered_deals:
            continue
        row = qmap.get(deal.get("inquiry_id")) or {}
        need = row.get("need") or {}
        wf = row.get("wf") or {}
        acts = _db2.list_deal_activity(deal["id"]) or []
        la = acts[0] if acts else {}
        # 询盘级 activity 兜底（更细粒度的事件）
        if not la and deal.get("inquiry_id"):
            inq_acts = list(_db2.list_activity(deal["inquiry_id"]) or [])
            if inq_acts:
                t_last = inq_acts[-1]
                la = {"type": t_last[0], "ts": t_last[1]}
        ctx = dict(biz=row.get("biz") or "", blockers=need.get("blockers") or [],
                   follow_up_at=wf.get("follow_up_at"),
                   follow_up_done=bool(wf.get("follow_up_done")),
                   has_draft=bool(need.get("has_draft")),
                   readiness=need.get("readiness") or "",
                   created=row.get("created") or "",
                   last_activity_type=la.get("type") or "",
                   last_activity_ts=la.get("ts") or "")
        sug = _fu.suggest_deal_followup(deal, ctx, now)
        if not sug:
            continue
        row2 = qmap.get(deal.get("inquiry_id")) or {}
        need2 = row2.get("need") or {}
        # 第十九轮（A3/D）：跟进队列产品列也只展示短名（完整规格入详情）
        _pd2 = _ui.product_display(need2)
        prod = _pd2["title"] or need2.get("intent") or deal.get("product") or ""
        items.append(dict(
            id=None, deal_id=deal["id"], auto=True, reason=sug["reason"],
            base_status="PENDING", due_at=sug.get("due_at") or "",
            nba=sug.get("nba") or _fu.REASON_NBA[sug["reason"]][0],
            why=sug.get("why") or _fu.REASON_WHY.get(sug["reason"], ""),
            company=row2.get("company") or deal.get("title") or "",
            contact=row2.get("contact") or "", country=row2.get("country") or "",
            stage=deal.get("stage") or "", deal_title=deal.get("title") or "",
            product=prod, qty=need2.get("qty") or "", inquiry_id=deal.get("inquiry_id"),
            pri=_ui.priority_tier(row2), note="", owner="",
            created=row2.get("created") or "", fu_status=""))
    # 计算展示态 + 排序
    for it in items:
        it["fu_status"] = _fu.fu_status_of(it["base_status"], it["due_at"], now)
    return _fu.sort_queue(items, now)


def _fu_reason_badge(item) -> str:
    st_ = item["fu_status"]
    tone = {"OVERDUE": "red", "DUE_TODAY": "amber", "WAITING_CUSTOMER": "blue",
            "SNOOZED": "low", "PENDING": "low"}.get(st_, "low")
    return tone, f"{_fu.STATUS_CN.get(st_, st_)}"


def _fu_mark(item, fu_status, days=None, note=""):
    """完成 / Snooze / 重开 —— auto 项先固化再流转；写 Timeline。"""
    tid = _fu_ensure_task(item)
    due = item.get("due_at")
    if days is not None:
        due = _fu_due_in(days)
    _db_upd_fu(tid, fu_status=fu_status, due_at=due, note=note, actor="销售")


def _fu_render_detail(item):
    """跟进详情面板（不是 Customer 360）：为什么现在 / 最近活动 /
    AI NEXT ACTION / 生成跟进邮件 / NEXT FOLLOW-UP / 操作。"""
    import db as _db3
    st.markdown("---")
    _w1, _w2 = st.columns([1.6, 1])
    with _w1:
        st.markdown(f"**为什么现在**　{_fu.REASON_WHY.get(item['reason'], item['reason'])}"
                    + (f"<br><span style='color:var(--text-disabled);font-size:.8rem'>"
                       f"{item.get('why') or ''}</span>" if item.get("why") else ""),
                    unsafe_allow_html=True)
        # 场景备注（业务员建任务时可写的具体原因；如 INFORMATION_WAITING 的缺口项）
        if (item.get("note") or "").strip():
            st.caption(f"📌 {_ui_flag_esc(str(item.get('note')).strip())}")
    with _w2:
        st.markdown(f"**Reason**　{_fu.REASON_CN.get(item['reason'], item['reason'])}")
    # LAST ACTIVITY
    # ROUND 7.2 性能：跟进台（tab2）与客户档案（tab4）都随每次 rerun 重跑，
    # 而 _fu_load_items() 会先把全部任务行 join 出来（单轮 16 次调用），
    # 再在卡片循环里为每条任务各查一次活动流 —— 侧栏点一下就白跑一遍。
    # 活动流按 (inquiry_id / deal_id, 数据版本) 缓存，写路径 bump 版本即失效。
    acts = []
    if item.get("inquiry_id"):
        acts = _cached_activity(_db3, "inquiry", item["inquiry_id"])
    dacts = _cached_activity(_db3, "deal", item["deal_id"]) if item.get("deal_id") else []
    acts += [("Deal", a.get("ts"), a.get("description") or a.get("type"), "", "")
             for a in dacts]
    if acts:
        t_last = acts[-1]
        st.caption(f"**LAST ACTIVITY**　{_at_cn(str(t_last[0]))} · {_ui.ago(t_last[1])}")
    else:
        st.caption("**LAST ACTIVITY**　暂无记录")
    # AI 下一步（单 NBA）
    _sug = _fu.suggest_next_followup(item["reason"], item.get("due_at"))
    st.markdown(f"**AI NEXT ACTION**　{item['nba']}"
                f"<br><span style='color:var(--text-muted);font-size:.82rem'>"
                f"{_sug['reason']} · 建议时间 {str(_sug['due_at'])[:16]}</span>",
                unsafe_allow_html=True)
    # 生成跟进邮件（惰性：点击后才出现草稿框，默认页面保持信息密度低；
    # 第二十一轮：改走统一客户邮件管线——自动意图 + 问句去重 + 事实护栏）
    _key = f"fuid_{item['id'] or ('a' + str(item['deal_id']) + item['reason'])}"
    _gen = bool(st.session_state.get(f"fu_gen_{_key}"))
    if not _gen:
        if st.button("✉️ 生成客户邮件", key=f"fu_genbtn_{_key}",
                     use_container_width=True):
            st.session_state[f"fu_gen_{_key}"] = True
            st.rerun()
    else:
        _uctx = dict(company=item.get("company") or "",
                     contact_name=item.get("contact") or "",
                     product=item.get("product") or "",
                     qty=item.get("qty") or "",
                     unresolved=[_field_cn(b) for b in _qblockers_of(item)],
                     note=item.get("note") or "")
        _uctx = dict(fu_reason=item["reason"], info=_uctx, fu_ctx=_uctx,
                     product_short=item.get("product") or "",
                     seller_company=(SELLER.get("company") or ""),
                     matches=[], gaps=[], text="", customer_product=item.get("product") or "")
        _uem = _emu.generate_customer_email(_uctx, intent=_emu.FOLLOW_UP)
        st.caption(f"🎯 邮件目的：{_uem['intent_cn']}　—　{_uem['reason']}")
        if _uem["issues"]:
            st.warning("；".join(_uem["issues"]))
        if f"fu_draft_{_key}" not in st.session_state:
            st.session_state[f"fu_draft_{_key}"] = _uem["body"]
        st.text_area("客户邮件草稿（可编辑）", key=f"fu_draft_{_key}", height=200,
                     label_visibility="collapsed")
        _b1, _b2 = st.columns(2)
        with _b1:
            if st.button("💾 存草稿并记入 Timeline", key=f"fu_save_{_key}",
                         use_container_width=True):
                tid = _fu_ensure_task(item)
                _db3.record_deal_activity(item["deal_id"], "FOLLOW_UP_EMAIL_DRAFTED",
                                          "保存跟进邮件草稿", actor="销售")
                if item.get("inquiry_id"):
                    _db3.record_activity(item["inquiry_id"], "FOLLOW_UP_EMAIL_DRAFTED",
                                         "保存跟进邮件草稿", actor="销售")
                st.toast("草稿已保存并记入 Timeline")
        with _b2:
            if st.button("✉️ 标记已发送 · 进入等待客户", key=f"fu_sent_{_key}",
                         type="primary", use_container_width=True,
                         help="确认发出后：任务进入 WAITING_CUSTOMER，3 天后复查"):
                tid = _fu_ensure_task(item)
                _db_fu_sent(tid, wait_days=3, actor="销售")
                st.toast("已记入 Timeline：跟进已发送 → 等待客户回复")
                st.rerun()
    # 操作：完成 / Snooze / 改期
    _o1, _o2, _o3 = st.columns(3)
    with _o1:
        st.button("✅ 完成跟进", key=f"fu_done_{_key}", use_container_width=True,
                  on_click=_fu_mark, args=(item, "COMPLETED"),
                  help="只完成本跟进任务，不改变 DealStage / 客户状态")
    with _o2:
        _sn = st.selectbox("稍后提醒", ["明天", "3 天", "下周"],
                           key=f"fu_snz_{_key}", label_visibility="collapsed")
        st.button("⏰ Snooze", key=f"fu_snooze_{_key}", use_container_width=True,
                  on_click=_fu_mark,
                  args=(item, "SNOOZED", _FU_SNOOZE_OPT[_sn]))
    with _o3:
        if st.button("📅 改期到今天", key=f"fu_re_{_key}",
                     use_container_width=True):
            _db_upd_fu(_fu_ensure_task(item), due_at=_fu_due_in(0), actor="销售")
            st.toast("已改期到今天并记入 Timeline")
            st.rerun()
    if item.get("inquiry_id"):
        _v1, _v2 = st.columns(2)
        with _v1:
            if st.button("👤 查看客户 / Deal", key=f"fu_view_{_key}",
                         use_container_width=True):
                _home_select_inquiry(item["inquiry_id"])
                st.toast("已选中，请到「📥 分析询盘」查看详情")
        with _v2:
            if item.get("id"):
                st.button("🕗 历史时间轴", key=f"fu_hist_{_key}",
                          use_container_width=True,
                          help="详见商机页 / 分析询盘详情 Timeline")


def _qblockers_of(item):
    """取该 deal 关联询盘的阻塞项（用于跟进邮件只问未解决信息）。"""
    qmap = {x["id"]: x for x in load_queue(None)}
    row = qmap.get(item.get("inquiry_id")) or {}
    return (row.get("need") or {}).get("blockers") or []


def _render_followup_workspace():
    """Tab2 跟进台：4 行动 KPI → 快速视图 → 跟进队列 → 手动创建。"""
    st.subheader("跟进台：今天该跟谁？")
    st.caption("跟进台 = 每天的行动清单　·　状态三轴独立："
               "询盘沟通 / 商机阶段 / 跟进任务")
    _items = _fu_load_items()
    now = datetime.datetime.now()
    # —— 4 个行动型 KPI（可点击筛选）——
    _k_overdue = [x for x in _items if x["fu_status"] == _fu.OVERDUE]
    _k_today = [x for x in _items if x["fu_status"] == _fu.DUE_TODAY]
    _k_wait = [x for x in _items if x["fu_status"] == _fu.WAITING]
    _k_p1 = [x for x in _items if x["pri"] == "P1"
             and x["fu_status"] not in (_fu.COMPLETED, _fu.CANCELLED)]
    _fv = st.session_state.get("fu_view", "今日跟进")
    _kc = st.columns(4, gap="small")
    _kpi_map = [("今日待跟进", len(_k_today), "fu_view", "今日跟进", "amber"),
                ("逾期未跟进", len(_k_overdue), "fu_view", "逾期", "red"),
                ("等待客户回复", len(_k_wait), "fu_view", "等待客户", "blue"),
                ("P1 商机", len(_k_p1), "fu_view", "P1", "green")]
    for _c, (_lb, _n, _sk, _sv, _tone) in zip(_kc, _kpi_map):
        with _c:
            st.button(f"{_lb}\n\n{_n}", key=f"fuk_{_sk}_{_sv}",
                      use_container_width=True, type="primary" if _n else "secondary",
                      help=f"点击筛选：{_sv}",
                      on_click=lambda _sv=_sv: st.session_state.update(fu_view=_sv))
    # —— 快速视图 ——
    _views = ["今日跟进", "逾期", "等待客户", "报价未回复", "样品未回复", "全部"]
    _fv = st.segmented_control("跟进视图", _views, default=_fv, key="fu_view",
                               label_visibility="collapsed") or "今日跟进"
    _fitems = []
    for x in _items:
        if _fv == "今日跟进" and x["fu_status"] in (_fu.DUE_TODAY, _fu.OVERDUE):
            _fitems.append(x)
        elif _fv == "逾期" and x["fu_status"] == _fu.OVERDUE:
            _fitems.append(x)
        elif _fv == "等待客户" and x["fu_status"] == _fu.WAITING:
            _fitems.append(x)
        elif _fv == "报价未回复" and x["reason"] == _fu.QUOTE_SENT_NO_REPLY \
                and x["fu_status"] not in (_fu.COMPLETED, _fu.CANCELLED):
            _fitems.append(x)
        elif _fv == "样品未回复" and x["reason"] == _fu.SAMPLE_SENT_NO_REPLY \
                and x["fu_status"] not in (_fu.COMPLETED, _fu.CANCELLED):
            _fitems.append(x)
        elif _fv == "全部":
            _fitems.append(x)
    st.markdown("<div class='workspace-section'><span class='title'>"
                f"跟进队列</span><span class='note'>"
                f"{_fv} · {len(_fitems)} 项 · 逾期优先排序</span></div>",
                unsafe_allow_html=True)
    if not _fitems:
        st.caption("✓ 暂无需要处理的跟进 —— 空间留给真正需要你的客户。")
    for _it in _fitems:
        _tone, _st_cn = _fu_reason_badge(_it)
        # ROUND 6.9 §2：默认卡只回答四件事 —— 谁 / 什么产品与数量 /
        # 什么时候到期（含逾期）/ 下一步做什么，外加一个主 CTA。
        # 邮件编辑器、完成、Snooze、改期、时间轴全部收进展开抽屉，
        # 卡片高度比原来下降约 40%，队列可以一屏扫完。
        _key = f"fuid_{_it['id'] or ('a' + str(_it['deal_id']) + _it['reason'])}"
        _open_key = f"fu_open_{_key}"
        _dot = {"red": "🔴", "amber": "🟠", "blue": "🔵", "green": "🟢",
                "low": "⚪"}.get(_tone, "⚪")
        _line = " · ".join(x for x in (str(_it["product"] or ""),
                                       str(_it["qty"] or "")) if x) or "产品待补充"
        _due = str(_it.get("due_at") or "未设时间")[:16]
        _due_tone = {"red": "var(--danger)", "amber": "var(--warning)"}.get(
            _tone, "var(--text-secondary)")
        with st.container(border=True):
            st.markdown(
                f"<div class='fucard {_tone}'>"
                f"<div class='r1'><b>{_ui_flag_esc(_it['company'] or _it['deal_title'] or '未知客户')}</b>"
                f"<span class='st'>{_dot} {_st_cn}</span></div>"
                f"<div class='r2'>{_ui_flag_esc(_line)}</div>"
                f"<div class='r3'><span style='color:{_due_tone};font-weight:700'>{_due}</span>"
                f"　·　{_ui_flag_esc(str(_it['nba']))}</div>"
                f"</div>", unsafe_allow_html=True)
            _cc = st.columns([1.05, 1.35])
            with _cc[0]:
                if st.button("✉️ 生成客户邮件", key=f"fu_cta_{_key}", type="primary",
                             use_container_width=True,
                             help="打开统一的客户邮件编辑器（自动判断邮件目的）"):
                    st.session_state[f"fu_gen_{_key}"] = True
                    st.session_state[_open_key] = True
                    st.rerun()
            with _cc[1]:
                with st.expander("处理（邮件 · 完成 · 稍后 · 详情）",
                                 expanded=bool(st.session_state.get(_open_key))):
                    _fu_render_detail(_it)
    # —— 手动创建跟进 ——
    with st.expander("➕ 手动创建跟进", expanded=False):
        _m_d = st.selectbox("商机 Deal", [f"#{o['id']} {o['title']}"
                                          for o in list_opportunities()],
                            key="fu_manual_deal")
        _m_r = st.selectbox("原因", [f"{r} · {_fu.REASON_CN[r]}"
                                    for r in _fu.REASONS], key="fu_manual_reason")
        _m_n = st.text_input("备注（可选）", key="fu_manual_note")
        _m_due = st.date_input("跟进日期", value=datetime.date.today(),
                               key="fu_manual_due")
        if st.button("创建跟进任务", key="fu_manual_create", type="primary"):
            deal_id = int(_m_d.split(" ")[0].lstrip("#"))
            reason = _m_r.split(" · ")[0]
            tid, reused = _db_create_fu(
                deal_id, reason,
                due_at=(datetime.datetime.combine(_m_due, datetime.time(18, 0))
                        .strftime("%Y-%m-%d %H:%M")),
                title=f"{_fu.REASON_CN.get(reason, reason)}（手动）",
                next_action=_fu.REASON_NBA.get(reason, ("跟进", "manual"))[0],
                note=_m_n, actor="销售")
            st.toast("已创建跟进任务" if not reused else "已有同类活跃任务，已复用未重复创建")
            st.rerun()


def _ui_flag_esc(s):
    import html as _h2
    return _h2.escape(str(s))


def _card(col, title: str, value, sub: str = "", tone: str = ""):
    """结论卡片：标题 + 数值 + 一句副注。"""
    cls = "card" + (f" {tone}" if tone in ("red", "amber", "green", "blue") else "")
    col.markdown(
        f"<div class='{cls}'><div class='t'>{title}</div><div class='v'>{value}</div>"
        f"{'<div class=\'s\'>' + sub + '</div>' if sub else ''}</div>",
        unsafe_allow_html=True)


def _new_analysis():
    """回到“新建分析”空白态（全局入口与侧边栏共用）。
    第十一轮（spec 十）：同时展开主工作区的「新建询盘分析」折叠区。"""
    st.session_state.selected_id = None
    st.session_state.analyzed = None
    st.session_state.pop("inquiry_text", None)
    st.session_state.new_inq_open = True
    st.rerun()


def _open_section(key: str):
    """展开某个默认折叠的区域（纯 UI 状态，不涉及业务数据）。"""
    st.session_state[key] = True


def _ago(created) -> str:
    """相对时间展示（实现在 queue_ui.py，无 Streamlit 依赖便于单测）。"""
    return _ui.ago(created)


def _flag(country) -> str:
    """国家名 → 国旗 emoji（实现在 queue_ui.py）。"""
    return _ui.flag(country)


def _hi_mid_low(score) -> str:
    """0-100 分 → 高/中/低 三档文字（仅展示，不改评分）。"""
    try:
        s = int(score)
    except Exception:
        return "—"
    return "高" if s >= 70 else ("中" if s >= 40 else "低")


# 业务员最关心的四个维度（从八维里挑出来做“3 秒结论”，口径不变）
_QUICK_DIMS = {"intent": "采购意向", "clarity": "需求清晰度",
               "order_value": "订单潜力", "conversion": "成交可能"}


_PROFILE_FIELDS = [
    ("company", "Company"),
    ("country", "Country"),
    ("contact_name", "Contact"),
    ("email", "Email"),
    ("website", "Website"),
]


def _compact_text(s: str, limit: int = 140) -> str:
    """Collapse display text to a short UI sentence. Does not change stored data."""
    txt = " ".join(str(s or "").split())
    return txt if len(txt) <= limit else txt[:limit - 1].rstrip() + "…"


def _quantity_text(info: dict) -> str:
    rev = (info or {}).get("quantity_revision") or {}
    if rev.get("current") is not None:
        try:
            return f"{int(rev['current']):,} {rev.get('unit') or info.get('quantity_unit') or 'pcs'}"
        except Exception:
            return f"{rev.get('current')} {rev.get('unit') or info.get('quantity_unit') or 'pcs'}"
    sems = info.get("quantity_semantics") or []
    if len(sems) > 1:
        return " / ".join(
            f"{s.get('role_short') or '数量'} {int(s['value']):,} {s.get('unit') or 'pcs'}"
            for s in sems[:3] if s.get("value") is not None)
    if info.get("quantity"):
        return f"{int(info['quantity']):,} {info.get('quantity_unit') or 'pcs'}"
    return "未识别"


def _price_text(info: dict) -> str:
    price = info.get("target_price")
    if not price:
        return "未提及"
    return f"{info.get('target_price_currency') or 'USD'} {price}"


def _product_text(info: dict, matches: list) -> str:
    prod = (info.get("product_query") or info.get("product")
            or info.get("product_name") or "")
    if prod:
        return str(prod)
    for p in matches or []:
        name = p.get("name_cn") or p.get("name")
        if name:
            return str(name)
    return "未识别"


def _timeline_text(info: dict) -> str:
    return (info.get("timeline") or info.get("delivery_time") or
            info.get("lead_time") or "未提及")


def _missing_fields(info: dict, gaps: list, blockers: list, known_email: str = "") -> dict:
    """Single display source for missing fields: sales blocking vs profile missing."""
    blocking = []
    seen = set()
    for b in blockers or []:
        key = str(b.get("field") if isinstance(b, dict) else b or "").strip()
        label = _field_cn(key) if key else ""
        if label and label not in seen:
            blocking.append({"key": key, "label": label})
            seen.add(label)
    if not blocking:
        for g in gaps or []:
            if g.get("level") != "A":
                continue
            label = str(g.get("name") or _field_cn(g.get("key")) or "").strip()
            if label and label not in seen:
                blocking.append({"key": g.get("key") or "", "label": label})
                seen.add(label)

    profile = []
    for key, label in _PROFILE_FIELDS:
        val = info.get(key) or (known_email if key == "email" else "")
        if not val:
            profile.append({"key": key, "label": label})
    return {"blocking": blocking, "profile": profile}


def _profile_complete(info: dict, known_email: str = "") -> tuple[int, int]:
    total = len(_PROFILE_FIELDS)
    done = sum(1 for key, _ in _PROFILE_FIELDS
               if info.get(key) or (known_email if key == "email" else ""))
    return done, total


def _first_value(*values) -> str:
    for value in values:
        if value is None:
            continue
        txt = str(value).strip()
        if txt and txt.lower() not in {"none", "null", "unknown", "未识别"}:
            return txt
    return ""


def _known_product_attribute(insight: dict, names: tuple[str, ...]) -> str:
    levels = (insight.get("product_hierarchy") or {}).get("levels") or []
    sems = insight.get("requirement_semantics") or []
    wanted = {n.lower() for n in names}
    for row in list(levels) + list(sems):
        key = str(row.get("level") or row.get("field") or row.get("key") or "").lower()
        if key in wanted and row.get("value"):
            return str(row.get("value"))
    return ""


def _customer_requirement_rows(info: dict, insight: dict, resolved: dict,
                               product: str, qty: str, price: str) -> list[tuple[str, str, str]]:
    blockers = resolved.get("customerBlockingItems") or []
    blocker_keys = {str(x.get("key") or x.get("field") or "").lower() for x in blockers if isinstance(x, dict)}
    product_blocked = bool({"product", "product_type", "category", "产品"} & blocker_keys)
    rows = []
    product_label = "Product"
    product_value = "⚠ 待确认" if product_blocked else f"✓ {product or '未识别'}"
    rows.append((product_label, product_value, "warn" if product_blocked else "ok"))
    spec = _first_value(info.get("specification"), info.get("customization"),
                        _known_product_attribute(insight, ("material", "材质", "specification")))
    if spec:
        rows.append(("Specification", f"✓ {_compact_text(spec, 52)}", "ok"))
    rows.append(("Quantity", qty or "未识别", "ok" if qty and qty != "未识别" else "warn"))
    rows.append(("Target", price, "ok" if price and price != "未提及" else ""))
    incoterm = _first_value(info.get("incoterm"), info.get("trade_term"))
    lead_time = _timeline_text(info)
    samples = _first_value(info.get("sample"), info.get("samples"),
                           _known_product_attribute(insight, ("sample", "samples")))
    customization = _first_value(info.get("customization"),
                                 _known_product_attribute(insight, ("customization", "logo", "packaging")))
    rows.extend([
        ("Incoterm", incoterm or "未提及", "ok" if incoterm else ""),
        ("Lead Time", lead_time, "ok" if lead_time != "未提及" else ""),
        ("Samples", samples or "未提及", "ok" if samples else ""),
        ("Customization", customization or "未提及", "ok" if customization else ""),
    ])
    outcome = resolved.get("customerRequestedOutcome") or ""
    if isinstance(outcome, (list, tuple, set)):
        has_reco = "REQUEST_PRODUCT_RECOMMENDATION" in {str(x) for x in outcome}
    else:
        has_reco = str(outcome) == "REQUEST_PRODUCT_RECOMMENDATION"
    rows.append(("Recommendation Request", "✓ 需要我方推荐方案" if has_reco else "未提及",
                 "ok" if has_reco else ""))
    return rows


def _render_requirement_compact(info: dict, insight: dict, resolved: dict, product: str, qty: str, price: str):
    import html as _h
    rows = _customer_requirement_rows(info, insight, resolved, product, qty, price)
    known_count = sum(1 for _, value, tone in rows if tone == "ok" or str(value).startswith("✓"))
    html = "<div class='req-compact'><div class='title'>客户需求</div><div class='req-grid'>"
    for key, value, tone in rows:
        html += (f"<div class='req-item {tone}'><div class='k'>{_h.escape(key)}</div>"
                 f"<div class='v'>{_h.escape(str(value))}</div></div>")
    html += f"</div><div class='req-note'>已确认 {known_count} 项 · 只展示推进销售所需字段，完整提取结果在折叠区查看。</div></div>"
    st.markdown(html, unsafe_allow_html=True)


def _render_timeline_compact(rows, wf: dict, created: str, has_draft: bool, has_blockers: bool):
    import html as _h
    tl = _wsx.build_timeline(rows, wf=wf, created=created,
                             has_draft=has_draft, has_blockers=has_blockers)
    html = "<div class='timeline-compact'><div class='title'>Activity Timeline</div>"
    if not tl:
        html += "<div class='req-note'>暂无活动记录。</div></div>"
    else:
        for item in tl[:4]:
            html += ("<div class='row'>"
                     f"<div class='ts'>{_h.escape(str(item.get('ts') or '')[:16])}</div>"
                     f"<div>{_h.escape(str(item.get('cn') or ''))}"
                     f"<span style='color:var(--text-secondary)'>　{_h.escape(str(item.get('desc') or ''))}</span></div>"
                     f"<div class='actor'>{_h.escape(str(item.get('actor') or ''))}</div></div>")
        html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def _summary_2line(info: dict, matches: list) -> str:
    parts = []
    who = " ".join(x for x in [info.get("country"), info.get("company")] if x)
    if who:
        parts.append(who)
    parts.append(f"采购 {_quantity_text(info)} {_product_text(info, matches)}")
    price = _price_text(info)
    if price != "未提及":
        parts.append(f"目标价 {price}")
    timeline = _timeline_text(info)
    if timeline != "未提及":
        parts.append(f"计划 {timeline}")
    return _compact_text("，".join(parts) + "。", 120)


def _summary_2line_resolved(info: dict, matches: list,
                            resolved: dict = None) -> str:
    resolved = resolved or {}
    req = resolved.get("resolvedRequirement") or {}
    info2 = dict(info or {})
    if req.get("quantity"):
        info2.pop("quantity_semantics", None)
        qn = req.get("currentQuantity")
        if qn is not None:
            info2["quantity"] = qn
            info2["quantity_unit"] = info2.get("quantity_unit") or "pcs"
    if resolved.get("customerProductRequirement"):
        info2["product_query"] = resolved.get("customerProductRequirement")
    summary = _summary_2line(info2, matches)
    if req.get("quantityChanged") and req.get("previousQuantity") is not None:
        cur = req.get("currentQuantity")
        prev = req.get("previousQuantity")
        if cur is not None:
            cur_txt = f"{int(cur):,}" if isinstance(cur, (int, float)) else str(cur)
            prev_txt = f"{int(prev):,}" if isinstance(prev, (int, float)) else str(prev)
            return _compact_text(summary.rstrip("。") + f"，当前数量 {cur_txt} pcs（由 {prev_txt} pcs 调整）。", 120)
    return summary


def _task_items_from_state(action_label: str, missing: dict, r_stat: str,
                           matches: list, draft: str) -> list:
    tasks = []
    if not matches:
        tasks.append(("Match Product", "P1", "待处理", "匹配产品"))
    for item in missing.get("blocking", [])[:3]:
        tasks.append((f"确认 {item['label']}", "P1", "待确认", "生成客户邮件"))
    if r_stat in ("ready_for_quotation", "READY_FOR_QUOTATION",
                  "preliminary_quote_ready", "PRELIMINARY_QUOTE_READY"):
        tasks.append(("Prepare Quotation", "P2", "待处理", "创建报价"))
    if draft:
        tasks.append(("Send Reply", "P2", "待处理", "生成客户邮件"))
    if not tasks and action_label:
        tasks.append((action_label, "P3", "待处理", "处理"))
    return tasks[:4]


def _render_ai_basis(lead: dict, insight: dict, report: dict, _ns):
    """「查看 AI 分析依据」折叠区：证据链 + 报价准备度依据 + 风险 + 一致性。

    只是把原第五层明细收进一个折叠区，内容与口径完全不变。
    """
    # 常量已统一在模块顶部导入（STATUS_DESC / ACTION_CN / CERT_STATUS_CN / SOURCE_CN）

    # —— 八维评分：每维分数 + 原因 + 事实→判断→分数证据链 ——
    st.markdown("**八维评分依据**")
    for d in lead.get("dims", []):
        st.markdown(f"**{d['name']}：{d['score']} / 100**")
        for r in d.get("detail", []):
            st.write(f"　· {r}")
        for e in d.get("evidence", []):
            st.caption(f"　　↳ 事实「{e['fact']}」→ 判断「{e['judgment']}」→ 分数 {e['score']}")
    if lead.get("reasons"):
        st.caption(lead["reasons"][-1])

    # —— 订单价值（不虚构金额） ——
    if lead.get("order_value_basis"):
        st.markdown(f"**💰 订单价值（置信度：{lead.get('order_value_confidence', 'low')}）**")
        for line in lead["order_value_basis"]:
            st.write(f"· {line}")
        st.caption("没有产品库真实单价时，订单价值只按采购规模档位估，不假装知道订单金额。")

    if not insight:
        return
    qr = insight.get("quotation_readiness") or {}

    # —— 报价准备度：每一分怎么来的 + A/B/C 确认项 ——
    status = _ns(qr.get("quotation_readiness_status", ""))
    if STATUS_DESC.get(status):
        st.markdown(f"**📊 报价准备度**　{STATUS_DESC[status]}")
    for line in qr.get("readiness_basis", []):
        st.write(f"· {line}")
    ci = qr.get("confirmation_items") or {}
    tier_cn = {"A": "🔴 A · 必须确认项（不确认不能正式报价）",
               "B": "🟡 B · 建议确认项（可先初步报价）",
               "C": "🟢 C · 可后续确认项（边谈边补）"}
    for tier in ("A", "B", "C"):
        items = ci.get(tier) or []
        if not items:
            continue
        st.markdown(f"**{tier_cn[tier]}**（缺 {len(items)} 项）")
        for it in items:
            sttxt = "部分确认" if it["state"] == "partially_confirmed" else "未确认"
            st.write(f"　· {it['label']}（{sttxt}）：{it['reason']} {it.get('advice', '')}")

    # —— 全部下一步动作（含时限） ——
    acts_all = insight.get("next_actions") or []
    if acts_all:
        st.markdown("**🚀 全部下一步动作（P0→P4）**")
        for a in acts_all:
            act = a.get("action_cn") or ACTION_CN.get(a["action"], a["action"])
            fld = f"　·　关联字段：{a['related_field']}" if a.get("related_field") else ""
            dl = f"　·　时限：{a['deadline']}" if a.get("deadline") else ""
            st.write(f"**{a.get('priority', '')} {act}**{fld}{dl}")
            st.caption(f"　↳ {a['reason']}")

    # —— 认证判定 / 软阻塞 ——
    cstatus = (insight.get("certification") or {}).get("certification_status", "unknown")
    if cstatus:
        st.caption(f"认证判定：{CERT_STATUS_CN.get(cstatus, cstatus)}")
    formal = qr.get("blockers_formal", [])
    if formal:
        st.markdown(f"**🟡 正式报价前再确认（{len(formal)} 项，可先初步报价）**")
        for b in formal:
            sev_icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(b["severity"], "")
            st.write(f"{sev_icon} **{b['field']}**　·　影响：{b['impact']}")
            st.caption(f"　↳ {b['reason']}")

    if insight.get("product_matching_note"):
        st.write(f"🔍 {insight['product_matching_note']}")

    # —— 风险（仅事实型） ——
    if insight.get("risks"):
        st.markdown(f"**⚠️ 风险提示（{len(insight['risks'])} 项，仅基于事实）**")
        for r in insight["risks"]:
            sev_icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(r["severity"], "")
            st.write(f"{sev_icon} **{r['type']}** — {r['reason']}")

    # —— 数据来源标注 ——
    ds = insight.get("data_sources")
    if ds:
        st.markdown("**🛡 数据来源标注（AI 推理不覆盖客户/公司/产品真实数据）**")
        for src_key in ("customer_information", "company_knowledge",
                        "product_data", "ai_inference"):
            st.markdown(f"**{SOURCE_CN.get(src_key, src_key)}**")
            for line in ds.get(src_key, []):
                st.write(f"　· {line}")
        st.caption(ds.get("rule", ""))

    # —— 系统一致性检查 ——
    cc = report.get("consistency") or []
    if cc:
        bad = sum(1 for c in cc if not c["ok"])
        st.markdown(f"**🧪 系统一致性检查（{'发现冲突 ' + str(bad) + ' 处' if bad else '全部通过'}）**")
        for c in cc:
            if c["ok"]:
                st.write(f"✅ {c['check']}：{c['detail']}")
            else:
                st.error(f"❌ {c['check']}：{c['detail']} ← 请优先人工核对，不要直接发错误结果")


def _kpi_snapshot() -> dict:
    """顶部 4 个 KPI + 「AI 今日建议」统计：只对现有数据做统计展示，不新增业务口径。"""
    rows = list_inquiries()   # 每行: (id, created, grade, score, ..., status, cg, clg, urgency, need)
    today = datetime.date.today().strftime("%Y-%m-%d")
    yest = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    stale_line = (datetime.datetime.now()
                  - datetime.timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
    today_n = sum(1 for r in rows if str(r[1]).startswith(today))
    yest_n = sum(1 for r in rows if str(r[1]).startswith(yest))
    todo_n = sum(1 for r in rows if r[7] == STATUS_TODO)
    done_n = sum(1 for r in rows if r[7] == STATUS_DONE)
    hi_n = miss_n = ready_n = stale_n = to_reply_n = to_quote_n = 0
    for r in rows:
        if r[7] != STATUS_TODO:
            continue
        need = r[11] if len(r) > 11 else {}
        wf0 = r[13] if len(r) > 13 else {}
        p, _ = calc_priority(r[8], r[2], r[3] or 0, r[10], STATUS_TODO)
        # 与侧栏队列同一口径：biz 派生规则一致，避免 KPI 与队列数字打架
        _biz = _wf.derive_biz(r[7], (need or {}).get("blockers") or [],
                              (need or {}).get("readiness") or "",
                              wf0.get("biz_status"),
                              bool((need or {}).get("has_draft")))
        if p == PRI_HIGH:
            hi_n += 1
        if _biz == _wf.READY_TO_REPLY:
            to_reply_n += 1
        if _biz == _wf.READY_FOR_QUOTE:
            to_quote_n += 1
        if need.get("blockers"):
            miss_n += 1
        if need.get("readiness") in ("ready_for_quotation", "quoted"):
            ready_n += 1
        if str(r[1]) <= stale_line:
            stale_n += 1
    # 第七轮：今日需跟进（follow_up_at 已到期 / 就是今天，且未完成、未结单）
    # ——与队列筛选、详情页同一套 followup_state 口径，避免"顶部 14 点击只有 10"
    fu_due_n = 0
    for r in rows:
        wf = r[13] if len(r) > 13 else {}
        if wf.get("deal_status"):
            continue
        if _wf.followup_state(wf.get("follow_up_at"), wf.get("follow_up_done")
                              ) in ("已逾期", "今日跟进"):
            fu_due_n += 1
    # CRM 商机 KPI：金额只汇总同一币种；多币种不错误相加，返回逐币种信息。
    try:
        _opps = list_opportunities()
    except Exception:
        _opps = []
    _active = [o for o in _opps if o.get("stage") not in _crm_core.TERMINAL]
    _deal_due_inquiries = {o.get("inquiry_id") for o in _active
                           if _crm_core.health_of(o)["status"] == "overdue" and o.get("inquiry_id")}
    _inquiry_due_ids = {r[0] for r in rows if _wf.followup_state(
        (r[13] if len(r) > 13 else {}).get("follow_up_at"),
        (r[13] if len(r) > 13 else {}).get("follow_up_done")) in ("已逾期", "今日跟进")}
    fu_due_n = len(_inquiry_due_ids | _deal_due_inquiries)
    _quoted = [o for o in _active if o.get("stage") == "QUOTED"]
    _money = {}
    _month = datetime.date.today().strftime("%Y-%m")
    _close_month = {}
    for _o in _active:
        _amt = _o.get("amount")
        if _amt:
            _cur = _o.get("currency") or "USD"
            _money[_cur] = _money.get(_cur, 0) + float(_amt)
            if str(_o.get("expected_close") or "").startswith(_month):
                _close_month[_cur] = _close_month.get(_cur, 0) + float(_amt)
    return {"today": today_n, "yest": yest_n, "todo": todo_n,
            "done": done_n, "high": hi_n, "miss": miss_n,
            "ready": ready_n, "stale": stale_n, "fu_due": fu_due_n,
            "to_reply": to_reply_n, "to_quote": to_quote_n,
            "active_opps": len(_active), "quoted_opps": len(_quoted),
            "pipeline_money": _money, "close_month_money": _close_month}


@st.cache_resource
def get_engine(mode: str):
    """构建分析引擎（缓存，避免每次交互重建）。"""
    import main
    if mode == "llm":
        from agent.llm_client import load_api_key
        key, _ = load_api_key()
        if not key:
            st.error("LLM 模式需要先配置 API Key（在 config.json 填入 DEEPSEEK_API_KEY）")
            st.stop()
    return main.build_engine(mode)


def resolve_deal_state(deal_id: int) -> dict:
    """按当前询盘/Deal ID 返回统一 DealWorkItem + resolvedState。

    UI 层统一从这里拿状态；内部复用 queue_ui.deal_work_item，不写库、
    不改 Deal 聚合、客户匹配或阶段规则。
    """
    rows = load_queue(None)
    opp_by_inq = {}
    for o in (list_opportunities() or []):
        details = o.get("details") or {}
        ids = []
        for iid in (o.get("inquiry_id"), details.get("current_inquiry_id")):
            if iid:
                ids.append(iid)
        for iid in (details.get("related_inquiry_ids") or []):
            if iid:
                ids.append(iid)
        for iid in set(ids):
            opp_by_inq[iid] = o
    tasks_by_opp = {}
    try:
        for t in (_db_mod.list_followup_tasks() or []):
            if t.get("status") == "OPEN" and t.get("fu_status") in (
                    "PENDING", "WAITING_CUSTOMER", "SNOOZED"):
                tasks_by_opp.setdefault(t.get("opportunity_id"), []).append(t)
    except Exception:
        tasks_by_opp = {}
    stage_of = {k: v.get("stage") for k, v in opp_by_inq.items()}
    group = next(
        (g for g in _ui.group_deal_threads(rows, stage_of)
         if any(x.get("id") == deal_id for x in g.get("items") or [])),
        None)
    if not group:
        cur = next((x for x in rows if x.get("id") == deal_id), None) or {}
        group = {"key": ("inquiry", deal_id), "lead": cur,
                 "items": [cur], "count": 1}
    return _ui.deal_work_item(group, opp_by_inq=opp_by_inq,
                              tasks_by_opp=tasks_by_opp)


# JS-style alias kept for the requested resolver name; UI code uses Python style.
resolveDealState = resolve_deal_state


# ==========================================================================
# ROUND 6.9 §5 · 统一客户邮件（第二十一轮单管线）
#   Deal Detail 是唯一共享详情页 —— 编写器必须在该页可达。
#   旧版把这段留在 _render_customer_workspace 的 return 之后（废弃分支），
#   导致所有「生成客户邮件」CTA 打开的是空区域。此处为其唯一实现（MOVE）。
# ==========================================================================
def _build_customer_mail_ctx(id_, text, info, matches, gaps, insight,
                             r_stat, biz, draft):
    """统一客户邮件上下文（意图自动判定 + 单生成管线，口径不变）。"""
    info = info or {}
    matches = matches or []
    gaps = gaps or []
    insight = insight or {}
    text = text or ""
    draft = draft if isinstance(draft, str) else ""
    r_stat = r_stat or ""
    biz = biz or ""
    # —— 第二十一轮：统一客户邮件上下文（意图自动判定 + 单生成管线）——
    # 业务员不再需要在“追问 / 回复 / 跟进”之间选；系统按当前状态自动判断。
    _mail_product = ""
    try:
        _mail_product = ((insight.get("product_match") or {}).get("customer_product")
                         or info.get("product_query") or "")
        if not _mail_product:
            from agent.extractor import extract_customer_product
            _mail_product = extract_customer_product(text) or ""
    except Exception:
        pass
    _fu_reason = ""
    if id_ is not None:
        try:
            _m_opps = [o for o in (list_opportunities() or [])
                       if o.get("inquiry_id") == id_]
            if _m_opps:
                for _mt in _db_mod.list_followup_tasks(
                        opportunity_id=_m_opps[-1]["id"]) or []:
                    if _mt.get("fu_status") not in ("COMPLETED", "CANCELLED"):
                        _fu_reason = _mt.get("reason") or _fu_reason
        except Exception:
            pass
    _mail_ctx = dict(
        text=text, info=info, matches=matches, gaps=gaps,
        stored_draft=draft or "", customer_product=_mail_product,
        seller_company=(SELLER.get("company") or ""),
        biz=biz,
        resolved_state=(resolve_deal_state(id_).get("resolvedState") if id_ is not None else {}),
        quotation_ready=(r_stat in ("READY_FOR_QUOTATION", "ready_for_quotation")),
        fu_reason=_fu_reason,
        known=[k for k, v in info.items()
               if k in ("email", "company", "website") and v],
        unresolved=[m.get("name", "") for m in gaps
                    if m.get("ask_timing") == "ASK_NOW"][:5],
        insight=insight,
    )
    try:
        _mail_intent, _mail_why = _emu.determine_email_intent(_mail_ctx)
    except Exception:
        _mail_intent, _mail_why = _emu.REPLY_INQUIRY, "按当前状态回复客户。"
    return _mail_ctx


def _render_deal_closure(id_, biz, wf=None):
    """回复闭环：设置跟进时间 + 人工确认结单（成交 / 丢单）。

    ROUND 6.9 §5：与统一邮件编写器同属 Deal Detail 的执行闭环，
    必须与「生成客户邮件」在同一页可达（fuset_ / deal_won_ 键不变）。
    """
    biz = biz or ""
    # 说明：wf 为工作流字段 dict（数据），状态机判断统一走 workflow 模块函数
    _ = wf
    if id_ is None:
        return
    if id_ is not None and biz in ("REPLIED", "FOLLOW_UP", "QUOTED",
                                        "NEGOTIATING", "READY_FOR_QUOTE"):
        with st.expander("创建跟进", expanded=st.session_state.get(f"ui_fuzone_{id_}", False)):
            st.selectbox("跟进时间", FU_OPTIONS, key=f"compact_fu_pick_{id_}", index=1)
            if st.button("⏰ 设置跟进", key=f"fuset_{id_}",
                         use_container_width=True, type="primary"):
                st.session_state[f"fu_pick_{id_}"] = st.session_state.get(f"compact_fu_pick_{id_}")
                _do_set_followup(id_)
                st.rerun()

    if id_ is not None and biz in ("WON", "LOST"):
        st.success("已成交，恭喜！" if biz == "WON" else "已标记丢单（可在跟进中复盘原因）")
    elif id_ is not None and _wf.can_transition(biz, "WON"):
        with st.expander("结单（成交 / 丢单 · 需人工确认）", expanded=False):
            _deal_ok = st.checkbox("我确认这是最终业务结果", key=f"deal_ok_{id_}")
            _dc1, _dc2 = st.columns(2)
            with _dc1:
                st.button("标记成交", key=f"deal_won_{id_}",
                          use_container_width=True, disabled=not _deal_ok,
                          on_click=_do_mark_deal, args=(id_, "WON"))
            with _dc2:
                st.button("标记丢单", key=f"deal_lost_{id_}",
                          use_container_width=True, disabled=not _deal_ok,
                          on_click=_do_mark_deal, args=(id_, "LOST"))



def _render_deal_pipeline(id_, biz, biz_cn, act, fu_state, fu_at, st_blockers):
    """Deal Detail ② Pipeline：阶段知识卡 + 受状态机约束的阶段推进。

    ROUND 6.9 §5：与统一邮件编写器 / 跟进 / 结单同属 Deal Detail 的执行闭环，
    必须与它们在同一页可达（ws_pipe_* 键与状态机判断完全不变）。
    """
    act = act or {}
    st_blockers = st_blockers or []
    fu_state = fu_state or ""
    biz = biz or ""
    biz_cn = biz_cn or ""
    _fu_at = fu_at or ""
    import html as _h
    # ========== ② Pipeline（真实状态 + 点击查看阶段知识 + 推进） ==========
    _fpos = _crm.funnel_position(biz)
    _pipe_html = "<div class='pipe-line'>"
    for _i, (_sn, _sk) in enumerate(_crm.FUNNEL):
        if _fpos >= 0 and _i < _fpos:
            _cls = "done"
        elif _i == _fpos:
            _cls = "cur"
        elif _i == 6:
            _cls = "won"
        else:
            _cls = ""
        _pipe_html += (f"<span class='pchip {_cls}'>"
                       + ("✓ " if _cls == "done" else "" if _cls != "cur" else "● ")
                       + f"{_sn}</span>")
        if _i < len(_crm.FUNNEL) - 1:
            _pipe_html += "<span class='pipe-arw'>→</span>"
    if biz == "LOST":
        _pipe_html += "<span class='pipe-arw'>→</span><span class='pchip lost'>⚫ LOST 已丢单（可复活到谈判）</span>"
    elif biz == "ON_HOLD":
        _pipe_html += "<span class='pipe-arw'>→</span><span class='pchip lost'>⏸ 暂缓中</span>"
    elif biz == "WON":
        _pipe_html += "<span class='pipe-arw'>→</span><span class='pchip won'>🏆 已成交</span>"
    _pipe_html += "</div>"

    with st.container(border=True):
        _pd = st.columns([1.2, 6.6])
        with _pd[0]:
            st.markdown("<div style='font-size:.92rem;font-weight:800'>销售漏斗 Pipeline</div>",
                        unsafe_allow_html=True)
        with _pd[1]:
            st.markdown(_pipe_html, unsafe_allow_html=True)
        # 点击阶段 → 看「进入条件 / 缺失信息 / AI建议 / 完成条件」（真实状态驱动）
        _pipe_opts = list(_wsx.FUNNEL_CN)
        _cur_cn = (_wsx.FUNNEL_CN[_fpos] if 0 <= _fpos < 7 else None)
        _sel_i = st.segmented_control(
            "查看阶段", _pipe_opts,
            default=_cur_cn if _cur_cn else None,
            key=f"ws_pipe_{id_}",
            help="点击任一阶段查看：进入条件 / 当前缺失信息 / AI 建议 / 完成条件；"
                 "可推进到目标阶段（受状态机约束）")
        _sel_cn = _sel_i or _cur_cn
        _sel_idx = _pipe_opts.index(_sel_cn) if _sel_cn in _pipe_opts else -1
        if 0 <= _sel_idx < 7:
            # 阶段知识卡（静态）
            st.markdown(_wsx.stage_knowledge_html(_sel_idx), unsafe_allow_html=True)
            # 缺失信息（真实 blockers / 跟进逾期，不编造）
            _miss_parts = []
            if st_blockers:
                _miss_cn = "、".join(_field_cn(f) for f in st_blockers[:4])
                _miss_parts.append(f"阻塞项 {len(st_blockers)} 项：{_miss_cn}")
            if fu_state == "已逾期":
                _miss_parts.append(f"跟进已逾期（原计划 {_fu_at or '—'}）")
            elif fu_state == "今日跟进":
                _miss_parts.append("今日到期需跟进")
            _miss_txt = "；".join(_miss_parts) if _miss_parts else "暂无阻塞（关键信息齐全）"
            st.markdown(
                f"<div class='pipe-know'><div class='pk-row'>"
                f"<span class='pk-lb gap'>缺失信息</span><span>{_h.escape(_miss_txt)}</span>"
                "</div></div>", unsafe_allow_html=True)
            # AI 建议下一步（复用 workflow 口径）
            _act_txt = (act.get("label") or "等待客户反馈")
            _act_p = (act.get("priority") or "")
            st.markdown(
                f"<div class='pipe-know'><div class='pk-row'>"
                f"<span class='pk-lb ai'>AI 建议</span><span>"
                f"<b>{_h.escape(_act_txt)}</b>"
                + (f"（优先级 {_h.escape(_act_p)}）" if _act_p else "") + "</span></div></div>",
                unsafe_allow_html=True)
            # 推进动作（真实状态机：目标段映射 biz，WON 走人工结单）
            _target = _wsx.STAGE_TARGET_BIZ[_sel_idx]
            _allow = _target != "WON" and _wf.can_transition(biz, _target)
            _btn_c1, _btn_c2 = st.columns([1.1, 4])
            with _btn_c1:
                if _sel_idx == _fpos and biz not in ("LOST", "ON_HOLD"):
                    st.button("已在本阶段", key=f"ws_pipe_cur_{id_}",
                              disabled=True, use_container_width=True)
                elif _target == "WON":
                    st.button(f"推进到「{_sel_cn}」", key=f"ws_pipe_won_{id_}",
                              disabled=True, use_container_width=True,
                              help="成交需人工确认：请在下方「🏁 结单」区操作")
                elif _allow:
                    st.button(f"推进到「{_sel_cn}」", key=f"ws_pipe_go_{id_}",
                              use_container_width=True, type="primary",
                              on_click=_do_set_stage, args=(id_, _target),
                              help="写入真实销售阶段并记入 Timeline")
                else:
                    st.button(f"推进到「{_sel_cn}」", key=f"ws_pipe_no_{id_}",
                              disabled=True, use_container_width=True,
                              help="状态机不允许跳变，需按流程逐步推进")
            with _btn_c2:
                if _sel_idx == _fpos and biz in ("LOST", "ON_HOLD"):
                    _extra_note = ("已丢单：可先推进到「谈判」复活" if biz == "LOST"
                                   else "暂缓中：恢复请先推进到其它活跃阶段")
                    st.caption(_extra_note)
                elif not _allow and _target != "WON":
                    st.caption(f"状态机不允许从「{biz_cn}」直接跳到「{_sel_cn}」。"
                               "请按销售流程逐步推进（或用右下阶段下拉退到暂缓再调整）。")
                elif _target == "WON":
                    st.caption("成交 = 最终业务结果，必须人工确认（AI 不自动判成交）。")
                else:
                    st.caption("点击后将更新真实销售阶段（Current Stage 随之变化）并记录一条 Timeline。")
            _smsg = st.session_state.get(f"stage_msg_{id_}")
            if _smsg:
                st.caption(_smsg)
        else:
            st.caption("该询盘当前不在七段主流程中（丢单/暂缓），阶段知识卡仅对活跃流程展示。")

def _render_customer_email_composer(id_, mail_ctx, biz, draft_text=""):
    """渲染「✉️ 生成客户邮件」唯一编写器（人工复核后才可标记发送）。

    draft_box_{rk} / mark_replied_{id_} 沿用既有 key 约定：
    保存草稿与确认发送的闭环（update_draft / _mark_replied）完全复用。
    """
    _rk = id_ if id_ is not None else "new"
    draft = draft_text or ""
    biz = biz or ""
    _mail_intent = mail_ctx.get("mail_intent") or _emu.REPLY_INQUIRY
    _mail_why = mail_ctx.get("mail_intent_why") or "按当前状态回复客户。"
    # —— 第二十一轮：统一客户邮件 Composer（追问/回复/报价/跟进共用一套）——
    # 显示：邮件目的（自动判断）→ Subject → Body → 重新生成 / 编辑 / 保存草稿 /
    # 确认发送（人工复核后手动确认，绝不自动外发）。草稿 key 沿用 draft_box_{rk}，
    # 保存/回复闭环与既有 update_draft / _mark_replied 完全一致。
    if id_ is not None and f"mail_seeded_{_rk}" not in st.session_state:
        try:
            _mres0 = _emu.generate_customer_email(mail_ctx, intent="AUTO")
        except Exception as _me:
            _mres0 = {"body": draft or "", "subject": "", "issues": [str(_me)[:80]],
                      "intent": _emu.REPLY_INQUIRY, "intent_cn": "回复客户",
                      "reason": ""}
        st.session_state[f"mail_subject_{_rk}"] = _mres0.get("subject") or ""
        st.session_state[f"draft_box_{_rk}"] = _mres0.get("body") or ""
        st.session_state[f"mail_res_{_rk}"] = _mres0
        st.session_state[f"mail_seeded_{_rk}"] = True
    _exp_mail = st.session_state.get(f"ui_mail_{_rk}", False)
    with st.expander("✉️ 生成客户邮件（邮件目的自动判断 · 草稿不自动发送）",
                     expanded=_exp_mail):
        if id_ is None:
            st.caption("分析保存后即可生成统一客户邮件。")
        else:
            _sel_codes = [c for c, _ in _emu.SELECT_OPTIONS]
            _sel_map = dict(_emu.SELECT_OPTIONS)
            _cur_sel = st.session_state.get(f"mail_sel_{_rk}", "AUTO")
            if _cur_sel not in _sel_map:
                _cur_sel = "AUTO"
            _cA, _cB = st.columns([2.6, 1], gap="small")
            with _cA:
                _choice = st.selectbox(
                    "邮件目的（默认自动判断）", _sel_codes,
                    index=_sel_codes.index(_cur_sel),
                    key=f"mail_sel_{_rk}",
                    format_func=lambda c: {"AUTO": "自动（推荐）"}.get(c, _sel_map[c]),
                    help="自动=按询盘状态/商机阶段/跟进原因判定；也可人工指定类型。")
                if _choice == "AUTO":
                    st.caption(f"🎯 系统判断邮件目的：{_emu.INTENT_CN.get(_mail_intent, _mail_intent)}"
                               f"　—　{_mail_why}")
                else:
                    st.caption(f"已指定邮件目的：{_sel_map.get(_choice, _choice)}"
                               "（点「重新生成」应用）")
            with _cB:
                _mres = st.session_state.get(f"mail_res_{_rk}") or {}
                _m_issues = st.session_state.get(f"mail_issues_{_rk}") or _mres.get("issues") or []
                st.caption("安全策略：无验证不承诺 · 问句 ≤3 · 不索取已知信息 · 不自动发送")
            if st.button("🔁 重新生成（按所选邮件目的）", key=f"mail_regen_{_rk}",
                         use_container_width=False):
                _nres = _emu.generate_customer_email(mail_ctx,
                                                     intent=_choice)
                st.session_state[f"mail_subject_{_rk}"] = _nres.get("subject") or ""
                st.session_state[f"draft_box_{_rk}"] = _nres.get("body") or ""
                st.session_state[f"mail_res_{_rk}"] = _nres
                st.session_state[f"mail_issues_{_rk}"] = _nres.get("issues") or []
                st.toast(f"已按「{_nres.get('intent_cn')}」重新生成草稿")
                st.rerun()
            if _m_issues:
                st.warning("**HUMAN REVIEW REQUIRED** 邮件含未经公司知识库验证的内容，"
                           "仅可人工复核后手动发送：\n\n"
                           + "\n".join("- " + str(i) for i in _m_issues[:5]))
            st.text_input("Subject", key=f"mail_subject_{_rk}")
            st.text_area("客户邮件草稿（可编辑 · 发送前人工复核）",
                         key=f"draft_box_{_rk}", height=200,
                         label_visibility="collapsed")
            _c1, _c2, _c3, _c4 = st.columns([1, 1, 1.6, 1], gap="small")
            with _c1:
                st.download_button("⬇️ 下载 txt",
                                   st.session_state.get(f"draft_box_{_rk}", ""),
                                   file_name="customer_email.txt",
                                   key=f"mail_dl_{_rk}", use_container_width=True)
            with _c2:
                if st.button("💾 保存草稿", key=f"mail_save_{_rk}",
                             use_container_width=True,
                             help="保存修改到该询盘草稿并记入 Timeline"):
                    _ws_save_draft(id_)
            with _c3:
                if biz not in ("REPLIED", "FOLLOW_UP", "QUOTED", "NEGOTIATING",
                                    "WON", "LOST", "ON_HOLD"):
                    if st.button("📤 确认发送（人工复核后手动确认）",
                                 key=f"mark_replied_{id_}", type="primary",
                                 use_container_width=True,
                                 help="只标记为已发送并推进状态，系统不会自动外发"):
                        _mark_replied(id_)
                        st.toast("已标记为已发送，下一步可设置跟进时间")
                        st.rerun()
            st.caption("ℹ️ 发送前请人工复核：本工作台从不自动发送邮件。"
                       "「保存草稿」与「确认发送」都会写入 Timeline。")


def _render_customer_workspace(ctx):
    """Phase 2 · Customer + Opportunity Workspace 主渲染。

    自上而下四块：① Customer Header（含四操作按钮）
    ② Pipeline（真实状态 + 点击阶段查看进入/缺失/AI建议/完成条件 + 推进）
    ③ AI Sales Assistant + Activity Timeline 双栏
    ④ Related Records（相关询盘/产品/报价/联系人）
    ctx：render_report 里收集好的上下文 dict（口径与侧栏队列一致）。
    所有可写动作都记 Activity；状态持久化到库，刷新/重开不丢失。
    """
    import html as _h
    import json
    id_ = ctx["id"]
    info = ctx["info"] or {}
    matches = ctx["matches"] or []
    lead = ctx["lead"] or {}
    insight = ctx["insight"] or {}
    prelim = ctx["prelim"] or []          # 阻塞项对象列表（blockers_preliminary）
    st_blockers = ctx["st_blockers"] or []  # 阻塞项字段 key 列表
    wf = ctx["wf"] or {}
    biz = ctx["biz"]
    biz_cn = ctx["biz_cn"]
    act = ctx["act"] or {}
    fu_state = ctx["fu_state"] or ""
    r_stat = ctx["r_stat"]
    r_cn = ctx["r_cn"]
    r_score = ctx["r_score"]
    known_email = ctx["known_email"] or ""
    created = ""
    stale48 = False
    _term = biz in ("WON", "LOST", "ON_HOLD")

    # —— 客户级 / 商机聚合（与侧栏同一取数口，保证 Opportunity 口径一致）——
    try:
        _q_all = load_queue(None)
    except Exception:
        _q_all = []
    _cur = next((x for x in _q_all if x.get("id") == id_), None)
    created = created or str((_cur or {}).get("created") or "")
    if created and ctx.get("stt") == STATUS_TODO:
        try:
            stale48 = (datetime.datetime.now()
                       - datetime.datetime.strptime(
                           created[:16], "%Y-%m-%d %H:%M")
                       ).total_seconds() > 48 * 3600
        except Exception:
            stale48 = False
    cid = (_cur or {}).get("cust_id")
    _cust_items = ([x for x in _q_all if x.get("cust_id") == cid]
                   if cid else ([_cur] if _cur else []))
    _opp = _crm.opportunity_of(_cust_items)
    # 客户档案行（list_customers 末位 = contacts JSON）
    _cust = None
    try:
        _cust = next((c for c in list_customers() if c[0] == cid), None)
    except Exception:
        _cust = None

    # —— Phase 3：AI Priority + Next Best Action + Queue Score（只读，不写库）——
    # 详情页有完整 lead/matches/insight，比队列行的派生摘要更精确；
    # 队列行 aip3/qs3（load_queue 已算）作为同源兜底。
    _p3r = None
    try:
        _p3_item = dict(id=id_, created=created,
                        status=ctx.get("stt") or STATUS_TODO, biz=biz,
                        need=((_cur or {}).get("need") or {}),
                        wf=wf or {}, matches=matches or [], lead=lead or {},
                        insight=insight or {})
        _p3r = _p3.analyze(_p3_item, act)
    except Exception:
        _p3r = None
    _aip = (_p3r or {}).get("aip")
    _aip_rows = (_p3r or {}).get("rows") or []
    _aip_lvl = (_p3r or {}).get("level_cn") or ""
    _aip_fml = (_p3r or {}).get("formula") or ""
    _nba = (_p3r or {}).get("nba") or {}
    _qs3 = ((_p3r or {}).get("qs") if _p3r else None) or (_cur or {}).get("qs3")

    # ========== ① Customer Header ==========
    _co_name = str(info.get("company") or "").strip()
    _ct_name = str(info.get("contact_name") or "").strip()
    # 本次询盘未识别公司/联系人但客户档案里有 → 用档案补齐（真实数据，非编造）
    if not _co_name and _cust and len(_cust) > 1 and _cust[1]:
        _co_name = str(_cust[1]).strip()
    if not _ct_name and _cust and len(_cust) > 4 and _cust[4]:
        _ct_name = str(_cust[4]).strip()
    _sub_bits = []
    _fg = _ui.flag(info.get("country"))
    _sub_bits.append(f"{_fg} {info.get('country')}" if info.get("country") else "")
    if _ct_name:
        _sub_bits.append(f"联系人：{_ct_name}")
    if known_email or info.get("email"):
        _sub_bits.append(f"邮箱：{_h.escape(known_email or info.get('email'))}")
    _sub_bits.append(f"询盘 #{id_}")
    _sub_bits.append(_ui.ago(created) or str(created)[:10])
    if _cust_items:
        _sub_bits.append(f"同客户 {len(_cust_items)} 条询盘")
    _brand = (
        "<div class='ws-hd'>"
        f"<div class='co'>{_h.escape(_co_name or _ct_name or '未知客户')}"
        f"{'<span style=\'font-size:.72rem;opacity:.55;font-weight:600\'>（未识别公司）</span>' if not _co_name and _ct_name else ''}</div>"
        f"<div class='sub'>" + "　·　".join(b for b in _sub_bits if b) + "</div></div>")

    _g_letter = ctx["g_letter"]
    _pri_name = ctx["pri_name"]
    _pts = ctx["pts"] or 0
    _score = ctx["score"]
    _opp_val = (f"{_opp['currency_hint']} {_opp['value']:,.0f}"
                if _opp.get("value") else "")
    _tone_biz, _ = _wf.BIZ_STYLE.get(biz, ("blue", "🔵"))
    _tone_cls = {"red": "red", "amber": "amber", "green": "green",
                 "low": "amber", "blue": "blue", "mid": "amber"}.get(_tone_biz, "")

    # 最近联系 = 人工回复时间优先，否则询盘到达时间（真实字段，不伪造）
    _last_act_ts = ""
    try:
        _lacts = list_activity(id_)
        _last_act_ts = max([a[1] for a in _lacts], default="") if _lacts else ""
    except Exception:
        _last_act_ts = ""
    _last_contact = wf.get("last_replied_at") or _last_act_ts or created or "—"
    _fu_at = (wf.get("follow_up_at") or "")[:16]
    _fu_cls = "red" if fu_state == "已逾期" else ("amber" if fu_state == "今日跟进" else "")
    _rq_txt = f"{r_cn}"

    # ========== ROUND 2 UI simplification：Deal Action View ==========
    _resolved_wv = resolve_deal_state(id_)
    _resolved = _resolved_wv.get("resolvedState") or {}
    _ws_err = st.session_state.get("ws_exec_error")
    if _ws_err and _ws_err.get("iid") == id_:
        st.error("⚠️ 当前动作执行失败：" + str(_ws_err.get("msg") or "请重试"))
    _resolved_action = ((_resolved.get("nextBestAction") or {}).get("type")
                        or "SEND_REPLY")
    _hero_what = str(((_resolved.get("nextBestAction") or {}).get("label"))
                     or _nba.get("action_label") or act.get("label")
                     or "查看详情")
    _hero_why = str(_nba.get("reason") or "").strip()
    if not _hero_why:
        if (_resolved.get("changedFields") or []):
            _hero_why = "同一客户同一产品的历史往来中出现需求变更，需先确认最新口径。"
        elif _resolved.get("needsRequote"):
            _hero_why = "客户需求发生变化，已有报价可能需要更新。"
        elif st_blockers:
            _product_blocked = any(str(x).lower() in {"product", "product_type", "category"} for x in st_blockers)
            if _product_blocked:
                _hero_why = "客户已提供部分产品属性，但尚未确认具体产品类型，因此暂不可报价。"
            else:
                _hero_why = "存在关键阻塞项，需先确认后再推进。"
        elif r_stat in ("READY_FOR_QUOTATION", "ready_for_quotation"):
            _hero_why = "需求信息已基本完整，可进入报价准备。"
        elif ctx.get("draft_text"):
            _hero_why = "客户邮件草稿已准备，可人工复核后发送。"
        elif _resolved.get("internalPrerequisites"):
            _hero_why = "客户需求已基本明确，但内部供应能力和报价条件尚未确认。"
        elif _resolved.get("customerProductRequirement"):
            _hero_why = "客户产品需求已明确，下一步应推进回复、报价或内部确认。"
        else:
            _hero_why = "客户需求已有可执行线索，下一步应推进当前销售动作。"
    _hero_why = _compact_text(_hero_why, 86)
    _product = (_resolved.get("customerProductRequirement")
                or _product_text(info, matches))
    _req_resolved = _resolved.get("resolvedRequirement") or {}
    _qty = _req_resolved.get("quantity") or _quantity_text(info)
    _price = _price_text(info)
    _recent = ""
    try:
        _acts_for_recent = list_activity(id_)
    except Exception:
        _acts_for_recent = []
    if _acts_for_recent:
        _last = _acts_for_recent[-1]
        _recent = f"{_wsx.act_meta(_last[0])[1]} · {str(_last[1])[:16]}"
    elif created:
        _recent = f"客户 {_ui.ago(created)} 发送询盘"
    else:
        _recent = "暂无活动记录"
    _cta_label = _resolved.get("primaryCta") or _hero_what
    _priority_text = str(_resolved.get("priority") or ctx.get("g_letter") or "—")
    # —— ROUND 7.1 §14：AI 销售助手消费 Deal Progression Engine 的输出 ——
    #   助手不再自己推导"健康/停留/下一步"，只展示 domain 层的解析结果。
    #   仍然保留原有四段结构（当前状态 / 下一步 / 原因 / 主 CTA），
    #   额外补充：Deal Health、Stage Aging、Next Activity、推荐的阶段迁移。
    _prog = {}
    try:
        _prog = _pg.progression_of(id_)
    except Exception:
        _prog = {}
    _pg_health_cn = _prog.get("health_cn") or ""
    _pg_health_icon = _prog.get("health_icon") or ""
    _pg_age = _prog.get("stage_age_text") or ""
    _pg_reason = _prog.get("primary_reason") or ""
    _pg_next = _prog.get("card_next") or ""
    _pg_na_status = _prog.get("next_activity_status") or ""
    _pg_na_cn = _prog.get("next_activity_status_cn") or ""
    _pg_trans = _prog.get("recommended_transition_cn") or ""
    _pg_trans_reason = _prog.get("transition_reason") or ""
    _pg_health_row = ""
    if _pg_health_cn:
        _age_bit = f"　·　{_h.escape(_pg_age)}" if _pg_age and _pg_age != "今天" else ""
        _na_bit = ""
        if _pg_na_cn and _pg_na_status not in ("", "CLOSED"):
            _na_tone = " style='color:var(--danger);font-weight:700'" \
                if _pg_na_status == "OVERDUE" else (
                    " style='color:var(--warning);font-weight:650'"
                    if _pg_na_status in ("MISSING", "DUE_TODAY") else "")
            _na_bit = f"　·　下一步<span{_na_tone}>{_h.escape(_pg_na_cn)}</span>"
        _pg_health_row = (
            f"<div class='prog'><b>Deal Health</b> {_pg_health_icon} "
            f"{_h.escape(_pg_health_cn)}{_age_bit}{_na_bit}</div>")
    # 推荐阶段迁移：只提示，不自动执行（§9）；原因走「为什么?」
    _pg_trans_row = ""
    if _pg_trans:
        _pg_trans_row = (
            f"<div class='prog tr'>建议推进至 {_h.escape(_pg_trans)}"
            f"　<span class='hint'>（需人工确认）</span></div>")
    st.markdown(
        "<div class='deal-action-view'>"
        "<div class='assistant-title'>AI销售助手</div>"
        "<div class='head'>"
        f"<div><div class='company'>{_h.escape(_co_name or _ct_name or '未知客户')}</div>"
        f"<div class='sub'>{_h.escape(_product)} · {_h.escape(_qty)}</div></div>"
        f"<div class='state'><span>状态</span><b>{_h.escape(biz_cn)}</b>"
        f"<span>{_h.escape(fu_state or _rq_txt or '正常推进')}</span></div>"
        "</div>"
        + _pg_health_row
        + "<div class='nba'><div class='lb'>下一步</div>"
        f"<div class='what'>{_h.escape(_hero_what)}</div>"
        f"<div class='why'>原因：{_h.escape(_pg_reason or _hero_why)}</div></div>"
        + _pg_trans_row
        + "</div>",
        unsafe_allow_html=True)
    _av1, _avwhy, _av2 = st.columns([1.25, .9, 2.1], gap="small")
    with _av1:
        if _resolved_action == "MARK_COMPLETE":
            st.button(_cta_label, key=f"action_primary_{id_}", type="primary",
                      use_container_width=True, on_click=_done_and_next,
                      args=(id_,))
        elif _resolved_action in ("MATCH_PRODUCT", "CHECK_SUPPLIER_CAPABILITY", "CHECK_PRODUCT_OPTIONS", "PREPARE_PRODUCT_RECOMMENDATION"):
            st.button(_cta_label, key=f"action_primary_{id_}", type="primary",
                      use_container_width=True, on_click=_execute_primary_action,
                      args=(id_, _resolved_action, None))
        elif _resolved_action in ("PREPARE_QUOTATION", "UPDATE_QUOTATION",
                                  "CHECK_INTERNAL_QUOTATION_PREREQUISITES",
                                  "PREPARE_UPDATED_QUOTATION"):
            st.button(_cta_label, key=f"action_primary_{id_}", type="primary",
                      use_container_width=True, on_click=_execute_primary_action,
                      args=(id_, _resolved_action, f"ui_quote_{id_}"))
        elif _resolved_action == "CONFIRM_REQUIREMENT_CHANGE":
            st.button(_cta_label, key=f"action_primary_{id_}", type="primary",
                      use_container_width=True, on_click=_execute_primary_action,
                      args=(id_, _resolved_action, f"ui_mail_{id_}"))
        elif _resolved_action == "FOLLOW_UP":
            st.button(_cta_label, key=f"action_primary_{id_}", type="primary",
                      use_container_width=True, on_click=_execute_primary_action,
                      args=(id_, _resolved_action, f"ui_fuzone_{id_}"))
        else:
            st.button(_cta_label, key=f"action_primary_{id_}", type="primary",
                      use_container_width=True, on_click=_execute_primary_action,
                      args=(id_, _resolved_action, f"ui_mail_{id_}"))
    with _avwhy:
        st.button("为什么？", key=f"action_why_{id_}",
                  use_container_width=True, on_click=_open_section,
                  args=(f"ui_basis_{id_}",))
    with _av2:
        with st.popover("更多"):
            st.button("生成客户邮件", key=f"action_more_mail_{id_}",
                      use_container_width=True, on_click=_open_section,
                      args=(f"ui_mail_{id_}",))
            st.button("创建报价", key=f"action_more_quote_{id_}",
                      use_container_width=True, on_click=_open_section,
                      args=(f"ui_quote_{id_}",))
            st.button("创建跟进", key=f"action_more_fu_{id_}",
                      use_container_width=True, on_click=_open_section,
                      args=(f"ui_fuzone_{id_}",))
            st.button("查看原始询盘", key=f"action_more_src_{id_}",
                      use_container_width=True, on_click=_open_section,
                      args=(f"ui_src_{id_}",))
    _render_requirement_compact(info, insight, _resolved, _product, _qty, _price)
    _render_timeline_compact(_acts_for_recent, wf=wf, created=created,
                             has_draft=bool(ctx["draft_text"]),
                             has_blockers=bool(st_blockers))

    with st.expander("查看完整分析", expanded=st.session_state.get(f"ui_basis_{id_}", False)):
        _outcome = _resolved.get("customerRequestedOutcome") or "—"
        if isinstance(_outcome, (list, tuple, set)):
            _outcome = "、".join(str(x) for x in _outcome)
        _cb = _resolved.get("customerBlockingItems") or []
        _ib = _resolved.get("internalPrerequisites") or []
        _risk = _resolved.get("dealRisks") or []
        _confirmed = []
        for _label, _value in (
                ("客户", _co_name or _ct_name),
                ("产品", _product),
                ("数量", _qty),
                ("目标价", _price if _price != "未提及" else ""),
                ("贸易条款", info.get("incoterm") or info.get("trade_term")),
                ("交期", _timeline_text(info) if _timeline_text(info) != "未提及" else "")):
            if _value:
                _confirmed.append(f"{_label}：{_value}")
        st.markdown("**已确认客户事实**")
        st.caption("；".join(_confirmed[:6]) or "暂无足够事实。")
        st.markdown("**当前判断**")
        st.caption(
            f"商机优先级：{_priority_text}；"
            f"需求完整度：{_resolved.get('requirementCompleteness') or '—'}；"
            f"报价准备度：{_resolved.get('quotationReadiness') or _rq_txt or '—'}；"
            f"产品匹配：{_resolved.get('productMatchStatus') or '—'}；"
            f"供应能力：{_resolved.get('supplierCapabilityStatus') or '—'}。")
        st.markdown("**阻塞与待确认**")
        st.caption("客户侧：" + ("、".join(_field_cn(x.get('key') or x.get('field')) for x in _cb[:4]) if _cb else "无"))
        st.caption("内部：" + ("、".join(str(x.get('name') or x.get('key')) for x in _ib[:4]) if _ib else "无"))
        st.caption("客户请求结果：" + str(_outcome))
        if _risk:
            st.caption("主要风险：" + "；".join(str(x.get("message") or x.get("type")) for x in _risk[:3]))
        st.markdown("**为什么产生当前 Next Best Action**")
        st.caption(_hero_why)
        with st.expander("开发者调试详情（评分、事实来源、Decision Trace）", expanded=False):
            _render_ai_basis(lead, insight, dict(lead=lead, insight=insight, report=ctx), _ns)

    _more_open = any(st.session_state.get(f"ui_{_k}_{id_}")
                     for _k in ("quote", "mail", "fuzone", "src"))
    with st.expander("更多", expanded=_more_open):
        if st.session_state.get(f"ui_quote_{id_}"):
            st.caption("报价草稿与报价记录使用下方既有报价工作区；本处只保留入口说明。")
        if st.session_state.get(f"ui_mail_{id_}"):
            st.caption("邮件生成器在下方既有区域打开；邮件目的由当前 Next Best Action 自动判断。")
        if st.session_state.get(f"ui_fuzone_{id_}"):
            st.caption("跟进任务使用现有跟进流程；本处只保留入口说明。")
        try:
            _rel_inq = customer_inquiries(cid) if cid else []
        except Exception:
            _rel_inq = []
        st.caption(f"相关询盘：{len(_rel_inq)} · 相关产品：{len(matches)} · 相关联系人：{1 if (_ct_name or known_email) else 0}")
        st.caption(f"客户：{_co_name or _ct_name or '未知客户'} · 联系人：{_ct_name or '—'} · 邮箱：{known_email or info.get('email') or '—'} · 国家：{info.get('country') or '—'}")
        if st.session_state.get(f"ui_src_{id_}"):
            st.code(str(info.get("raw_text") or "").strip() or "当前记录未保留原文。")

    # —— 第二十一轮统一客户邮件编写器（ROUND 6.9 §5：放回共享 Deal Detail）——
    # 首页 / 侧栏 / 商机页 / 跟进台的所有「生成客户邮件」CTA 都指向
    # ui_mail_{id_}；编写器必须在本页可达，否则 CTA 打开的是空区域。
    if not _term:
        try:
            _render_deal_pipeline(id_, biz, ctx.get("biz_cn") or "", act,
                                  fu_state, _fu_at, st_blockers)
        except Exception as _pce:
            st.caption(f"Pipeline 暂不可用：{str(_pce)[:80]}")
        try:
            _mail_ctx = _build_customer_mail_ctx(
                id_, ctx.get("text") or "", info, matches,
                ctx.get("gaps") or [], insight,
                r_stat, biz, ctx.get("draft_text") or "")
            _render_customer_email_composer(
                id_, _mail_ctx, biz, ctx.get("draft_text") or "")
            # 回复闭环：设置跟进时间 + 人工确认结单（同一页可达）
            _render_deal_closure(id_, biz, ctx.get("wf"))
        except Exception as _mce:
            st.caption(f"邮件/跟进闭环暂不可用：{str(_mce)[:80]}")
    return

    # ========== ⓪ AI NEXT BEST ACTION Hero（Action-First，spec 四）==========
    # Action First → Context Second → Detail Third：主 CTA 只有一个且文案随
    # Action Type 动态切换；次级操作（稍后处理/查看原始询盘）降权；
    # 上一个 / 下一个 / 完成并进入下一条与主 CTA 同一视觉行。
    _ws_err = st.session_state.get("ws_exec_error")
    if _ws_err and _ws_err.get("iid") == id_:
        st.error("⚠️ **操作失败，当前记录未完成。**　"
                 + str(_ws_err.get("msg") or "")
                 + "　（防重复守卫已生效，可直接重试）", icon="⚠️")
    _ws_route = _ex.route_of(_nba.get("action_type") or act.get("type"))
    _ws_ids = st.session_state.get("queue_ids") or []
    _ws_i = _ws_ids.index(id_) if id_ in _ws_ids else None
    _hero_why = str(_nba.get("reason") or "").strip()
    if not _hero_why:
        _bits = [str(r.get("reason")) for r in _aip_rows
                 if r.get("state") != "nodata" and r.get("reason")]
        _hero_why = "；".join(_bits[:2]) if _bits else "暂无数据"
    _hero_what = str(_nba.get("action_label") or act.get("label") or "—")
    _hero_pri = str(_nba.get("priority") or "")
    st.markdown(
        "<div class='nba-hero'>"
        "<div class='ttl'>🎯 建议下一步</div>"
        f"<div class='what'>{_h.escape(_hero_what)}</div>"
        f"<div class='why'>原因：{_h.escape(_hero_why)}"
        + (f"<span style='opacity:.62'>　·　{_h.escape(_hero_pri)}</span>"
           if _hero_pri else "")
        + "</div></div>", unsafe_allow_html=True)
    _ws_busy = bool(st.session_state.get("exec_busy"))
    _hcta, _hs1, _hs2, _hgap, _hp, _hn, _hnum, _hdn = st.columns(
        [1.55, .82, .95, .18, .72, .72, .62, 1.42], gap="small",
        vertical_alignment="center")
    with _hcta:
        # —— 主 CTA：唯一 primary，按路由动作 ——
        if _ws_route == "mark_complete":
            st.button("✅ 标记完成并进入下一条", key=f"hero_cta_{id_}",
                      type="primary", use_container_width=True, disabled=_ws_busy,
                      help="推荐动作：标记完成并推进队列",
                      on_click=_done_and_next, args=(id_,))
        elif _ws_route == "match_product":
            st.button("🔎 开始匹配产品", key=f"hero_cta_{id_}",
                      type="primary", use_container_width=True, disabled=_ws_busy,
                      help="推荐动作：到产品库选择匹配产品",
                      on_click=_goto_products)
        else:
            _lbl, _sec = _NBA_CTA.get(_ws_route, ("✉️ 生成客户邮件",
                                                  "ui_mail_{id}"))
            st.button(_lbl, key=f"hero_cta_{id_}", type="primary",
                      use_container_width=True, disabled=_ws_busy,
                      help="AI 推荐的下一步销售动作",
                      on_click=_open_section, args=(_sec.format(id=id_),))
    with _hs1:
        st.button("⏭ 稍后处理", key=f"hero_later_{id_}", use_container_width=True,
                  disabled=(_ws_busy or _ws_i is None
                            or _ws_i >= len(_ws_ids) - 1),
                  on_click=_nav_queue, args=(1,))
    with _hs2:
        st.button("📄 查看原始询盘", key=f"hero_view_{id_}",
                  use_container_width=True,
                  on_click=_open_section, args=(f"ui_src_{id_}",))
    with _hgap:
        st.caption(f"**{_ws_i + 1}/{len(_ws_ids)}**"
                   if _ws_i is not None else "")
    with _hp:
        st.button("←", key=f"hero_prev_{id_}", use_container_width=True,
                  disabled=(_ws_i is None or _ws_i <= 0),
                  on_click=_nav_queue, args=(-1,), help="上一个")
    with _hn:
        st.button("→", key=f"hero_next_{id_}", use_container_width=True,
                  disabled=(_ws_i is None or _ws_i >= len(_ws_ids) - 1),
                  on_click=_nav_queue, args=(1,), help="下一个")
    with _hdn:
        st.button("✅ 完成并进入下一条", key=f"hero_done_{id_}",
                  use_container_width=True, disabled=_ws_busy,
                  help="完整事务：记 Activity → 更新状态/最近联系 → "
                       "重算 AI Priority → 进入下一条（失败不跳转）",
                  on_click=_done_and_next, args=(id_,))

    with st.container(border=True):
        _hc = st.columns([2.35, 1, 1, 1, 1], gap="small")
        with _hc[0]:
            st.markdown(_brand, unsafe_allow_html=True)
        # —— 右侧操作按钮：不可用 = Disabled + 显示原因 ——
        _gen_ok = (id_ is not None) and not _term
        _gen_why = ("已成交，无需回复" if biz == "WON" else
                    "已丢单，暂不回复（可在下方结单区复活到谈判）" if biz == "LOST" else
                    "暂缓中，先恢复状态" if biz == "ON_HOLD" else "")
        _fu_ok = (id_ is not None) and not _term and biz in (
            "REPLIED", "FOLLOW_UP", "QUOTED", "NEGOTIATING", "READY_FOR_QUOTE")
        _fu_why = ("" if _fu_ok else
                   ("已成交" if biz == "WON" else "已丢单" if biz == "LOST" else
                    "暂缓中" if biz == "ON_HOLD" else
                    "先回复客户（或完成报价）后再安排跟进"))
        _quo_ok = (id_ is not None) and bool(matches) and not _term and (
            r_stat == "ready_for_quotation"
            or biz in ("QUOTED", "NEGOTIATING", "READY_FOR_QUOTE"))
        _quo_why = ("" if _quo_ok else
                    ("已成交" if biz == "WON" else "已丢单" if biz == "LOST" else
                     "暂缓中" if biz == "ON_HOLD" else
                     "产品库暂无匹配产品，无法生成报价" if not matches else
                     "报价准备度不足（" + r_cn + "）：先补齐关键信息再报价"))
        with _hc[1]:
            st.button("✉️ 生成客户邮件", key=f"ws_gen_{id_}", use_container_width=True,
                      disabled=not _gen_ok, help=_gen_why or "按当前状态自动判断邮件目的并生成草稿",
                      on_click=_open_section, args=(f"ui_mail_{id_}",))
        with _hc[2]:
            st.button("📅 记录跟进", key=f"ws_fu_{id_}", use_container_width=True,
                      disabled=not _fu_ok, help=_fu_why or "设置跟进时间并记入 Timeline",
                      on_click=_open_section, args=(f"ui_fuzone_{id_}",))
        with _hc[3]:
            st.button("💰 创建报价", key=f"ws_quote_{id_}", use_container_width=True,
                      disabled=not _quo_ok, help=_quo_why or "生成报价草稿并记入 Timeline",
                      on_click=_open_section, args=(f"ui_quote_{id_}",))
        with _hc[4]:
            with st.popover("⋯ 更多", help="跳转到本询盘的其它工作区"):
                st.button("📩 生成客户邮件（统一编写器）", key=f"ws_m1_{id_}",
                          use_container_width=True,
                          on_click=_open_section, args=(f"ui_mail_{id_}",))
                st.button("🧭 查看原始询盘", key=f"ws_m2_{id_}",
                          use_container_width=True,
                          on_click=_open_section, args=(f"ui_src_{id_}",))
                st.button("📊 AI 分析依据", key=f"ws_m3_{id_}",
                          use_container_width=True,
                          on_click=_open_section, args=(f"ui_basis_{id_}",))
                st.caption("🏁 成交 / 丢单：请在下方「结单」区人工确认")
        # —— 字段格：优先级 / 商机 / 当前阶段 / 最近联系 / 下次跟进 / 报价准备度 ——
        st.markdown(
            "<div class='ws-cells'>"
            f"<div class='ws-cell'><div class='lb'>优先级</div>"
            f"<div class='v'>"
            f"<span class='ws-aip {_aip_band(_aip)}'>{_h.escape(str(_g_letter or '—'))}</span>"
            f"<span class='ws-aipg'>级</span></div>"
            f"<div class='s'>{_h.escape(str(_aip_lvl or ''))}</div></div>"
            f"<div class='ws-cell'><div class='lb'>Opportunity 商机</div>"
            f"<div class='v'>{_opp['emoji']} {_opp['stage_cn']}</div>"
            f"<div class='s'>{_opp['inquiry_count']} 条询盘"
            + (f" · ≈{_opp_val}" if _opp_val else "") + "</div></div>"
            f"<div class='ws-cell'><div class='lb'>当前阶段 Current Stage</div>"
            f"<div class='v {_tone_cls}'>{_h.escape(biz_cn)}</div>"
            f"<div class='s'>{'终态' if _term else '由 AI 分析派生，可手动推进'}</div></div>"
            f"<div class='ws-cell'><div class='lb'>最近联系 Last Contact</div>"
            f"<div class='v'>{_h.escape(str(_last_contact)[:16])}</div>"
            f"<div class='s'>{'上次回复' if wf.get('last_replied_at') else '询盘到达'}</div></div>"
            f"<div class='ws-cell'><div class='lb'>下次跟进 Next Follow-up</div>"
            f"<div class='v {_fu_cls}'>{_h.escape(_fu_at or '—')}</div>"
            f"<div class='s'>{_h.escape(fu_state or '未设置')}</div></div>"
            f"<div class='ws-cell'><div class='lb'>报价准备度</div>"
            f"<div class='v'>{_h.escape(str(r_cn or '—'))}</div>"
            f"<div class='s'>{len(st_blockers)} 项阻塞</div></div>"
            "</div>", unsafe_allow_html=True)

    # —— Pipeline（阶段知识卡 + 阶段推进）已抽为 _render_deal_pipeline ——
    # 唯一实现在共享 Deal Detail（_render_customer_workspace）内调用，
    # 这里不再重复实现（历史缺陷：阶段推进被留在 return 之后的废弃分支）。

    with st.expander("完整工作记录（Activity Timeline / Related Records）",
                     expanded=False):
        _acts_db = []
        try:
            _acts_db = list_activity(id_)
        except Exception:
            _acts_db = []
        _tl_rows = _wsx.build_timeline(
            _acts_db, wf=wf, created=created,
            has_draft=bool(ctx["draft_text"]),
            has_blockers=bool(st_blockers))
        if _tl_rows:
            for _t in _tl_rows[:8]:
                _res_html = (f"<div class='res'>→ {_h.escape(_t['result'])}</div>"
                             if _t.get("result") else "")
                st.markdown(
                    "<div class='tl-item'>"
                    f"<div class='tl-ic'>{_t['icon']}</div>"
                    f"<div class='tl-bd'><div class='t1'>{_h.escape(_t['cn'])}"
                    f"<span style='opacity:.55;font-weight:500'>　{_h.escape(_t['desc'])}</span></div>"
                    f"{_res_html}</div>"
                    f"<div class='tl-side'>{_h.escape(str(_t['ts'])[:16])}<br>"
                    f"{_h.escape(_t['actor'])}</div></div>",
                    unsafe_allow_html=True)
        else:
            st.caption("暂无活动记录。")
        _rel_bits = []
        try:
            _rel_inq = customer_inquiries(cid) if cid else []
        except Exception:
            _rel_inq = []
        _rel_bits.append(f"相关询盘 {len(_rel_inq)}")
        _rel_bits.append(f"相关产品 {len(matches)}")
        _rel_quote = [a for a in _acts_db if a[0] in ("QUOTE_CREATED", "QUOTE_SENT")]
        _rel_bits.append(f"相关报价 {len(_rel_quote)}")
        st.caption(" · ".join(_rel_bits))

    return

    # ========== ③ AI Sales Assistant + Activity Timeline ==========
    _a1, _a2 = st.columns([1.18, 1], gap="medium")
    # —— AI Sales Assistant ——
    with _a1:
        with st.container(border=True):
            st.markdown("<div style='font-size:.92rem;font-weight:800'>🤖 AI 销售助手</div>",
                        unsafe_allow_html=True)
            st.caption("AI 依据真实询盘给出建议；每条动作都会写入 Timeline，可追溯")
            # —— Phase 3：AI Priority 评分解释（spec 三）+ Next Best Action（spec 四/五）——
            if _aip is not None and _aip_rows:
                _aip_lis = []
                _risk_row = None
                for _r in sorted(_aip_rows, key=lambda x: -(x["score"])):
                    _wn = _p3.AIP_WEIGHTS.get(_r["key"], 0)
                    if _r["key"] == "followup_risk":      # 风险独立警示行（非加分理由）
                        _risk_row = _r
                        continue
                    if _r["state"] == "nodata":
                        _aip_lis.append(
                            f"<li><span class='sgn na'>◻</span>"
                            f"{_h.escape(_r['cn'])}：暂无数据"
                            f"<span class='dim'>（不虚估 · 按 0 计入总分）</span></li>")
                    else:
                        _net = (_r["net"] or 0)
                        _cl = "up" if _net >= 0 else "dn"
                        _sign = "+" if _net >= 0 else ""
                        _aip_lis.append(
                            f"<li><span class='sgn {_cl}'>{_sign}{_net}</span>"
                            f"{_h.escape(_r['reason'])}"
                            f"<span class='dim'>（{_h.escape(_r['cn'])} "
                            f"{_r['score']}/100 × 权重{_wn:.2f}）</span></li>")
                _risk_html = ""
                if _risk_row is not None and _risk_row["state"] != "nodata":
                    _risk_html = (f"<li style='border-top:1px solid "
                                  f"rgba(220,38,38,.25);padding-top:.22rem'>"
                                  f"<span class='sgn dn'>⚠</span>"
                                  f"{_h.escape(_risk_row['reason'])}"
                                  f"<span class='dim'>（跟进风险 "
                                  f"{_risk_row['score']}/100 × 权重0.15，"
                                  f"已计入总分与 Queue Score）</span></li>")
                st.markdown(
                    "<div class='aip-card'>"
                    f"<div class='hd'><span class='n {_aip_band(_aip)}'>{_aip}</span>"
                    f"<span class='u'>/ 100</span>"
                    f"<span class='lvl'>{_h.escape(_aip_lvl)}</span></div>"
                    "<ul class='aip-lst'>" + "".join(_aip_lis) + _risk_html
                    + "</ul>"
                    f"<div class='aip-fml'>AI Priority = Σ(维度分×权重) = "
                    f"{_h.escape(_aip_fml)} = <b>{_aip}</b>（可复算）</div>"
                    "</div>", unsafe_allow_html=True)
                if _nba.get("action_label"):
                    st.markdown(
                        "<div class='nba-chip'>🎯 Next Best Action："
                        f"<b>{_h.escape(str(_nba['action_label']))}</b>"
                        f"<span class='dim'>　{_h.escape(str(_nba['action_type_cn']))}"
                        + (f" · P{_h.escape(str(_nba['priority']))}"
                           if _nba.get("priority") else "")
                        + f"</span></div>"
                        f"<div class='nba-reason'>{_h.escape(str(_nba['reason']))}"
                        f"（{_h.escape(str(_nba['current_stage']))} → "
                        f"{_h.escape(str(_nba['current_status']))}）</div>",
                        unsafe_allow_html=True)
            # 为什么（与 workflow next_action 同一依据）
            _why = ""
            _acts_all = insight.get("next_actions") or []
            if _acts_all and (_acts_all[0].get("reason") or "").strip():
                _why = str(_acts_all[0]["reason"]).strip()
            elif prelim:
                _have = [f.get("field") for f in
                         (insight.get("requirement_semantics") or [])
                         if f.get("state") in ("Confirmed", "Explicit", "Approximate")]
                _why = ("客户已明确 " + "、".join(str(x) for x in _have[:3])
                        if _have else "客户需求已收到")
                _why += ("；但 " + "、".join(_field_cn(x) for x in st_blockers[:3])
                         + " 尚未确认，暂时无法准确报价")
            elif r_stat == "ready_for_quotation":
                _why = "关键信息已齐全，可以进入报价流程"
            # 风险（只列真实事实风险 + 时效规则，不编造）
            _risk_lines = []
            _sev_ic = {"high": "🔴", "medium": "🟠", "low": "🟢"}
            for _rk_ in (insight.get("risks") or [])[:2]:
                _risk_lines.append(
                    f"{_sev_ic.get(_rk_.get('severity'), '•')} "
                    f"{_rk_.get('type', '')}：{_rk_.get('reason', '')}")
            if stale48:
                _risk_lines.append("🔴 距询盘到达已超 48 小时仍未回复，继续拖延会明显降低转化概率")
            if fu_state == "已逾期":
                _risk_lines.append("🟠 跟进已逾期，客户可能已转向其它供应商，请立即跟进")
            if not _risk_lines:
                _risk_lines.append("当前无显著风险（基于已有事实，AI 不凭空提示）")
            # 下一步五要素
            _pri_txt = act.get("priority") or ""
            if act.get("type") == "FOLLOW_UP_CUSTOMER" and wf.get("follow_up_at"):
                _due = ("今天" if fu_state in ("今日跟进",)
                        else "已逾期" if fu_state == "已逾期"
                        else str(wf["follow_up_at"])[:10])
            elif act.get("type") == "CREATE_FOLLOW_UP":
                _due = "今天设置"
            else:
                _due = "—"
            _st_now = fu_state or biz_cn
            _act_disp = act.get("label") or (
                "已成交" if biz == "WON" else "已丢单" if biz == "LOST"
                else "暂缓" if biz == "ON_HOLD" else "等待客户反馈")
            st.markdown(
                "<div class='asst'>"
                f"<div class='lb'>当前建议</div><div class='val'><b>{_h.escape(str(_act_disp))}</b>"
                + (f"（P{_h.escape(str(_pri_txt))}）" if _pri_txt else "") + "</div>"
                f"<div class='lb'>为什么</div><div class='val why'>{_h.escape(_why or '—')}</div>"
                f"<div class='lb'>报价准备度</div><div class='val'>{_h.escape(str(r_cn or '—'))}"
                + (f"　<small style='opacity:.6'>（{r_stat} · {r_score} 分）</small>"
                   if r_score is not None else "") + "</div>"
                f"<div class='lb'>风险</div><div class='val'>"
                + "<br>".join(_h.escape(x) for x in _risk_lines) + "</div>"
                f"<div class='lb'>下一步</div><div class='val'>{_h.escape(str(_act_disp))}　"
                f"<small style='opacity:.6'>优先级 {_pri_txt or '—'} ｜ 截止 {_due} ｜ "
                f"负责人 Sales ｜ 状态 {_h.escape(str(_st_now))}</small></div>"
                "</div>", unsafe_allow_html=True)
            st.button("✉️ 生成客户邮件", key=f"ws_asst_gen_{id_}", use_container_width=True,
                      disabled=not _gen_ok, help=_gen_why or "打开统一客户邮件编写器",
                      on_click=_open_section, args=(f"ui_mail_{id_}",))
    # —— Activity Timeline ——
    with _a2:
        with st.container(border=True):
            _tlh1, _tlh2 = st.columns([1.8, 1])
            with _tlh1:
                st.markdown("<div style='font-size:.92rem;font-weight:800'>🧭 Activity Timeline</div>",
                            unsafe_allow_html=True)
                st.caption("时间倒序 · 每条含 时间/类型/内容/执行人/结果")
            with _tlh2:
                with st.popover("➕ 记录活动", help="电话/会议/邮件等手动记录"):
                    _rt_opt = ["📞 电话", "🤝 会议", "✉️ 邮件"]
                    _rt_type = st.radio(
                        "活动类型", _rt_opt, index=0, horizontal=True,
                        key=f"ws_rt_type_{id_}",
                        help="选择的类型会写入 Timeline（时间/类型/内容/执行人）")
                    _rt_map = {"📞 电话": "PHONE_CALL", "🤝 会议": "MEETING",
                               "✉️ 邮件": "EMAIL"}
                    _rt_desc = st.text_input("内容", key=f"ws_rt_desc_{id_}",
                                             placeholder="例如：与采购经理电话沟通 30 分钟，确认规格…")
                    if st.button("✅ 保存活动", key=f"ws_rt_save_{id_}",
                                 use_container_width=True):
                        _d = (st.session_state.get(f"ws_rt_desc_{id_}") or "").strip()
                        if _d:
                            record_activity(id_, _rt_map.get(_rt_type, "NOTE"),
                                            _d, actor="销售")
                            st.toast("已记录到 Timeline ✅")
                            st.rerun()
                        else:
                            st.warning("请填写活动内容")
            _acts_db = []
            try:
                _acts_db = list_activity(id_)
            except Exception:
                _acts_db = []
            _tl_rows = _wsx.build_timeline(
                _acts_db, wf=wf, created=created,
                has_draft=bool(ctx["draft_text"]),
                has_blockers=bool(st_blockers))
            if not _tl_rows:
                st.caption("暂无活动记录 —— 使用上方「➕ 记录活动」或完成一次回复/跟进。")
            for _t in _tl_rows[:15]:
                _res_html = (f"<div class='res'>→ {_h.escape(_t['result'])}</div>"
                             if _t.get("result") else "")
                st.markdown(
                    "<div class='tl-item'>"
                    f"<div class='tl-ic'>{_t['icon']}</div>"
                    f"<div class='tl-bd'><div class='t1'>{_h.escape(_t['cn'])}"
                    f"<span style='opacity:.55;font-weight:500'>　{_h.escape(_t['desc'])}</span></div>"
                    f"{_res_html}</div>"
                    f"<div class='tl-side'>{_h.escape(str(_t['ts'])[:16])}<br>"
                    f"{_h.escape(_t['actor'])}</div></div>",
                    unsafe_allow_html=True)

    # ========== ④ Related Records ==========
    st.markdown("<div style='font-size:.92rem;font-weight:800;margin:.2rem 0 .15rem'>"
                "🔗 Related Records 关联记录</div>", unsafe_allow_html=True)
    _rel_c1, _rel_c2, _rel_c3, _rel_c4 = st.columns(4, gap="small")
    # 相关询盘（同客户名下其它询盘；无归并客户则只含本条）
    _rel_inq = []
    try:
        _rel_inq = customer_inquiries(cid) if cid else []
    except Exception:
        _rel_inq = []
    with _rel_c1:
        st.markdown(
            f"<div class='rel-box'><div class='t'>📬 相关询盘（{len(_rel_inq)}）</div>",
            unsafe_allow_html=True)
        if not _rel_inq:
            st.markdown("<div class='empty'>暂无同客户询盘</div>", unsafe_allow_html=True)
        else:
            for _ri in _rel_inq[:3]:
                _c1, _c2 = st.columns([3.2, 1], gap="small")
                _c1.markdown(
                    f"<div class='line'>#{_ri[0]} · {_h.escape(str(_ri[6] or _ri[5] or '—'))}<br>"
                    f"<small style='opacity:.55'>{_ri[1]}</small></div>",
                    unsafe_allow_html=True)
                if _c2.button("查看", key=f"ws_relq_{id_}_{_ri[0]}",
                              use_container_width=True):
                    _open_inquiry(_ri[0])
        st.markdown("</div>", unsafe_allow_html=True)
    # 相关产品（本询盘 AI 匹配结果，真实数据）
    with _rel_c2:
        st.markdown(
            f"<div class='rel-box'><div class='t'>📦 相关产品（{len(matches)}）</div>",
            unsafe_allow_html=True)
        if not matches:
            st.markdown("<div class='empty'>产品库暂无匹配产品<br>"
                        "（不编造：可先补全客户产品信息后重新分析）</div>",
                        unsafe_allow_html=True)
        else:
            for _pm in matches[:3]:
                try:
                    _pr = (f"${_pm['price_range'][0]:.2f}–{_pm['price_range'][1]:.2f}"
                           if _pm.get("price_range") else "")
                except Exception:
                    _pr = ""
                _moq = (f"MOQ {_pm['moq']:,}" if _pm.get("moq") else "")
                st.markdown(
                    f"<div class='line'>· {_h.escape(_pm.get('name_cn') or _pm.get('name', ''))}"
                    f"<br><small style='opacity:.6'>match "
                    f"{int(_pm.get('match_score', 0) * 100)}% · {_pr} · {_moq}</small></div>",
                    unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
    # 相关报价（来自 Timeline 的 QUOTE_CREATED / QUOTE_SENT 真实记录）
    _rel_quote = [a for a in _acts_db if a[0] in ("QUOTE_CREATED", "QUOTE_SENT")]
    with _rel_c3:
        st.markdown(
            f"<div class='rel-box'><div class='t'>💰 相关报价（{len(_rel_quote)}）</div>",
            unsafe_allow_html=True)
        if not _rel_quote:
            st.markdown("<div class='empty'>暂无报价记录<br>"
                        "（点击上方「创建报价」生成草稿）</div>",
                        unsafe_allow_html=True)
        else:
            for _rq_ in _rel_quote[-3:]:
                st.markdown(
                    f"<div class='line'>· {_wsx.act_meta(_rq_[0])[1]} {_rq_[1][:16]}"
                    f"<br><small style='opacity:.6'>{_h.escape((_rq_[2] or '')[:40])}</small></div>",
                    unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
    # 相关联系人（客户档案 contacts + 各询盘联系人去重）
    _rel_pers = []
    try:
        _ctj = json.loads((_cust[11] if _cust and len(_cust) > 11 else "[]") or "[]")
        if isinstance(_ctj, list):
            for _p in _ctj:
                if str(_p.get("name") or "").strip():
                    _rel_pers.append(str(_p["name"]).strip())
    except Exception:
        _ctj = []
    if _ct_name and _ct_name not in _rel_pers:
        _rel_pers.append(_ct_name)
    with _rel_c4:
        st.markdown(
            f"<div class='rel-box'><div class='t'>👥 相关联系人（{len(_rel_pers)}）</div>",
            unsafe_allow_html=True)
        if not _rel_pers:
            st.markdown("<div class='empty'>暂无联系人信息</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='line'>" + "；".join(
                _h.escape(p) for p in _rel_pers[:5]) + "</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    # ========== 报价工作区（Header「创建报价」展开） ==========
    _qk = f"ui_quote_{id_}"
    _quo_open = _quo_ok or bool(st.session_state.get(_qk, False))
    with st.expander("💰 创建报价（草稿 · 不自动发送 · 确认后记入 Timeline）",
                     expanded=_quo_open):
        if not matches:
            st.warning("当前产品库没有匹配产品，暂无法生成报价草稿。"
                       "可先请客户提供产品图片 / 链接 / 参考型号，或到「📦 产品库」补全产品。")
        elif _term:
            st.warning("终态询盘（成交/丢单/暂缓）不再创建新报价。")
        else:
            _cur_cur = (info.get("target_price_currency") or "USD")
            _qty_txt = (f"{info.get('quantity'):,} {info.get('quantity_unit')}"
                        if info.get("quantity") else "待客户确认")
            _pl = []
            for _pm in matches[:2]:
                try:
                    _lo, _hi = _pm["price_range"][0], _pm["price_range"][1]
                except Exception:
                    _lo, _hi = None, None
                _moq = _pm.get("moq")
                _lt = _pm.get("lead_time") or ""
                _pl.append(
                    f"- {_pm.get('name_cn') or _pm.get('name', '')}"
                    f"（{_pm.get('name', '')}）："
                    f"参考价 {_lo and f'${_lo:.2f}–${_hi:.2f}' or '待确认'} / pc，"
                    f"MOQ {_moq or '待确认'}" + (f"，常规交期 {_lt}" if _lt else ""))
            _df_q = (f"Dear {_ct_name or 'Sir/Madam'},\n\n"
                     f"Thank you for your inquiry. Please find our quotation below "
                     f"(subject to final specification confirmation):\n\n"
                     + "\n".join(_pl)
                     + f"\n\nQuantity: {_qty_txt}\n"
                     + (f"Target price: {_cur_cur} {info.get('target_price')}\n"
                        if info.get("target_price") else "")
                     + "\n* 以上价格区间来自我方产品库，正式报价以最终确认规格后为准。\n"
                     "Best regards,\n" + (SELLER.get("company") or ""))
            with st.form(f"ws_qf_{id_}"):
                st.markdown("**报价要素预览**（来自产品库 + 询盘提取，可改）：")
                for _pm in matches[:2]:
                    _mref = _wsx.money_text(
                        {"qty_num": info.get("quantity")}, [_pm], _cur_cur)
                    st.caption("· " + _mref if _mref else "· 数量待确认（暂不算金额）")
                _q_txt = st.text_area("报价草稿（英文，发送前人工复核）",
                                      value=_df_q, height=230,
                                      key=f"ws_q_txt_{id_}")
                _q_summary = "、".join(
                    str(_pm.get("name_cn") or _pm.get("name", "")) for _pm in matches[:2])
                _sub_q = st.form_submit_button("✅ 确认创建报价（写入 Timeline）",
                                               type="primary")
                if _sub_q:
                    _record_quote_draft(id_, info, matches, _q_summary, _qty_txt)
                    st.toast("报价草稿已创建并记入 Timeline ✅")
                    st.rerun()


def render_report(report: dict, text: str, id_=None):
    """把一份分析报告渲染成界面。id_ 用于绑定备注编辑。"""
    info = report["extracted"]
    matches = report["matches"]
    lead = report["lead"]
    draft = report.get("draft", "")
    # 邮箱特殊处理：询盘通常来自 Email/Alibaba/WhatsApp/LinkedIn/展会/CRM 等渠道，
    # 客户档案里早就存过邮箱 → 已知就不该再问客户要一遍
    known_email = get_customer_email_by_inquiry(id_) if id_ is not None else ""

    # ============ 第一层 · 业务结果（先给结论，不先堆细节） ============
    # 提前算好缺口与洞察（与后文同一套逻辑，仅提升到顶部展示）
    gaps = []
    try:
        gaps = gapcheck.detect_missing(text, info, matches, known_email)
    except Exception:
        gaps = []
    insight = {}
    try:
        from agent.insight import build_insight
        insight = report.get("insight") or build_insight(text, info, matches, lead, gaps)
    except Exception:
        insight = {}
    # _ns / _scn / _ACN 已统一在模块顶部导入，不再在函数内重复 import

    # 展示层兜底：旧记录 grade 可能存了整句（如「A（重点跟进：…）」）——
    # 仅指标展示取首字母，is_high 等既有判断仍用原始 grade，不改口径
    grade = lead.get("grade") or ""
    _g_letter = grade.strip()[:1] if grade.strip() else ""
    _g_extra = ""
    if "（" in grade and "）" in grade:
        _g_extra = grade.split("（", 1)[1].split("）", 1)[0]
    resp_t = lead.get("recommended_response_time") or RESP_TIME.get(grade, "")
    q_score = lead.get("customer_quality_total")
    urg_lbl = URGENCY_CN.get(lead.get("urgency") or info.get("urgency"), "未判断")
    _qr = insight.get("quotation_readiness") or {}
    r_stat = _ns(_qr.get("quotation_readiness_status") or "")
    r_score = _qr.get("quotation_readiness_score")
    r_cn = _scn.get(r_stat, r_stat)

    if id_ is not None:
        _stt, _lg, _ls, _cg, _u = get_inquiry_priority_inputs(id_)
        _pri, _pts = calc_priority(_cg, _lg, _ls, _u, _stt)
        _wfd = get_workflow(id_)          # 第七轮：闭环字段（业务状态/回复/跟进/成交）
    else:
        _stt, _pri, _pts = STATUS_TODO, None, None
        _wfd = {}
    is_high = (_stt != STATUS_DONE) and (grade in ("A", "B") or _pri == PRI_HIGH
                                         or lead.get("urgency") == "high")

    # ============ Level 1 · 询盘头区（3 秒看完：谁 / 什么状态 / 下一步） ============
    _rk = id_ if id_ is not None else "new"
    _prelim = _qr.get("blockers_preliminary") or []
    _acts = insight.get("next_actions") or []
    _verdict = _qr.get("quote_summary") or lead.get("advice") or info.get("summary") or ""

    # —— 第七轮：业务状态与 Next Action（与左侧队列同一口径，spec 二十六）——
    _biz_now = _wf.derive_biz(_stt, [b.get("field", "") for b in _prelim],
                              r_stat, _wfd.get("biz_status"), bool(draft))
    _act_now = _wf.next_action(
        _stt, _biz_now, [b.get("field", "") for b in _prelim],
        r_stat, has_draft=bool(draft), follow_up_at=_wfd.get("follow_up_at"),
        follow_up_done=bool(_wfd.get("follow_up_done")),
        intent=info.get("intent") or "")
    _fu_state = _wf.followup_state(_wfd.get("follow_up_at"),
                                   _wfd.get("follow_up_done"))
    _runtime_wv = resolve_deal_state(id_) if id_ is not None else {}
    _runtime_state = (_runtime_wv.get("resolvedState") or {})
    _runtime_req = _runtime_state.get("resolvedRequirement") or {}
    _runtime_nba = _runtime_state.get("nextBestAction") or {}
    _runtime_customer_blocking = _runtime_state.get("customerBlockingItems") or []
    if _runtime_state:
        _act_now = {"type": _runtime_nba.get("type") or "SEND_REPLY",
                    "label": _runtime_state.get("primaryCta")
                    or _runtime_nba.get("label") or "查看详情",
                    "priority": _runtime_state.get("priority") or ""}

    # 旧「询盘头区 + 商机状态条 + 阶段下拉」由 Phase 2 Workspace 取代
    #（Header / Pipeline / AI 销售助手 / Timeline / Related / 报价区），口径不变。
    import html as _vh
    _st_blockers = [b.get("field", "") for b in _prelim]
    _missing = _missing_fields(info, gaps, _prelim, known_email)
    if _runtime_state:
        _missing["blocking"] = [
            {"key": str(m.get("key") or m.get("field") or ""),
             "label": str(m.get("name") or _field_cn(m.get("key") or m.get("field")))}
            for m in _runtime_customer_blocking
        ]
    _profile_done, _profile_total = _profile_complete(info, known_email)
    _action_label = _act_now.get("label") or "等待客户反馈"
    _reason = ""
    if _acts and (_acts[0].get("reason") or "").strip():
        _reason = str(_acts[0]["reason"]).strip()
    elif _missing["blocking"]:
        _reason = "关键报价信息未齐，先补齐阻塞项再推进报价。"
    elif r_stat == "ready_for_quotation":
        _reason = "核心报价要素已完整，可以进入报价动作。"
    else:
        _reason = "客户需求已有可执行线索，需保持对话并推进当前销售动作。"
    _reason = _compact_text(_reason, 96)
    _match_state = "MATCHED" if matches else "UNRESOLVED"
    _match_tone = "green" if matches else "amber"
    _req_level = str(_runtime_state.get("requirementCompleteness") or "").upper()
    _req_complete_text = (_req_level if _req_level in ("HIGH", "COMPLETE")
                          else ("HIGH" if not _missing["blocking"] else "NEEDS CONFIRMATION"))
    _req_tone = "green" if _req_complete_text in ("HIGH", "COMPLETE") else "amber"
    _quote_tone = "green" if r_stat == "ready_for_quotation" else (
        "amber" if r_stat else "blue")
    _priority_label = f"{_g_letter or '—'} 级"
    if isinstance(lead.get("score"), (int, float)):
        _priority_label += f" · {lead.get('score')}/100"

    if id_ is None:
        st.markdown(
            "<div class='sales-brief'>"
            f"<div class='summary'>{_vh.escape(_summary_2line_resolved(info, matches, _runtime_state))}</div>"
            "<div class='sales-grid'>"
            f"<div class='sales-fact'><div class='lb'>客户</div>"
            f"<div class='v'>{_vh.escape(str(info.get('company') or '未知客户'))}</div>"
            f"<div class='s'>{_vh.escape(str(info.get('country') or '国家未识别'))} · "
            f"{_vh.escape(str(info.get('contact_name') or '联系人未识别'))}</div></div>"
            f"<div class='sales-fact'><div class='lb'>产品</div>"
            f"<div class='v'>{_vh.escape(str(_runtime_state.get('customerProductRequirement') or _product_text(info, matches)))}</div>"
            f"<div class='s'>Quantity: {_vh.escape(str(_runtime_req.get('quantity') or _quantity_text(info)))}</div></div>"
            f"<div class='sales-fact'><div class='lb'>目标价</div>"
            f"<div class='v'>{_vh.escape(_price_text(info))}</div>"
            f"<div class='s'>Timeline: {_vh.escape(_timeline_text(info))}</div></div>"
            f"<div class='sales-fact'><div class='lb'>优先级</div>"
            f"<div class='v'>{_vh.escape(_priority_label)}</div>"
            f"<div class='s'>{_vh.escape(resp_t or urg_lbl or '尽快处理')}</div></div>"
            f"<div class='sales-fact'><div class='lb'>客户资料</div>"
            f"<div class='v'>{_profile_done} / {_profile_total} complete</div>"
            f"<div class='s'>Missing: {_vh.escape('、'.join(x['label'] for x in _missing['profile']) or '—')}</div></div>"
            "</div>"
            "<div class='status-row'>"
            f"<div class='status-tile {_req_tone}'><div class='lb'>需求完整度</div>"
            f"<div class='v'>{_vh.escape(_req_complete_text)}</div></div>"
            f"<div class='status-tile {_match_tone}'><div class='lb'>产品匹配</div>"
            f"<div class='v'>{_match_state}</div></div>"
            f"<div class='status-tile {_quote_tone}'><div class='lb'>Quotation</div>"
            f"<div class='v'>{_vh.escape(str(r_cn or 'UNKNOWN'))}</div></div>"
            "</div>"
            "<div class='next-action-simple'><div class='eyebrow'>建议下一步</div>"
            f"<div class='action'>{_vh.escape(str(_action_label))}</div>"
            f"<div class='reason'>{_vh.escape(_reason)}</div></div></div>",
            unsafe_allow_html=True)
        # 新分析结果默认只保留轻量业务状态；详细评分和推理统一放到折叠区。
        _pmx = insight.get("product_match") or {}
        _cmpx = insight.get("requirement_completeness") or {}
        if not matches and (_pmx.get("product_match_status") == "NO_MATCH"):
            st.caption(
                "商机状态："
                f"{_pmx.get('opportunity_type_cn') or '非现有产品线机会'} · "
                f"需求：{_cmpx.get('level_cn') or '—'} · "
                "产品匹配：库内无匹配 · "
                f"供应能力：{_pmx.get('supplier_capability_label') or '待确认'}")
        _miss_tags = _missing["blocking"]
        if _miss_tags:
            st.markdown(
                "<div class='missing-simple'>"
                + "".join(f"<span class='tag'>{_vh.escape(x['label'])}</span>"
                          for x in _miss_tags[:6])
                + "</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='missing-simple'><span class='tag ok'>暂无关键阻塞项</span></div>",
                        unsafe_allow_html=True)
        _tsk = _task_items_from_state(str(_action_label), _missing, r_stat, matches, draft)
        if _tsk and id_ is None:
            st.markdown(
                "<div class='task-list'>"
                + "".join(
                    f"<div class='task-item'><div class='t'>□ {_vh.escape(t)}</div>"
                    f"<div class='m'>{_vh.escape(p)} · {_vh.escape(s)} · {_vh.escape(c)}</div></div>"
                    for t, p, s, c in _tsk)
                + "</div>", unsafe_allow_html=True)

    # —— Phase 2：Customer + Opportunity Workspace（详情 = 客户工作区）——
    if id_ is not None:
        _render_customer_workspace(dict(
            id=id_, rk=_rk, text=text, info=info, matches=matches, lead=lead,
            draft_text=draft or "", known_email=known_email, gaps=gaps,
            insight=insight, qr=_qr, r_stat=r_stat, r_score=r_score, r_cn=r_cn,
            g_letter=_g_letter, grade=grade, stt=_stt, pri_name=_pri,
            pts=_pts,
            score=(lead.get("score")
                   if isinstance(lead.get("score"), (int, float)) else None),
            biz=_biz_now, biz_cn=_wf.BIZ_CN.get(_biz_now, _biz_now),
            act=_act_now, fu_state=_fu_state, wf=_wfd,
            prelim=_prelim,
            st_blockers=[b.get("field", "") for b in _prelim],
        ))
        return

    # ============ Level 1 · Next Action（一句话说清现在最该做什么） ============
    # 第七轮：优先用 workflow 结构化结论（与左侧队列/建议同一口径），AI actions 兜底
    if _act_now.get("label") and _act_now.get("type"):
        _nextstep = _act_now["label"]
        if _prelim:
            _nm = "、".join(b.get("field", "") for b in _prelim[:3])
            _nextstep += f"（{_nm}" + ("等" if len(_prelim) > 3 else "") + "）"
    elif _prelim:
        _nm = "、".join(b.get("field", "") for b in _prelim[:3])
        _nextstep = (f"先确认「{_nm}" + ("等" if len(_prelim) > 3 else "")
                     + f"」（共 {len(_prelim)} 项），可用下方英文追问邮件一次性问清")
    elif _acts:
        _a0 = _acts[0]
        _an0 = _a0.get("action_cn") or _ACN.get(_a0.get("action", ""), _a0.get("action", ""))
        _p0 = str(_a0.get("priority") or "")
        if _p0.isdigit():          # 旧记录 priority 存的是数字 1 → 展示为 P1
            _p0 = f"P{_p0}"
        _f0 = f"（{_a0['related_field']}）" if _a0.get("related_field") else ""
        _nextstep = f"{_p0 + ' ' if _p0 else ''}{_an0}{_f0}"
    else:
        _nextstep = "信息齐全，可直接用回复草稿回信"
    # —— 第十轮（spec 七）：Next Action 五要素行动卡——
    #    action / priority / due_date / owner / status，AI 分析必须落到行动
    _pri_txt = _act_now.get("priority") or ""
    if _act_now.get("type") == "FOLLOW_UP_CUSTOMER" and _wfd.get("follow_up_at"):
        _due = "今天" if _fu_state in ("今日跟进",) else (
            "已逾期" if _fu_state == "已逾期" else str(_wfd["follow_up_at"])[:10])
    elif _act_now.get("type") == "CREATE_FOLLOW_UP":
        _due = "今天设置"
    else:
        _due = "—"
    _st_now = _fu_state or _wf.BIZ_CN.get(_biz_now, _biz_now)
    # 第十一轮（spec 七）：AI 建议必须带"原因"——销售要知道为什么要做这件事。
    # 只复用已有口径：next_actions 的 reason / 阻塞项 / 报价准备度，不新造判断。
    # （HTML 卡已由 Phase 2「AI 销售助手」承接；_nextstep 仍供回复策略引用）
    _why = ""
    if _acts and (_acts[0].get("reason") or "").strip():
        _why = str(_acts[0]["reason"]).strip()
    elif _prelim:
        _have = [f.get("field") for f in (insight.get("requirement_semantics") or [])
                 if f.get("state") in ("Confirmed", "Explicit", "Approximate")]
        _why = ("客户已明确 " + "、".join(str(x) for x in _have[:3])
                if _have else "客户需求已收到")
        _why += ("；但 " + "、".join(b.get("field", "") for b in _prelim[:3])
                 + " 尚未确认，暂时无法准确报价")
    elif r_stat in ("READY_FOR_QUOTATION", "ready_for_quotation"):
        _why = "关键信息已齐全，可以进入报价流程"

    # —— 统一客户邮件上下文 / 编写器已抽为模块级函数（ROUND 6.9 §5）——
    # 唯一实现在 _build_customer_mail_ctx / _render_customer_email_composer，
    # 由共享 Deal Detail（_render_customer_workspace）调用；此处不再重复实现，
    # 保持「同一问句只有一套生成逻辑」（第二十一轮 spec 1/4/7）。

    # ROUND 2 UI simplification：默认 Action View 已提供唯一 Primary CTA。
    # 旧的多按钮行动行在默认界面隐藏；下方邮件 / 报价 / 跟进工作区仍按
    # ui_mail / ui_quote / ui_fuzone 状态展开，业务逻辑不变。
    # 第二十一轮：追问/回复/跟进的邮件编写已统一到下方「生成客户邮件」编写器，
    # 不再单开“生成追问邮件”区，避免同一问句多套生成逻辑并存（spec 1/4/7）。

    if id_ is not None:
        _qk = f"ui_quote_{id_}"
        _can_quote = bool(matches) and _biz_now not in ("WON", "LOST", "ON_HOLD")
        with st.expander("创建报价（草稿 · 不自动发送 · 确认后记入 Timeline）",
                         expanded=bool(st.session_state.get(_qk, False))):
            if not _can_quote:
                st.warning("当前还不能创建报价：需先匹配产品并保持商机为活跃状态。")
            else:
                _qty_txt = _quantity_text(info)
                _cur_cur = info.get("target_price_currency") or "USD"
                _lines = []
                for _pm in matches[:2]:
                    _name = _pm.get("name_cn") or _pm.get("name", "")
                    try:
                        _rng = f"${_pm['price_range'][0]:.2f}–${_pm['price_range'][1]:.2f}/pc"
                    except Exception:
                        _rng = "价格待确认"
                    _lines.append(f"- {_name}: {_rng}, MOQ {_pm.get('moq') or '待确认'}")
                _default_quote = (
                    f"Dear {info.get('contact_name') or 'Sir/Madam'},\n\n"
                    "Thank you for your inquiry. Based on the current information, "
                    "please find the preliminary quotation draft below:\n\n"
                    + "\n".join(_lines)
                    + f"\n\nQuantity: {_qty_txt}\n"
                    + (f"Customer target price: {_cur_cur} {info.get('target_price')}\n"
                       if info.get("target_price") else "")
                    + "\nFinal quotation is subject to confirmed specification and trade terms.\n\n"
                    "Best regards,\n"
                    + (SELLER.get("company") or "")
                )
                with st.form(f"compact_quote_top_{id_}"):
                    st.text_area("报价草稿（英文，发送前人工复核）",
                                 value=_default_quote, height=170)
                    if st.form_submit_button("确认创建报价（写入 Timeline）",
                                             type="primary"):
                        _q_summary = "、".join(str(p.get("name_cn") or p.get("name", ""))
                                               for p in matches[:2])
                        _record_quote_draft(id_, info, matches, _q_summary, _qty_txt)
                        st.toast("报价草稿已创建并记入 Timeline")
                        st.rerun()

    # —— 统一客户邮件上下文 / 编写器已抽为模块级函数（ROUND 6.9 §5）——
    # 唯一实现在 _build_customer_mail_ctx / _render_customer_email_composer，
    # 由共享 Deal Detail（_render_customer_workspace）调用；此处不再重复实现，
    # 保持「同一问句只有一套生成逻辑」（第二十一轮 spec 1/4/7）。

    # —— 跟进区 / 结单区已抽为 _render_deal_closure（ROUND 6.9 §5）——
    # 唯一实现在共享 Deal Detail（_render_customer_workspace）内调用，
    # 这里不再重复实现（历史缺陷：回复闭环被留在 return 之后的废弃分支）。
    with st.expander("完整客户需求", expanded=False):
        kc = st.columns(len(KEY_FIELDS), gap="small")
        for (key, label), col in zip(KEY_FIELDS, kc):
            val = info.get(key) or ""
            if key == "email" and not val and known_email:
                val = known_email
            col.markdown(
                f"<div class='cust-chip {'ok' if val else 'miss'}'><div class='t'>{label}</div>"
                f"<div class='v'>{_vh.escape(str(val or '未识别'))}</div></div>",
                unsafe_allow_html=True)
        _detail_rows = [
            ("Product", _product_text(info, matches)),
            ("Quantity", _quantity_text(info)),
            ("Target Price", _price_text(info)),
            ("Timeline", _timeline_text(info)),
            ("Certification", info.get("certification") or "未提及"),
            ("Customization", info.get("customization") or "未提及"),
            ("Incoterm", info.get("incoterm") or "未提及"),
            ("Payment", info.get("payment") or "未提及"),
            ("Sample", info.get("sample") or "未提及"),
        ]
        st.markdown(
            "<div class='metric-strip'>"
            + "".join(f"<div class='mi'><label>{_vh.escape(k)}</label><b>{_vh.escape(str(v))}</b></div>"
                      for k, v in _detail_rows)
            + "</div>", unsafe_allow_html=True)
        if matches:
            st.markdown("**产品匹配**")
            for p in matches[:3]:
                st.caption(
                    f"{p.get('name') or p.get('name_cn')} · match "
                    f"{int(p.get('match_score', 0) * 100)}% · MOQ {p.get('moq') or '—'}")
        else:
            st.caption("产品库暂无匹配产品。")

    with st.expander("AI 判断依据（Why?）",
                     expanded=st.session_state.get(f"ui_basis_{_rk}", False)):
        _render_ai_basis(lead, insight, report, _ns)

    if id_ is not None:
        with st.expander("客户资料维护 / 备注", expanded=False):
            with st.form(f"edit_info_compact_top_{id_}"):
                e1, e2 = st.columns(2)
                n_country = e1.text_input("客户国家/地区", value=info.get("country") or "")
                n_company = e2.text_input("客户公司", value=info.get("company") or "")
                n_website = e1.text_input("公司网址", value=info.get("website") or "")
                n_email = e2.text_input("联系邮箱", value=info.get("email") or "")
                n_contact = e1.text_input("联系人", value=info.get("contact_name") or "")
                if st.form_submit_button("保存客户信息"):
                    update_inquiry_info(id_, {
                        "country": n_country, "company": n_company,
                        "website": n_website, "email": n_email,
                        "contact_name": n_contact,
                    })
                    st.toast("已保存，客户档案已同步更新")
                    st.rerun()
            cur_note = get_inquiry_note(id_) or ""
            note_val = st.text_area("备注", value=cur_note, height=80,
                                    key=f"compact_note_top_{id_}")
            if st.button("保存备注", key=f"compact_savenote_top_{id_}"):
                update_inquiry_note(id_, note_val)
                st.toast("备注已保存")

    with st.expander("原始询盘", expanded=st.session_state.get(f"ui_src_{_rk}", False)):
        st.code(text.strip())

    return
    return

# --- ROUND 7.0 CRM V1 FINAL FREEZE（§2 清理 unreachable legacy UI）----
# render_report 原函数尾部约 727 行（包括 Level 2 待处理 / 需求确认 / 跟进 /
# 结单 / 客户需求 / AI 依据 / 客户资料维护 / 三套报价 UI 等）整体已不可达，
# 上面 §1-§5 抽出的模块级 helper 完整覆盖其业务语义：
#   · 跟进 / 结单                → _render_deal_closure()
#   · 商机阶段推进 / 状态         → _render_deal_pipeline()
#   · 统一客户邮件 Composer     → _render_customer_email_composer()
#   · AI 依据                   → _render_ai_basis()
# 报价逻辑函数（_quote_amount_from_context / _record_quote_draft）保留为模块级
# helper 以备未来 Quote Workflow 调用；具体报价 UI 由 Quote Workflow 重新设计。

# ====================== 导出 Excel ======================
def _build_xlsx(rows, columns, sheet_name):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(columns)
    for r in rows:
        ws.append([r.get(col, "") for col in columns])
    # 简单列宽
    for i, col in enumerate(columns, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(10, min(40, len(str(col)) + 4))
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ============ 界面布局 ============
st.set_page_config(page_title="AI外贸业务工作台", layout="wide")

# —— 顶栏：产品名（置顶）+ 公司徽标；全局新建入口只保留在侧栏 ——
_hh = st.columns([3.2, 1.2])
with _hh[0]:
    st.title("AI外贸业务工作台")
    st.caption("询盘分析 · 产品匹配 · 报价决策 · 客户跟进")
with _hh[1]:
    _co = (SELLER.get("company") or "").strip()
    _st = (SELLER.get("strength") or "").strip()
    st.markdown(
        f"<div class='brand-box'>"
        + (f"<div class='co'>{_co}</div>" if _co else "<div class='co'>我方公司资料</div>")
        + (f"<div class='st'>{_st}</div>" if _st else "")
        + "</div>", unsafe_allow_html=True)
st.markdown(PAGE_CSS, unsafe_allow_html=True)

# —— 数据准备（先算再渲染，保证 KPI / 今日优先 / 为什么现在 / 销售队列 /
#    Sidebar 全部读取同一份 Deal Work View Model）——
# 第二十三轮：ONE ACTIONABLE DEAL = ONE WORK ITEM。
#   先按 deal_thread_key（客户身份 + 产品签名 + 活跃/已闭环桶）把多封往来
#   聚合成 Deal 组，再经 deal_work_item 产出该 Deal 的当前状态 ——
#   任何组件都不再各自拼接历史消息状态（杜绝 Sidebar 与首页重复/状态不一）。
_home_items = load_queue(None)
_stage_of = {o.get("inquiry_id"): o.get("stage")
             for o in (list_opportunities() or []) if o.get("inquiry_id")}
# Deal 级索引（商机 1:1 询盘 + OPEN 跟进任务）—— Sidebar 与首页共用同一份
_opp_by_inq = {}
for _o in (list_opportunities() or []):
    _details = _o.get("details") or {}
    _ids = []
    for _iid in (_o.get("inquiry_id"), _details.get("current_inquiry_id")):
        if _iid:
            _ids.append(_iid)
    for _iid in (_details.get("related_inquiry_ids") or []):
        if _iid:
            _ids.append(_iid)
    for _iid in set(_ids):
        _opp_by_inq[_iid] = _o
_tasks_by_opp = {}
for _t in (_db_mod.list_followup_tasks() or []):
    if _t.get("status") == "OPEN" and _t.get("fu_status") in (
            "PENDING", "WAITING_CUSTOMER", "SNOOZED"):
        _tasks_by_opp.setdefault(_t.get("opportunity_id"), []).append(_t)


def _deal_wv(_g):
    """单个 Deal 组 → 统一 DealWorkItem（代表商机 + OPEN 跟进任务）。"""
    _o = _ui.representative_opp(_g, _opp_by_inq, _tasks_by_opp)
    _ts = ((_tasks_by_opp or {}).get((_o or {}).get("id")) if _o else None)
    return _ui.deal_work_item(_g, _o, _ts)


# 全量 Deal（历史往来也计入 conversation_count）→ KPI 副行口径
# ROUND 6.9 §4：分组键以商机层 Deal 身份为准（_deal_identity_map），
# 同一客户的同一产品/数量修订不再裂成两张卡。
_deal_of = _deal_identity_map()
_home_all_deals = _ui.group_deal_threads(_home_items, _stage_of, _deal_of)
_home_all_wv = [_deal_wv(_g) for _g in _home_all_deals]
# 可执行 Deal（仍有待处理往来）→ 今日优先 / 为什么现在 / 销售工作队列
_home_wv = [w for w in _home_all_wv if w["open_count"] > 0]


def _deal_sort_rank(_w):
    """第二十三轮 §10：Deal 级排序 —— Overdue → P1 → Due Today →
    报价就绪 → 待回复 → 其他活跃 Deal。同一 Deal 只参与一次排序。"""
    _w_biz = (_w.get("lead") or {}).get("biz") or ""
    if _w.get("overdue"):
        return 0
    if _w.get("tier") == "P1":
        return 1
    if _w.get("lead_fu_state") == "今日跟进":
        return 2
    if _w.get("stage") == "REQUIREMENT_CONFIRMED" \
            or _w_biz == _wf.READY_FOR_QUOTE:
        return 3
    if _w_biz == _wf.READY_TO_REPLY or _w.get("stage"):
        return 4
    return 5


def _matches_sales_work_view(work_item: dict, view_name: str) -> bool:
    """Smart View 的唯一 Deal 级筛选口径。

    首页“今日行动”与侧栏计数都消费同一 DealWorkItem；这里仅把既有业务状态
    映射到工作视图，不排序、不重算优先级，也不创建新的队列数据。
    """
    items = work_item.get("items") or []
    if view_name == "优先处理":
        return bool(work_item.get("tier") == "P1" or work_item.get("overdue"))
    if view_name == "待回复":
        return any(x.get("biz") == _wf.READY_TO_REPLY for x in items)
    if view_name == "待报价":
        return any(x.get("biz") == _wf.READY_FOR_QUOTE for x in items)
    if view_name == "今日跟进":
        return bool(work_item.get("lead_fu_state") == "今日跟进" or any(
            (x.get("action") or {}).get("type") == "FOLLOW_UP_CUSTOMER"
            for x in items))
    if view_name == "已逾期":
        return bool(work_item.get("overdue") or work_item.get("lead_fu_state") == "已逾期")
    if view_name == "新询盘":
        return bool(str(work_item.get("stage") or "").upper() in ("", "NEW") or any(
            x.get("biz") == _wf.NEW for x in items))
    return True


_home_wv.sort(key=lambda _w: (_deal_sort_rank(_w),
                               -(_w.get("queue_score") or 0)))
# A4 · KPI 单位桥接（只读）：上方 KPI 按询盘/消息计数，这里给"聚合后 Deal 数"，
#   避免出现"待回复 5 但只有 2 个可执行 Deal"的困惑。
_deal_reply = sum(1 for _w in _home_wv
                  if any(_x.get("biz") == _wf.READY_TO_REPLY
                         for _x in _w["items"]))
_deal_quote = sum(1 for _w in _home_wv
                  if any(_x.get("biz") == _wf.READY_FOR_QUOTE
                         for _x in _w["items"]))
_deal_fu = sum(1 for _w in _home_wv
               if any((_x.get("action") or {}).get("type")
                      == "FOLLOW_UP_CUSTOMER" for _x in _w["items"]))
_deal_open_msg = sum(_w["open_count"] for _w in _home_wv)

# —— 今日概览：轻量状态条，只做环境信息，不抢首屏主任务焦点 ——
_kpi = _kpi_snapshot()
_td = _kpi["today"] - _kpi["yest"]
import html as _home_html
_kpi_bits = [
    ("blue", "今日新增", _kpi["today"],
     f"较昨日 {'+' if _td > 0 else ''}{_td}"),
    ("amber" if _kpi["to_reply"] else "neutral", "待回复", _kpi["to_reply"],
     f"{_deal_reply} 个 Deal"),
    ("red" if _kpi["fu_due"] else "neutral", "到期跟进", _kpi["fu_due"],
     f"{_deal_fu} 个 Deal"),
    ("green" if _kpi["to_quote"] else "neutral", "待报价", _kpi["to_quote"],
     f"{_deal_quote} 个 Deal"),
    ("neutral", "待办商机", len(_home_wv), f"{_deal_open_msg} 条消息"),
]
st.markdown(
    "<div class='kpi-strip'>"
    + "".join(
        f"<div class='kpi-chip {tone}'><span>{_home_html.escape(label)}</span>"
        f"<b>{_home_html.escape(str(value))}</b>"
        f"<span>{_home_html.escape(str(sub))}</span></div>"
        for tone, label, value, sub in _kpi_bits)
    + "</div>",
    unsafe_allow_html=True)

_home_work_view = st.session_state.get("sales_inbox_filter") or "全部商机"
_home_view_label = (f" · {_home_html.escape(_home_work_view)}"
                    if _home_work_view != "全部商机" else "")
st.markdown("<div class='workspace-section compact'><span class='title'>今日行动</span><span class='note'>按紧急程度与销售推进需求排序" + _home_view_label + "</span></div>", unsafe_allow_html=True)
if st.session_state.get("home_opened_id"):
    st.info(f"已打开询盘 #{st.session_state['home_opened_id']}；详情已在下方“分析询盘”区域加载，可继续执行回复、报价或跟进。")
with st.container(border=False):
    # 第二十三轮：销售工作队列 = 统一 DealWorkItem 视图（与 Sidebar 同一
    # 数据源）。同一个 customer + product + Deal 只渲染一行；历史 Inquiry /
    # Reply 折叠在该 Deal 的展开器里，永远不单独成行。禁止按公司名去重
    # （同客户不同产品仍是两行）。
    _mq_wv = [w for w in _home_wv if _matches_sales_work_view(w, _home_work_view)][:8]
    if not _mq_wv:
        st.caption(f"“{_home_work_view}”视图当前没有需要处理的 Deal。")
    else:
        import html as _hq
        for _w in _mq_wv:
            _need = _w.get("need") or {}
            _lead = _w.get("lead") or {}
            _q_state = _w.get("resolvedState") or {}
            _q_action = ((_q_state.get("nextBestAction") or {}).get("type")
                         or _w.get("nba_type") or "SEND_REPLY")
            _q_cta = (_q_state.get("primaryCta") or _w.get("nba")
                      or "查看并发送客户邮件")
            _q_opp = _opp_by_inq.get(_w.get("lead_id"))
            try:
                _q_prog = _pg.progression_of(_q_opp.get("id")) if _q_opp else {}
            except Exception:
                _q_prog = {}
            _q_na = _q_prog.get("next_activity_status") or ""
            _q_health = _q_prog.get("health") or ""
            _q_action_state = _q_state.get("actionState") or ""
            # Why now 只读取 progression / resolved state 已确定的操作信号，
            # 不创建首页专用优先级或新的 AI 判断。
            if _q_na == "OVERDUE":
                _q_why = "已逾期"
            elif _q_na == "DUE_TODAY":
                _q_why = "今天到期"
            elif _q_health == "AT_RISK":
                _q_why = "存在推进风险"
            elif _q_action_state == "WAITING_INTERNAL":
                _q_why = "待内部处理"
            elif str(_lead.get("biz") or "").upper() == _wf.NEW:
                _q_why = "新询盘"
            elif str(_lead.get("biz") or "").upper() == _wf.READY_TO_REPLY:
                _q_why = "客户已回复"
            else:
                _q_why = _q_prog.get("primary_reason") or _w.get("badge") or "需要推进"
            _q_deal = " · ".join(str(x) for x in (
                _w.get("company") or _w.get("contact") or "未知客户",
                _w.get("product") or "产品未标注",
                _w.get("quantity") or "数量未标注") if x)
            _qcols = st.columns([5.2, 1.25], gap="small")
            with _qcols[0]:
                st.markdown(
                    f"<div class='today-action-row'>"
                    f"<div class='action'>{_hq.escape(str(_q_cta))}</div>"
                    f"<div class='deal'>{_hq.escape(_q_deal)}</div>"
                    f"<div class='why'>{_hq.escape(str(_q_why)[:72])}</div>"
                    f"</div>", unsafe_allow_html=True)
            with _qcols[1]:
                st.button(str(_q_cta), key=f"mq_open_{_w['lead_id']}",
                          type="primary", use_container_width=True,
                          on_click=_home_execute_resolved_action,
                          args=(_w["lead_id"], _q_action),
                          help=f"执行：{_q_cta}")
            # 同一 Deal 的历史往来仍保留在详情 Timeline；首页队列不再插入
            # 大面积展开条，避免扫描列表时被历史消息打断。

def _queue_card_html(it: dict, sel_id, child: bool = False) -> str:
    """询盘卡片 HTML（第六轮）：
    状态(+等待时长) > 客户名 > 主题·数量 > 国旗·国家·相对时间·#ID > AI评分。
    视觉权重依次降低；颜色只用于状态点/状态字/左侧线（spec 十一）。"""
    import html as _esc
    _need = it.get("need") or {}
    # 第七轮：业务状态优先用 load_queue 预计算的 workflow 口径（旧进程无此键时派生兜底）
    _biz = it.get("biz") or _wf.derive_biz(
        it["status"], _need.get("blockers") or [], _need.get("readiness") or "",
        (it.get("wf") or {}).get("biz_status"))
    _tone, _bemoji = _wf.BIZ_STYLE.get(_biz, ("blue", "🔵"))
    _badge = _wf.BIZ_CN.get(_biz, _biz)
    if it["status"] == STATUS_TODO and it["pri"] == PRI_HIGH and not _need.get("blockers"):
        _tone, _badge = "high", "高优 · 待回复"
    if it["status"] == STATUS_DONE:
        _tone = "green"
    # 等待处理时长（spec 十）：待回复/待补信息显示「等 N分钟/N小时/N天」，
    # 已处理(跟进中)没有状态时间戳 → 不显示等待（不伪造时间）
    _wait = _ui.wait(it["created"]) if it["status"] == STATUS_TODO else ""
    _badge_txt = f"{_bemoji} {_badge}"
    if _wait and _wait not in ("刚刚",):
        _badge_txt += f" · {_wait}"
    _who = _esc.escape(it["company"] or it["contact"] or it["country"] or "未知客户")
    _intent = _esc.escape(_need.get("intent") or "询盘主题未标注")
    _need_txt = f"{_intent} · {_esc.escape(_need['qty'])}" if _need.get("qty") \
        else f"{_intent} · 数量未识别"
    _fg = _ui.flag(it["country"])
    _ctn = _esc.escape(str(it["contact"] or ""))
    _co = _esc.escape(str(it["company"] or ""))
    # Phase 1：卡片上 Company / Contact / Country 分层明确；
    # 联系人 ≠ 公司名时才单独展示，避免同客户组内重复堆砌
    if _ctn and _ctn.lower() == _co.lower():
        _ctn = ""
    _meta_bits = [b for b in [_fg, _ctn, _esc.escape(str(it["country"] or "")),
                              _ui.ago(it["created"])] if b]
    _score_txt = (f"P{it['pts']}"
                  + (f" · {(it['cust_grade'] or it['grade'] or '')}级"
                     if (it["cust_grade"] or it["grade"]) else "")
                  if it.get("pts") else "—")
    # 数字 AI Priority / Queue Score 继续保留为内部排序字段，普通销售侧栏不展示分数。
    _is_sel = (it["id"] == sel_id)
    _cls = ("sq " + _tone + (" child" if child else "") + (" sel" if _is_sel else "")
            + " clickable")
    _full_time = str(it["created"] or "")[:16].replace('"', "'")
    # 第七轮（spec 十一）：Next Action 短句——一行"下一步：确认产品"，不超过一句话
    _act = it.get("action") or {}
    _next_txt = ""
    if _act.get("label"):
        _next_txt = (f"<div class='r2 next'>下一步：<b>{_esc.escape(str(_act['label']))}"
                     f"</b></div>")
    return (f"<div class='{_cls}' title='#{it['id']} · {_full_time}'>"
            f"<div class='r1'><span class='sdot'></span>"
            f"<span class='stx'>{_badge_txt}</span></div>"
            f"<div class='co'>{_who}</div>"
            f"<div class='r2 need'>{_need_txt}</div>"
            f"<div class='r2 meta'>{(' · '.join(_meta_bits))}"
            f"&nbsp;&nbsp;<span class='iid'>#{it['id']}</span></div>"
            + _next_txt +
            f"<div class='score'>{_score_txt}</div></div>")


def _render_queue_card(it: dict, sel_id, child: bool = False):
    """渲染单张询盘卡：整卡可点击（透明覆盖按钮），点击即选中、主区切换。"""
    with st.container():
        st.markdown(_queue_card_html(it, sel_id, child), unsafe_allow_html=True)
        st.button("", key=f"open_{it['id']}", use_container_width=True,
                  on_click=_open_inquiry, args=(it["id"],),
                  help=f"打开 #{it['id']}")



# ==================================================================
# 第十七轮（CRM 对标升级 R1）：全局模块 —— 今日工作台 / 销售管道 / 客户列表
# 对标 HubSpot（My Day + Contacts）/ Pipedrive（Pipeline 看板）：
# 纯新增渲染层，只读 load_queue / list_customers 现有口径，
# 不写库、不改状态机、不动工作区 5 Tab 逻辑（默认模块 = 工作区）。
# ==================================================================
_CRM_HOME = "💼 工作区"
_CRM_TODAY = "🏠 今日"
_CRM_PIPE = "📊 管道"
_CRM_CUST = "👥 客户"


def _open_from_module(iid: int):
    """从 CRM 模块打开询盘：复用 _open_inquiry 的状态切换，并切回工作区模块。"""
    _open_inquiry(iid)
    st.session_state.crm_module = _CRM_HOME


def _crm_module_nav():
    """主区顶部的全局模块导航；默认=工作区，老功能原样渲染。"""
    return st.pills(
        "模块", [_CRM_HOME, _CRM_TODAY, _CRM_PIPE, _CRM_CUST],
        key="crm_module", default=_CRM_HOME, label_visibility="collapsed")


def _crm_items():
    """CRM 模块共用询盘口径（与侧栏队列同源同排序；失败兜底为空列表）。"""
    try:
        return load_queue(None)
    except Exception:
        return []


def _crm_week_new(items):
    """本周新增询盘（近 7 天，按 created_at 日期粗分）。"""
    _cut = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    return [x for x in items if str(x.get("created") or "")[:10] >= _cut]


def _crm_open_row(x: dict, key: str):
    """模块页的一行询盘：信息 + [打开] 按钮（点击后切回工作区并选中该询盘）。"""
    import html as _eh
    act = x.get("action") or {}
    biz_cn = _wf.BIZ_CN.get(x.get("biz"), x.get("biz") or "—")
    fu = x.get("fu_state") or ""
    who = _eh.escape(str(x["company"] or x["contact"] or "未署名客户"))
    nxt = (f" · 下一步：<b>{_eh.escape(str(act['label']))}</b>"
           if act.get("label") else "")
    aip = x.get("aip3")
    aip_txt = str(aip) if aip is not None else "—"
    c1, c2 = st.columns([7, 1])
    with c1:
        st.markdown(
            f"**{who}** · {_flag(x['country'])} {x['country'] or '未知'}"
            f" · {biz_cn}"
            + (f" · 跟进：<b style='color:var(--danger)'>{fu}</b>" if fu == "已逾期"
               else (f" · 跟进：{fu}" if fu else ""))
            + nxt)
        st.caption(f"#{x['id']} · {str(x['created'])[:16]}"
                   f" · AI Priority {aip_txt}"
                   + (f" · {str(x.get('need', {}).get('product') or '')[:40]}"
                      if x.get("need", {}).get("product") else ""))
    with c2:
        st.button("打开", key=key, use_container_width=True,
                  on_click=_open_from_module, args=(x["id"],))


def _render_today_page():
    """🏠 今日工作台（对标 HubSpot My Day）：逾期 → 今日 → 待回复 → 本周新增。"""
    st.subheader("🏠 今日工作台（My Day）")
    st.caption("今天该做什么，按顺序来：逾期跟进 → 今日跟进 → 待回复 → 本周新增。"
               "口径与侧栏队列完全一致。")
    items = _crm_items()
    todo = [x for x in items if x["status"] == STATUS_TODO]
    overdue = [x for x in items if x.get("fu_state") == "已逾期"]
    today_fu = [x for x in items if x.get("fu_state") == "今日跟进"]
    n_reply = sum(1 for x in todo if x.get("biz") == _wf.READY_TO_REPLY)
    week = _crm_week_new(items)
    k1, k2, k3, k4 = st.columns(4)
    _kpi_card(k1, "已逾期跟进", len(overdue), "最优先处理，别让客户凉掉", "red")
    _kpi_card(k2, "今日跟进", len(today_fu), "今天要触达的客户", "amber")
    _kpi_card(k3, "待回复", n_reply, "具备回复条件的询盘", "blue")
    _kpi_card(k4, "本周新增询盘", len(week), "近 7 天新线索", "green")
    st.divider()

    if overdue:
        st.markdown("**⏰ 逾期跟进（先做这些）**")
        for x in overdue:
            _crm_open_row(x, f"crm_od_{x['id']}")
    else:
        st.markdown("✅ **没有逾期跟进**")
    if today_fu:
        st.markdown("**📌 今日跟进**")
        for x in today_fu:
            _crm_open_row(x, f"crm_td_{x['id']}")
    st.divider()
    st.markdown("**💬 待回复 Top 5（按 Queue Score）**")
    top = sorted([x for x in todo if x.get("biz") == _wf.READY_TO_REPLY],
                 key=lambda x: -(x.get("qs3") or x.get("score") or 0))[:5]
    for x in top:
        _crm_open_row(x, f"crm_rp_{x['id']}")
    if not top:
        st.caption("暂无待回复询盘。")
    if week:
        st.markdown("**🆕 本周新增询盘（最新 5 条）**")
        for x in sorted(week, key=lambda x: str(x.get("created") or ""),
                        reverse=True)[:5]:
            _crm_open_row(x, f"crm_nw_{x['id']}")


# 管道看板展示的商机阶段（crm.py 九态，与客户工作区商机状态条同一口径）；
# 左→右 = 推进顺序。WON / LOST / NURTURE 为终态，收进折叠区
_PIPE_OPP = [_crm.OPP_NEW, _crm.OPP_QUALIFYING, _crm.OPP_MATCHING,
             _crm.OPP_QUOTE_PENDING, _crm.OPP_QUOTED, _crm.OPP_NEGOTIATING]


def _render_pipeline_page():
    """📊 销售管道（对标 Pipedrive Pipeline）：按商机阶段的只读看板。

    阶段口径 = crm.py 商机九态（opp_stage_of_biz），与详情页 7 段状态条
    完全一致——一个阶段只有一个名字，不发明新词。
    """
    import html as _eh
    st.subheader("📊 销售管道（Pipeline）")
    st.caption("全部询盘按商机阶段分布，左→右 = 推进顺序。点击卡片回到工作区处理。")
    items = _crm_items()
    groups = {b: [] for b in _PIPE_OPP}
    finals = []
    for x in items:
        groups.get(_crm.opp_stage_of_biz(x.get("biz")), finals).append(x)

    def _pipe_col(col, b):
        g = groups[b]
        emoji = _crm.OPP_EMOJI.get(b, "")
        with col:
            st.markdown(f"**{emoji} {_crm.OPP_CN.get(b, b)}**　`{len(g)}`")
            st.divider()
            for x in sorted(g, key=lambda x: -(x.get("qs3") or 0))[:8]:
                who = _eh.escape(str(x["company"] or x["contact"] or "未署名"))
                st.markdown(
                    f"<div style='font-size:.78rem;line-height:1.55'>"
                    f"<b>{who}</b><br>"
                    f"{_flag(x['country'])} {x['country'] or '未知'} · #{x['id']}"
                    f" · AIP {x.get('aip3') if x.get('aip3') is not None else '—'}"
                    f"</div>",
                    unsafe_allow_html=True)
                st.button("打开", key=f"pipe_{b}_{x['id']}",
                          use_container_width=True,
                          on_click=_open_from_module, args=(x["id"],))
            if len(g) > 8:
                st.caption(f"…另有 {len(g) - 8} 条")

    half = (len(_PIPE_OPP) + 1) // 2
    st.markdown("##### 前期阶段")
    cols_top = st.columns(half)
    for col, b in zip(cols_top, _PIPE_OPP[:half]):
        _pipe_col(col, b)
    st.markdown("##### 推进阶段")
    cols_bot = st.columns(len(_PIPE_OPP) - half)
    for col, b in zip(cols_bot, _PIPE_OPP[half:]):
        _pipe_col(col, b)
    if finals:
        with st.expander(f"终态（已成交 / 已丢单 / 长期培育，共 {len(finals)} 条）"):
            for x in sorted(finals, key=lambda x: str(x.get("created") or ""),
                            reverse=True):
                st.caption(f"#{x['id']} · {x['company'] or '未署名'}"
                           f" · {_crm.OPP_CN.get(_crm.opp_stage_of_biz(x.get('biz')), '—')}"
                           f" · {str(x.get('created') or '')[:10]}")


def _render_contacts_page():
    """👥 客户列表（对标 HubSpot Contacts）：全量客户档案 + 搜索 + 打开。"""
    st.subheader("👥 客户（Contacts）")
    st.caption("全部客户档案。点「打开」进入该客户名下最新询盘的工作区"
               "（Header / Pipeline / AI Assistant / Timeline）。")
    _q = (st.text_input("搜索", key="crm_cust_search",
                        label_visibility="collapsed",
                        placeholder="🔍 公司 / 国家 / 邮箱 / 联系人")
          or "").strip().lower()
    items = _crm_items()
    # 每个客户名下最新一条询盘（用于「打开」跳转）
    latest = {}
    for x in items:
        cid = x.get("cust_id")
        if cid is not None and (cid not in latest
                                or str(x.get("created") or "")
                                > str(latest[cid].get("created") or "")):
            latest[cid] = x
    try:
        rows = list_customers()
    except Exception:
        rows = []
    if _q:
        rows = [r for r in rows
                if _q in " ".join(str(v) for v in r[:5]).lower()]
    if not rows:
        st.caption("暂无客户档案，先在工作区完成一次询盘分析。")
        return
    for r in rows:
        cid, company, country, email, contact, cnt, last_grade, score, seen, \
            grade, note = r[:11]
        g = grade or last_grade or "—"
        c1, c2 = st.columns([9, 1])
        with c1:
            st.markdown(
                f"{GRADE_COLOR.get(g, '')} **{company or '（未署名）'}**"
                f" · {_flag(country)} {country or ''} · {email or ''}"
                + (f" · {contact}" if contact else ""))
            st.caption(f"等级 {g} · 询盘 {cnt} 条"
                       f" · 最近互动 {str(seen)[:10] or '—'}"
                       + (f" · {note[:30]}" if note else ""))
        with c2:
            if cid in latest:
                st.button("打开", key=f"crm_cust_{cid}",
                          use_container_width=True,
                          on_click=_open_from_module,
                          args=(latest[cid]["id"],))
            else:
                st.button("打开", key=f"crm_cust_{cid}",
                          use_container_width=True, disabled=True)

def _render_deal_history_row(it: dict):
    """Sidebar Deal 历史往来：轻量 timeline，不再显示当前 Next Action。"""
    import html as _esc
    _need = it.get("need") or {}
    _title = (_ui.product_title({"product_query": _need.get("product_query")})
              or _need.get("product") or _need.get("intent") or "历史往来")
    _qty = _need.get("qty") or ""
    _count = int(it.get("_history_count") or 1)
    _delta = str(it.get("_history_delta") or "")
    _meta = " · ".join(x for x in (
        f"#{it.get('id')}",
        _ui.ago(it.get("created")),
        it.get("contact") or "",
        it.get("country") or "",
    ) if x)
    if _count > 1:
        _meta += f" · 含 {_count} 条相同往来"
    _line = f"{_title}" + (f" · {_qty}" if _qty else "")
    st.markdown(
        f"<div class='deal-history-row'>"
        f"<div class='meta'>{_esc.escape(_meta)}</div>"
        f"<div class='title'>{_esc.escape(str(_line))}</div>"
        f"<div class='next'>{('<span class=\"delta\">' + _esc.escape(_delta) + '</span>') if _delta else '历史往来 · 当前动作以主卡为准'}</div>"
        f"</div>",
        unsafe_allow_html=True)
    st.button("查看往来", key=f"open_hist_{it['id']}",
              use_container_width=True, on_click=_home_select_inquiry,
              args=(it["id"],), help=f"打开历史往来 #{it['id']}")


def _compact_deal_history_items(items: list, lead_id=None, limit: int = 4) -> list:
    """压缩 Sidebar 历史往来展示；不删除历史，只合并相同产品+数量的视觉行。"""
    hist = sorted((x for x in items or [] if x.get("id") != lead_id),
                  key=lambda x: -(x.get("id") or 0))
    buckets = []
    seen = {}
    current_qty = ""
    for it in sorted(items or [], key=lambda x: (str(x.get("created") or ""), x.get("id") or 0)):
        q = ((it.get("need") or {}).get("qty") or "").strip()
        if q:
            current_qty = q
    for it in hist:
        need = it.get("need") or {}
        sig = (_ui.product_signature(need.get("product_query")
                                     or need.get("product")
                                     or need.get("product_cat") or ""),
               str(need.get("qty") or ""))
        if sig in seen:
            seen[sig]["_history_count"] = int(seen[sig].get("_history_count") or 1) + 1
            continue
        clone = dict(it)
        clone["_history_count"] = 1
        q = str(need.get("qty") or "")
        if q and current_qty and q != current_qty:
            clone["_history_delta"] = f"历史数量：{q}，当前以主卡 {current_qty} 为准"
        seen[sig] = clone
        buckets.append(clone)
    return buckets[:limit]


def _deal_opp_of(g: dict, opp_by_inq: dict, tasks_by_opp: dict) -> dict | None:
    """Deal 组代表商机（第 23 轮起统一委托 queue_ui.representative_opp，
    与首页销售队列共用同一选择口径）。"""
    return _ui.representative_opp(g, opp_by_inq, tasks_by_opp)


def _sidebar_operational_status(w: dict) -> str:
    """侧栏卡片唯一的操作状态。

    只把现有 DealWorkItem / ResolvedDealState 的状态降维为一条文字；
    不在侧栏生成第二套健康度、评分或 AI 判断。优先显示真正影响执行顺序的
    跟进状态，其他情况才回落到当前唯一的 Next Best Action。
    """
    resolved = (w or {}).get("resolvedState") or {}
    action_state = str(resolved.get("actionState") or "")
    follow_up = str((w or {}).get("lead_fu_state") or "")
    if (w or {}).get("overdue") or follow_up == "已逾期":
        return "已逾期"
    if follow_up == "今日跟进":
        return "今日到期"
    if action_state == "NEEDS_CUSTOMER_INFO":
        return "待补关键信息"
    if follow_up == "等待客户":
        return "等待客户"
    if action_state == "WAITING_INTERNAL":
        return "待内部处理"
    if action_state == "READY_TO_REPLY":
        return "待回复"
    return f"下一步：{(w or {}).get('nba') or '查看 Deal'}"


def _deal_card_html(g: dict, sel_id, opp=None, opp_tasks=None,
                    clickable: bool = True) -> str:
    """Sidebar Quick Deal 卡片：一张卡一个 Deal，最多三行事实。

    Deal 身份与代表商机由 group_deal_threads / representative_opp 决定；
    卡片只呈现公司、产品数量和一个可执行状态。评分、等级、国家、历史数量
    与健康度解释继续保留在主工作区，不进入侧栏。
    """
    import html as _esc
    w = _ui.deal_work_item(g, opp, opp_tasks)
    lead = g.get("lead") or {}
    need = w.get("need") or {}
    _who = _esc.escape(str(w.get("company") or w.get("contact")
                            or w.get("country") or "未知客户"))
    _ptitle = _esc.escape(str(w.get("product")
                              or need.get("intent") or "询盘主题未标注"))
    _pq = (f"{_ptitle} · {_esc.escape(str(w.get('quantity')))}"
           if w.get("quantity") else _ptitle)
    _status = _esc.escape(_sidebar_operational_status(w))
    # ROUND 6.8：Quick Access 默认不显示 Deal ID、消息数、时间戳或历史摘要；
    # 数量变更等历史信息进入 Deal Detail 的 Activity Timeline。
    _sel = (sel_id is not None and any(
        str(x.get("id")) == str(sel_id) for x in w["items"]))
    _tone = w.get("tone") or "blue"
    _cls = ("sq " + _tone + (" sel" if _sel else "")
            + (" clickable" if clickable else "") + " deal")
    _card_title = _esc.escape(str(w.get("company") or "Deal"))
    return (f"<div class='{_cls}' title='{_card_title}'>"
            f"<div class='co'>{_who}</div>"
            f"<div class='r2 need'>{_pq}</div>"
            f"<div class='r2 next'>{_status}</div>"
            "</div>")


# 侧边栏：轻量 CRM Navigation + Deal Quick Access（ROUND 6.8）
def _set_sidebar_view(name: str):
    st.session_state.sales_inbox_filter = name
    st.session_state.queue_filter = {
        "优先处理": "高优先级", "待回复": "待回复", "待报价": "待报价",
        "今日跟进": "待跟进", "已逾期": "待跟进", "新询盘": "全部",
        "全部商机": "全部",
    }.get(name, "全部")


def _toggle_sidebar_collapse():
    st.session_state.sidebar_collapsed = not bool(st.session_state.get("sidebar_collapsed"))


if st.session_state.get("sidebar_collapsed"):
    st.markdown("<style>[data-testid='stSidebar']{width:62px!important;min-width:62px!important;}</style>", unsafe_allow_html=True)
else:
    st.markdown("<style>[data-testid='stSidebar']{width:244px!important;min-width:244px!important;}</style>", unsafe_allow_html=True)

with st.sidebar:
    _collapsed = bool(st.session_state.get("sidebar_collapsed"))
    if _collapsed:
        st.button("›", key="side_expand", use_container_width=True,
                  on_click=_toggle_sidebar_collapse, help="展开导航")
        st.markdown("<div class='icon-rail-note'>CRM</div>", unsafe_allow_html=True)
        if st.button("＋", key="rail_new", use_container_width=True, help="新建询盘"):
            _new_analysis()
        for _ico, _view in (("⚡", "优先处理"), ("↩", "待回复"), ("💰", "待报价"),
                            ("📅", "今日跟进"), ("⏰", "已逾期"), ("◎", "全部商机")):
            st.button(_ico, key=f"rail_{_view}", use_container_width=True,
                      on_click=_set_sidebar_view, args=(_view,), help=_view)
    else:
        _top1, _top2 = st.columns([1, .28], gap="small")
        with _top1:
            st.markdown("#### CRM")
        with _top2:
            st.button("‹", key="side_collapse", use_container_width=True,
                      on_click=_toggle_sidebar_collapse, help="收起侧栏")
        if st.button("＋ 新建询盘", key="new_side", use_container_width=True):
            _new_analysis()

        try:
            _all_rows = load_queue(None)
        except Exception as _qe:
            st.error("询盘列表加载失败")
            st.caption(str(_qe)[:140])
            if st.button("🔄 重新加载", key="queue_reload", use_container_width=True):
                st.rerun()
            _all_rows = []

        _all_deals = _ui.group_deal_threads(_all_rows, _stage_of, _deal_of)
        # ROUND 7.2 性能：原写法在同一表达式里对每个 group 调用 _deal_opp_of
        # 三次（选商机 → 取任务 → 再取一次兜底），每次都做一遍身份键匹配。
        # 这里显式循环，一次求值、复用结果，语义完全等价。
        _all_wv = []
        for _g in _all_deals:
            _g_opp = _deal_opp_of(_g, _opp_by_inq, _tasks_by_opp)
            _g_tasks = ((_tasks_by_opp or {}).get(_g_opp.get("id"))
                        if _g_opp else None)
            _all_wv.append(_ui.deal_work_item(_g, _g_opp, _g_tasks))
        _open_wv = [w for w in _all_wv if w.get("open_count", 0) > 0]
        _counts = {
            "优先处理": sum(1 for w in _open_wv if _matches_sales_work_view(w, "优先处理")),
            "待回复": sum(1 for w in _open_wv if _matches_sales_work_view(w, "待回复")),
            "待报价": sum(1 for w in _open_wv if _matches_sales_work_view(w, "待报价")),
            "今日跟进": sum(1 for w in _open_wv if _matches_sales_work_view(w, "今日跟进")),
            "已逾期": sum(1 for w in _open_wv if _matches_sales_work_view(w, "已逾期")),
            "新询盘": sum(1 for w in _open_wv if _matches_sales_work_view(w, "新询盘")),
            "全部商机": len(_all_deals),
        }
        _active_view = st.session_state.get("sales_inbox_filter") or "全部商机"
        if _active_view not in _counts:
            _active_view = "全部商机"

        # 搜索是导航级能力，保持在 Smart Views 上方；高级条件仍留在折叠区。
        _q = (st.text_input("搜索", key="inbox_search",
                            label_visibility="collapsed",
                            placeholder="🔍 客户、公司、产品或商机…") or "").strip().lower()

        def _view_button(label: str, icon: str):
            _active = label == _active_view
            _mark = "• " if _active else ""
            # 侧栏只承担“进入哪个工作视图”。保留 0 数量入口以保证导航位置
            # 稳定，但不把 0 当作显眼的业务提醒。
            _count = _counts.get(label, 0)
            _count_text = f"  {_count}" if _count else ""
            st.button(f"{_mark}{icon} {label}{_count_text}",
                      key=f"sv_{label}", use_container_width=True,
                      on_click=_set_sidebar_view, args=(label,), help=f"进入{label}")

        st.markdown("<div class='crm-side-title'>工作视图</div>", unsafe_allow_html=True)
        for _label, _icon in (("优先处理", "⚡"), ("待回复", "↩"),
                              ("今日跟进", "📅"), ("已逾期", "⏰")):
            _view_button(_label, _icon)
        st.markdown("<div class='crm-side-title'>收件箱</div>", unsafe_allow_html=True)
        _view_button("新询盘", "◎")
        st.markdown("<div class='crm-side-title'>商机</div>", unsafe_allow_html=True)
        _view_button("全部商机", "◇")

        with st.expander("筛选与视图", expanded=False):
            st.caption("低频工作视图与高级筛选")
            _view_button("待报价", "💰")
            _eu = {"Germany", "France", "Italy", "Spain", "Netherlands", "Sweden", "Poland", "Belgium", "Denmark", "Finland", "Austria", "Portugal", "Ireland", "Europe"}
            _sv_counts = {
                "高价值客户": sum(1 for x in _all_rows if (x.get("aip3") or 0) >= 70 or (x.get("cust_grade") or x.get("grade")) == "A"),
                "欧洲客户": sum(1 for x in _all_rows if str(x.get("country") or "") in _eu),
                "Alibaba": 0,
                "本月询盘": sum(1 for x in _all_rows if str(x.get("created") or "").startswith(datetime.date.today().strftime("%Y-%m"))),
            }
            _sv_cols = st.columns(2)
            for _i, _sv in enumerate(("高价值客户", "欧洲客户", "Alibaba", "本月询盘")):
                with _sv_cols[_i % 2]:
                    st.button(f"{_sv} {_sv_counts[_sv]}", key=f"saved_{_sv}", use_container_width=True,
                              disabled=(_sv == "Alibaba"), on_click=_set_saved_view, args=(_sv,),
                              help="当前询盘数据没有来源字段，Alibaba 视图将在接入来源记录后启用" if _sv == "Alibaba" else "应用保存视图")
            _f_qr = st.selectbox("报价准备度", ["全部", "NOT_READY", "PARTIALLY_READY", "READY_FOR_QUOTE", "QUOTED"], key="adv_qr")
            _f_opp = st.selectbox("商机阶段", ["全部"] + [_crm.OPP_CN[s] for s in _crm.OPP_ORDER], key="adv_opp")
            _f_ctry = st.selectbox("国家", ["全部"] + sorted({str(x["country"]) for x in _all_rows if x.get("country")}), key="adv_ctry")
            _sort = st.selectbox("排序", ["AI 综合排序（Queue Score）", "今日待办优先", "待处理优先", "最新询盘", "最久未回复", "报价准备度", "高商机分"], key="queue_sort")

        _q = (st.session_state.get("inbox_search") or "").strip().lower()
        rows = list(_all_rows)
        if _q:
            def _hit(x):
                _nd = x["need"] or {}
                hay = " ".join([x["company"] or "", x["contact"] or "", x["country"] or "",
                                _nd.get("intent") or "", _nd.get("qty") or "",
                                _nd.get("product") or "", _nd.get("product_query") or "", str(x["id"])]).lower()
                return _q in hay
            rows = [x for x in rows if _hit(x)]

        _filter_map = {"优先处理": "高优先级", "待回复": "待回复", "待报价": "待报价",
                       "今日跟进": "待跟进", "已逾期": "待跟进", "新询盘": "全部",
                       "全部商机": "全部"}
        _sel = _filter_map.get(_active_view, "全部")
        st.session_state.queue_filter = _sel
        rows = _filter_rows(rows, _sel)
        if _active_view == "新询盘":
            rows = [x for x in rows if x.get("biz") in (_wf.NEW, _wf.READY_TO_REPLY)]
        elif _active_view == "已逾期":
            _deal_overdue_ids = {o.get("inquiry_id") for o in list_opportunities()
                                 if o.get("inquiry_id") and _crm_core.health_of(o)["status"] == "overdue"}
            rows = [x for x in rows if x.get("fu_state") == "已逾期" or x.get("id") in _deal_overdue_ids]

        _saved = st.session_state.get("sales_saved_view")
        if _saved == "高价值客户":
            rows = [x for x in rows if (x.get("aip3") or 0) >= 70 or (x.get("cust_grade") or x.get("grade")) == "A"]
        elif _saved == "欧洲客户":
            rows = [x for x in rows if str(x.get("country") or "") in _eu]
        elif _saved == "本月询盘":
            rows = [x for x in rows if str(x.get("created") or "").startswith(datetime.date.today().strftime("%Y-%m"))]
        if 'adv_qr' in st.session_state and st.session_state.adv_qr != "全部":
            rows = [x for x in rows if _ns((x.get("need") or {}).get("readiness") or "") == st.session_state.adv_qr]
        if 'adv_opp' in st.session_state and st.session_state.adv_opp != "全部":
            rows = [x for x in rows if _crm.OPP_CN.get(_crm.opp_stage_of_biz(x.get("biz") or "")) == st.session_state.adv_opp]
        if 'adv_ctry' in st.session_state and st.session_state.adv_ctry != "全部":
            rows = [x for x in rows if str(x.get("country") or "") == st.session_state.adv_ctry]

        def _day_rank(x):
            _a = (x.get("action") or {}).get("type") or ""
            if _a == "FOLLOW_UP_CUSTOMER": return 0
            if x["status"] == STATUS_TODO and x["pri"] == PRI_HIGH: return 1
            if x["status"] == STATUS_TODO and not (x["need"] or {}).get("blockers"): return 2
            if _a == "CREATE_FOLLOW_UP": return 3
            if x["status"] == STATUS_TODO: return 4
            if x["status"] == STATUS_DONE: return 6
            return 5
        _qs_key = lambda x: (x.get("qs3") if x.get("qs3") is not None else -x.get("pts", 0))
        _sort = st.session_state.get("queue_sort") or "AI 综合排序（Queue Score）"
        if _sort == "最新询盘":
            rows = sorted(rows, key=lambda x: -x["id"])
        elif _sort == "AI 综合排序（Queue Score）":
            rows = sorted(rows, key=lambda x: (-_qs_key(x), _day_rank(x), -x["id"]))
        elif _sort == "今日待办优先":
            rows = sorted(rows, key=lambda x: (_day_rank(x), -x["pts"], -x["id"]))
        elif _sort == "待处理优先":
            rows = sorted(rows, key=lambda x: (0 if x["status"] == STATUS_TODO else 1, -x["id"]))
        elif _sort == "最久未回复":
            rows = sorted(rows, key=lambda x: (0 if x["status"] == STATUS_TODO else 1, str(x["created"]), x["id"]))
        elif _sort == "报价准备度":
            rows = sorted(rows, key=lambda x: ({"quoted": 0, "ready_for_quotation": 1}.get((x["need"] or {}).get("readiness"), 2), -(x["score"] or 0), -x["id"]))
        elif _sort == "高商机分":
            rows = sorted(rows, key=lambda x: -(x["score"] or 0))

        _vis_ids = [x["id"] for x in rows]
        _cur_sel = st.session_state.get("selected_id")
        # 筛选只能改变当前队列的可见范围，不能改写用户已打开的 Deal。
        # 否则从“最近访问”打开一个不属于当前 Smart View 的 Deal 时，下一次
        # rerun 会立即被这里替换成队列首条，表现为点击无效或切换卡顿。
        st.session_state["queue_ids"] = _vis_ids

        # 左侧不是第二个销售队列。“最近访问”仅反映用户实际打开过的 Deal，
        # 不按优先级再次复制首页的执行列表，也不受当前 Smart View 筛选影响。
        _groups_by_inquiry_id = {}
        for _all_group in _all_deals:
            for _all_item in _all_group.get("items") or []:
                _groups_by_inquiry_id[str(_all_item.get("id"))] = _all_group
            _all_opp = _deal_opp_of(_all_group, _opp_by_inq, _tasks_by_opp)
            if _all_opp and _all_opp.get("inquiry_id"):
                _groups_by_inquiry_id[str(_all_opp["inquiry_id"])] = _all_group

        _recent_groups = []
        _recent_seen = set()
        for _recent_iid in st.session_state.get("recent_opened_inquiry_ids", []):
            _recent_group = _groups_by_inquiry_id.get(str(_recent_iid))
            if not _recent_group:
                continue
            _recent_key = repr(_recent_group["key"])
            if _recent_key not in _recent_seen:
                _recent_seen.add(_recent_key)
                _recent_groups.append(_recent_group)
            if len(_recent_groups) == 3:
                break

        st.markdown("<div class='crm-side-title'>最近访问</div>", unsafe_allow_html=True)
        _in_scroll = st.container(height=min(max(len(_recent_groups) * 96 + 12, 72), 300), border=False)
        with _in_scroll:
            if not _recent_groups:
                st.markdown("<div class='quick-access-empty'>打开商机后会显示在这里</div>",
                            unsafe_allow_html=True)
            else:
                for _g in _recent_groups:
                    _lead = _g["lead"]
                    _g_opp = _deal_opp_of(_g, _opp_by_inq, _tasks_by_opp)
                    _g_otasks = ((_tasks_by_opp or {}).get(_g_opp.get("id")) if _g_opp else None)
                    st.markdown(_deal_card_html(_g, _cur_sel, _g_opp, _g_otasks, clickable=False),
                                unsafe_allow_html=True)
                    # Streamlit 的 HTML 卡片没有可靠的原生 click handler。保留一
                    # 个可见、可键盘访问的按钮，避免透明 CSS 覆盖层在滚动容器内失效。
                    _open_iid = ((_g_opp or {}).get("inquiry_id") or _lead["id"])
                    st.button("查看 →", key=f"open_recent_{_open_iid}", use_container_width=True,
                              on_click=_home_select_inquiry, args=(_open_iid,),
                              help="打开 Deal")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["分析询盘", "跟进台", "商机", "客户档案", "产品库", "导出"])

# ---------- Tab1: 分析询盘 ----------
with tab1:
    # —— Phase 4：Sales Execution Queue（spec 一~六）——
    # AI Priority → Next Best Action → Execute → Activity → Update State
    # → Recalculate → Rebuild Queue → Next Customer
    _aq_ids = st.session_state.get("actq_ids") or []
    _aq_err = st.session_state.get("exec_error")
    _aq_busy = bool(st.session_state.get("exec_busy"))

    if _aq_err:
        # spec 六：操作失败 → 不跳到下一条，当前记录未完成，提供 [重试]
        with st.container(border=True):
            st.markdown("⚠️ **操作失败，当前记录未完成。**")
            st.caption(f"记录 #{_aq_err.get('iid')} · "
                       f"{_ex.ROUTE_CN.get(_aq_err.get('route') or '', '执行')}"
                       f"　{_aq_err.get('msg') or ''}")
            _ec1, _ec2 = st.columns(2)
            with _ec1:
                st.button("🔄 重试", key="exec_retry", type="primary",
                          use_container_width=True, on_click=_exec_retry,
                          disabled=_aq_busy)
            with _ec2:
                st.button("✖ 退出队列", key="exec_err_exit",
                          use_container_width=True, on_click=_actq_close)
    elif st.session_state.get("actq_done_all"):
        # 队列处理完毕（最后一条已完成）
        st.success("🎉 执行队列处理完毕 —— 当前筛选下没有更多待处理任务。")
        st.button("返回工作台", key="exec_done_back", use_container_width=True,
                  on_click=lambda: st.session_state.pop("actq_done_all", None))
    elif (st.session_state.get("actq_entered") and not _aq_ids):
        # spec 六：队列为空
        st.info("当前筛选条件下没有待处理任务。")
        st.button("返回工作台", key="exec_empty_back", use_container_width=True,
                  on_click=_actq_close)
    elif _aq_ids:
        _aq_i = min(st.session_state.get("actq_idx", 0), len(_aq_ids) - 1)
        _aq_cur = load_queue(None)
        _aq_map = {x["id"]: x for x in _aq_cur}
        _aq_it = _aq_map.get(_aq_ids[_aq_i]) if _aq_ids else None
        if _aq_it is None:
            _actq_close()
        else:
            import html as _vh_aq
            _aq_need = _aq_it.get("need") or {}
            _aq_act = _aq_it.get("action") or {}
            # Phase 3 全量重算（Current Customer 卡：AIP + NBA + 推荐理由）
            _aq_item = dict(id=_aq_it["id"], created=_aq_it["created"],
                            status=_aq_it["status"], biz=_aq_it["biz"],
                            need=_aq_need, wf=_aq_it.get("wf") or {})
            try:
                _aq_p3 = _p3.analyze(_aq_item, _aq_act)
            except Exception:
                _aq_p3 = None
            _nba = (_aq_p3 or {}).get("nba") or {}
            _aip3 = (_aq_p3 or {}).get("aip")
            _route = _ex.route_of(_nba.get("action_type") or _aq_act.get("type"))
            _opp_stg = _crm.opp_stage_of_biz(_aq_it["biz"])
            _opp_cn = _crm.OPP_CN.get(_opp_stg, _opp_stg)
            _biz_cn = _wf.BIZ_CN.get(_aq_it["biz"], _aq_it["biz"])
            # AI Recommendation：NBA 理由 + AIP 前两条加减原因（同一优先级引擎口径）
            _rec_bits = []
            if _nba.get("reason"):
                _rec_bits.append(_nba["reason"])
            for _r in sorted(((_aq_p3 or {}).get("rows") or []),
                             key=lambda x: -(x["score"]))[:2]:
                if _r["state"] != "nodata" and _r.get("reason"):
                    _rec_bits.append(_r["reason"])
            with st.container(border=True):
                _ar1 = st.columns([3, 1, 1])
                with _ar1[0]:
                    st.markdown(
                        f"**🎯 Sales Execution Queue　{_aq_i + 1} / {len(_aq_ids)}**")
                with _ar1[1]:
                    st.button("← 上一个", key="aq_prev", use_container_width=True,
                              disabled=(_aq_i <= 0) or _aq_busy,
                              on_click=_actq_nav, args=(-1,))
                with _ar1[2]:
                    st.button("下一个 →", key="aq_next", use_container_width=True,
                              disabled=(_aq_i >= len(_aq_ids) - 1) or _aq_busy,
                              on_click=_actq_nav, args=(1,))
                # —— 当前客户（spec 二：Customer/Contact/Inquiry/Opportunity/
                #     AI Priority/Stage/Next Action/AI Recommendation）——
                _aq_reason = " · ".join(
                    b for b in [_aq_need.get("intent") or "", _aq_need.get("qty") or "",
                                ("缺：" + "、".join(_aq_need.get("blockers")[:2]))
                                if _aq_need.get("blockers") else ""] if b)
                st.markdown(
                    f"<div style='font-size:1.02rem;font-weight:700;padding:.1rem 0'>"
                    f"{_vh_aq.escape(str(_aq_it['company'] or _aq_it['contact'] or '未知客户'))}"
                    f"<span style='font-size:.72rem;color:var(--text-disabled)'>　#{_aq_it['id']} · 询盘</span></div>"
                    f"<div style='font-size:.8rem;color:var(--text-secondary)'>"
                    f"{_vh_aq.escape(str(_aq_it['contact'] or '—'))}"
                    + (f" · {_ui.flag(_aq_it.get('country'))}"
                       f"{_vh_aq.escape(str(_aq_it.get('country') or ''))}"
                       if _aq_it.get("country") else "")
                    + "　|　Customer："
                    f"{_vh_aq.escape(str(_aq_it.get('cust_grade') or _aq_it.get('grade') or '—'))} 级"
                    + (f"　·　{_vh_aq.escape(_aq_reason)}" if _aq_reason else "")
                    + "</div>"
                    + ("<div class='ws-cells' style='margin:.45rem 0 0'>"
                       f"<div class='ws-cell'><div class='lb'>优先级</div>"
                       f"<div class='v'>{_vh_aq.escape(str(_aq_it.get('g_letter') or _aq_it.get('grade') or '—'))}</div>"
                       f"<div class='s'>等级</div></div>"
                       f"<div class='ws-cell'><div class='lb'>Opportunity 商机</div>"
                       f"<div class='v'>{_vh_aq.escape(_opp_cn)}</div>"
                       f"<div class='s'>客户级商机阶段</div></div>"
                       f"<div class='ws-cell'><div class='lb'>Stage 阶段</div>"
                       f"<div class='v'>{_vh_aq.escape(_biz_cn)}</div>"
                       f"<div class='s'>询盘业务状态</div></div>"
                       f"<div class='ws-cell'><div class='lb'>Next Action</div>"
                       f"<div class='v'>{_vh_aq.escape(str(_aq_act.get('label') or '—'))}</div>"
                       f"<div class='s'>{_vh_aq.escape(str((_nba.get('action_type_cn') or _aq_act.get('type') or '—')))}"
                       f"{(' · ' + _nba['priority']) if _nba.get('priority') else ''}</div></div>"
                       "</div>")
                    + (f"<div class='nextstep'><b>AI Recommendation</b>　"
                       f"{_vh_aq.escape('；'.join(_rec_bits[:3])) if _rec_bits else _vh_aq.escape(str(_aq_act.get('label') or '—'))}</div>"
                       if True else ""),
                    unsafe_allow_html=True)
                # —— 执行路由（spec 三）：按 Action Type 打开对应工作区 ——
                _ROUTE_BTN = {
                    "reply": ("✉️ 生成客户邮件", f"ui_mail_{_aq_it['id']}"),
                    "collect_info": ("✉️ 生成客户邮件", f"ui_mail_{_aq_it['id']}"),
                    "create_quote": ("💰 打开报价", f"ui_quote_{_aq_it['id']}"),
                    "follow_up": ("✉️ 生成客户邮件", f"ui_mail_{_aq_it['id']}"),
                    "schedule_follow_up": ("⏰ 设置跟进时间", f"ui_fuzone_{_aq_it['id']}"),
                    "match_product": ("🔎 产品匹配", None),      # 引导产品库
                    "negotiate": (None, None),                    # 内联表单
                    "mark_complete": (None, None),                # 直接标记完成
                }
                _rq_lbl, _rq_sec = _ROUTE_BTN.get(_route, ("✉️ 生成客户邮件",
                                                           f"ui_mail_{_aq_it['id']}"))
                _aq_c = st.columns([1.5, 1.0, 1.0, 1.0, 1.2])
                with _aq_c[0]:
                    if _rq_lbl:
                        st.button(_rq_lbl, key=f"aq_route_{_aq_it['id']}",
                                  type="primary", use_container_width=True,
                                  disabled=_aq_busy,
                                  on_click=_actq_open,
                                  args=(_aq_it["id"], _rq_sec))
                    elif _route == "negotiate":
                        st.button("🤝 记录谈判", key=f"aq_route_{_aq_it['id']}",
                                  type="primary", use_container_width=True,
                                  disabled=_aq_busy,
                                  on_click=_actq_open, args=(_aq_it["id"],))
                    else:                       # mark_complete
                        st.button("✅ 标记完成", key=f"aq_route_{_aq_it['id']}",
                                  type="primary", use_container_width=True,
                                  disabled=_aq_busy,
                                  on_click=_actq_done, args=(_aq_it["id"],))
                with _aq_c[1]:
                    # spec 五：完成 → 保存 Activity → 更新状态 → 重算 → 下一条
                    st.button(_ex.completion_label(_route),
                              key=f"exec_primary_{_aq_it['id']}",
                              use_container_width=True, disabled=_aq_busy,
                              on_click=_exec_advance,
                              args=(_aq_it["id"], _route))
                with _aq_c[2]:
                    st.button("⏭ 稍后处理", key=f"aq_skip_{_aq_it['id']}",
                              use_container_width=True, disabled=_aq_busy,
                              on_click=_actq_skip)
                with _aq_c[3]:
                    st.button("👤 查看客户", key=f"aq_view_{_aq_it['id']}",
                              use_container_width=True, disabled=_aq_busy,
                              on_click=_actq_open, args=(_aq_it["id"],))
                with _aq_c[4]:
                    st.button("✖ 退出队列", key="aq_close",
                              use_container_width=True, on_click=_actq_close)
                if _route == "negotiate":
                    # 谈判记录内联表单（保存后可推进到「谈判」阶段）
                    with st.expander("🤝 谈判记录", expanded=True):
                        st.text_area("谈判内容（条款 / 价格 / 交期 / 下一步）",
                                     key=f"exec_neg_{_aq_it['id']}", height=90)
                        st.button("💾 保存谈判记录", key=f"exec_neg_save_{_aq_it['id']}",
                                  disabled=_aq_busy,
                                  on_click=_exec_save_negotiation,
                                  args=(_aq_it["id"],))
                        _ngm = st.session_state.get(f"exec_neg_msg_{_aq_it['id']}")
                        if _ngm:
                            st.caption(_ngm)
                if _route == "match_product":
                    st.caption("🔎 产品匹配：请到下方「📦 产品库」标签页选择产品，"
                               "再回到详情页确认匹配（匹配结果将影响 AI Priority 与报价准备度）")
    # —— 队列导航已并入详情区顶部 NBA Hero（Action-First，spec 四/十一）：
    #    上一个 / 下一个 / N-M / 完成并进入下一条 与主 CTA 同一视觉行 ——
    # Phase 1：运行模式从首页显著区收进「分析设置」折叠区（功能保留，仅改 UI 位置；
    # 折叠时沿用上次选择或默认 auto——业务逻辑不依赖页面常显）
    _run_mode = st.session_state.get("run_mode", "auto")
    with st.expander("⚙️ 分析设置（运行模式）", expanded=False):
        _mode_idx = {"auto": 0, "llm": 1, "rule": 2}.get(_run_mode, 0)
        mode_label = st.radio(
            "运行模式", ["● 智能模式（推荐）", "○ AI深度分析", "○ 快速规则分析"],
            index=_mode_idx, horizontal=True,
            help="智能模式=有Key用AI否则自动转规则；AI深度分析=强制AI调用；快速规则分析=离线纯本地")
        MODE_MAP = {"● 智能模式（推荐）": "auto", "○ AI深度分析": "llm", "○ 快速规则分析": "rule"}
        _run_mode = MODE_MAP[mode_label]
    st.session_state["run_mode"] = _run_mode
    mode = _run_mode
    # 输入 + 分析（工作台核心动作）。
    # 第十一轮（spec 十）：粘贴框收进「＋ 新建询盘分析」折叠区——默认收起，
    # 不再让巨大输入框占据主工作区、把产品变成"AI 文本分析器"；
    # 顶栏按钮会置 new_inq_open=True 自动展开，分析后自动进入详情。
    _new_open = st.session_state.get("new_inq_open", False)
    with st.expander("＋ 新建询盘分析（点击展开 → 粘贴询盘 → AI 分析 → 进入工作队列）",
                     expanded=_new_open):
        col_h1, col_h2 = st.columns([6, 1])
        with col_h1:
            st.markdown("**① 粘贴客户询盘原文**　"
                        "<span style='font-size:.76rem;color:var(--text-muted)'>邮件 / WhatsApp / "
                        "平台消息，整段粘贴均可</span>", unsafe_allow_html=True)
        with col_h2:
            if st.button("🧹 清空", use_container_width=True):
                st.session_state.inquiry_text = ""
                st.rerun()
        text = st.text_area("询盘原文", height=170, key="inquiry_text",
                            label_visibility="collapsed",
                            placeholder="Dear Sir / Madam, We are looking for swimming caps …（整段粘贴，无需整理）")
        if st.button("🚀 开始分析：提取客户 → 匹配产品 → 分级评分 → 生成回复",
                     type="primary", use_container_width=True):
            if not text.strip():
                st.warning("请先粘贴询盘内容")
            else:
                try:
                    extractor, matcher, client = get_engine(mode)
                    import main
                    with st.spinner("正在分析询盘：提取客户需求 · 判断商机 · 匹配产品 · 生成回复草稿…"):
                        report = main.analyze(text, extractor, matcher, client)
                    new_id = save_inquiry(text, report)
                    # 第十轮：ANALYZED / REPLY_GENERATED 已在 db.save_inquiry
                    # 入库点统一记录（旧记录派生兜底不变），此处不再重复写
                    st.session_state.selected_id = new_id
                    st.session_state.analyzed = report
                    st.session_state.analyzed_text = text
                    st.session_state.new_inq_open = False
                    st.rerun()
                except Exception as e:
                    st.error(f"AI 分析失败：{e}")
                    st.caption("可切换到「快速规则分析」模式离线重试，或检查 API Key / 网络后重新分析。")

    if st.session_state.pop("pipeline_opened_id", None):
        st.markdown("<div class='ok-tip'>✅ 已从 Pipeline 打开该 Deal 的共享 Deal Detail</div>",
                    unsafe_allow_html=True)
    if st.session_state.get("analyzed"):
        st.markdown("<div class='ok-tip'>✅ 已存入历史记录 · 客户档案已自动归并</div>",
                    unsafe_allow_html=True)
        render_report(st.session_state.analyzed, st.session_state.analyzed_text,
                      st.session_state.selected_id)
    elif st.session_state.get("selected_id"):
        src, rep = get_inquiry(st.session_state.selected_id)
        if rep:
            col_a, col_b = st.columns([5, 1])
            with col_b:
                if st.button("🗑 删除", use_container_width=True):
                    delete_inquiry(st.session_state.selected_id)
                    st.session_state.selected_id = None
                    st.rerun()
            render_report(rep, src, st.session_state.selected_id)
    else:
        st.caption("ℹ️ 还没有打开的询盘 —— 点击左侧「＋ 新建询盘」开始分析，或在左侧最近访问中打开一条记录。")

# ---------- Tab2: 今日行动（状态 + 优先级排队） ----------
with tab2:
    _render_followup_workspace()
    with st.expander("ℹ️ 排序与优先级口径", expanded=False):
        st.markdown(
            "**队列排序**：逾期 > 今日到期 > 等待客户 > 稍后提醒 > 未来排程；"
            "同级内参考 Deal Priority（P1/P2/P3）→ 到期时间 → 录入顺序，可解释、不随机。\n\n"
            "**三轴独立**：InquiryStatus（询盘沟通）≠ DealStage（商机阶段）≠ "
            "FollowUpStatus（跟进任务）。完成一次跟进 ≠ 完成商机，也不会自动 Won/Lost。\n\n"
            "**逾期判定**只看 followUpDueAt，与客户/询盘创建时间无关。\n\n"
            "**客户优先级分** = 客户等级 + 线索等级×0.6 + 评分×0.1 + 紧急度加分"
            "（≥65 优先 / 35~64 正常 / <35 可延后）——评分归评分，跟进的"
            "紧迫度另按「逾期 + 客户优先级 + 原因」综合。"
        )# ---------- Tab3: 独立商机 CRM ----------
with tab3:
    # ROUND 6.9 §1：Pipeline 只做概览；卡片点击进共享 Deal Detail。
    # 旧版「裸字符串兜底 UI」已删除（DELETE > HIDE，见本轮报告 §1）。
    _pipeline_ui.render_pipeline_page(open_deal=_open_deal_from_pipeline)
# ---------- Tab4: 客户档案 ----------
with tab4:
    # ROUND 6.9 §3：客户档案 = CRM 记录列表，不是等级看板。
    #   默认行只回答：公司 / 主要联系人 / 活跃商机 / 最近联系 / 下一次活动。
    #   A/B/C/D 等级降为行尾一个次要 chip（仍可在详情里手动归档），
    #   不再决定页面结构，也不再占据首屏 4 张 KPI 卡。
    st.subheader("客户档案（按邮箱/公司自动归并去重）")
    st.caption("一行一个客户记录：公司 · 主要联系人 · 活跃商机 · 最近联系 · 下一次活动。"
               "客户等级只是次要属性，不影响页面结构。")
    _cq = (st.text_input("搜索", key="cust_search", label_visibility="collapsed",
                         placeholder="🔍 公司 / 国家 / 邮箱 / 联系人") or "").strip().lower()
    _allc = list_customers()
    _opps_all = list_opportunities()
    _opps_of_cust, _cust_of_opp = {}, {}
    for _o in _opps_all:
        _opps_of_cust.setdefault(_o.get("customer_id"), []).append(_o)
        _cust_of_opp[_o.get("id")] = _o.get("customer_id")
    # ROUND 7.2 性能：_fu_of_cust 只依赖 opportunities（客户 id ↔ 商机 id）与跟进任务，
    # 与搜索词 _cq 完全无关，却能支撑整页渲染，因此在同一进程内是可缓存的常量。
    # 原写法每次 rerun 都全表拉一次跟进任务（cProfile: list_followup_tasks 单轮 16 次调用）。
    # 任何写路径（新建/回复/任务/阶段）都会 bump 版本号，缓存随之失效，不会读到陈旧数据。
    _fu_key = ("fu_of_cust", _queue_version())
    _fu_of_cust = _FU_OF_CUST_CACHE.get(_fu_key)
    if _fu_of_cust is None:
        _fu_of_cust = {}
        for _t in (_db_mod.list_followup_tasks() or []):
            _cid_of_task = _cust_of_opp.get(_t.get("opportunity_id"))
            if _cid_of_task is not None:
                _fu_of_cust.setdefault(_cid_of_task, []).append(_t)
        _FU_OF_CUST_CACHE.clear()
        _FU_OF_CUST_CACHE[_fu_key] = _fu_of_cust
    _pairs = [(_row, _crm_core.customer_record(_row, _opps_of_cust.get(_row[0]),
                                               _fu_of_cust.get(_row[0])))
              for _row in _allc]
    if _cq:
        _pairs = [(_r, _rec) for _r, _rec in _pairs
                  if _cq in " ".join(str(_rec.get(_k) or "")
                                     for _k in ("company", "country", "email", "contact")).lower()]
    if not _pairs:
        st.caption("还没有客户。分析询盘后，系统会自动把同一个人/公司归并到一条档案。")
    else:
        st.caption(f"共 {len(_pairs)} 个客户记录")
        _hcols = st.columns([2.5, 1.7, .85, 1.25, 1.85, .75], gap="small")
        for _hc, _hl in zip(_hcols, ["公司", "主要联系人", "活跃商机",
                                     "最近联系", "下一次活动", "等级"]):
            _hc.markdown(f"<div style='font-size:.68rem;opacity:.62'>{_hl}</div>",
                         unsafe_allow_html=True)
        for _row, _rec in _pairs:
            _r12 = (list(_row) + [None] * 12)[:12]
            cid, company, country, email, contact, cnt, last_grade, score, seen, \
                grade, note, contacts_json = _r12
            _cols = st.columns([2.5, 1.7, .85, 1.25, 1.85, .75], gap="small")
            with _cols[0]:
                st.markdown(
                    f"<div style='font-weight:700;font-size:.86rem'>"
                    f"{_ui_flag_esc(_rec['company'])}</div>"
                    f"<div style='font-size:.68rem;opacity:.65'>"
                    f"{_flag(_rec['country'])}{_ui_flag_esc(_rec['country'])}"
                    f"　{_ui_flag_esc(_rec['email'])}</div>", unsafe_allow_html=True)
            with _cols[1]:
                _extra = (f"<div style='font-size:.66rem;opacity:.6'>"
                          f"共 {_rec['contact_count']} 位联系人</div>"
                          if _rec["contact_count"] > 1 else "")
                st.markdown(f"<div style='font-size:.8rem'>"
                            f"{_ui_flag_esc(_rec['contact'])}</div>{_extra}",
                            unsafe_allow_html=True)
            with _cols[2]:
                st.markdown(
                    f"<div style='font-weight:800;font-size:.95rem;color:"
                    f"{'var(--brand-500)' if _rec['active_deals'] else 'var(--text-disabled)'}'>"
                    f"{_rec['active_deals']}</div>", unsafe_allow_html=True)
            with _cols[3]:
                st.markdown(f"<div style='font-size:.76rem'>"
                            f"{str(_rec['last_contact'] or '—')[:10]}</div>",
                            unsafe_allow_html=True)
            with _cols[4]:
                _na = _rec.get("next_activity")
                if _na:
                    st.markdown(
                        f"<div style='font-size:.72rem;font-weight:700'>"
                        f"{_ui_flag_esc(str(_na['due_at'])[:10] or '未设时间')}</div>"
                        f"<div style='font-size:.66rem;opacity:.72'>"
                        f"{_ui_flag_esc(_na['action'])[:28]}</div>", unsafe_allow_html=True)
                else:
                    st.markdown("<div style='font-size:.72rem;opacity:.5'>未安排</div>",
                                unsafe_allow_html=True)
            with _cols[5]:
                st.markdown(f"<div style='font-size:.76rem;font-weight:750'>"
                            f"{GRADE_COLOR.get(_rec['grade'], '')}"
                            f"{_ui_flag_esc(_rec['grade'])}</div>", unsafe_allow_html=True)
            with st.expander(f"客户详情 · {_rec['company']}"):
                # —— 第十轮（spec 五）：客户视角 = 客户概览 + 当前商机 + 联系人
                #     + 历史记录，一屏看懂"这个客户现在怎么样了" ——
                import json as _cj
                try:
                    _contacts = _cj.loads(contacts_json or "[]")
                    if not isinstance(_contacts, list):
                        _contacts = []
                except Exception:
                    _contacts = []

                st.markdown("**🧭 客户概览**")
                st.write(f"**国家/地区**：{country or '—'}　**邮箱**：{email or '—'}")
                _cl = "、".join(str(c.get("name", "")).strip()
                                for c in _contacts if str(c.get("name", "")).strip())
                st.write(f"**联系人（{len(_contacts)}）**：{_cl or (contact or '—')}")
                st.write(f"**客户等级（次要属性）**："
                         f"{GRADE_COLOR.get(_rec['grade'], '')} {_rec['grade']}"
                         f"　·　最新线索评级 {GRADE_COLOR.get(last_grade, '')}{last_grade} {score}分")

                # 当前商机：从该客户全部询盘派生（复用 crm 纯函数，不写业务数据）
                _c_items = [x for x in (load_queue(None) if cid else [])
                            if x.get("cust_id") == cid]
                if _c_items:
                    _c_opp = _crm.opportunity_of(_c_items)
                    _c_val = (f"　金额≈{_c_opp['currency_hint']} "
                              f"{_c_opp['value']:,.0f}" if _c_opp.get("value") else "")
                    _c_next = ((_c_opp.get("next_action") or {}).get("label") or "")
                    st.markdown(
                        f"<div class='sum-hero'><div class='lbl'>💼 当前商机</div>"
                        f"<div class='txt'>{_c_opp['emoji']} <b>{_c_opp['stage_cn']}</b>"
                        f"　·　{_c_opp['inquiry_count']} 条询盘（{_c_opp['todo_count']} 待回复）"
                        f"{_c_val}"
                        + (f"　·　下一步：<b>{_c_next}</b>" if _c_next else "")
                        + "</div></div>",
                        unsafe_allow_html=True)

                # 手动定级（分类归档）—— 次要属性，收在详情里
                gidx = ["A", "B", "C", "D"].index(_rec["grade"]) \
                    if _rec["grade"] in ("A", "B", "C", "D") else 0
                ng = st.selectbox("客户等级归档", ["A", "B", "C", "D"],
                                  index=gidx, key=f"grade_{cid}")
                if st.button("💾 保存等级", key=f"savegrade_{cid}"):
                    update_customer_grade(cid, ng)
                    st.rerun()

                # 客户备注
                cnote = st.text_area("客户备注", value=note or "", height=70, key=f"cnote_{cid}")
                if st.button("💾 保存客户备注", key=f"savecnote_{cid}"):
                    update_customer_note(cid, cnote)
                    st.toast("客户备注已保存")

                inqs = customer_inquiries(cid)
                if inqs:
                    st.divider()
                    st.write("**名下询盘：**")
                    for iid, icreated, igrade, iscore, icountry, icompany, icontact in inqs:
                        c1, c2 = st.columns([5, 1])
                        with c1:
                            st.caption(f"#{iid} · {icreated} · {GRADE_COLOR.get(igrade, '')}{igrade} {iscore}分")
                        with c2:
                            if st.button("查看询盘", key=f"custview_{iid}", use_container_width=True):
                                st.session_state.selected_id = iid
                                st.session_state.analyzed = None
                                st.rerun()
                    # 最近沟通（spec 九）：该客户全部询盘的 Activity，取最近 5 条。
                    # 第二十四轮：当描述只是动作中文名的复述（如「生成客户回复 ·
                    # 生成客户回复草稿」）时不再重复显示，只保留描述里的新增信息。
                    _acts = []
                    for iid, *_r in inqs:
                        for _t, _ts, _desc, _actor, _res in list_activity(iid):
                            _acts.append((_ts, iid, _at_cn(_t), _desc))
                    if _acts:
                        _acts.sort(reverse=True)
                        st.write("**最近沟通：**")
                        for _ts, _iid, _tcn, _desc in _acts[:5]:
                            _d = str(_desc or "").strip().replace(" ", "")
                            _l = str(_tcn or "").strip().replace(" ", "")
                            _dup = (not _d or _d == _l
                                    or _d.startswith(_l) or _l.startswith(_d))
                            st.caption(f"{_ts} · #{_iid} · {_tcn}"
                                       + ("" if _dup else f" · {_desc}"))
                if st.button("🗑 删除该客户", key=f"delcust_{cid}"):
                    delete_customer(cid)
                    st.rerun()

# ---------- Tab5: 产品库 ----------
with tab5:
    st.subheader("产品库管理（改动后分析会立即生效）")
    products = catalog.load_products()

    # 列表
    low_total = 0
    for p in products:
        stock = p.get("stock", 0)
        safety = p.get("safety_stock", 0)
        low = safety > 0 and stock <= safety
        if low:
            low_total += 1
        cols = st.columns([3, 3, 1, 1, 1, 1])
        cols[0].write(f"**{p['name_cn']}** ({p['name']})")
        cols[1].write(f"MOQ {p['moq']:,}　${p['price_range'][0]:.2f}-{p['price_range'][1]:.2f}")
        if low:
            cols[2].error(f"库存 {stock} ⚠️低于安全线 {safety}")
        else:
            cols[2].write(f"库存 {stock}")
        cols[3].write(p.get("lead_time", ""))
        cols[4].button("✏️ 编辑", key=f"edit_{p['id']}",
                       on_click=lambda pid=p['id']: _set_edit(pid))
        cols[5].button("🗑 删除", key=f"del_{p['id']}",
                       on_click=lambda pid=p['id']: _del_product(pid))
    if low_total:
        st.warning(f"⚠️ 有 {low_total} 个产品库存低于安全线，请及时补货")
    st.divider()

    # 新增 / 编辑 表单
    editing = st.session_state.get("edit_pid")
    is_edit = editing is not None
    title_txt = f"{'编辑' if is_edit else '新增'}产品"
    st.subheader(title_txt)
    defaults = {}
    if is_edit:
        prod = next((x for x in products if x["id"] == editing), None)
        if prod:
            defaults = {
                "name": prod["name"], "name_cn": prod["name_cn"],
                "keywords": ", ".join(prod.get("keywords", [])),
                "moq": prod["moq"], "price_min": prod["price_range"][0],
                "price_max": prod["price_range"][1], "unit": prod.get("unit", ""),
                "lead_time": prod.get("lead_time", ""), "hs_code": prod.get("hs_code", ""),
                "stock": prod.get("stock", 0), "safety_stock": prod.get("safety_stock", 0),
            }
    else:
        defaults = {"name": "", "name_cn": "", "keywords": "", "moq": 1000,
                    "price_min": 0.0, "price_max": 0.0, "unit": "USD/pc (FOB Ningbo)",
                    "lead_time": "25-30 days", "hs_code": "",
                    "stock": 0, "safety_stock": 0}

    with st.form("product_form", clear_on_submit=not is_edit):
        c1, c2 = st.columns(2)
        name = c1.text_input("英文名称", value=defaults["name"])
        name_cn = c2.text_input("中文名称", value=defaults["name_cn"])
        keywords = st.text_input("关键词（逗号分隔）", value=defaults["keywords"],
                                 help="用于询盘匹配，尽量覆盖客户可能的写法")
        c3, c4 = st.columns(2)
        moq = c3.number_input("起订量 MOQ", min_value=1, value=int(defaults["moq"]))
        c5, c6 = st.columns(2)
        price_min = c5.number_input("价格下限", min_value=0.0, value=float(defaults["price_min"]), step=0.01)
        price_max = c6.number_input("价格上限", min_value=0.0, value=float(defaults["price_max"]), step=0.01)
        unit = st.text_input("计价单位", value=defaults["unit"])
        c7, c8 = st.columns(2)
        lead_time = c7.text_input("交期", value=defaults["lead_time"])
        hs_code = c8.text_input("HS 编码", value=defaults["hs_code"])
        c9, c10 = st.columns(2)
        stock = c9.number_input("当前库存", min_value=0, value=int(defaults["stock"]))
        safety_stock = c10.number_input("安全库存(预警线)", min_value=0, value=int(defaults["safety_stock"]))
        submitted = st.form_submit_button("💾 保存")
        if submitted:
            fields = {
                "name": name, "name_cn": name_cn,
                "keywords": [k for k in keywords.split(",")],
                "moq": moq, "price_min": price_min, "price_max": price_max,
                "unit": unit, "lead_time": lead_time, "hs_code": hs_code,
                "stock": stock, "safety_stock": safety_stock,
            }
            if is_edit:
                catalog.update_product(editing, fields)
            else:
                catalog.add_product(fields)
            get_engine.clear()  # 让分析引擎重新加载产品库
            st.session_state.edit_pid = None
            st.success("已保存，分析引擎已刷新产品库")
            st.rerun()

    if is_edit and st.button("取消编辑"):
        st.session_state.edit_pid = None
        st.rerun()

# ---------- Tab6: 导出 ----------
with tab6:
    st.subheader("导出 Excel")
    st.write("把工作台里的数据一键导出成 .xlsx，方便留档或发给客户。")

    inq_rows = dump_inquiries()
    if inq_rows:
        inq_cols = ["id", "created_at", "处理状态", "客户等级", "线索等级", "分数", "国家",
                    "公司", "网址", "邮箱", "联系人", "产品名称", "咨询数量",
                    "采购意向", "紧急度", "备注", "draft"]
        data = _build_xlsx(inq_rows, inq_cols, "询盘记录")
        st.download_button("导出询盘记录（含产品/国家/数量/意向/备注）",
                           data, file_name="询盘记录.xlsx")

    cust_rows = []
    for r in list_customers():
        # 第十轮：list_customers 末位新增 contacts 列，导出用前 11 列不受影响
        cid, company, country, email, contact, cnt, last_grade, score, seen, \
            grade, note = r[:11]
        cust_rows.append({"id": cid, "公司": company, "国家": country,
                          "邮箱": email, "联系人": contact,
                          "客户等级": grade or last_grade or "",
                          "询盘数": cnt, "备注": note or "", "最近互动": seen})
    if cust_rows:
        data = _build_xlsx(cust_rows,
                           ["id", "公司", "国家", "邮箱", "联系人", "客户等级",
                            "询盘数", "备注", "最近互动"],
                           "客户档案")
        st.download_button("导出客户档案（按等级归档）", data, file_name="客户档案.xlsx")

    prod_rows = []
    for p in catalog.load_products():
        prod_rows.append({
            "id": p["id"], "name": p["name"], "name_cn": p["name_cn"],
            "keywords": ", ".join(p.get("keywords", [])), "moq": p["moq"],
            "price_min": p["price_range"][0], "price_max": p["price_range"][1],
            "unit": p.get("unit", ""), "lead_time": p.get("lead_time", ""),
            "hs_code": p.get("hs_code", ""),
            "库存": p.get("stock", 0), "安全库存": p.get("safety_stock", 0),
        })
    if prod_rows:
        data = _build_xlsx(prod_rows,
                           ["id", "name", "name_cn", "keywords", "moq",
                            "price_min", "price_max", "unit", "lead_time", "hs_code",
                            "库存", "安全库存"],
                           "产品库")
        st.download_button("导出产品库（含库存）", data, file_name="产品库.xlsx")

    if not (inq_rows or cust_rows or prod_rows):
        st.caption("暂无数据可导出。")
