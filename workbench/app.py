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
        and hasattr(_db_mod, "R10_CONTACTS")):
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
)
import catalog
import gapcheck
import queue_ui as _ui
# 自愈式 reload：Streamlit 只热重载 app.py，旧进程里缓存的 queue_ui 模块可能
# 缺少第六轮新增函数（group_customers / wait / customer_key），页面会报
# AttributeError。发现缺函数就从磁盘重新加载一次，旧进程无需重启。
if not hasattr(_ui, "group_customers"):
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


def load_queue(status=None):
    """取出询盘并按优先级排好队，返回字典列表。"""
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
    return items

init_db()


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


def _do_set_followup(iid):
    at = _fu_time_from_choice(iid)
    if at:
        set_follow_up(iid, at)
        st.session_state[f"fu_msg_{iid}"] = f"跟进已设：{at}"
    else:
        st.session_state[f"fu_msg_{iid}"] = "自定义时间格式应为 2026-09-08 10:00"


def _do_complete_followup(iid):
    complete_follow_up(iid)


def _do_mark_deal(iid, result):
    """成交 / 丢单（UI 已做人工二次确认，这里只落库）"""
    set_deal(iid, result)


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
            "WON": "成交 🏆", "LOST": "丢单"}.get(t, t)


def _set_filter(v):
    """切换侧边栏询盘队列的筛选（纯 UI 状态，供「AI 今日建议」按钮复用）"""
    st.session_state.queue_filter = v


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
    # （创建报价 → QUOTE_CREATED + 阶段推进；产品匹配 → 引导产品库），
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
_NBA_CTA = {
    "reply": ("✉️ 查看并发送回复", "ui_draft_{id}"),
    "collect_info": ("📝 补充关键信息", "ui_draft_{id}"),
    "create_quote": ("💰 创建报价", "ui_quote_{id}"),
    "follow_up": ("📞 执行跟进", "ui_fuzone_{id}"),
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
/* —— Level 1 · AI 核心商机结论：大字号 + 高字重 + 限宽，不用渐变/科技感 —— */
.verdict-hero { margin: .55rem 0 .2rem; max-width: 64ch; }
.verdict-hero .lbl { font-size: .68rem; font-weight: 800; letter-spacing: .12em;
                     color: var(--primary-color,var(--brand-500)); opacity: .92;
                     text-transform: uppercase; margin-bottom: .12rem; }
.verdict-hero .txt { font-size: 1.24rem; font-weight: 700; line-height: 1.62;
                     letter-spacing: -.005em; color: inherit; }
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
/* 客户聚合：折叠组头 + 展开后的子卡缩进（同一客户多条询盘时才出现） */
.sq.ghead { border-style: dashed; opacity: .96; }
.sq.child { margin-left: 1rem; }
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
/* 侧栏宽度：桌面端 292px（spec 十七：260–300px），窄屏自适应 */
@media (min-width: 901px) {
  [data-testid="stSidebar"] { width: 292px; min-width: 292px; }
}
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
    return {"today": today_n, "yest": yest_n, "todo": todo_n,
            "done": done_n, "high": hi_n, "miss": miss_n,
            "ready": ready_n, "stale": stale_n, "fu_due": fu_due_n,
            "to_reply": to_reply_n, "to_quote": to_quote_n}


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
    _rq_txt = f"{r_cn}" + (f" · {r_score} 分" if r_score is not None else "")

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
        "<div class='ttl'>🎯 AI Next Best Action</div>"
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
            _lbl, _sec = _NBA_CTA.get(_ws_route, ("✉️ 查看并发送回复",
                                                  "ui_draft_{id}"))
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
            st.button("✉️ 生成回复", key=f"ws_gen_{id_}", use_container_width=True,
                      disabled=not _gen_ok, help=_gen_why or "打开/生成客户回复草稿",
                      on_click=_open_section, args=(f"ui_draft_{id_}",))
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
                st.button("📩 回复草稿（生成/修改）", key=f"ws_m1_{id_}",
                          use_container_width=True,
                          on_click=_open_section, args=(f"ui_draft_{id_}",))
                st.button("🧭 查看原始询盘", key=f"ws_m2_{id_}",
                          use_container_width=True,
                          on_click=_open_section, args=(f"ui_src_{id_}",))
                st.button("📊 AI 分析依据", key=f"ws_m3_{id_}",
                          use_container_width=True,
                          on_click=_open_section, args=(f"ui_basis_{id_}",))
                st.caption("🏁 成交 / 丢单：请在下方「结单」区人工确认")
        # —— 字段格：AI Priority / 商机 / 当前阶段 / 最近联系 / 下次跟进 / 报价准备度 ——
        _aip_txt = str(_aip) if _aip is not None else "—"
        _qs_txt = str(_qs3) if _qs3 is not None else "—"
        st.markdown(
            "<div class='ws-cells'>"
            f"<div class='ws-cell'><div class='lb'>AI Priority</div>"
            f"<div class='v'>"
            f"<span class='ws-aip {_aip_band(_aip)}'>{_h.escape(_aip_txt)}</span>"
            f"<small class='ws-aipu'>/ 100</small>"
            f"<span class='ws-aipg'>{_h.escape(str(_g_letter or '—'))} 级</span></div>"
            f"<div class='s'>{_h.escape(str(_aip_lvl or ''))}"
            + (f"　·　Queue {_qs_txt}" if _aip is not None else "") + "</div></div>"
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
            st.button("✉️ 生成回复", key=f"ws_asst_gen_{id_}", use_container_width=True,
                      disabled=not _gen_ok, help=_gen_why or "打开回复草稿区",
                      on_click=_open_section, args=(f"ui_draft_{id_}",))
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
                    record_activity(
                        id_, "QUOTE_CREATED",
                        f"创建报价草稿：{_q_summary} · {_qty_txt}",
                        actor="销售", result="报价草稿已保存")
                    _lift = (biz == "READY_FOR_QUOTE")
                    if _lift:
                        update_biz_status(id_, "QUOTED")
                        record_activity(id_, "STATUS_CHANGE",
                                        "创建报价后推进到「已报价」", actor="销售")
                    st.toast("报价已创建并记入 Timeline ✅"
                             + ("；销售阶段已推进到「已报价」" if _lift else ""))
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

    # 旧「询盘头区 + 商机状态条 + 阶段下拉」由 Phase 2 Workspace 取代
    #（Header / Pipeline / AI 销售助手 / Timeline / Related / 报价区），口径不变。
    import html as _vh
    _st_blockers = [b.get("field", "") for b in _prelim]

    # —— Phase 2：Customer + Opportunity Workspace（详情 = 客户工作区）——
    if id_ is not None:
        _render_customer_workspace(dict(
            id=id_, rk=_rk, info=info, matches=matches, lead=lead,
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

    # AI 核心商机结论：第一视觉层级（大字号 + 高字重 + 合理行高与限宽，纯排版层级，
    # 不用大面积渐变 / AI 科技感视觉）。业务员不读完整报告也能看懂结论。
    if _verdict:
        st.markdown(
            f"<div class='verdict-hero'><div class='lbl'>AI 商机判断</div>"
            f"<div class='txt'>{_vh.escape(str(_verdict))}</div></div>",
            unsafe_allow_html=True)

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

    # 按钮行：1 个 Primary（Next Action → CTA，spec 十九：默认只有一个主动作）
    # + 状态流转 + 查证类 Secondary。ask=追问补信息 reply=发送/报价 fu=跟进
    _cta = _wf.primary_action_type(_act_now)
    _bc = st.columns([1.25, 1.0, 1.0, 1.0, 2.2])
    with _bc[0]:
        if _cta == "ask":
            st.button("✉️ 生成询问", key=f"pri_ask_{_rk}", type="primary",
                      use_container_width=True,
                      on_click=_open_section, args=(f"ui_fu_{_rk}",))
        elif _cta == "reply":
            st.button("📦 准备报价" if _act_now.get("type") == "CREATE_QUOTE"
                      else "✉️ 查看并发送", key=f"pri_draft_{_rk}", type="primary",
                      use_container_width=True,
                      on_click=_open_section, args=(f"ui_draft_{_rk}",))
        elif _cta == "fu":
            st.button("⏰ 跟进", key=f"pri_fu_{_rk}", type="primary",
                      use_container_width=True,
                      on_click=_open_section, args=(f"ui_fuzone_{_rk}",))
        else:
            st.button(f"⏸ {(_act_now.get('label') or '等待客户反馈')}",
                      key=f"pri_none_{_rk}", use_container_width=True,
                      disabled=True)
    if id_ is not None:
        with _bc[1]:
            if _stt == STATUS_DONE:
                st.button("🔵 转回待回复", key=f"todo_{id_}", use_container_width=True,
                          on_click=_set_status, args=(id_, STATUS_TODO))
            else:
                st.button("✅ 标记跟进", key=f"done_{id_}", use_container_width=True,
                          on_click=_set_status, args=(id_, STATUS_DONE))
    with _bc[2]:
        st.button("📦 查看产品库", key=f"goto_cat_{_rk}", use_container_width=True,
                  on_click=_goto_products)
    with _bc[3]:
        st.button("❓ 为什么？", key=f"why_{_rk}", use_container_width=True,
                  help="展开 AI 判断依据（评分证据链 · 报价准备度 · 风险）",
                  on_click=_open_section, args=(f"ui_basis_{_rk}",))
    with _bc[4]:
        st.button("查看原始询盘", key=f"pri_src_{_rk}", use_container_width=True,
                  on_click=_open_section, args=(f"ui_src_{_rk}",))

    # ============ Level 2 · 待处理（紧跟 AI 判断：先看缺什么，再看做什么） ============
    _zone("待处理", lv=2)
    _fkey = f"followup_{_rk}"
    try:
        _grp = gapcheck.group_by_level(gaps)
        _ready, _at = gapcheck.readiness(gaps)
    except Exception:
        _grp, _ready, _at = {"A": [], "B": [], "C": []}, 0, 0

    # 三级表达：阻塞（逐项列出，最重，红边加粗）/ 建议确认（黄边中等）/ 可选（弱化纯文字）
    _gl = []
    if _prelim:
        _bl = "".join(f"<li>⚠ {_vh.escape(str(b.get('field', '')))}</li>" for b in _prelim)
        _gl.append(
            f"<div class='gapline blk'><div class='hd'>阻塞项 · {len(_prelim)} 项"
            f"（确认前无法有效报价）</div><ul class='blk-list'>{_bl}</ul></div>")
    if _grp.get("B"):
        _bn = "、".join(m["name"] for m in _grp["B"][:3])
        _gl.append(f"<div class='gapline sug'><span class='lb'>建议确认</span>　{_bn}"
                   + ("等" if len(_grp["B"]) > 3 else "")
                   + f"（共 {len(_grp['B'])} 项，可先初步报价）</div>")
    if _grp.get("C"):
        _cn3 = "、".join(m["name"] for m in _grp["C"][:3])
        _gl.append(f"<div class='gapline opt'><span class='lb'>可选</span>　{_cn3}"
                   + ("等" if len(_grp["C"]) > 3 else "")
                   + f"（共 {len(_grp['C'])} 项，边谈边补即可）</div>")
    if _gl:
        st.markdown("".join(_gl), unsafe_allow_html=True)
    else:
        st.success("✅ 关键信息齐全，没有阻塞事项，可直接报价 / 回信。")

    # 关键数字一行带（参考指标：弱化为 hairline 分隔带，只汇总已有口径，不做新计算）
    _mi = [
        ("询盘等级", f"{_g_letter or '—'} 级", resp_t or _g_extra),
        ("报价准备度", r_cn or "—", f"{r_score} 分" if r_score is not None else "暂未计算"),
        ("客户质量分", str(q_score) if q_score is not None else "—", "六维判定"),
        ("紧急度", urg_lbl, f"状态：{_stt}"),
        ("回复时限", resp_t or "尽快", "按客户等级给出"),
    ]
    st.markdown(
        "<div class='metric-strip'>" + "".join(
            f"<div class='mi'><label>{t}</label><b>{v}</b>"
            f"<small>{'　' + s if s else ''}</small></div>" for t, v, s in _mi)
        + "</div>", unsafe_allow_html=True)

    # 后续动作（第一条已放在顶部「下一步」，这里列第 2-5 条）
    if len(_acts) > 1:
        _task_lines = []
        for _a in _acts[1:5]:
            _an = _a.get("action_cn") or _ACN.get(_a.get("action", ""), _a.get("action", ""))
            _fld = f"（{_a['related_field']}）" if _a.get("related_field") else ""
            _task_lines.append(
                f"- **{_a.get('priority', '')} {_an}**{_fld}　—　{_a.get('reason', '')}")
        st.markdown("\n".join(_task_lines))

    # 部分确认 ≠ 缺失：客户当前意向 + 销售建议（一行一条，不再单独开卡）
    for p in (insight.get("partially_confirmed") or [])[:2]:
        opts = " / ".join(p.get("customer_options", p.get("values", [])))
        st.caption(f"🟡 部分确认 · {p['field']}：客户意向 {opts}"
                   + (f"　💡 {p['sales_advice']}" if p.get("sales_advice") else ""))

    # ============ 需求确认状态（第五轮第二次补丁 03） ============
    # 严格区分三层语义：客户已提出(Customer Requirement) / 已确认(Confirmed) /
    # 最终商业确认(Final Commercial Confirmation)。展示为五态标签，不改任何业务判定。
    _sem = insight.get("requirement_semantics") or []
    _hier = insight.get("product_hierarchy") or {}
    if _sem or _hier:
        _st_icon = {"confirmed": ("green", "✔ 已确认"),
                    "partially_confirmed": ("amber", "◐ 部分确认"),
                    "pending_confirmation": ("amber", "⏳ 待确认"),
                    "unknown": ("low", "⚪ 未提供"),
                    "not_applicable": ("low", "— 不适用")}

        def _st_chip(state: str) -> str:
            tone, txt = _st_icon.get(state, ("low", state))
            return f"<span class='chip {tone}'>{txt}</span>"

        _sem_lines = []
        # ① 产品需求层级条：Category → Type → Model → Spec → Final Selection
        if _hier.get("levels"):
            _lv_bits = []
            for _l in _hier["levels"]:
                _nm = (_l.get("short") or _l.get("label", "")).strip()
                _vv = (f"　<b>{_vh.escape(str(_l['value']))}</b>" if _l.get("value") else "")
                _lv_bits.append(f"{_nm} {_st_chip(_l['state'])}{_vv}")
            _sem_lines.append(
                "<div class='gapline sug'><span class='lb'>产品需求层级</span>　"
                + "　<span class='lb'>→</span>　".join(_lv_bits) + "</div>")
        # ② 商业条件五态行（未提供的字段交给「待处理」表达，不重复堆）
        _sem_bits = []
        for _it in _sem:
            _vv = (f"　<b>{_vh.escape(str(_it['value']))}</b>" if _it.get("value") else "")
            _sem_bits.append(f"{_it['label']} {_st_chip(_it['state'])}{_vv}")
        if _sem_bits:
            _sem_lines.append(
                "<div class='gapline opt'><span class='lb'>需求确认状态</span>　"
                + "　·　".join(_sem_bits) + "</div>")
        if _sem_lines:
            st.markdown("".join(_sem_lines), unsafe_allow_html=True)
            if _hier.get("note"):
                st.caption(f"ℹ️ {_hier['note']}")

    # 英文追问邮件（生成器收进折叠区：需要发追问信时才展开，平时不占屏；
    # 「生成询问」主按钮通过 ui_fu_{_rk} 直接展开本区）
    if gaps:
        with st.expander("✉️ 生成英文追问邮件（选要问的内容，生成即可发）",
                         expanded=st.session_state.get(f"ui_fu_{_rk}", False)):
            # 邮箱已知的来源提示：没识别出来 ≠ 不知道，可能档案里 / 原文里早就有
            if not any(g["key"] == "email" for g in gaps) and not (info.get("email") or "").strip():
                src_email = (known_email or "").strip() or gapcheck.find_email_in_text(text)
                if src_email:
                    src = "客户档案（来自历史询盘渠道）" if known_email else "询盘原文 / 邮件签名"
                    st.caption(f"📧 联系邮箱已知（{src}）：**{src_email}**，不会再向客户询问邮箱。")

            n0 = 0
            for lv in ("A", "B", "C"):
                items = _grp[lv]
                if not items:
                    continue
                meta = gapcheck.LEVEL_META[lv]
                st.markdown(f"**{meta['icon']} {meta['title']}　·　还缺 {len(items)} 项**　·　{meta['tip']}")
                for m in items:
                    n0 += 1
                    badge = "　🎯 智能追问" if m.get("smart") else ""
                    st.write(f"**{n0}. {m['name']}**{badge}")
                    if m.get("reason"):
                        st.caption(f"　　↳ {m['reason']}")
                    st.caption(f"　　💬 {m['question']}")

            # 选要追问哪些等级（C 类默认不选，一次问太多会把客户问跑）
            st.markdown("**选择要追问的内容**")
            cA, cB, cC = st.columns(3)
            sel_a = cA.checkbox(f"⚠️ 报价前需确认（缺 {len(_grp['A'])} 项）", value=True, key=f"selA_{_fkey}")
            sel_b = cB.checkbox(f"📌 建议确定（缺 {len(_grp['B'])} 项）", value=True, key=f"selB_{_fkey}")
            sel_c = cC.checkbox(f"💡 可选问题（缺 {len(_grp['C'])} 项）", value=False, key=f"selC_{_fkey}",
                                help="参考信息，建议第二封信再问，一次问太多会把客户问跑")
            levels = tuple(l for l, on in (("A", sel_a), ("B", sel_b), ("C", sel_c)) if on)

            if levels:
                # 邮件按"选中的等级组合"分别缓存，切换勾选会自动换草稿
                mail_key = f"{_fkey}_{''.join(levels)}"
                if mail_key not in st.session_state:
                    st.session_state[mail_key] = gapcheck.build_followup_email(
                        gaps, info, SELLER.get("company", ""), levels)

                n_q = sum(1 for m in gaps if m["level"] in levels)
                h1, h2 = st.columns([4, 1])
                with h1:
                    st.markdown(f"**英文追问邮件（{'/'.join(levels)} 类，共 {n_q} 问）**")
                with h2:
                    if st.button("✨ AI 润色", key=f"ai_fu_{mail_key}", use_container_width=True,
                                 help="让 AI 结合这条询盘的原文，写一封更自然的追问邮件"):
                        cl = _get_llm_client()
                        if cl:
                            with st.spinner("AI 正在写追问邮件…"):
                                better = gapcheck.build_followup_email_llm(
                                    gaps, text, info, cl, SELLER.get("company", ""), levels)
                            if better:
                                st.session_state[mail_key] = better
                                st.toast("已用 AI 重写追问邮件")
                            else:
                                st.warning("AI 生成失败，已保留规则版草稿")
                        else:
                            st.warning("没检测到可用 API Key，规则版草稿即可直接用")

                st.text_area("英文追问邮件草稿（可修改后直接复制发送）",
                             key=mail_key, height=200)
                st.download_button("⬇️ 下载追问邮件 txt",
                                   st.session_state.get(mail_key, ""),
                                   file_name="followup_email.txt",
                                   key=f"dl_{mail_key}")
            else:
                st.caption("至少勾选一类，才会生成追问邮件。")

    # ============ Level 2 · 行动产出 · 回复客户（策略 → Email 预览 → 操作） ============
    _dk = f"ui_draft_{_rk}"
    with st.container(border=True):
        _zone("行动产出 · 回复客户", lv=2)
        # 第一部分：回复策略（先说清"为什么这么回"，再看邮件写得怎么样）
        _plan = report.get("reply_plan") or {}
        _sel_q = _plan.get("selected") or []
        _adv2 = lead.get("advice", "") or ""
        _st_parts = [f"**回复目的**：{_nextstep}"]
        if _plan.get("intent"):
            # 第五轮第三次优化：意图 → 销售阶段 → 首要动作
            _st_parts.append(
                f"**意图**：{_plan.get('intent_cn') or _plan.get('intent')}"
                f"（阶段：{_plan.get('sales_stage', '')}）")
            if _plan.get("immediate_action"):
                _st_parts.append(f"**首要动作**：{_plan.get('immediate_action')}")
        if r_cn:
            _st_parts.append(f"**当前状态**：{r_cn}")
        if _sel_q:
            _st_parts.append(f"**追问数量**：{len(_sel_q)} 问（限流 ≤3）")
        if _adv2:
            _st_parts.append(f"**策略**：{_adv2}")
        st.markdown(
            "<div class='advice'><b>🎯 回复策略</b><br>"
            + "　·　".join(_st_parts) + "</div>",
            unsafe_allow_html=True)
        # 第十一轮（spec 十四）：建议问题逐条列出（首轮最多 2-3 个，与草稿同一来源
        # reply_plan.selected，不另造问题）；纯动作用的占位项（如"首轮禁止提问"）不显示
        _q_show = [q for q in _sel_q
                   if (q.get("question") or "").strip()
                   and not (q.get("question") or "").strip().startswith("（")]
        if _q_show:
            _q_md = "\n".join(
                f"**{_qi}.** {_vh.escape((q.get('tier_label') or q.get('priority') or '').strip())}　"
                f"{_vh.escape(q['question'].strip())}"
                for _qi, q in enumerate(_q_show[:3], 1))
            st.markdown(
                "<div class='advice'><b>❓ 建议问题（最多 3 个，先问卡住报价的关键项）</b><br>"
                + _q_md.replace("\n", "<br>") + "</div>",
                unsafe_allow_html=True)
        # 第二部分：Email 预览（内容口径不变，仅改表述）
        if draft:
            _hr_flag = ("　⚠️ HUMAN REVIEW REQUIRED"
                        if report.get("human_review_required") else "")
            if report.get("draft_issues"):
                # Fact Guard：邮件含未经公司知识库验证的商业承诺
                # → 横幅放在折叠区外，不展开也必须看到；阻止自动发送
                st.error(
                    "**⚠️ HUMAN REVIEW REQUIRED**　邮件包含 **未经公司知识库验证的"
                    "商业承诺**（" + str(len(report["draft_issues"])) + " 处），"
                    "已阻止自动发送，请人工复核修改后再使用：\n\n"
                    + "\n\n".join("- " + i for i in report["draft_issues"][:5]))
            with st.expander("📩 Email 预览（基于本司资料生成，发送前请人工复核）"
                             + _hr_flag,
                             expanded=st.session_state.get(_dk, False)):
                # 注意：key 必须按询盘隔离（draft_box_{id}）。
                # 早期版本所有询盘共用 key="draft_box"，Streamlit 会沿用上一条询盘
                # 残留在 session_state 里的草稿文本，造成跨询盘上下文污染。
                st.text_area("可直接复制发送", draft, height=170,
                             key=f"draft_box_{_rk}", label_visibility="collapsed")
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    st.download_button("⬇️ 下载草稿 txt", draft, file_name="reply_draft.txt",
                                       use_container_width=True)
                with col_d2:
                    if st.button("📋 复制草稿", use_container_width=True):
                        st.toast("草稿在上方文本框：Ctrl+A 全选 → Ctrl+C 复制")
                # Phase 2：保存草稿修改 → 写回库并记 REPLY_EDITED（生成回复必须留痕）
                if id_ is not None:
                    _s_c1, _s_c2 = st.columns([1.4, 3.6])
                    with _s_c1:
                        if st.button("💾 保存修改", key=f"ws_saved_{id_}",
                                     use_container_width=True,
                                     help="把上方改动保存为最新草稿，并记入 Timeline"):
                            _ws_save_draft(id_)
                    with _s_c2:
                        st.caption("AI 生成的草稿可在此修改；保存后记入 Timeline（REPLY_EDITED）。")
                # 发送后的闭环动作（第七轮 spec 七/八）：
                # 「标记为已发送」= CRM 状态操作（REPLIED + last_replied_at + 事件），
                # 不发送真实邮件、不伪造发送成功；发送后进入跟进流程
                if id_ is not None and _biz_now not in (
                        "REPLIED", "FOLLOW_UP", "QUOTED", "NEGOTIATING",
                        "WON", "LOST", "ON_HOLD"):
                    if st.button("📤 标记为已发送", key=f"mark_replied_{id_}",
                                 type="primary", use_container_width=True,
                                 help="把这条询盘标记为「已回复」，之后可设置跟进时间"):
                        _mark_replied(id_)
                        st.toast("已标记为已发送 ✅ 下一步：设置跟进时间")
                        # 立即重跑：让「跟进区」和业务状态徽章马上刷新
                        st.rerun()
                elif id_ is not None and _wfd.get("last_replied_at"):
                    st.caption(f"📤 已标记发送于 {_wfd['last_replied_at']}"
                               + ("　·　回复内容以本条询盘草稿为准" if draft else ""))
        else:
            st.write("草稿生成失败（极少见），请重试分析。")

    # ============ 行动产出 · 跟进（第七轮 spec 八/九：发送后进入跟进流程） ============
    # 跟进 = CRM 待办，不是邮件发送；状态：待跟进 / 今日跟进 / 已逾期 / 已完成。
    # Phase 2：整区收进可展开容器（ui_fuzone），供 Header「记录跟进」一键展开。
    if id_ is not None and _biz_now in ("REPLIED", "FOLLOW_UP", "QUOTED",
                                        "NEGOTIATING", "READY_FOR_QUOTE"):
        with st.expander("⏰ 跟进（设置下次联系时间 · 所有动作记入 Timeline）",
                         expanded=st.session_state.get(f"ui_fuzone_{_rk}", False)):
            _fu_bits = []
            if _wfd.get("last_replied_at"):
                _fu_bits.append(f"上次回复 {_wfd['last_replied_at']}")
            if _wfd.get("follow_up_at") and _fu_state != "已完成":
                _fu_bits.append(f"计划跟进 {_wfd['follow_up_at'][:16]}")
            if _fu_state:
                _fu_bits.append(f"状态：{_fu_state}")
            if _fu_bits:
                st.markdown("<div class='advice'>" + "　·　".join(_fu_bits) + "</div>",
                            unsafe_allow_html=True)
            if _fu_state == "已逾期":
                st.warning("⏰ 跟进已到期 —— 现在就该跟进这位客户了")
            elif _fu_state == "今日跟进":
                st.info("📅 这条询盘今天需要跟进")
            # 设置跟进时间（到期后可反复改期；完成跟进后可再次设置新一轮）
            if _fu_state != "已完成":
                st.selectbox("跟进时间", FU_OPTIONS, key=f"fu_pick_{id_}", index=1,
                             help="到期后这条询盘会进入「待跟进」，出现在 AI 今日建议里")
                if (st.session_state.get(f"fu_pick_{id_}") == "自定义"):
                    st.text_input("自定义时间（格式 2026-09-08 10:00）",
                                  key=f"fu_custom_{id_}",
                                  placeholder="2026-09-08 10:00")
                c_f1, c_f2 = st.columns(2)
                with c_f1:
                    st.button("⏰ 设置跟进", key=f"fuset_{id_}",
                              use_container_width=True, type="primary",
                              on_click=_do_set_followup, args=(id_,))
                with c_f2:
                    if _wfd.get("follow_up_at"):
                        st.button("✅ 完成本次跟进", key=f"fudone_{id_}",
                                  use_container_width=True,
                                  on_click=_do_complete_followup, args=(id_,))
                _fu_msg = st.session_state.get(f"fu_msg_{id_}")
                if _fu_msg:
                    st.caption(_fu_msg)

    # ============ 结单：成交 / 丢单（第七轮 spec 二十二/二十三：必须人工确认） ============
    # AI 不自动标记成交；且状态机禁止 NEW/NEEDS_INFO/READY_TO_REPLY 直接跳 WON
    if id_ is not None and _biz_now in ("WON", "LOST"):
        st.success("🏆 已成交，恭喜！" if _biz_now == "WON" else "⚫ 已标记丢单（可在跟进中复盘原因）")
    elif id_ is not None and _wf.can_transition(_biz_now, "WON"):
        with st.expander("🏁 结单（成交 / 丢单 · 需人工确认）"):
            st.caption("结单只能由人工确认，AI 判断仅供参考（spec：不把 AI 推断当业务事实）")
            _deal_ok = st.checkbox("我确认这是最终业务结果", key=f"deal_ok_{id_}")
            _dc1, _dc2 = st.columns(2)
            with _dc1:
                st.button("🏆 标记成交", key=f"deal_won_{id_}",
                          use_container_width=True, disabled=not _deal_ok,
                          on_click=_do_mark_deal, args=(id_, "WON"))
            with _dc2:
                st.button("⚫ 标记丢单", key=f"deal_lost_{id_}",
                          use_container_width=True, disabled=not _deal_ok,
                          on_click=_do_mark_deal, args=(id_, "LOST"))

    st.divider()

    # ============ 第三层 · AI 分析结果（客户 → 匹配 → 评分） ============
    _zone("AI 分析结果", lv=3)

    # ① 客户关键信息（绿=已识别 / 黄=缺失可补全；档案已有邮箱会直接带出）
    st.markdown("**① 客户关键信息**")
    kc = st.columns(len(KEY_FIELDS), gap="small")
    for (key, label), col in zip(KEY_FIELDS, kc):
        val = info.get(key) or ""
        # 邮箱：本次没识别出来，但客户档案里有（历史渠道带来的）→ 直接展示
        from_archive = False
        if key == "email" and not val and known_email:
            val, from_archive = known_email, True
        if val:
            _mark = "<span title='来自客户档案'>📎</span>" if from_archive else "✔"
            col.markdown(
                f"<div class='cust-chip ok'><div class='t'>{label}</div>"
                f"<div class='v'>{_mark} {val}</div></div>",
                unsafe_allow_html=True)
        else:
            col.markdown(
                f"<div class='cust-chip miss'><div class='t'>{label}</div>"
                f"<div class='v'>⚠️ 未识别</div></div>",
                unsafe_allow_html=True)

    _missing_keys = [lab for k, lab in KEY_FIELDS if not info.get(k)]
    if _missing_keys:
        st.caption("部分字段未识别 —— 可在底部「✏️ 补全 / 修正客户信息」补全，会自动同步到客户档案。")

    # 其余采购信息（四列小卡，单行看全）
    # 多数量语义补丁：多数量时逐个标注商业语义（报价数量 2,000 pcs · 试单 500 pcs …），
    # 不再只显示一个数字让业务员误以为客户只要这一种量
    sems = info.get("quantity_semantics") or []
    if len(sems) > 1:
        qty = "　".join(f"{s['role_short']} {s['value']:,} {s.get('unit') or 'pcs'}"
                        for s in sems[:4])
    else:
        qty = f"{info['quantity']:,} {info['quantity_unit']}" if info.get("quantity") else "未识别"
    price = info.get("target_price")
    fields = [
        ("采购数量", qty),
        ("目标价", f"{info.get('target_price_currency') or 'USD'} {price}" if price else "未提及"),
        ("采购意图", info.get("intent") or "常规采购"),
        ("紧急度", URGENCY_CN.get(info.get("urgency"), "未判断")),
    ]
    _fr = st.columns(4, gap="small")
    for (k, v), _fc in zip(fields, _fr):
        _fc.markdown(
            f"<div class='card'><div class='t'>{k}</div>"
            f"<div class='v' style='font-size:.9rem'>{v}</div></div>",
            unsafe_allow_html=True)

    # —— 第九轮：业务事实层（客户说的 ≠ 公司确认的 ≠ AI 推断的）——
    # 每个关键业务字段带 Value / Source / Certainty 三元组；旧记录用当前口径实时重建
    _facts = report.get("facts") or []
    if not _facts:
        try:
            from agent.facts import build_fact_layer
            _facts = build_fact_layer(text, info, matches, insight)
        except Exception:
            _facts = []
    if _facts:
        try:
            from agent.facts import quote_readiness_of
            _qr9 = quote_readiness_of(insight)
        except Exception:
            _qr9 = None
        _badge = {
            "Confirmed": ("var(--success)", "已确认"), "Explicit": ("var(--brand-500)", "客户明确"),
            "Approximate": ("var(--warning)", "约数"), "Preferred": ("var(--brand-500)", "客户期望"),
            "Unconfirmed": ("var(--text-muted)", "待客户确认"), "Unknown": ("var(--text-disabled)", "未提供"),
            "Missing": ("var(--danger)", "缺失"),
        }
        import html as _fh
        _rows = []
        for _f in _facts:
            _color, _ccn = _badge.get(_f["certainty"], ("var(--text-disabled)", _f["certainty"]))
            # Fact Source 四源归一（本轮）：Product Data / Company Data 同属
            # COMPANY_FACT（公司知识库）；AI Inference / Unknown 不得当公司事实
            _src = str(_f["source"])
            _fsrc = ("CUSTOMER_FACT" if _src == "Customer Fact"
                     else "COMPANY_FACT" if _src in ("Company Data", "Product Data")
                     else "SYSTEM_RULE" if _src == "系统判定"
                     else "AI_INFERENCE" if _src == "AI Inference" else "UNKNOWN")
            _note = (f"<div class='fnote'>{_fh.escape(str(_f['note']))}</div>"
                     if _f.get("note") else "")
            _rows.append(
                "<div class='frow'>"
                f"<div class='flab'>{_fh.escape(str(_f['label']))}</div>"
                f"<div class='fval'>{_fh.escape(str(_f['display']))}</div>"
                f"<div class='fsrc' title='{_fsrc}'>{_fh.escape(_src)}"
                f"<br><span style='font-size:.58rem;opacity:.75'>{_fsrc}</span></div>"
                f"<div class='fcert' style='color:{_color};border-color:{_color}'>{_ccn}</div>"
                f"</div>{_note}")
        if _qr9:
            _rows.append(
                "<div class='frow'>"
                "<div class='flab'>报价状态</div>"
                f"<div class='fval'>{_qr9['status_cn']} <span class='fqr'>{_qr9['status']}</span></div>"
                "<div class='fsrc'>系统判定</div>"
                "<div class='fcert' style='color:var(--text-secondary);border-color:var(--text-muted)'>与数据一致</div>"
                "</div>")
        st.markdown(
            "<div class='factbox'><div class='ftitle'>📋 业务事实层　"
            "<span class='fsub'>FACT SOURCE：CUSTOMER_FACT 客户说的 ｜ "
            "COMPANY_FACT 公司/产品库 ｜ AI_INFERENCE 推断 ｜ UNKNOWN 未知</span></div>"
            + "".join(_rows) + "</div>",
            unsafe_allow_html=True)

    if info.get("summary"):
        # AI 摘要突出显示：放大字号 + 高亮卡片，数量/价格/认证/交期自动标红（业务员一眼抓关键）
        import html as _html
        import re as _re

        def _hero(s: str) -> str:
            s = _html.escape(str(s))
            # 数量 / 交期：5000pcs、4周、14天、3个月…
            s = _re.sub(
                r"(\$ ?\d[\d,]*(?:\.\d+)?|USD ?\d[\d,]*(?:\.\d+)?"
                r"|\d[\d,]*(?:\.\d+)?\s*(?:k\b|pcs|pieces|units|sets|pairs|个|件|只|套|双|周|天|日|个月))",
                r"<span class='hl'>\1</span>", s, flags=_re.I)
            # 认证 / 标准：CE、RoHS、FDA…（用字母数字边界，兼容中文紧邻的情况）
            s = _re.sub(
                r"(?<![A-Za-z0-9])(CE|RoHS|REACH|FDA|BSCI|ISO ?9001|EN ?71|SGS)"
                r"(?![A-Za-z0-9])",
                r"<span class='hl'>\1</span>", s)
            return s

        _conf = (f"<span class='conf'>（置信度 {info.get('confidence')}）</span>"
                 if info.get("confidence") else "")
        st.markdown(
            "<div class='sum-hero'><div class='lbl'>🔍 AI 摘要 · 关键信息</div>"
            f"<div class='txt'>{_hero(info['summary'])}{_conf}</div></div>",
            unsafe_allow_html=True)

    # ② 产品匹配（八要素展示）
    st.markdown("**② 产品匹配（命中 / 依据 / 待确认）**")
    if matches:
        for p in matches[:3]:
            with st.expander(
                    f"**{p['name']}**（{p['name_cn']}）　匹配度 "
                    f"{int(p['match_score'] * 100)}%　"
                    f"${p['price_range'][0]:.2f}-{p['price_range'][1]:.2f}　MOQ {p['moq']:,}",
                    expanded=(p is matches[0])):
                st.write(f"**匹配依据**：{p.get('match_basis') or p.get('match_reason', '')}")
                st.write(f"**已满足条件**：" + ("、".join(p["matched_conditions"])
                         if p.get("matched_conditions") else "（无，仅关键词间接相关）"))
                if p.get("unmatched_conditions"):
                    st.write(f"**未满足 / 待确认**：\n\n" +
                             "\n\n".join(f"　· {u}" for u in p["unmatched_conditions"]))
                if p.get("missing_info"):
                    st.write(f"**缺失信息**：{'；'.join(p['missing_info'])}")
                st.write(f"**推荐理由**：{p.get('recommendation', '')}")
                for u in p.get("uncertainty", []):
                    st.caption(f"❓ {u}")
    else:
        # 第五轮第二次补丁 03：品类已明确 ≠ 无产品信息——提示转为"查库匹配候选"口径
        _hier_lv = (insight.get("product_hierarchy") or {}).get("levels") or []
        _cat0 = next((l for l in _hier_lv if l.get("level") == "category"), {})
        if _cat0.get("state") == "confirmed" and _cat0.get("value"):
            st.warning("🔍 产品库暂无该品类匹配产品。客户已明确产品类别「"
                       + str(_cat0["value"]) + "」→ 下一步：查看产品库并匹配候选产品"
                       "（检索该品类 / 相近品类），匹配不上再请客户提供参考型号 / 图片"
                       "（不编造产品，也不向客户反问产品类别）。")
        else:
            st.warning("🔍 当前产品库没有足够证据找到匹配产品（如实提示，不编造产品）——"
                       "建议在回复草稿中请客户提供产品图片 / 链接 / 参考型号。")

    # ③ AI 商机判断（结论已在顶部给过；这里给四个关键维度 + 完整依据折叠区）
    st.markdown("**③ AI 商机判断**")
    # 兼容历史记录：旧报告没存八维明细 → 用当前模型重算展示
    if "dims" not in lead or any(d.get("key") == "product_match"
                                 for d in lead.get("dims", [])) is False:
        try:
            from agent.lead_score import LeadScorer
            lead = LeadScorer().score(info, matches, text=text)
        except Exception:
            pass

    # 快四维 chips：业务员最关心的四个维度（口径不变，只做展示挑选）
    _dmap = {d.get("key"): d for d in lead.get("dims", [])}
    _qd = st.columns(4, gap="small")
    for (k, lab), _qcol in zip(_QUICK_DIMS.items(), _qd):
        _dd = _dmap.get(k)
        _dv = _dd.get("score") if _dd else None
        _qc_tone = "high" if (_dv or 0) >= 70 else ("mid" if (_dv or 0) >= 40 else "low")
        _qcol.markdown(
            f"<div class='chip {_qc_tone}'>{lab}　<b>{_dv if _dv is not None else '—'}</b></div>",
            unsafe_allow_html=True)

    _q3 = lead.get("customer_quality_total")
    st.caption(f"综合评分 **{lead.get('score', '—')} · {_g_letter or '—'} 级**"
               + (f"　等级由「客户质量分 {_q3}」（六维，不含接单能力）判定；"
                  f"综合评分含产品匹配与成交概率，仅内部参考。" if _q3 is not None else ""))

    with st.expander("查看 AI 分析依据（八维评分 · 订单价值 · 报价准备度 · 风险 · 数据来源）",
                     expanded=st.session_state.get(f"ui_basis_{_rk}", False)):
        _render_ai_basis(lead, insight, report, _ns)

    # ============ 第四层 · 客户与产品详情（需要时才展开） ============
    _zone("客户与产品", lv=4)

    # （原④缺失信息逐项 + 英文追问邮件已上移到「待处理事项」；原⑤报价准备度依据
    #    / 风险 / 一致性已收进 L3「查看 AI 分析依据」折叠区，口径不变）

    # ⑥ 维护：补全 / 修正 / 备注（只对已入库的历史询盘显示，新分析入库后自动出现）
    if id_ is not None:
        with st.expander("✏️ 补全 / 修正客户信息（AI 没识别准的可以手改）"):
            with st.form(f"edit_info_{id_}"):
                e1, e2 = st.columns(2)
                n_country = e1.text_input("客户国家/地区", value=info.get("country") or "")
                n_company = e2.text_input("客户公司", value=info.get("company") or "")
                n_website = e1.text_input("公司网址", value=info.get("website") or "")
                n_email = e2.text_input("联系邮箱", value=info.get("email") or "")
                n_contact = e1.text_input("联系人", value=info.get("contact_name") or "")
                if st.form_submit_button("💾 保存客户信息"):
                    update_inquiry_info(id_, {
                        "country": n_country, "company": n_company,
                        "website": n_website, "email": n_email,
                        "contact_name": n_contact,
                    })
                    st.toast("已保存，客户档案已同步更新")
                    st.rerun()

        st.markdown("**备注**")
        cur_note = get_inquiry_note(id_) or ""
        note_val = st.text_area("给这条询盘加备注（如跟进状态、特殊要求）",
                                value=cur_note, height=90, key=f"note_{id_}")
        if st.button("💾 保存备注", key=f"savenote_{id_}"):
            update_inquiry_note(id_, note_val)
            st.toast("备注已保存")

    # 旧「询盘进度 Timeline」折叠区已被 Phase 2 Workspace 的 Activity Timeline
    # 取代（时间倒序 · 时间/类型/内容/执行人/结果 · 支持手动记录），此处不再重复。

    # ============ 第五层 · 原始数据与元数据（默认折叠，进一步弱化） ============
    _zone("原始数据 / 元数据", lv=5)

    # 原始询盘：默认折叠（AI 分析负责理解，原文负责核对；顶部按钮可一键展开）
    with st.expander("查看原始询盘（AI 分析负责理解，原文负责核对）",
                     expanded=st.session_state.get(f"ui_src_{_rk}", False)):
        st.code(text.strip())


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
st.set_page_config(page_title="AI 外贸询盘工作台", layout="wide")

# —— 顶栏：产品名（置顶）+ 公司徽标 + 全局“新建分析”入口 ——
_hh = st.columns([2.4, 1.8])
with _hh[0]:
    st.title("AI 外贸询盘工作台")
    st.caption("询盘分析 · 产品匹配 · 报价决策 · 客户跟进")
with _hh[1]:
    _co = (SELLER.get("company") or "").strip()
    _st = (SELLER.get("strength") or "").strip()
    st.markdown(
        f"<div class='brand-box'>"
        + (f"<div class='co'>{_co}</div>" if _co else "<div class='co'>我方公司资料</div>")
        + (f"<div class='st'>{_st}</div>" if _st else "")
        + "</div>", unsafe_allow_html=True)
    if st.button("＋ 新建询盘分析", key="new_top", use_container_width=True):
        _new_analysis()
st.markdown(PAGE_CSS, unsafe_allow_html=True)

# —— 主工作区顶部 4 个核心 KPI（Phase 1：今日新增 / 待回复 / 待跟进 / 待报价，真实业务指标）——
_kpi = _kpi_snapshot()
kc = st.columns(4, gap="small")
_td = _kpi["today"] - _kpi["yest"]
_kpi_card(kc[0], "今日新增", _kpi["today"],
          f"昨日 {_kpi['yest']} 条"
          + (f"　<b>较昨日 +{_td}</b>" if _td > 0
             else ("　与昨日持平" if _td == 0 else f"　较昨日 {_td}")),
          tone="blue")
_kpi_card(kc[1], "待回复", _kpi["to_reply"],
          "尚未回复的待办询盘",
          tone="red" if _kpi["to_reply"] > 0 else "")
_kpi_card(kc[2], "待跟进", _kpi["fu_due"],
          "今日到期或已逾期",
          tone="amber" if _kpi["fu_due"] > 0 else "")
_kpi_card(kc[3], "待报价", _kpi["to_quote"],
          "已具备报价条件",
          tone="green" if _kpi["to_quote"] > 0 else "")
# KPI 整卡点击 → 左侧队列自动应用对应筛选（统计数字与筛选联动；覆盖按钮铺满 KPI 卡）
for _ki, _kf in enumerate(["全部", "待回复", "待跟进", "待报价"]):
    with kc[_ki]:
        st.button("", key=f"kpi_nav_{_ki}", use_container_width=True,
                  on_click=_set_filter, args=(_kf,),
                  help="点击后在左侧队列应用对应筛选")

# —— 今日销售任务：四类可执行待办（真实统计，无数据即 0，不伪造）——
#    每项 [开始处理] → 进入 AI 建议行动队列逐条执行（与侧栏筛选取值完全一致）
_tasks = [
    ("🔴", "高优先级待回复", _kpi["high"], "AI 判定高优且仍待处理，建议今天优先回复", "高优先级", "red"),
    ("🟠", "缺少关键产品信息", _kpi["miss"], "规格/数量/交期等关键信息不足，无法报价", "待补关键信息", "amber"),
    ("🟡", "超过48小时未跟进", _kpi["stale"], "距创建已超 48 小时仍未处理，回复时效已过", "超48小时", "amber"),
    ("🟢", "已具备报价条件", _kpi["to_quote"], "产品/数量等关键信息已确认，可直接报价", "待报价", "green"),
]
with st.container(border=True):
    _tk_hd = st.columns([1.4, 8.6])
    with _tk_hd[0]:
        st.markdown("<div style='font-size:.9rem;font-weight:700'>🗂 今日销售任务</div>",
                    unsafe_allow_html=True)
    with _tk_hd[1]:
        st.caption("AI 从真实询盘数据聚合的今日待办 —— 每项点「开始处理」进入逐条执行队列，0 = 暂无该项")
    _tk_cols = st.columns(4, gap="small")
    for _ti, (_e, _t2, _cnt, _rs, _flt, _tone) in enumerate(_tasks):
        with _tk_cols[_ti]:
            _tcol = {"red": "var(--danger)", "amber": "var(--warning)",
                     "green": "var(--success)", "blue": "#185FA5"}[_tone]
            st.markdown(
                f"<div style='border-left:3px solid {_tcol};padding:.15rem 0 .1rem .45rem'>"
                f"<div style='font-size:.74rem;font-weight:700;color:{_tcol}'>{_e} {_t2}</div>"
                f"<div style='font-size:1.5rem;font-weight:700;line-height:1.15'>{_cnt}</div>"
                f"<div style='font-size:.7rem;opacity:.7;min-height:2.1em'>{_rs}</div></div>",
                unsafe_allow_html=True)
            st.button("开始处理", key=f"task_{_flt}", use_container_width=True,
                      disabled=(_cnt <= 0), on_click=_enter_actq, args=(_flt,),
                      help="进入 AI 建议行动队列，逐条连续处理")

# —— 我的销售队列：首页核心工作台（AI 优先级前 8 条待处理，真实数据，点「处理」直接打开）——
with st.container(border=True):
    _mq_hd = st.columns([1.4, 8.6])
    with _mq_hd[0]:
        st.markdown("<div style='font-size:.9rem;font-weight:700'>📋 我的销售队列</div>",
                    unsafe_allow_html=True)
    with _mq_hd[1]:
        st.caption("按 AI 优先级排序的待处理询盘 —— 点「处理」打开详情执行下一步（完整队列在左侧）")
    _mq_rows = [x for x in load_queue(None) if x["status"] == STATUS_TODO][:8]
    if not _mq_rows:
        st.caption("暂无待处理询盘 —— 今天没有必须立即处理的任务。")
    else:
        _MQW = [1.8, 1.5, 1.0, 1.05, 0.8, 1.0, 1.7, 0.85]
        _mq_lab = ["客户（公司 / 联系人）", "需求 / 产品", "数量", "阶段",
                   "AI 优先级", "上次联系", "下一步", "动作"]
        _mq_hcols = st.columns(_MQW, gap="small")
        for _c, _lb in zip(_mq_hcols, _mq_lab):
            _c.markdown(f"<div style='font-size:.68rem;opacity:.62'>{_lb}</div>",
                        unsafe_allow_html=True)
        import html as _hq
        for _it in _mq_rows:
            _qcols = st.columns(_MQW, gap="small")
            _need = _it.get("need") or {}
            _wq = _it.get("wf") or {}
            _qtone, _qemo = _wf.BIZ_STYLE.get(_it.get("biz"), ("blue", "🔵"))
            _qcn = _wf.BIZ_CN.get(_it.get("biz"), _it.get("biz"))
            _q_last = _wq.get("last_replied_at") or _it.get("created")
            _q_pri = _it.get("pts") or 0
            _q_pcol = ("var(--danger)" if _q_pri >= 65
                       else ("var(--warning)" if _q_pri >= 35 else "var(--text-disabled)"))
            with _qcols[0]:
                st.markdown(
                    f"<div style='font-weight:700;font-size:.85rem'>"
                    f"{_hq.escape(str(_it['company'] or _it['contact'] or '未知客户'))}</div>"
                    f"<div style='font-size:.72rem;opacity:.7'>"
                    f"{_hq.escape(str(_it['contact'] or ''))}"
                    + (f" · {_ui.flag(_it['country'])}"
                       f"{_hq.escape(str(_it['country'] or ''))}"
                       if _it.get("country") else "")
                    + "</div>", unsafe_allow_html=True)
            with _qcols[1]:
                _q_prod = (_need.get("product") or _need.get("intent") or "—")
                st.markdown(
                    f"<div style='font-size:.78rem'>{_hq.escape(str(_q_prod))}</div>",
                    unsafe_allow_html=True)
            with _qcols[2]:
                st.markdown(
                    f"<div style='font-size:.8rem'>{_hq.escape(str(_need.get('qty') or '—'))}</div>",
                    unsafe_allow_html=True)
            with _qcols[3]:
                st.markdown(f"<div style='font-size:.78rem'>{_qemo} {_qcn}</div>",
                            unsafe_allow_html=True)
            with _qcols[4]:
                st.markdown(
                    f"<div style='font-weight:700;font-size:.85rem;color:{_q_pcol}'>{_q_pri}</div>",
                    unsafe_allow_html=True)
            with _qcols[5]:
                st.markdown(
                    f"<div style='font-size:.76rem;opacity:.75'>{_ui.ago(_q_last)}</div>",
                    unsafe_allow_html=True)
            with _qcols[6]:
                st.markdown(
                    f"<div style='font-size:.78rem'>"
                    f"{_hq.escape(str((_it.get('action') or {}).get('label') or '—'))}</div>",
                    unsafe_allow_html=True)
            with _qcols[7]:
                st.button("处理", key=f"mq_open_{_it['id']}", use_container_width=True,
                          on_click=_open_inquiry, args=(_it["id"],),
                          help=f"打开 #{_it['id']} 详情执行下一步")

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
    _score_txt = (f"P{it['pts']} · {it['score']}分"
                  + (f" · {(it['cust_grade'] or it['grade'] or '')}级"
                     if (it["cust_grade"] or it["grade"]) else "")
                  if (it["score"] or it["pts"]) else "—")
    # Phase 3：AI Priority 与 Queue Score 徽标（只读派生，缺数据不显示）
    _p3_txt = ""
    if it.get("aip3") is not None:
        _p3_txt = (f"<span class='aip'>AI {it['aip3']}</span>"
                   f"<span class='qs'>队列 {it['qs3']}</span>")
    if _p3_txt:
        _score_txt = (_p3_txt + ("<br>" + _score_txt if _score_txt != "—"
                                 else ""))
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


# 侧边栏：销售工作队列（Sales Work Queue —— “我现在该处理什么”）
with st.sidebar:
    st.markdown("#### 询盘队列")
    _sb_c1, _sb_c2 = st.columns(2)
    with _sb_c1:
        if st.button("＋ 新建分析", key="new_side", use_container_width=True):
            _new_analysis()
    with _sb_c2:
        # Phase 4（spec 一）：开始处理 → 按当前 Filter+Sort+Status 生成执行队列
        st.button("▶ 开始处理", key="exec_start_side", use_container_width=True,
                  type="primary", on_click=_enter_actq, args=(None,),
                  help="按当前筛选与排序生成执行队列，逐条处理、完成后自动进入下一条")

    # —— 队列加载（spec 二十四：失败兜底，不影响已加载的主工作区）——
    try:
        _all_rows = load_queue(None)
    except Exception as _qe:
        st.error("询盘列表加载失败")
        st.caption(str(_qe)[:140])
        if st.button("🔄 重新加载", key="queue_reload", use_container_width=True):
            st.rerun()
        _all_rows = []

    # —— 队列统计行（只做小字统计，来源与筛选/建议同一口径）——
    # Phase 1：「待回复」与 KPI/筛选同口径（biz=READY_TO_REPLY），不再用待处理总数冒充
    _n_reply = sum(1 for x in _all_rows
                   if x["status"] == STATUS_TODO and x.get("biz") == _wf.READY_TO_REPLY)
    _n_miss = sum(1 for x in _all_rows
                  if x["status"] == STATUS_TODO and x["need"].get("blockers"))
    _n_high = sum(1 for x in _all_rows
                  if x["status"] == STATUS_TODO and x["pri"] == PRI_HIGH)
    # 第七轮：今日需跟进（到期/今天 + 未完成 + 未结单，与 KPI/AI建议同一口径）
    _n_fudue = sum(1 for x in _all_rows
                   if (x.get("action") or {}).get("type") == "FOLLOW_UP_CUSTOMER")
    # Phase 1：待报价（已具备报价条件，与 KPI/今日任务同口径）
    _n_quote = sum(1 for x in _all_rows
                   if x["status"] == STATUS_TODO and x.get("biz") == _wf.READY_FOR_QUOTE)
    # 第八轮（spec 十二）：客户数 ≠ 询盘数——同一口径的身份键统计客户数
    _n_cust = len({_ui.customer_key(x.get("cust_id"), x["company"],
                                    x["contact"], x["id"])
                   for x in _all_rows})
    st.markdown(
        f"<div style='font-size:.76rem;opacity:.75;margin:.1rem 0 .3rem'>"
        f"待回复 {_n_reply} · 待补关键信息 {_n_miss} · 高优先级 {_n_high}"
        + (f" · 待报价 {_n_quote}" if _n_quote else "")
        + (f" · <b style='color:var(--danger)'>今日跟进 {_n_fudue}</b>" if _n_fudue else "")
        + f" · 客户 {_n_cust} 家</div>",
        unsafe_allow_html=True)

    # —— 搜索：客户姓名 / 公司 / 国家 / 询盘主题 / 数量 / 询盘 ID（实时过滤）——
    _q = (st.text_input("搜索", key="inbox_search",
                        label_visibility="collapsed",
                        placeholder="🔍 搜索客户、公司、产品或询盘…") or "").strip().lower()
    if _q:
        def _hit(x):
            _nd = x["need"] or {}
            hay = " ".join([
                x["company"] or "", x["contact"] or "", x["country"] or "",
                _nd.get("intent") or "", _nd.get("qty") or "",
                _nd.get("product") or "", str(x["id"]),
            ]).lower()
            return _q in hay
        _all_rows = [x for x in _all_rows if _hit(x)]

    # —— 筛选（第八轮：chip 文案与商机阶段 BIZ_CN 完全一致——一个状态只有一个名字，
    #    杜绝「待处理/待回复/等待回复」式同义混用，spec 六/七）——
    _sel = st.segmented_control(
        "筛选", ["全部", "待回复", "待补关键信息", "高优先级", "待报价", "已回复",
               "待跟进", "超48小时"],
        selection_mode="single", default="全部", key="queue_filter")
    _sel = _sel or "全部"
    # 第十轮：筛选规则抽到 _filter_rows（与 AI 行动队列共用同一口径，spec 八/十五）
    rows = _filter_rows(_all_rows, _sel)

    # —— 第十轮（spec 十五）：高级筛选（收进 expander 保持界面干净）——
    with st.expander("高级筛选"):
        _af1, _af2 = st.columns(2)
        with _af1:
            _f_qr = st.selectbox(
                "报价准备度",
                ["全部", "NOT_READY", "PARTIALLY_READY", "READY_FOR_QUOTE", "QUOTED"],
                key="adv_qr")
        with _af2:
            _f_opp = st.selectbox(
                "商机阶段", ["全部"] + [_crm.OPP_CN[s] for s in _crm.OPP_ORDER],
                key="adv_opp")
        _f_ctry = st.selectbox(
            "国家", ["全部"] + sorted({str(x["country"]) for x in _all_rows
                                       if x.get("country")}),
            key="adv_ctry")
    if _f_qr != "全部":
        rows = [x for x in rows
                if _ns((x.get("need") or {}).get("readiness") or "") == _f_qr]
    if _f_opp != "全部":
        rows = [x for x in rows
                if _crm.OPP_CN.get(_crm.opp_stage_of_biz(x.get("biz") or ""))
                == _f_opp]
    if _f_ctry != "全部":
        rows = [x for x in rows if str(x.get("country") or "") == _f_ctry]

    # —— 排序（Phase 3 默认「AI 综合排序」= Queue Score，spec 六）——
    # Queue Score = AIP×0.50 + 跟进风险×0.15 + 紧迫度×0.15 + 阶段紧急度×0.20
    # （综合 Priority / Urgency / Overdue / Last Contact / Stage，见 priority3.py）
    # 同分时按业务紧急度（今日待办 > 高优 > 待回复 > 需设跟进 > 缺信息 > 普通 > 已完成）
    def _day_rank(x):
        _a = (x.get("action") or {}).get("type") or ""
        if _a == "FOLLOW_UP_CUSTOMER":
            return 0                                      # 今日需人工处理（到期跟进）
        if x["status"] == STATUS_TODO and x["pri"] == PRI_HIGH:
            return 1                                      # 高优先级
        if x["status"] == STATUS_TODO and not (x["need"] or {}).get("blockers"):
            return 2                                      # 待回复
        if _a == "CREATE_FOLLOW_UP":
            return 3                                      # 需设置跟进时间
        if x["status"] == STATUS_TODO:
            return 4                                      # 缺少关键业务信息
        if x["status"] == STATUS_DONE:
            return 6                                      # 已完成 / 已关闭
        return 5                                          # 普通询盘
    _qs_key = lambda x: (x.get("qs3") if x.get("qs3") is not None
                         else -x.get("pts", 0))
    _sort = st.selectbox(
        "排序", ["AI 综合排序（Queue Score）", "今日待办优先", "待处理优先",
                 "最新询盘", "最久未回复", "报价准备度", "高商机分"],
        key="queue_sort",
        help="默认按 AI Queue Score：AI Priority×0.5 + 跟进风险×0.15 + "
             "紧迫度×0.15 + 阶段紧急度×0.2 综合排队（不只看单一分数）")
    if _sort == "最新询盘":
        rows = sorted(rows, key=lambda x: -x["id"])
    elif _sort == "AI 综合排序（Queue Score）":
        rows = sorted(rows, key=lambda x: (-_qs_key(x), _day_rank(x), -x["id"]))
    elif _sort == "今日待办优先":
        rows = sorted(rows, key=lambda x: (_day_rank(x), -x["pts"], -x["id"]))
    elif _sort == "待处理优先":
        rows = sorted(rows, key=lambda x: (0 if x["status"] == STATUS_TODO else 1,
                                           -x["id"]))
    elif _sort == "最久未回复":
        rows = sorted(rows, key=lambda x: (0 if x["status"] == STATUS_TODO else 1,
                                           str(x["created"]), x["id"]))
    elif _sort == "最久未跟进":
        # 第十轮（spec 十五）：按"最久没有动作"排——跟进时间/回复时间/创建时间
        # 取最早者优先；都没有的排最后（不伪造时间）
        def _fu_key(x):
            _w = x.get("wf") or {}
            for _t in (_w.get("follow_up_at"), _w.get("last_replied_at"),
                       x.get("created")):
                if _t:
                    return (0, str(_t))
            return (1, "")
        rows = sorted(rows, key=lambda x: (_fu_key(x), -x["id"]))
    elif _sort == "报价准备度":
        rows = sorted(rows, key=lambda x: (
            {"quoted": 0, "ready_for_quotation": 1}.get(
                (x["need"] or {}).get("readiness"), 2),
            -(x["score"] or 0), -x["id"]))
    elif _sort == "高商机分":
        rows = sorted(rows, key=lambda x: -(x["score"] or 0))

    # —— 筛选联动（spec 六）：当前选中不在筛选结果里 → 自动选中第一条。
    #    只切换查看对象，不重新分析、不刷新页面 ——
    _vis_ids = [x["id"] for x in rows]
    _cur_sel = st.session_state.get("selected_id")
    if _cur_sel is not None and _vis_ids and _cur_sel not in _vis_ids:
        st.session_state.selected_id = _vis_ids[0]
        st.session_state.analyzed = None
        _cur_sel = _vis_ids[0]
    # 供主工作区「上一个 / 下一个」导航（跟随当前筛选与排序，spec 十九）
    st.session_state["queue_ids"] = _vis_ids

    # —— 队列列表：独立滚动（顶部 搜索/筛选/排序 不随列表滚动）——
    _in_scroll = st.container(height=min(max(len(rows) * 118 + 40, 150), 640),
                              border=False)
    with _in_scroll:
        if not rows:
            # —— 空状态（spec 二十二）：搜索 / 筛选 / 全空 分开提示 ——
            if _q:
                st.markdown("**未找到匹配询盘**")
                st.caption("试试其他客户、公司、产品或询盘关键词")
                # 必须走 on_click 回调：回调在下一次运行前执行，可直接改 widget 值；
                # 脚本体内直接改已实例化 widget 的 key 会报错
                st.button("清除搜索", key="clear_search", use_container_width=True,
                          on_click=_clear_search)
            elif _sel != "全部":
                st.markdown(f"**暂无符合条件的询盘**")
                st.caption(f"当前筛选「{_sel}」下没有询盘，可切换到「全部」查看")
            else:
                st.markdown("**暂无询盘**")
                st.caption("点上方「＋ 新建分析」粘贴一条询盘开始")
        else:
            # —— 客户视觉聚合（spec 十三）：同客户 ≥2 条 → 折叠组。
            #    身份键：customer_id 优先，无则规范化公司名；不用模糊合并。
            #    不删除/不合并/不改任何询盘记录，仅展示层聚合 ——
            for _gk, _gitems in _ui.group_customers(rows):
                if len(_gitems) == 1:
                    _render_queue_card(_gitems[0], _cur_sel)
                    continue
                _gkey = f"{_gk[0]}::{_gk[1]}"
                _opened = (_gkey in st.session_state.setdefault("sq_open", set())
                           or (_cur_sel is not None
                               and _cur_sel in [x["id"] for x in _gitems]))
                _top = _gitems[0]
                _t_need = _top.get("need") or {}
                # —— 第十轮（spec 三/六）：客户级商机 = 从该客户全部询盘派生，
                #    一个客户一个商机；阶段/金额/下一步全部来自真实数据 ——
                _opp = _crm.opportunity_of(_gitems)
                _t_biz = _top.get("biz") or _wf.derive_biz(
                    _top["status"], _t_need.get("blockers") or [],
                    _t_need.get("readiness") or "",
                    (_top.get("wf") or {}).get("biz_status"))
                _t_tone, _ = _wf.BIZ_STYLE.get(_t_biz, ("blue", "🔵"))
                _tone_map = {"NEW": "blue", "QUALIFYING": "amber",
                             "PRODUCT_MATCHING": "blue", "QUOTE_PENDING": "green",
                             "QUOTED": "green", "NEGOTIATING": "amber",
                             "WON": "green", "LOST": "low", "NURTURE": "mid"}
                _t_tone = _tone_map.get(_opp["stage"], _t_tone)
                if _top["status"] == STATUS_DONE and _opp["stage"] not in (
                        "WON", "LOST"):
                    _t_tone = "green"
                _t_who = _top["company"] or _top["contact"] or "未知客户"
                _t_fg = _ui.flag(_top["country"])
                _g_sel = (_cur_sel is not None
                          and _cur_sel in [x["id"] for x in _gitems])
                _g_cls = ("sq ghead " + _t_tone + (" sel" if _g_sel else "")
                          + " clickable")
                # —— 第十轮组卡（spec 四）：公司视角五段布局——
                # ① 状态点+公司名（商机阶段徽章）② 询盘数·待回复数·商机金额
                # ③ 最新需求摘要 ④ 下一步行动（客户级最高优先级）⑤ 国家·时间·AI级
                import html as _vh4
                _g_stage = f"{_opp['emoji']} {_opp['stage_cn']}"
                _g_money = (f" · 金额{_opp['currency_hint']} {_opp['value_txt'][2:]}"
                            if _opp.get("value") else "")
                _g_cnt = (f"{_opp['inquiry_count']} 个询盘 · "
                          f"{_opp['todo_count']} 个待回复{_g_money}")
                # 客户级 Next Action：P0 > P1 > P2，跨询盘挑最高优先级
                _g_act = _opp.get("next_action") or {}
                _g_score = (f"{_top['score']}分 · "
                            f"{(_top['cust_grade'] or _top['grade'] or '')}级"
                            if _top["score"] else "")
                _g_sum = " · ".join(b for b in
                                    [_vh4.escape(str(_t_need.get("intent") or "")),
                                     _vh4.escape(str(_t_need.get("qty") or ""))]
                                    if b)
                _g_meta2 = " · ".join(b for b in
                                      [_t_fg, _top["country"],
                                       "最近 " + _ui.ago(_opp.get("last_seen")
                                                        or _top["created"]),
                                       _g_score] if b)
                # —— 整卡可点击：必须包在独立 st.container() 里（与子卡同模式）——
                # 否则覆盖按钮 absolute 的定位锚点会变成整个队列列表容器，
                # 后绘制的组头覆盖按钮会盖住前面的组头 → 点击"展开"没反应/点错组
                with st.container():
                    st.markdown(
                        f"<div class='{_g_cls}' title='点击展开/收起该客户的全部询盘'>"
                        f"<div class='r1'><span class='sdot'></span>"
                        f"<span class='co'>{_vh4.escape(str(_t_who))}</span>"
                        f"<span class='stx' style='font-size:.68rem'>{_g_stage}</span></div>"
                        f"<div class='r2 need'>{_g_cnt}</div>"
                        + (f"<div class='r2 need'>最新需求：{_g_sum}</div>"
                           if _g_sum else "")
                        + (f"<div class='r2 next'>下一步：<b>"
                           f"{_vh4.escape(str(_g_act['label']))}</b></div>"
                           if _g_act.get("label") else "")
                        + f"<div class='r2 meta'>{_g_meta2}</div>"
                        f"<div class='score'>{'点击收起 ▴' if _opened else '点击展开 ▾'}</div></div>",
                        unsafe_allow_html=True)
                    st.button("", key=f"g_{_gkey}", use_container_width=True,
                              on_click=_toggle_group, args=(_gkey,),
                              help="展开/收起该客户的全部询盘")
                if _opened:
                    for _git in _gitems:
                        _render_queue_card(_git, _cur_sel, child=True)

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["分析询盘", "跟进台", "客户档案", "产品库", "导出"])

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
                    f"{_vh_aq.escape(str(_aq_it.get('cust_grade') or _aq_it.get('grade') or '—'))} 级 · "
                    f"{_aq_it.get('score') or '—'} 分"
                    + (f"　·　{_vh_aq.escape(_aq_reason)}" if _aq_reason else "")
                    + "</div>"
                    + ("<div class='ws-cells' style='margin:.45rem 0 0'>"
                       f"<div class='ws-cell'><div class='lb'>AI Priority</div>"
                       f"<div class='v'>{_aip3 if _aip3 is not None else '—'}</div>"
                       f"<div class='s'>/ 100</div></div>"
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
                    "reply": ("✉️ 打开回复编写器", f"ui_draft_{_aq_it['id']}"),
                    "collect_info": ("📝 打开信息补充回复", f"ui_draft_{_aq_it['id']}"),
                    "create_quote": ("💰 打开报价", f"ui_quote_{_aq_it['id']}"),
                    "follow_up": ("📞 打开跟进", f"ui_fuzone_{_aq_it['id']}"),
                    "schedule_follow_up": ("⏰ 设置跟进时间", f"ui_fuzone_{_aq_it['id']}"),
                    "match_product": ("🔎 产品匹配", None),      # 引导产品库
                    "negotiate": (None, None),                    # 内联表单
                    "mark_complete": (None, None),                # 直接标记完成
                }
                _rq_lbl, _rq_sec = _ROUTE_BTN.get(_route, ("✉️ 打开回复编写器",
                                                           f"ui_draft_{_aq_it['id']}"))
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
        st.caption("ℹ️ 还没有打开的询盘 —— 点上方「＋ 新建询盘分析」粘贴原文开始分析，或在左侧队列里选择一条历史记录。")

# ---------- Tab2: 跟进台（状态 + 优先级排队） ----------
with tab2:
    st.subheader("跟进台：按客户等级自动排优先级")

    with st.expander("ℹ️ 优先级是怎么算的？", expanded=False):
        st.markdown(
            "优先级分数 = **客户等级** + 线索等级×0.6 + 评分×0.1 + 紧急度加分\n\n"
            "| 客户等级 | 加分 |\n|---|---|\n"
            "| A | 50 |\n| B | 35 |\n| C | 20 |\n| D | 5 |\n\n"
            "- 线索等级 A/B/C/D 按上表的 60% 再加一次\n"
            "- 评分每 10 分加 1 分\n"
            "- 紧急度：高 +15，中 +7\n\n"
            "**分档：** ≥65 分 优先处理　·　35~64 分 正常处理　·　<35 分 可延后\n\n"
            "大致对应：A 级客户 + B 级高分客户 → 优先；C 级客户 → 正常；D 级客户 → 可延后。\n\n"
            "已标记跟进的询盘会从队列里退出，需要恢复时再切换状态。"
        )

    all_items = load_queue()
    todo_cnt, done_cnt = count_by_status()
    hi = [i for i in all_items if i["pri"] == PRI_HIGH]
    mid = [i for i in all_items if i["pri"] == PRI_MID]
    low = [i for i in all_items if i["pri"] == PRI_LOW]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("待回复", todo_cnt)
    k2.metric("高优 · 待回复", len(hi))
    k3.metric("中优 · 待回复", len(mid))
    k4.metric("低优 · 待回复", len(low))
    st.divider()

    view = st.radio("查看", [f"待回复（{todo_cnt}）", f"已跟进（{done_cnt}）", "全部"],
                    index=0, horizontal=True, label_visibility="collapsed")
    if view.startswith("待回复"):
        show = [i for i in all_items if i["status"] == STATUS_TODO]
    elif view.startswith("已跟进"):
        show = [i for i in all_items if i["status"] == STATUS_DONE]
    else:
        show = all_items

    if not show:
        st.caption("队列暂无记录 —— 先到「分析询盘」跑一条真实询盘。")
    for it in show:
        id_ = it["id"]
        if it["status"] == STATUS_DONE:
            _tone, _badge = "done", "跟进中"
        elif it["pri"] == PRI_HIGH:
            _tone, _badge = "high", "高优 · 待回复"
        elif it["pri"] == PRI_MID:
            _tone, _badge = "mid", "中优 · 待回复"
        else:
            _tone, _badge = "low", "低优 · 待回复"
        _who = it["company"] or it["country"] or "未知客户"
        _loc = " · ".join(x for x in (it["country"], it["contact"]) if x)
        st.markdown(
            f"<div class='qc {_tone}'><div class='row1'>"
            f"<span class='badge'>{_badge}</span>"
            f"<span class='who'>{_who}</span>"
            + (f"<span class='meta'>{_loc}</span>" if _loc else "")
            + f"</div><div class='meta'>#{id_} · {it['created']}"
            f" · 客户等级 {it['cust_grade'] or it['grade'] or '—'} · 评分 {it['score']}"
            f" · 优先级分 {it['pts']}</div></div>",
            unsafe_allow_html=True)
        _ra, _rb, _rc = st.columns([4.4, 1.4, 1.4])
        with _ra:
            if it["status"] == STATUS_TODO:
                st.caption("查看回复草稿 → 确认发出后回来标记跟进")
            else:
                st.caption("客户有新消息时，可一键转回待回复")
        with _rb:
            if it["status"] == STATUS_TODO:
                st.button("✅ 标记跟进", key=f"q_done_{id_}", use_container_width=True,
                          on_click=_set_status, args=(id_, STATUS_DONE))
            else:
                st.button("🔵 转回待回复", key=f"q_todo_{id_}", use_container_width=True,
                          on_click=_set_status, args=(id_, STATUS_TODO))
        with _rc:
            if st.button("查看详情", key=f"q_view_{id_}", use_container_width=True):
                st.session_state.selected_id = id_
                st.session_state.analyzed = None
                st.toast(f"已选中 #{id_}，请到「📥 分析询盘」查看")

# ---------- Tab3: 客户档案 ----------
with tab3:
    st.subheader("客户档案（按邮箱/公司自动归并去重）")
    # 等级筛选 + 分类计数
    fsel = st.selectbox("按等级筛选 / 归档", ["全部", "A", "B", "C", "D"], index=0)
    filter_grade = None if fsel == "全部" else fsel
    allc = list_customers()
    cnt_by = {"A": 0, "B": 0, "C": 0, "D": 0}
    for r in allc:
        g = r[9] or r[6]            # 手动等级 优先于 线索等级
        if g in cnt_by:
            cnt_by[g] += 1
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("A 级客户", cnt_by["A"])
    m2.metric("B 级客户", cnt_by["B"])
    m3.metric("C 级客户", cnt_by["C"])
    m4.metric("D 级客户", cnt_by["D"])

    custs = list_customers(filter_grade)
    if not custs:
        st.caption("还没有客户。分析询盘后，系统会自动把同一个人/公司归并到一条档案。")
    else:
        for cid, company, country, email, contact, cnt, last_grade, score, seen, \
                grade, note, contacts_json in custs:
            cur_grade = grade or last_grade or "未定"
            with st.expander(f"{GRADE_COLOR.get(cur_grade, '')} {company or email or '未知客户'}　·　{cnt} 条询盘　·　最近 {seen}"):
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

                st.write(f"**最新线索评级**：{GRADE_COLOR.get(last_grade, '')} {last_grade} {score}分")

                # 手动定级（分类归档）
                gidx = ["A", "B", "C", "D"].index(cur_grade) if cur_grade in ("A", "B", "C", "D") else 0
                ng = st.selectbox("客户等级归档", ["A", "B", "C", "D"], index=gidx, key=f"grade_{cid}")
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
                            if st.button("查看", key=f"custview_{iid}", use_container_width=True):
                                st.session_state.selected_id = iid
                                st.session_state.analyzed = None
                                st.rerun()
                    # 最近沟通（spec 九）：该客户全部询盘的 Activity，取最近 5 条
                    _acts = []
                    for iid, *_r in inqs:
                        for _t, _ts, _desc, _actor, _res in list_activity(iid):
                            _acts.append((_ts, iid, _at_cn(_t), _desc))
                    if _acts:
                        _acts.sort(reverse=True)
                        st.write("**最近沟通：**")
                        for _ts, _iid, _tcn, _desc in _acts[:5]:
                            st.caption(f"{_ts} · #{_iid} · {_tcn}"
                                       + (f" · {_desc}" if _desc else ""))
                if st.button("🗑 删除该客户", key=f"delcust_{cid}"):
                    delete_customer(cid)
                    st.rerun()

# ---------- Tab4: 产品库 ----------
with tab4:
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

# ---------- Tab5: 导出 ----------
with tab5:
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
