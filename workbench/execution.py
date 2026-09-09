# -*- coding: utf-8 -*-
"""Phase 4（第十四轮）：Sales Execution Queue 纯逻辑层（无 Streamlit 依赖）。

闭环：AI Priority → Next Best Action → Execute → Activity → Update State
      → Recalculate → Next Customer。

本模块只做三件事（与 app.py 侧边栏口径一字不差地复用同一套规则）：
  1. build_exec_queue：按当前 Filter + Sort 生成执行队列（Current Queue）
  2. route_of：NBA Action Type → 执行入口路由（打开哪个工作区）
  3. is_dup_activity：Activity 去重判断（防重复点击）

不写任何业务数据、不发明新口径：筛选/排序语义与 app.py 侧边栏完全一致。
"""
import datetime

# 与 workflow.py 12 态口径一致（避免循环 import，这里只读常量）
STATUS_TODO = "待处理"
STATUS_DONE = "已处理"

try:                                    # workflow 常量（同目录，纯函数模块）
    import workflow as _wf
    READY_TO_REPLY = _wf.READY_TO_REPLY
    NEEDS_INFO = _wf.NEEDS_INFO
    REPLIED = _wf.REPLIED
    FOLLOW_UP = _wf.FOLLOW_UP
    READY_FOR_QUOTE = _wf.READY_FOR_QUOTE
except Exception:                       # 兜底：状态机缺失时降级（不崩溃）
    READY_TO_REPLY = NEEDS_INFO = REPLIED = FOLLOW_UP = READY_FOR_QUOTE = ""

# calc_priority 的等级词（与 db.calc_priority 输出一致）
PRI_HIGH = "🔴 优先处理"

# 排序选项（与 app.py 侧边栏 selectbox 完全一致）
SORT_DEFAULT = "AI 综合排序（Queue Score）"
SORT_OPTIONS = [SORT_DEFAULT, "今日待办优先", "待处理优先",
                "最新询盘", "最久未回复", "报价准备度", "高商机分"]


# ---------------- Filter（与 app._filter_rows 同口径） ----------------
def filter_items(items, flt):
    """队列筛选口径：与侧边栏 chips / 今日销售任务同一套规则。"""
    if flt == "待回复":
        return [x for x in items if x.get("biz") == READY_TO_REPLY]
    if flt == "待补关键信息":
        return [x for x in items if x.get("biz") == NEEDS_INFO]
    if flt == "高优先级":
        return [x for x in items
                if x.get("status") == STATUS_TODO and x.get("pri") == PRI_HIGH]
    if flt == "已回复":
        return [x for x in items if x.get("biz") == REPLIED]
    if flt == "待跟进":
        return [x for x in items
                if x.get("biz") == FOLLOW_UP
                or (x.get("action") or {}).get("type")
                in ("FOLLOW_UP_CUSTOMER", "CREATE_FOLLOW_UP")]
    if flt == "待报价":
        return [x for x in items
                if x.get("status") == STATUS_TODO
                and x.get("biz") == READY_FOR_QUOTE]
    if flt == "超48小时":
        _sline = (datetime.datetime.now()
                  - datetime.timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
        return [x for x in items
                if x.get("status") == STATUS_TODO
                and str(x.get("created") or "") <= _sline]
    return list(items)


# ---------------- Sort（与 app.py 侧边栏排序同一套规则） ----------------
def day_rank(x):
    """同分业务紧急度：今日待办 > 高优 > 待回复 > 需设跟进 > 缺信息 > 普通 > 已完成"""
    _a = (x.get("action") or {}).get("type") or ""
    if _a == "FOLLOW_UP_CUSTOMER":
        return 0
    if x.get("status") == STATUS_TODO and x.get("pri") == PRI_HIGH:
        return 1
    if x.get("status") == STATUS_TODO and not (x.get("need") or {}).get("blockers"):
        return 2
    if _a == "CREATE_FOLLOW_UP":
        return 3
    if x.get("status") == STATUS_TODO:
        return 4
    if x.get("status") == STATUS_DONE:
        return 6
    return 5


def _qs_key(x):
    return x.get("qs3") if x.get("qs3") is not None else -x.get("pts", 0)


def sort_items(items, sort):
    """排序语义与侧边栏 selectbox 一致（Phase 3 Queue Score 默认）。"""
    if sort == "最新询盘":
        return sorted(items, key=lambda x: -x["id"])
    if sort == SORT_DEFAULT:
        return sorted(items, key=lambda x: (-_qs_key(x), day_rank(x), -x["id"]))
    if sort == "今日待办优先":
        return sorted(items, key=lambda x: (day_rank(x), -x.get("pts", 0), -x["id"]))
    if sort == "待处理优先":
        return sorted(items, key=lambda x: (0 if x.get("status") == STATUS_TODO else 1,
                                            -x["id"]))
    if sort == "最久未回复":
        return sorted(items, key=lambda x: (0 if x.get("status") == STATUS_TODO else 1,
                                            str(x.get("created")), x["id"]))
    if sort == "报价准备度":
        return sorted(items, key=lambda x: (
            {"quoted": 0, "ready_for_quotation": 1}.get(
                (x.get("need") or {}).get("readiness"), 2),
            -(x.get("score") or 0), -x["id"]))
    if sort == "高商机分":
        return sorted(items, key=lambda x: -(x.get("score") or 0))
    return list(items)


def build_exec_queue(items, flt, sort=SORT_DEFAULT):
    """Current Queue = filter + sort 后的询盘 id 列表（按当前 Filter/Sort/Status）。

    执行队列语义 = 待处理任务队列：两态「已处理」的记录不进入
    （它们没有待执行的 Next Action；查看类需求走侧边栏队列，不走执行模式）。
    """
    rows = [x for x in filter_items(items, flt)
            if x.get("status") != STATUS_DONE]
    return [x["id"] for x in sort_items(rows, sort)]


# ---------------- Action Type → 执行路由（spec 三/五） ----------------
# route_key → (按钮文案, 打开的工作区 section)
#   draft     = 回复编写器（Reply Composer，含信息补充回复）
#   quote     = 报价区
#   followup  = 跟进区（设时间 / 完成跟进）
#   product   = 产品匹配（引导产品库）
#   negotiate = 谈判记录（执行队列内联表单）
#   done      = 标记完成
ROUTE_CN = {
    "reply": "回复编写器", "collect_info": "信息补充回复",
    "create_quote": "报价", "follow_up": "跟进",
    "schedule_follow_up": "设置跟进", "match_product": "产品匹配",
    "negotiate": "谈判记录", "mark_complete": "标记完成",
}

# NBA Action Type → route_key（priority3.ACT_TYPE_CN 的八类全覆盖）
ACT_ROUTE = {
    "REPLY": "reply",
    "COLLECT_INFO": "collect_info",
    "MATCH_PRODUCT": "match_product",
    "CREATE_QUOTE": "create_quote",
    "FOLLOW_UP": "follow_up",
    "NEGOTIATE": "negotiate",
    "SCHEDULE_FOLLOW_UP": "schedule_follow_up",
    "MARK_COMPLETE": "mark_complete",
}

# 完成按钮文案（spec 五：下一条）
COMPLETION_LABEL = {
    "reply": "✉️ 已发送回复 · 下一条",
    "collect_info": "✉️ 已发送信息补充回复 · 下一条",
    "create_quote": "✅ 完成此条 · 下一条",
    "follow_up": "📞 已完成跟进 · 下一条",
    "schedule_follow_up": "⏰ 已设置跟进 · 下一条",
    "match_product": "✅ 已确认产品匹配 · 下一条",
    "negotiate": "🤝 已记录谈判 · 下一条",
    "mark_complete": "✅ 标记完成 · 下一条",
}


def route_of(action_type: str) -> str:
    """NBA Action Type → route_key（未知类型兜底为 reply —— 打开回复编写器）。"""
    return ACT_ROUTE.get(action_type or "", "reply")


def completion_label(route_key: str) -> str:
    return COMPLETION_LABEL.get(route_key, "✅ 完成此条 · 下一条")


# ---------------- 数据一致性：Activity 去重 ----------------
def is_dup_activity(acts, type_: str, minutes: int = 2) -> bool:
    """acts 为 db.list_activity 输出（(type, ts, desc, actor, result) 正序）。
    window_min 内已有同类型事件 → 判定重复（防重复 Activity）。"""
    cut = (datetime.datetime.now()
           - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    for t, ts, *_ in (acts or [])[-10:]:
        if t == type_ and str(ts or "") >= cut:
            return True
    return False
