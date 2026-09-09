# -*- coding: utf-8 -*-
"""Phase 2 · Customer + Opportunity Workspace 纯逻辑层（无 Streamlit 依赖）。

职责（第十二轮 / CRM 详情工作区升级）：
- Pipeline 七段知识卡（进入条件 / AI 建议 / 完成条件，静态话术，不含客户数据）；
- 漏斗段 → 建议写入的业务状态（biz_status）映射，供「推进到此阶段」使用；
- Activity Timeline 元数据（图标 / 中文名 / 执行人规范）与组装；
- AI Sales Assistant 各字段聚合（当前建议 / 为什么 / 风险 / 下一步）；
- Related Records（相关询盘 / 产品 / 报价 / 联系人）组装；
- 各类 HTML 片段构建（Header / Pipeline / Timeline / Related 卡）。
本模块只做展示层组装：真实状态全部来自调用方传入的数据，绝不自己造数。
"""
import datetime

# ---------------- Pipeline 七段（与 crm.FUNNEL 一致） ----------------
FUNNEL_CN = ["新询盘", "需求确认", "产品匹配", "待报价", "已报价", "谈判", "成交"]

# 漏斗段 → 「推进到此阶段」时应写入的业务执行状态（biz_status）
# 成交(WON) 除外：成交必须人工在结单区二次确认，本卡不直接推进
STAGE_TARGET_BIZ = ["NEW", "NEEDS_INFO", "REPLIED",
                    "READY_FOR_QUOTE", "QUOTED", "NEGOTIATING", "WON"]

# 每段的阶段知识卡（进入条件 / AI建议 / 完成条件）。
# 缺失信息与真实 blockers 在 UI 层动态拼接，不写死在这里。
STAGE_INFO = [
    {"cn": "新询盘", "emoji": "📥",
     "enter": "客户来信首次入库；AI 完成初步分析后即处于本段。",
     "advice": "先看等级/分数判断值不值得投入，再按报价准备度分流：能报价尽早报价，缺信息尽快追问。",
     "complete": "已识别客户与产品方向，且已完成首次回复（或已明确需要补哪些信息）。"},
    {"cn": "需求确认", "emoji": "🟠",
     "enter": "AI 判定存在需向客户确认的关键信息（产品/规格/数量/交期/认证/贸易条件等）。",
     "advice": "用下方英文追问邮件一次性问清 A/B 类缺项；一次别超过 3 问，避免把客户问跑。",
     "complete": "阻塞报价的关键信息已确认，可以从模板推进到回复/报价。"},
    {"cn": "产品匹配", "emoji": "🔍",
     "enter": "已与客户建立沟通（已回复 / 跟进中），进入选品、匹配、索样阶段。",
     "advice": "用产品库给客户对口型号与参数；产品库未命中时，请客户提供参考链接/图片/型号，不要编造产品。",
     "complete": "产品型号/规格基本锁定，客户对候选产品无异议。"},
    {"cn": "待报价", "emoji": "🟢",
     "enter": "报价准备度 = 可正式报价：产品/数量/交期/贸易条件等报价要素已具备。",
     "advice": "生成正式报价（含 MOQ、单价区间、交期、付款方式、认证）。",
     "complete": "报价已创建并确认发送给客户。"},
    {"cn": "已报价", "emoji": "💵",
     "enter": "已向客户发送正式报价单。",
     "advice": "立即设置跟进时间；报价后 2-3 天主动跟进一次反馈。",
     "complete": "客户对价格/条款给出明确回应（接受 / 还价 / 提出调整）。"},
    {"cn": "谈判", "emoji": "🤝",
     "enter": "客户对报价提出调整（价格/数量/交期/付款…），进入讨价还价。",
     "advice": "记录每轮让步条件与底线条款；多轮往返后结合客户质量判断成交可能。",
     "complete": "双方就主要商务条款达成一致，准备确认订单。"},
    {"cn": "成交", "emoji": "🏆",
     "enter": "商务条款全部确认、客户明确下单。",
     "advice": "成交只能由人工在『结单』区二次确认后标记，AI 不自动判成交。",
     "complete": "人工确认成交（进入履约/生产跟进）。"},
]

# ---------------- Activity 元数据 ----------------
# type → (图标, 中文名, 执行人展示修正)。actor_fix=None 表示用记录 actor。
ACT_META = {
    "ANALYZED": ("🤖", "AI 分析", "AI"),
    "REPLY_GENERATED": ("✉️", "生成回复草稿", "AI"),
    "REPLY_EDITED": ("✏️", "修改回复草稿", "销售"),
    "REPLIED": ("📤", "标记已回复", "销售"),
    "EMAIL": ("✉️", "邮件", None),
    "PHONE_CALL": ("📞", "电话", None),
    "MEETING": ("🤝", "会议", None),
    "FOLLOW_UP_CREATED": ("⏰", "创建跟进", "销售"),
    "FOLLOW_UP_COMPLETED": ("✅", "完成跟进", "销售"),
    "QUOTE_CREATED": ("💰", "创建报价", "销售"),
    "QUOTE_SENT": ("💵", "报价已发送", "销售"),
    "STATUS_CHANGE": ("🔄", "阶段变化", "销售"),
    "WON": ("🏆", "成交", "销售"),
    "LOST": ("⚫", "丢单", "销售"),
    "NOTE": ("📝", "备注", None),
}

# 时间线分类（spec 四）：询盘 / 邮件 / 回复 / 电话 / 会议 / 报价 / 跟进 /
# AI分析 / 阶段变化。type → 大类（时间线可筛选展示用）
ACT_CATEGORY = {
    "ANALYZED": "AI分析",
    "REPLY_GENERATED": "邮件", "REPLY_EDITED": "邮件", "EMAIL": "邮件",
    "REPLIED": "回复",
    "PHONE_CALL": "电话", "MEETING": "会议",
    "QUOTE_CREATED": "报价", "QUOTE_SENT": "报价",
    "FOLLOW_UP_CREATED": "跟进", "FOLLOW_UP_COMPLETED": "跟进", "NOTE": "跟进",
    "STATUS_CHANGE": "阶段变化",
    "WON": "成交", "LOST": "丢单",
}
TIMELINE_FILTERS = ["全部", "询盘", "邮件", "回复", "电话", "会议", "报价",
                    "跟进", "AI分析", "阶段变化"]

# 用户手动「记录活动」时可选的活动类型（真实类型，非伪造）
RECORDABLE_TYPES = [("EMAIL", "✉️ 邮件（已发/收到）"),
                    ("PHONE_CALL", "📞 电话沟通"),
                    ("MEETING", "🤝 会议 / 视频"),
                    ("FOLLOW_UP_CREATED", "⏰ 安排跟进"),
                    ("NOTE", "📝 备注 / 其它")]

# ---------------- 工具 ----------------
def act_meta(t: str):
    """取活动展示元数据；未知类型给中性兜底。"""
    return ACT_META.get(t, ("•", t, None))


def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------- Timeline 组装 ----------------
def build_timeline(acts, wf=None, created="", has_draft=False,
                   has_blockers=False, nextstep=""):
    """把 activity 记录组装成展示列表（时间倒序，最新在上）。

    acts: list_activity() 的 5 元组 (type, ts, description, actor, result)。
    wf:   get_workflow() 闭环字段 dict（用于旧记录派生兜底）。
    created: 询盘创建时间；has_draft/has_blockers 仅在兜底行用。
    返回 [{ts, icon, cn, actor, desc, result, cat}]，已按时间倒序。
    绝不伪造历史：没有 activity 的旧记录只派生「收到询盘 / AI 完成分析」两行事实行。
    """
    out = []
    for idx, (t, ts, desc, actor, res) in enumerate(acts or []):
        icon, cn, fix = act_meta(t)
        cat = ACT_CATEGORY.get(t, "其它")
        _actor = fix if fix else (actor or "销售")
        out.append({"ts": ts, "icon": icon, "cn": cn, "cat": cat,
                    "actor": _actor, "desc": (desc or "").strip(),
                    "result": (res or "").strip()})
    # 无任何活动记录 → 从字段派生兜底（有记录时不需要，避免与真实事件重复）
    if not out and created:
        out.append({"ts": str(created)[:16], "icon": "📥", "cn": "收到询盘",
                    "cat": "询盘", "actor": "客户", "desc": "客户询盘入库",
                    "result": ""})
        out.append({"ts": str(created)[:16], "icon": "🤖", "cn": "AI 完成分析",
                    "cat": "AI分析", "actor": "AI",
                    "desc": ("已生成回复草稿" if has_draft
                             else ("发现待补关键信息" if has_blockers
                                   else "完成询盘分析")),
                    "result": ""})
    # 时间倒序（ts 相同时保持业务先后）
    seq = list(enumerate(out))
    seq.sort(key=lambda x: (x[1]["ts"], -x[0]), reverse=True)
    return [x[1] for x in seq]


def stage_knowledge_html(idx: int) -> str:
    """某漏斗段的静态知识卡 HTML（进入条件/AI建议/完成条件）。"""
    if not (0 <= idx < len(STAGE_INFO)):
        return ""
    k = STAGE_INFO[idx]
    return ("<div class='pipe-know'>"
            f"<div class='pk-row'><span class='pk-lb'>进入条件</span>"
            f"<span>{k['enter']}</span></div>"
            f"<div class='pk-row'><span class='pk-lb'>AI 建议</span>"
            f"<span>{k['advice']}</span></div>"
            f"<div class='pk-row'><span class='pk-lb'>完成条件</span>"
            f"<span>{k['complete']}</span></div>"
            "</div>")


# ---------------- 报价 / 产品金额参考 ----------------
def money_text(need: dict, matches: list, currency: str = "USD") -> str:
    """报价参考金额估算：数量 × 产品单价区间（只用于展示参考，绝不当作报价）。"""
    qty = need.get("qty_num")
    if not qty:
        return ""
    parts = []
    for m in (matches or [])[:1]:
        try:
            lo, hi = m["price_range"][0], m["price_range"][1]
        except Exception:
            continue
        parts.append(f"{currency} {int(qty * lo):,} ~ {int(qty * hi):,}"
                     f"（按 {m.get('name_cn') or m.get('name')} 区间估算）")
    return "；".join(parts)
