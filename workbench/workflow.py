# -*- coding: utf-8 -*-
"""询盘销售执行工作流（第七轮）：
业务状态机 + AI Finding → Next Action 映射 + 跟进状态计算。

设计原则（spec 第七轮）：
- 兼容现有两态数据模型（待处理/已处理），业务状态 biz_status 为新增持久化列，
  旧记录无此列时从现有数据派生，绝不改写历史数据；
- AI 判断 / 业务状态 / 报价准备度 三个概念严格分开；
- 状态机禁止非法跳转（如 NEW → WON）；
- 成交 / 丢单只能由人工确认（本模块只定义状态，不自动判定）。
本模块无 Streamlit 依赖，便于单元测试。
"""
import datetime

# ---------------- 业务状态（12 态） ----------------
NEW = "NEW"                          # 新询盘
ANALYZING = "ANALYZING"              # AI 分析中
NEEDS_INFO = "NEEDS_INFO"            # 待补充关键信息
READY_TO_REPLY = "READY_TO_REPLY"    # 待回复
REPLIED = "REPLIED"                  # 已回复（标记为已发送）
FOLLOW_UP = "FOLLOW_UP"              # 待跟进
READY_FOR_QUOTE = "READY_FOR_QUOTE"  # 已具备报价条件
QUOTED = "QUOTED"                    # 已报价
NEGOTIATING = "NEGOTIATING"          # 报价 / 谈判中
WON = "WON"                          # 已成交（人工确认）
LOST = "LOST"                        # 已丢单（人工确认）
ON_HOLD = "ON_HOLD"                  # 暂缓

BIZ_CN = {
    NEW: "新询盘", ANALYZING: "AI分析中", NEEDS_INFO: "待补关键信息",
    READY_TO_REPLY: "待回复", REPLIED: "已回复", FOLLOW_UP: "待跟进",
    READY_FOR_QUOTE: "已具备报价条件", QUOTED: "已报价",
    NEGOTIATING: "谈判中", WON: "已成交", LOST: "已丢单", ON_HOLD: "暂缓",
}

# 展示样式：(css色调, emoji)——与第六轮卡片色系一致
BIZ_STYLE = {
    NEW: ("blue", "🔵"), ANALYZING: ("blue", "🔵"),
    NEEDS_INFO: ("amber", "🟠"), READY_TO_REPLY: ("blue", "🔵"),
    REPLIED: ("green", "🟢"), FOLLOW_UP: ("amber", "🟠"),
    READY_FOR_QUOTE: ("green", "🟢"), QUOTED: ("green", "🟢"),
    NEGOTIATING: ("amber", "🟠"), WON: ("green", "🏆"),
    LOST: ("low", "⚫"), ON_HOLD: ("low", "⏸"),
}

# ---------------- 状态机：合法转换（spec 二十一） ----------------
# 原则：业务动作驱动前进；禁止 NEW→WON、NEEDS_INFO→QUOTED 之类跳变；
# 丢单后允许复活到谈判（业务上常见"死单复活"）；成交/暂缓为终/挂起态。
_ALLOWED = {
    NEW: {ANALYZING, NEEDS_INFO, READY_TO_REPLY, READY_FOR_QUOTE, LOST, ON_HOLD},
    ANALYZING: {NEEDS_INFO, READY_TO_REPLY, READY_FOR_QUOTE, LOST, ON_HOLD},
    NEEDS_INFO: {READY_TO_REPLY, READY_FOR_QUOTE, ANALYZING, REPLIED, LOST, ON_HOLD},
    READY_TO_REPLY: {REPLIED, NEEDS_INFO, ANALYZING, READY_FOR_QUOTE, LOST, ON_HOLD},
    REPLIED: {FOLLOW_UP, READY_FOR_QUOTE, ANALYZING, QUOTED, WON, LOST, ON_HOLD},
    FOLLOW_UP: {REPLIED, ANALYZING, READY_FOR_QUOTE, QUOTED, WON, LOST, ON_HOLD},
    READY_FOR_QUOTE: {QUOTED, REPLIED, FOLLOW_UP, WON, LOST, ON_HOLD},
    QUOTED: {NEGOTIATING, WON, LOST, FOLLOW_UP, ON_HOLD},
    NEGOTIATING: {WON, LOST, QUOTED, ON_HOLD},
    WON: set(),
    LOST: {NEGOTIATING},
    ON_HOLD: {NEW, ANALYZING, NEEDS_INFO, READY_TO_REPLY, REPLIED, FOLLOW_UP,
              READY_FOR_QUOTE, QUOTED, NEGOTIATING, LOST},
}


def can_transition(src: str, dst: str) -> bool:
    """是否允许从 src 跳到 dst（未知状态一律放行，避免旧数据被卡死）。"""
    if src not in _ALLOWED or dst not in BIZ_CN:
        return True
    return dst in _ALLOWED[src]


# ---------------- 兼容派生：旧记录无 biz_status 时 ----------------
def derive_biz(two_state: str, blockers, readiness: str, saved_biz=None,
               has_draft: bool = False):
    """从现有数据派生业务状态（不改两态模型，saved_biz 优先）。

    - 人工保存过的业务状态（REPLIED/QUOTED/WON…）直接用；
    - 已处理（两态）= 旧版「标记跟进」语义 → FOLLOW_UP；
    - 有阻塞 → NEEDS_INFO；
    - 报价准备度 ready_for_quotation → READY_FOR_QUOTE；quoted → QUOTED；
    - 其余待处理 → READY_TO_REPLY。
    """
    if saved_biz in BIZ_CN:
        return saved_biz
    if two_state == "已处理":
        return FOLLOW_UP
    if readiness == "quoted":
        return QUOTED
    if readiness == "ready_for_quotation" and not blockers:
        return READY_FOR_QUOTE
    if blockers:
        return NEEDS_INFO
    return READY_TO_REPLY


# ---------------- AI Finding → Next Action 映射（spec 六） ----------------
# 阻塞项 field（来自 insight.blockers_preliminary / gapcheck key）→ 结构化 Action
BLOCKER_ACTION = {
    "product": ("CONFIRM_PRODUCT", "确认产品"),
    "product_spec": ("CONFIRM_SPECIFICATION", "确认规格"),
    "quantity": ("CONFIRM_QUANTITY", "确认数量"),
    "customization": ("CONFIRM_SPECIFICATION", "确认定制要求"),
    "destination": ("CONFIRM_DESTINATION", "确认目的地"),
    "certification": ("CHECK_CERTIFICATION", "确认认证要求"),
    "delivery": ("CONFIRM_SPECIFICATION", "确认交期"),
    "packaging": ("CONFIRM_SPECIFICATION", "确认包装"),
    "payment": ("CONFIRM_TERMS", "确认付款方式"),
    "incoterm": ("CONFIRM_TERMS", "确认贸易术语"),
    "email": ("CONFIRM_CONTACT", "确认联系方式"),
    "company": ("CONFIRM_CONTACT", "确认公司信息"),
}

ACTION_CN = {
    "CONFIRM_PRODUCT": "确认产品",
    "CONFIRM_QUANTITY": "确认数量",
    "CONFIRM_SPECIFICATION": "确认规格",
    "CONFIRM_DESTINATION": "确认目的地",
    "CONFIRM_TERMS": "确认贸易条件",
    "CONFIRM_CONTACT": "确认联系方式",
    "CHECK_CERTIFICATION": "确认认证要求",
    "CHECK_SAMPLE": "确认样品",
    "SEND_CATALOG": "发送目录",
    "REVIEW_REPLY": "查看并发送回复",
    "SEND_REPLY": "回复客户",
    "CREATE_FOLLOW_UP": "设置跟进时间",
    "FOLLOW_UP_CUSTOMER": "立即跟进",
    "CREATE_QUOTE": "生成报价",
    "FOLLOW_UP_QUOTE": "跟进报价反馈",
    "ANALYZE_REPLY": "分析客户回复",
}


def _intent_action(intent: str):
    """客户明确提出目录 / 样品时给出对应 Action（轻量关键词，不引入 AI 判断）。"""
    low = str(intent or "").lower()
    if "sample" in low:
        return ("CHECK_SAMPLE", ACTION_CN["CHECK_SAMPLE"])
    if "catalog" in low or "catalogue" in low:
        return ("SEND_CATALOG", ACTION_CN["SEND_CATALOG"])
    return None


def next_action(two_state: str, biz: str, blockers, readiness: str,
                has_draft: bool, follow_up_at=None, follow_up_done=False,
                intent: str = "", now=None):
    """计算当前应做的 Next Action（spec 四/五：短、可执行、有唯一主动作）。

    返回 dict：{"type", "label", "priority"}；无需行动时 type 为 None。
    优先级：终态 > 报价后跟进 > 阻塞项 > 到期跟进 > 报价 > 发送回复 > 设置跟进。
    """
    now = now or datetime.datetime.now()
    blockers = blockers or []
    # 1) 终态 / 挂起：无动作
    if biz == WON:
        return {"type": None, "label": "已成交", "priority": ""}
    if biz == LOST:
        return {"type": None, "label": "已丢单", "priority": ""}
    if biz == ON_HOLD:
        return {"type": None, "label": "暂缓", "priority": ""}
    # 2) 已报价 / 谈判中：跟报价反馈
    if biz in (QUOTED, NEGOTIATING):
        return {"type": "FOLLOW_UP_QUOTE",
                "label": ACTION_CN["FOLLOW_UP_QUOTE"], "priority": "P1"}
    # 3) 有阻塞项：映射第一个阻塞（AI Finding → Action）
    if blockers:
        t, lb = BLOCKER_ACTION.get(str(blockers[0]),
                                   ("CONFIRM_SPECIFICATION", "确认信息"))
        return {"type": t, "label": lb, "priority": "P0"}
    # 4) 跟进到期：立即跟进
    due = _follow_due(follow_up_at, follow_up_done, now)
    if due == "已逾期" or due == "今日跟进":
        return {"type": "FOLLOW_UP_CUSTOMER",
                "label": ACTION_CN["FOLLOW_UP_CUSTOMER"], "priority": "P1"}
    # 5) 报价条件满足（产品/数量等已确认且系统判定可报价）
    if readiness == "ready_for_quotation" and biz not in (QUOTED,):
        return {"type": "CREATE_QUOTE",
                "label": ACTION_CN["CREATE_QUOTE"], "priority": "P1"}
    # 6) 已生成草稿且未发送
    if has_draft and two_state == "待处理":
        return {"type": "REVIEW_REPLY",
                "label": ACTION_CN["REVIEW_REPLY"], "priority": "P1"}
    # 7) 已回复但还没设跟进时间
    if biz == REPLIED and not follow_up_at:
        return {"type": "CREATE_FOLLOW_UP",
                "label": ACTION_CN["CREATE_FOLLOW_UP"], "priority": "P2"}
    # 8) 客户明确提出目录 / 样品
    ia = _intent_action(intent)
    if ia:
        return {"type": ia[0], "label": ia[1], "priority": "P2"}
    if two_state == "已处理":
        return {"type": None, "label": "等待客户反馈", "priority": ""}
    return {"type": "SEND_REPLY", "label": ACTION_CN["SEND_REPLY"],
            "priority": "P2"}


def _follow_due(follow_up_at, follow_up_done, now=None):
    """跟进状态：'' / 已完成 / 已逾期 / 今日跟进 / 待跟进。"""
    if follow_up_done:
        return "已完成"
    s = str(follow_up_at or "").strip()
    if not s:
        return ""
    try:
        due = datetime.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return ""
    now = now or datetime.datetime.now()
    if now >= due:
        return "已逾期"
    if due.date() == now.date():
        return "今日跟进"
    return "待跟进"


# 公开别名（UI / 测试共用）
followup_state = _follow_due


def primary_action_type(act: dict) -> str:
    """把 Next Action 映射为详情页 Primary CTA 类别（spec 十九）：
    ask   = 打开英文追问邮件（补信息类）
    reply = 打开回复草稿（发送 / 报价类）
    fu    = 打开跟进设置（跟进类）
    none  = 无主动作
    """
    t = act.get("type")
    if t in ("CONFIRM_PRODUCT", "CONFIRM_QUANTITY", "CONFIRM_SPECIFICATION",
             "CONFIRM_DESTINATION", "CONFIRM_TERMS", "CONFIRM_CONTACT",
             "CHECK_CERTIFICATION", "CHECK_SAMPLE", "SEND_CATALOG"):
        return "ask"
    if t in ("REVIEW_REPLY", "SEND_REPLY", "CREATE_QUOTE", "ANALYZE_REPLY"):
        return "reply"
    if t in ("CREATE_FOLLOW_UP", "FOLLOW_UP_CUSTOMER", "FOLLOW_UP_QUOTE"):
        return "fu"
    return "none"
