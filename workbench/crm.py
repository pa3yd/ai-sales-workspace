# -*- coding: utf-8 -*-
"""客户级商机（Opportunity）纯函数模块（第十轮）。

设计原则（spec 三/六/十六）：
- 不建新表、不改状态机：客户级商机 = 从该客户全部询盘的 biz_status / 报价准备度
  派生出的轻量视图，绝不写回任何业务数据；
- 一个客户 = 一个当前商机（外贸业务现实：同一买家的多封询盘围绕同一采购意图）；
- 没有数据就不显示（商机金额只在 数量×目标价 都存在且未丢单时估算）；
- 无 Streamlit 依赖，便于单元测试。
"""
import re

# ---------------- 商机九态（spec 六） ----------------
OPP_NEW = "NEW"
OPP_QUALIFYING = "QUALIFYING"
OPP_MATCHING = "PRODUCT_MATCHING"
OPP_QUOTE_PENDING = "QUOTE_PENDING"
OPP_QUOTED = "QUOTED"
OPP_NEGOTIATING = "NEGOTIATING"
OPP_WON = "WON"
OPP_LOST = "LOST"
OPP_NURTURE = "NURTURE"

OPP_CN = {
    OPP_NEW: "新商机", OPP_QUALIFYING: "需求确认", OPP_MATCHING: "产品匹配",
    OPP_QUOTE_PENDING: "待报价", OPP_QUOTED: "已报价", OPP_NEGOTIATING: "谈判中",
    OPP_WON: "已成交", OPP_LOST: "已丢单", OPP_NURTURE: "长期培育",
}
OPP_EMOJI = {
    OPP_NEW: "🔵", OPP_QUALIFYING: "🟠", OPP_MATCHING: "🔵",
    OPP_QUOTE_PENDING: "🟢", OPP_QUOTED: "🟢", OPP_NEGOTIATING: "🟠",
    OPP_WON: "🏆", OPP_LOST: "⚫", OPP_NURTURE: "🟡",
}

# 活跃阶段推进顺序（越大越靠后）；WON/LOST/NURTURE 为终态不参与排序
OPP_ORDER = [OPP_NEW, OPP_QUALIFYING, OPP_MATCHING, OPP_QUOTE_PENDING,
             OPP_QUOTED, OPP_NEGOTIATING]

# 询盘 12 态业务状态 → 客户商机阶段（复用第七轮状态机词汇，不发明新口径）
_BIZ_TO_OPP = {
    "NEW": OPP_NEW, "ANALYZING": OPP_NEW, "READY_TO_REPLY": OPP_NEW,
    "NEEDS_INFO": OPP_QUALIFYING,
    "REPLIED": OPP_MATCHING, "FOLLOW_UP": OPP_MATCHING,
    "READY_FOR_QUOTE": OPP_QUOTE_PENDING,
    "QUOTED": OPP_QUOTED,
    "NEGOTIATING": OPP_NEGOTIATING,
    "WON": OPP_WON, "LOST": OPP_LOST, "ON_HOLD": OPP_NURTURE,
}

# 详情页 7 段商机状态条（spec 十）：固定流程，当前段高亮
FUNNEL = [("新询盘", OPP_NEW), ("需求确认", OPP_QUALIFYING),
          ("产品匹配", OPP_MATCHING), ("待报价", OPP_QUOTE_PENDING),
          ("已报价", OPP_QUOTED), ("谈判", OPP_NEGOTIATING), ("成交", OPP_WON)]


def opp_stage_of_biz(biz: str) -> str:
    """单条询盘业务状态 → 商机阶段。"""
    return _BIZ_TO_OPP.get(biz or "", OPP_NEW)


def _stage_rank(stage: str) -> int:
    try:
        return OPP_ORDER.index(stage)
    except ValueError:
        return -1  # 终态不参与"最靠前活跃阶段"排序


def opportunity_of(items: list) -> dict:
    """从某客户全部询盘条目（load_queue 输出的 dict）派生客户级商机。

    规则：
    - 阶段 = 全部活跃（未成交/未丢单/未培育）询盘中"推进最靠前"的阶段；
      一个客户只显示一个商机，成交/丢单由活跃询盘的存在与否决定；
    - 无活跃询盘时：有任何成交 → 已成交；否则 → 长期培育（历史客户）；
    - 商机金额 = Σ（数量 × 目标价），仅统计未丢单且两者都存在的询盘，
      任一询盘缺数据就跳过该条（绝不编造）；整体无数据时金额为 None；
    - next_action = 组内最高优先级（P0 > P1 > P2）的询盘动作，平分取优先分高者。
    """
    items = items or []
    active, won, lost = [], False, 0
    for it in items:
        stg = opp_stage_of_biz(it.get("biz") or "")
        if stg in (OPP_WON, OPP_LOST, OPP_NURTURE):
            if stg == OPP_WON:
                won = True
            if stg == OPP_LOST:
                lost += 1
        else:
            active.append((it, stg))

    if not items:
        stage = OPP_NEW
    elif active:
        stage = max((s for _, s in active), key=_stage_rank)
    elif won:
        stage = OPP_WON
    elif lost and lost == len(items):
        stage = OPP_LOST
    else:
        stage = OPP_NURTURE

    # 商机金额（估算，非承诺）：只认真实提取出的数量与目标价
    total, any_val = 0.0, False
    for it in items:
        if opp_stage_of_biz(it.get("biz") or "") == OPP_LOST:
            continue
        need = it.get("need") or {}
        qty, price = need.get("qty_num"), need.get("price_num")
        try:
            q, p = float(qty), float(price)
        except (TypeError, ValueError):
            continue
        if q > 0 and p > 0:
            total += q * p
            any_val = True
    value = round(total) if any_val else None

    # 客户级 Next Action：P0 > P1 > P2；同优先级取优先分（pts）最高
    best = None
    for it in items:
        act = it.get("action") or {}
        if not act.get("label"):
            continue
        rank = {"P0": 0, "P1": 1, "P2": 2}.get(act.get("priority") or "P2", 2)
        key = (rank, -(it.get("pts") or 0))
        if best is None or key < best[0]:
            best = (key, act, it)
    nxt = {"label": best[1]["label"], "priority": best[1].get("priority") or "",
           "inquiry_id": best[2]["id"]} if best else None

    todo = sum(1 for it in items
               if (it.get("biz") or "") == "READY_TO_REPLY")
    last_seen = max((str(it.get("created") or "") for it in items), default="")
    return {"stage": stage, "stage_cn": OPP_CN.get(stage, stage),
            "emoji": OPP_EMOJI.get(stage, "🔵"), "value": value,
            "value_txt": (f"≈ {total:,.0f}" if any_val else ""),
            "currency_hint": _currency_hint(items),
            "inquiry_count": len(items), "todo_count": todo,
            "next_action": nxt, "last_seen": last_seen,
            "active_count": len(active)}


def _currency_hint(items: list) -> str:
    """取组内出现过的目标价币种（默认 USD，只做展示提示，绝不编造）。"""
    for it in items:
        c = str(((it.get("need") or {}).get("target_price_currency")) or "").strip()
        if c:
            return c
    return "USD"


def funnel_position(biz: str) -> int:
    """详情页 7 段状态条：当前业务状态对应的段下标（0~6）。

    已丢单/暂缓等不在漏斗里的状态 → -1（状态条显示"未进入流程"由 UI 处理）。
    """
    stg = opp_stage_of_biz(biz)
    for i, (_, s) in enumerate(FUNNEL):
        if s == stg:
            return i
    return -1


_NUM_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def to_num(v):
    """宽松转数字：'10,000' / '2.80' / 10000 → float；失败返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v).replace(",", ""))
    try:
        return float(m.group()) if m else None
    except ValueError:
        return None
