# -*- coding: utf-8 -*-
"""询盘队列 UI 展示辅助函数（无 Streamlit 依赖，便于单元测试）。"""
import datetime

# 常用国家 → 国旗 emoji（仅展示，缺省不给旗，绝不虚构）
FLAG_MAP = {
    "united kingdom": "🇬🇧", "uk": "🇬🇧", "britain": "🇬🇧", "england": "🇬🇧",
    "英国": "🇬🇧", "germany": "🇩🇪", "德国": "🇩🇪", "united states": "🇺🇸",
    "usa": "🇺🇸", "us": "🇺🇸", "美国": "🇺🇸", "france": "🇫🇷", "法国": "🇫🇷",
    "sweden": "🇸🇪", "瑞典": "🇸🇪", "australia": "🇦🇺", "澳大利亚": "🇦🇺",
    "canada": "🇨🇦", "加拿大": "🇨🇦", "netherlands": "🇳🇱", "荷兰": "🇳🇱",
    "spain": "🇪🇸", "西班牙": "🇪🇸", "italy": "🇮🇹", "意大利": "🇮🇹",
    "japan": "🇯🇵", "日本": "🇯🇵", "korea": "🇰🇷", "韩国": "🇰🇷",
    "brazil": "🇧🇷", "巴西": "🇧🇷", "mexico": "🇲🇽", "墨西哥": "🇲🇽",
    "india": "🇮🇳", "印度": "🇮🇳", "russia": "🇷🇺", "俄罗斯": "🇷🇺",
    "poland": "🇵🇱", "波兰": "🇵🇱", "norway": "🇳🇴", "挪威": "🇳🇴",
    "denmark": "🇩🇰", "丹麦": "🇩🇰", "finland": "🇫🇮", "芬兰": "🇫🇮",
    "switzerland": "🇨🇭", "瑞士": "🇨🇭", "uae": "🇦🇪", "dubai": "🇦🇪",
    "saudi": "🇸🇦", "turkey": "🇹🇷", "越南": "🇻🇳", "vietnam": "🇻🇳",
}


def flag(country) -> str:
    """国家名 → 国旗 emoji；识别不出返回空串（不硬造）。"""
    low = str(country or "").strip().lower()
    if not low:
        return ""
    for k, v in FLAG_MAP.items():
        if k in low:
            return v
    return ""


def ago(created, now=None) -> str:
    """把 created_at 转成「N分钟前」这类相对时间（仅展示用）。
    规则：刚刚 / 12分钟前 / 2小时前 / 昨天 / 3天前 / 超过7天显示「9月2日」。
    完整时间由卡片 title 悬停展示。now 参数仅供测试注入。
    """
    s = str(created or "")
    try:
        dt = datetime.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return s[:10] if s else ""
    ref = now or datetime.datetime.now()
    m = int((ref - dt).total_seconds() // 60)
    if m < 1:
        return "刚刚"
    if m < 60:
        return f"{m}分钟前"
    if m < 60 * 24:
        return f"{m // 60}小时前"
    d = m // (60 * 24)
    if d == 1:
        return "昨天"
    if d < 7:
        return f"{d}天前"
    return f"{dt.month}月{dt.day}日"


def wait(created, now=None) -> str:
    """「等待处理时长」（第六轮 UI 新增，仅展示用，业务口径不带"前"字）：
    刚刚 / 32分钟 / 5小时 / 2天 / 7天+。
    规则与 spec 十一致：不精确到秒，>7 天不再数天（业务上已属积压，
    完整时间仍由卡片 title 悬停展示）。now 参数仅供测试注入。
    """
    s = str(created or "")
    try:
        dt = datetime.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return ""
    ref = now or datetime.datetime.now()
    m = int((ref - dt).total_seconds() // 60)
    if m < 0:
        m = 0
    if m < 1:
        return "刚刚"
    if m < 60:
        return f"{m}分钟"
    h = m // 60
    if h < 24:
        return f"{h}小时"
    d = m // (60 * 24)
    if d < 7:
        return f"{d}天"
    return "7天+"


def customer_key(cust_id=None, company=None, contact=None, id_=None):
    """客户聚合身份键（第六轮 UI，仅展示聚合用，不改数据）。

    可靠性优先级：customer_id > 规范化公司名 > 联系人 > 询盘自身。
    规范化 = 去首尾 + 压缩连续空白 + 小写；绝不用模糊/AI 相似度合并。
    返回 (类型, 值) 元组，可直接当 dict 键。
    """
    if cust_id:
        try:
            return ("cid", int(cust_id))
        except Exception:
            pass
    c = " ".join(str(company or "").split()).lower()
    if c:
        return ("co", c)
    n = str(contact or "").strip().lower()
    if n:
        return ("nm", n)
    return ("id", id_)


def deal_customer_key(cust_id=None, company=None, contact=None, id_=None):
    """Deal 展示聚合用客户键。

    销售队列服务的是“今天处理哪个客户的哪个商机”，不是展示数据库客户表
    主键。同一公司被多次分析生成多个 customer_id 时，应在 Deal 队列里
    合并为同一公司；客户档案数据不在这里合并或改写。
    """
    c = " ".join(str(company or "").split()).lower()
    if c:
        return ("co", c)
    return customer_key(cust_id, company, contact, id_)


def group_customers(items):
    """把队列条目按客户身份聚合，保持既有排序（首次出现顺序）。

    items: 带 id / company / contact / cust_id 键的字典列表（load_queue 的输出）。
    返回 [(key, [items...])]；客户只有一条询盘时组长度为 1（UI 层不显示"N个询盘"）。
    """
    ordered = {}
    for it in items:
        k = customer_key(it.get("cust_id"), it.get("company"),
                         it.get("contact"), it.get("id"))
        ordered.setdefault(k, []).append(it)
    return list(ordered.items())


# =====================================================================
# 第十七轮 · 销售工作队列展示聚合（Sales Home）
# 目标：首页一个"可执行的工作项 / Deal"一行，而不是一条历史询盘一行。
# 硬约束：只读展示聚合 —— 不合并、不删除、不改写任何业务数据；
#         被折叠的历史询盘仍然可在 Deal 详情的 Timeline / 相关询盘里逐条打开。
# =====================================================================

# 产品短语里对"是不是同一件事"没有区分度的词（数字/单位/修饰语）
_SIG_STOP = {
    "with", "and", "or", "the", "a", "an", "of", "for", "in", "on", "to",
    "custom", "customized", "logo", "packaging", "package", "please", "new",
    "pcs", "pc", "pcs.", "units", "unit", "sets", "set", "pieces", "piece",
    "black", "white", "color", "colour", "size", "oem", "odm",
}


def product_signature(text, n: int = 4) -> str:
    """产品短语 → 稳定签名（只读，用于判断"是否同一件事"）。

    取前 n 个有区分度的实词：忽略数字、单位、颜色/包装等修饰语。
    例：
      "Wireless ANC earbuds with Bluetooth 5.4, ANC, 40h battery…"
      "Wireless ANC earbuds with Bluetooth 5.4, ANC, 40 hours battery…"
      → 两者签名都是 "wireless anc earbuds"（同一 Deal 的两次往来）
      "stainless steel water bottles" → "stainless steel water bottles"

    保守策略：签名不同就拆成两行（宁可不合并，也不误并两个 Deal）。
    """
    import re as _re
    toks = _re.findall(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-]*",
                       str(text or "").lower())
    keep = []
    for t in toks:
        if not t or t in _SIG_STOP:
            continue
        if _re.fullmatch(r"[\d\.\-]+", t):      # 纯数字（5.4 / 40 / 500）
            continue
        if t.endswith(("h", "ml", "mm", "cm", "kg", "g")) and \
                _re.match(r"^[\d\.]+", t):      # 40h / 500ml 这类带单位数字
            continue
        keep.append(t)
        if len(keep) >= n:
            break
    return " ".join(keep)


def _created_key(item: dict):
    """用于判断最新客户明确信息；失败时回落 id。"""
    return (str((item or {}).get("created") or ""),
            int((item or {}).get("id") or 0))


def _clean_fact(value) -> str:
    return " ".join(str(value or "").split())


def resolved_requirement(group: dict) -> dict:
    """同一 Deal Thread 的客户需求单一视图。

    历史往来可能包含重新询盘或数量更新。这里不删除历史，也不静默覆盖，
    而是给出最新明确需求 + 变更历史：
      quantity          = 最新明确数量
      quantityHistory   = 所有不同数量（按时间）
      quantityChanged   = 是否出现多个不同数量
      changedFields     = 目前先覆盖 quantity，后续可扩展 targetPrice/spec
      sourceInquiryId   = 当前最新明确需求来自哪条询盘
    """
    items = list((group or {}).get("items") or [])
    ordered = sorted(items, key=_created_key)
    product, qty, source_id = "", "", None
    qty_history = []
    product_history = []
    for it in ordered:
        need = it.get("need") or {}
        p = _clean_fact(need.get("product_query")
                        or need.get("product")
                        or need.get("product_cat"))
        q = _clean_fact(need.get("qty"))
        if p:
            product = p
            product_history.append(p)
            source_id = it.get("id")
        if q:
            qty = q
            qty_history.append(q)
            source_id = it.get("id")
    distinct_qty = []
    for q in qty_history:
        if q and q not in distinct_qty:
            distinct_qty.append(q)
    distinct_product = []
    for p in product_history:
        if p and p.lower() not in [x.lower() for x in distinct_product]:
            distinct_product.append(p)
    changed = []
    if len(distinct_qty) > 1:
        changed.append("quantity")
    if len(distinct_product) > 1:
        changed.append("product_description")
    current_qty_value = _quantity_number(qty)
    previous_qty_value = _quantity_number(distinct_qty[-2]) if len(distinct_qty) > 1 else None
    return {
        "product": product,
        "quantity": qty,
        "currentQuantity": current_qty_value,
        "previousQuantity": previous_qty_value,
        "quantityEvolution": "REVISION" if len(distinct_qty) > 1 else "UNCHANGED",
        "quantityConflict": False,
        "sourceInquiryId": source_id,
        "quantityHistory": distinct_qty,
        "productHistory": distinct_product,
        "quantityChanged": len(distinct_qty) > 1,
        "changedFields": changed,
    }


def supplier_recommendation_requested(text: str) -> bool:
    """客户请求供应商推荐方案，不等于客户缺信息。"""
    import re as _re
    low = str(text or "").lower()
    return bool(_re.search(
        r"please\s+(?:recommend|suggest|advise)|(?:would\s+like|need|want)\s+(?:your\s+)?(?:recommendation|suggestion|advice)|"
        r"which\s+(?:option|model|solution).{0,40}(?:recommend|suitable|better)|"
        r"recommend\s+the\s+suitable|suitable\s+(?:option|model|solution)|which\s+option\s+would\s+be\s+better",
        low))


def customer_requested_outcome_from_text(text: str):
    """客户明确要求的结果；只看客户文本，不用内部状态猜。

    单一结果保持字符串，复合请求返回列表，调用方使用 contains 判断。
    """
    import re as _re
    low = str(text or "").lower()
    if _re.search(r"\b(updated|revised|new)\s+quot(?:e|ation)\b|"
                  r"\bquot(?:e|ation).{0,24}\b(updated|revised)\b", low):
        return "REQUEST_UPDATED_QUOTATION"
    outcomes = []
    if supplier_recommendation_requested(text):
        outcomes.append("REQUEST_PRODUCT_RECOMMENDATION")
    if _re.search(r"\bquot(?:e|ation)|best\s+price|price\s+list\b", low):
        outcomes.append("REQUEST_QUOTATION")
    if _re.search(r"\bsamples?\b", low):
        outcomes.append("REQUEST_SAMPLE")
    if _re.search(r"\b(ce|rohs|certificat\w*|test\s+report|documentation)\b", low):
        outcomes.append("REQUEST_DOCUMENTS")
    if _re.search(r"\bdiscount|price\s+reduction|lower\s+price\b", low):
        outcomes.append("REQUEST_PRICE_REDUCTION")
    if _re.search(r"\bdelivery|lead\s*time|ship(?:ment|ping)?\b", low):
        outcomes.append("REQUEST_DELIVERY_CONFIRMATION")
    if _re.search(r"\bconfirm.{0,20}(spec|requirement)|spec.{0,20}confirm\b", low):
        outcomes.append("REQUEST_SPEC_CONFIRMATION")
    if _re.search(r"\bnegotia|counter\s*offer|target\s+price\b", low):
        outcomes.append("REQUEST_NEGOTIATION")
    outcomes = list(dict.fromkeys(outcomes))
    if not outcomes:
        return ""
    return outcomes[0] if len(outcomes) == 1 else outcomes


def _quantity_number(value):
    import re as _re
    s = str(value or "").replace(",", "")
    m = _re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        n = float(m.group(0))
        return int(n) if n == int(n) else n
    except Exception:
        return None


def requirement_buckets(info: dict = None, missing: list = None,
                        matches: list = None, text: str = "") -> dict:
    """BLOCKING / OPTIONAL_ENHANCEMENT / INTERNAL_PREREQUISITE 三分法。"""
    info = info or {}
    missing = missing or []
    matches = matches or []
    low = str(text or "").lower()
    has_product = bool(info.get("product_query") or info.get("intent")
                       or info.get("product") or info.get("product_cat"))
    has_quantity = bool(info.get("quantity") or info.get("qty"))
    spec_signals = sum(bool(x) for x in (
        info.get("specification"), info.get("customization"),
        info.get("certification"), info.get("target_price"),
        info.get("incoterm"), info.get("destination"), info.get("country"),
    ))
    text_spec_signals = sum(1 for pat in (
        r"bluetooth\s*\d", r"\banc\b|active noise cancellation",
        r"\b\d+\s*(?:hours?|h)\b", r"black|white|colou?r",
        r"custom\s+logo|retail\s+packag", r"\bfob\b|\bcif\b|\bexw\b",
        r"\bhamburg|germany\b", r"\bce\b|\brohs\b", r"\bsamples?\b",
        r"target\s+(?:fob\s+)?price|usd\s*[\d\.]+",
    ) if __import__("re").search(pat, low, __import__("re").I))
    detailed_enough = spec_signals >= 3 or text_spec_signals >= 5

    customer_blocking, optional, internal = [], [], []
    optional_keys = {"reference_model", "reference_photo", "design_reference",
                     "competitor", "company_scale", "annual_volume", "website",
                     "linkedin", "email", "packaging_reference"}
    for m in missing:
        key = str(m.get("key") or "")
        q = str(m.get("question") or "").lower()
        is_ref = key in {"product_spec", "reference_model"} and (
            "reference" in q or "photo" in q or "design" in q)
        if key in optional_keys or is_ref:
            optional.append({**m, "requirement_type": "OPTIONAL_ENHANCEMENT"})
        elif m.get("ask_timing") == "ASK_NOW":
            customer_blocking.append({**m, "requirement_type": "BLOCKING"})
        else:
            optional.append({**m, "requirement_type": "OPTIONAL_ENHANCEMENT"})

    if detailed_enough:
        customer_blocking = [
            m for m in customer_blocking
            if str(m.get("key")) not in {"product_spec", "reference_model"}
        ]
    if not has_product:
        customer_blocking.insert(0, {"key": "product", "name": "产品",
                                     "requirement_type": "BLOCKING"})
    if not has_quantity:
        customer_blocking.insert(0, {"key": "quantity", "name": "数量",
                                     "requirement_type": "BLOCKING"})
    product_match = "MATCHED" if matches else "UNRESOLVED"
    if product_match != "MATCHED":
        internal.append({"key": "VERIFY_PRODUCT_OPTION",
                         "name": "确认产品方案",
                         "requirement_type": "INTERNAL_PREREQUISITE"})
    if supplier_recommendation_requested(text or info.get("raw_text") or ""):
        internal.append({"key": "CHECK_LID_OPTIONS",
                         "name": "确认盖子方案",
                         "requirement_type": "INTERNAL_PREREQUISITE"})
    for key, name in (
        ("CHECK_PRODUCT_OPTIONS", "确认产品方案"),
        ("CHECK_COST", "确认成本"),
        ("CHECK_SAMPLE_AVAILABILITY", "确认样品可用性"),
        ("CHECK_SAMPLE_COST", "确认样品费用"),
        ("CHECK_LEAD_TIME", "确认生产交期"),
        ("CHECK_CUSTOMIZATION_COST", "确认定制成本"),
        ("CHECK_COMPLIANCE_DOCUMENTS", "核验证书文件"),
    ):
        internal.append({"key": key, "name": name,
                         "requirement_type": "INTERNAL_PREREQUISITE"})
    completeness = "HIGH" if has_product and has_quantity and detailed_enough else (
        "MEDIUM" if has_product and has_quantity else "LOW")
    return {"customerBlockingItems": customer_blocking,
            "optionalEnhancements": optional,
            "internalPrerequisites": internal,
            "requirementCompleteness": completeness}


def work_item_key(item: dict, stage_of: dict = None):
    """工作项身份键（展示聚合用，不写任何数据）。

    组合信号（缺任一信号时自动降级为更严格的键，绝不只用公司名合并）：
      1) Deal 客户身份：规范化公司名 > customer_id > 联系人
      2) 产品/品类签名：product_query → 产品库匹配名 → 品类
      3) 当前阶段：商机 stage（有商机记录时）> 待处理/已处理
    产品短语缺失 → 该条单独成行（按 id 隔离），避免把两个未知产品的 Deal 误并。
    """
    it = item or {}
    cust = deal_customer_key(it.get("cust_id"), it.get("company"),
                             it.get("contact"), it.get("id"))
    need = it.get("need") or {}
    sig = product_signature(need.get("product_query")
                            or need.get("product")
                            or need.get("product_cat")
                            or need.get("intent") or "")
    stage = (stage_of or {}).get(it.get("id")) or (
        "active" if it.get("status") != "已处理" else "closed")
    if not sig:
        return ("inq", cust, stage, it.get("id"))
    return ("deal", cust, sig, stage)


def group_work_items(items, stage_of: dict = None):
    """把队列条目聚合成"一个可执行工作项一行"（只读，保序 = 首次出现顺序）。

    返回 [{"key", "items": [...], "lead": item, "count": n}]：
      lead = 组内最该先处理的一条（优先级分高者优先，其次最新）。
    历史询盘完整保留在 items 里，UI 必须提供入口逐条打开。
    """
    ordered = {}
    for it in items or []:
        k = work_item_key(it, stage_of)
        ordered.setdefault(k, []).append(it)
    groups = []
    for k, bucket in ordered.items():
        lead = sorted(bucket, key=lambda x: (-(x.get("pts") or 0), -(x.get("id") or 0)))[0]
        groups.append({"key": k, "items": bucket, "lead": lead,
                       "count": len(bucket)})
    return groups


def priority_tier(item) -> str:
    """优先级分级 P1 / P2 / P3（仅展示，不改动任何评分口径）。

    P1 优先处理 · P2 正常处理 · P3 可延后 · 已完成不显示等级。
    原始分仍在详情页 / Why / AI 依据里保留（分数给 AI 解释性，等级给决策）。
    """
    pri = str((item or {}).get("pri") or "")
    if "优先处理" in pri:
        return "P1"
    if "正常处理" in pri:
        return "P2"
    if "可延后" in pri:
        return "P3"
    if "已完成" in pri:
        return "—"
    return "P2"


# =====================================================================
# 第二十二轮 · 左侧 Sidebar Deal 聚合（Deal Threading）
# 目标：同一客户 + 同一产品项目的多封往来，Sidebar 只渲染一张主卡；
#       同客户 + 不同产品项目 仍是两张独立 Deal 卡。
# 硬约束：纯只读展示聚合 —— 不删除、不合并、不改写任何历史记录；
#         Deal 内历史往来仍可展开逐条打开，完整历史保留在详情 Timeline。
# =====================================================================

# 终态/长期培育：与「新的活跃往来」分开成卡，避免把已结束的旧 Deal
# 和同产品的全新询盘并成一张卡（宁可不合并，也不误并）。
_CLOSED_STAGE = {"WON", "LOST", "NURTURE", "ON_HOLD"}
_CLOSED_BIZ = {"WON", "LOST", "ON_HOLD"}


def deal_thread_key(item: dict, stage_of: dict = None) -> tuple:
    """Deal / Conversation Thread 展示身份键（只读，不写任何数据）。

    组合信号（缺任一信号自动降级，绝不只靠公司名或产品名合并）：
      1) Deal 客户身份：规范化公司名 > customer_id > 联系人
      2) 产品签名：product_signature —— 同一产品项目的多封往来同一把键
      3) 活跃/已闭环桶：商机终态（WON/LOST/NURTURE/ON_HOLD，来自
         opportunities.stage）或业务终态（WON/LOST/ON_HOLD）的历史往来
         与新的活跃往来分开成卡
    产品短语缺失 → 退回按单条询盘隔离（宁可不合并，不误并两个 Deal）。
    """
    it = item or {}
    cust = deal_customer_key(it.get("cust_id"), it.get("company"),
                             it.get("contact"), it.get("id"))
    need = it.get("need") or {}
    sig = product_signature(need.get("product_query")
                            or need.get("product")
                            or need.get("product_cat")
                            or need.get("intent") or "")
    if not sig:
        return ("raw", cust, it.get("id"))
    stg = str(((stage_of or {}).get(it.get("id")) or "")).upper()
    biz = str(it.get("biz") or "").upper()
    closed = stg in _CLOSED_STAGE or biz in _CLOSED_BIZ
    return ("deal", cust, sig, "closed" if closed else "open")


def group_deal_threads(items: list, stage_of: dict = None) -> list:
    """把队列条目按「Deal / Conversation Thread」聚合（只读，保序）。

    输入 items 已按队列排序（Queue Score 等）。同一个 Deal 只出现一次：
      lead  = 该 Deal 内排序最靠前（最该先处理）的条目 —— 卡片状态 /
              优先级 / Next Best Action 一律取自 lead（最新可执行口径），
              绝不用历史某条旧 Inquiry 的状态冒充当前状态；
      items = 该 Deal 全部往来（历史完整保留，UI 展开后逐条可见）。
    返回 [{"key", "items", "lead", "count"}]。
    """
    ordered = {}
    for it in items or []:
        k = deal_thread_key(it, stage_of)
        ordered.setdefault(k, []).append(it)
    groups = []
    for k, bucket in ordered.items():
        groups.append({"key": k, "items": bucket,
                       "lead": bucket[0], "count": len(bucket)})
    return groups


def deal_current_state(group: dict) -> dict:
    """单个 Deal 组的「当前工作状态」（第 22 轮：Sidebar 主卡唯一状态来源）。

    只读派生，全部来自 lead（组内排序最靠前的可执行往来）+ 组内聚合，
    调用方不得再各自拼接历史数据判断当前状态：
      biz / nba / nba_type / tier(优先级) / fu_state / overdue
      last_created(组内最近往来时间) / conversation_count /
      open_count(待处理往来数) / lead_id / lead_status
    """
    g = group or {}
    lead = g.get("lead") or {}
    items = g.get("items") or []
    act = lead.get("action") or {}
    fu = str(lead.get("fu_state") or "")
    created_vals = [str(x.get("created") or "") for x in items]
    last = max(created_vals) if created_vals else str(lead.get("created") or "")
    return {
        "biz": lead.get("biz") or "",
        "nba": act.get("label") or "",
        "nba_type": act.get("type") or "",
        "tier": priority_tier(lead),
        "fu_state": fu,
        "overdue": fu == "已逾期",
        "last_created": last,
        "conversation_count": len(items),
        "open_count": sum(1 for x in items
                          if x.get("status") != "已处理"),
        "lead_id": lead.get("id"),
        "lead_status": lead.get("status") or "",
    }


# =====================================================================
# 第二十三轮 · 统一 Deal Work View Model（Deal-level 单一数据源）
# Sidebar / 今日优先处理 / WHY NOW / 销售工作队列 全部读取同一份
# DealWorkItem —— ONE ACTIONABLE DEAL = ONE WORK ITEM。组件禁止再
# 各自拼接历史消息判断当前状态（状态/数量/Next Action/重复 不一致的根因）。
# 纯只读派生，不写任何业务数据；有商机关联时以「商机阶段 + 真实跟进任务」
# 为准，无商机的新询盘回落消息级业务状态（与 R22 Sidebar 同口径）。
# =====================================================================

# 商机阶段 → (emoji, tone)：NEW 蓝 / 确认需求 橙 / 已报价 绿 / 风险橙 /
# 逾期红 / 已闭环灰 —— 颜色只表达阶段与紧迫度，「选中 = 蓝」由 .sel 负责，
# 红色只用于 urgency（逾期/高优），蓝色绝不表达 urgency。
_DEAL_STAGE_META = {
    "NEW": ("🔵", "blue"), "QUALIFIED": ("🔵", "blue"),
    "REQUIREMENT_CONFIRMED": ("🟠", "amber"), "QUOTED": ("🟢", "green"),
    "SAMPLE": ("🧪", "amber"), "NEGOTIATION": ("🟣", "amber"),
    "PO_PENDING": ("📦", "green"), "WON": ("🏆", "green"),
    "LOST": ("⚫", "gray"),
}


def deal_stage_meta(stage: str):
    """商机阶段 → (emoji, tone)；未知名阶段回落蓝色（视为新询盘）。"""
    _s = str(stage or "").upper()
    return _DEAL_STAGE_META.get(_s, ("🔵", "blue"))


def has_overdue_task(tasks, now=None) -> bool:
    """该商机的 OPEN 跟进任务里是否有已到期（真实 due_at，不猜时间）。"""
    import datetime as _dt
    now = now or _dt.datetime.now()
    for _t in tasks or []:
        try:
            _due = _dt.datetime.strptime(
                str(_t.get("due_at") or "")[:16], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if _due < now:
            return True
    return False


def representative_opp(group: dict, opp_by_inq: dict = None,
                       tasks_by_opp: dict = None):
    """Deal 组代表商机（第 23 轮统一，取代各组件自选）。

    从组内往来所属商机里挑「最该代表该 Deal」的一个：
    有 OPEN 跟进任务者优先 > 阶段推进最靠前 > 最近更新 > 新 ID。
    同客户同产品的重复旧行（各自独立商机行）由此归到同一主卡口径。
    """
    cands = [o for it in (group or {}).get("items") or []
             if (o := (opp_by_inq or {}).get(it.get("id")))]
    if not cands:
        return None

    def _rk(o):
        return (1 if (tasks_by_opp or {}).get(o.get("id")) else 0,
                _stage_rank(o.get("stage")),
                str(o.get("updated_at") or o.get("created_at") or ""),
                o.get("id") or 0)
    return max(cands, key=_rk)


def _stage_rank(stage) -> int:
    try:
        from sales_crm import STAGES
        return STAGES.index(str(stage or "")) if stage in STAGES else -1
    except Exception:
        return -1


def deal_work_item(group: dict, opp=None, opp_tasks=None,
                   opp_by_inq: dict = None, tasks_by_opp: dict = None) -> dict:
    """统一 Deal Work View Model（DealWorkItem，第 23 轮核心）。

    传入 group（group_deal_threads 输出）与代表商机；若只给 group + 两张
    索引表（opp_by_inq/tasks_by_opp），内部自动选代表商机。
    返回一个只读 dict，包含该 Deal 当前工作状态的**全部展示字段**：
      key / lead / items / conversation_count / open_count / lead_id
      company / contact / country / need / product(短名) / spec(空·不进卡)
      quantity / tier / queue_score / overdue / stage / stage_cn /
      emoji / tone / badge(状态文案) / nba(唯一 Next Best Action) /
      wait / last_created / due_at / lead_fu_state
    UI 只允许读它；同一 Deal 在各首页组件里因此只有一份状态。
    """
    if opp is None and opp_by_inq is not None:
        opp = representative_opp(group, opp_by_inq, tasks_by_opp)
        opp_tasks = ((tasks_by_opp or {}).get((opp or {}).get("id"))
                     if opp else None)
    st = deal_current_state(group)
    lead = group.get("lead") or {}
    items = group.get("items") or []
    need = lead.get("need") or {}
    req = resolved_requirement(group)
    details = (opp or {}).get("details") or {}
    req_info = {
        "product_query": req.get("product"),
        "quantity": req.get("quantity"),
        "specification": need.get("specification") or details.get("specification"),
        "customization": need.get("customization") or details.get("customization"),
        "certification": need.get("certification") or details.get("certification"),
        "target_price": need.get("target_price") or details.get("target_price"),
        "incoterm": need.get("incoterm") or details.get("incoterm"),
        "destination": need.get("destination") or details.get("destination"),
        "country": lead.get("country"),
        "raw_text": need.get("raw_text") or "",
        "customer_requested_outcome": need.get("customer_requested_outcome") or "",
    }
    buckets = requirement_buckets(req_info, missing=[], matches=[], text="")
    pd = product_display(need)
    customer_product = (
        str(need.get("product_query") or "").strip()
        or str(need.get("product") or "").strip()
        or str(need.get("product_cat") or "").strip()
        or str((opp or {}).get("product") or "").strip()
        or str(need.get("intent") or "").strip()
    )
    product_match_status = str(
        details.get("product_match_status")
        or ("MATCHED" if (need.get("match_top") or {}).get("name") else "")
        or ("NO_MATCH" if customer_product else "INSUFFICIENT_INFORMATION")
    ).upper()
    supplier_capability = str(
        details.get("supplier_capability_status")
        or ("UNKNOWN" if product_match_status in {"NO_MATCH", "UNRESOLVED"} else "")
        or ("CAPABLE" if product_match_status in {"MATCHED", "PARTIAL_MATCH"} else "UNKNOWN")
    ).upper()
    bucket_level = str(buckets.get("requirementCompleteness") or "").upper()
    detail_level = str(details.get("requirement_completeness") or "").upper()
    if detail_level in {"HIGH", "COMPLETE"}:
        req_level = detail_level
    elif bucket_level in {"HIGH", "COMPLETE"}:
        req_level = bucket_level
    else:
        req_level = detail_level or bucket_level
    product_requirement_status = "KNOWN" if customer_product else "UNKNOWN"
    requirement_status = req_level or ("KNOWN" if customer_product else "UNKNOWN")
    quotation_readiness = str(
        details.get("quotation_readiness") or need.get("readiness") or ""
    ).upper()
    blocking_items = [str(x) for x in (need.get("blockers") or []) if str(x)]
    if req_level in {"HIGH", "COMPLETE"}:
        blocking_items = [
            x for x in blocking_items
            if str(x) not in {"reference_model", "reference_photo",
                              "design_reference", "product_spec"}
        ]
    w = {
        "key": group.get("key"), "lead": lead, "items": items,
        "conversation_count": st["conversation_count"],
        "open_count": st["open_count"], "lead_id": st["lead_id"],
        "company": str(lead.get("company") or ""),
        "contact": str(lead.get("contact") or ""),
        "country": str(lead.get("country") or ""),
        "need": need,
        "product": product_title({"product_query": req.get("product")}) or pd["title"] or customer_product,
        "spec": "",                       # 第二十三轮：规格不进列表主卡
        "quantity": str(req.get("quantity") or need.get("qty") or ""),
        "tier": st["tier"],
        "queue_score": (lead.get("qs3") if lead.get("qs3") is not None
                        else -(lead.get("pts") or 0)),
        "last_created": st["last_created"],
        "lead_fu_state": str(lead.get("fu_state") or ""),
        "overdue": False, "stage": "", "stage_cn": "", "emoji": "🔵",
        "tone": "blue", "badge": "", "nba": st["nba"] or "",
        "wait": "", "due_at": "", "opp": opp,
    }
    try:
        if opp:
            _stg = str(opp.get("stage") or "")
            w["stage"] = _stg
            from sales_crm import STAGE_CN, health_of, recommended_action
            w["stage_cn"] = STAGE_CN.get(_stg, _stg or "新询盘")
            w["due_at"] = str(opp.get("next_action_at") or "")
            _emo, _tone = deal_stage_meta(_stg)
            w["emoji"], w["tone"] = _emo, _tone
            w["badge"] = w["stage_cn"]
            if has_overdue_task(opp_tasks):
                w["overdue"] = True
                w["tone"] = "high"
                w["badge"] = f"{w['stage_cn']} · 已逾期"
            else:
                _h = health_of(opp)
                if (_h or {}).get("status") == "at_risk":
                    w["tone"] = "amber"
                    w["badge"] = f"{w['stage_cn']} · 有风险"
                _rec = recommended_action(opp, _h) if _h else None
                if (_rec or {}).get("action"):
                    w["nba"] = _rec["action"]
        else:
            # 无商机 → 消息级业务状态（与 R22 Sidebar 回落口径一致）
            from workflow import BIZ_CN, BIZ_STYLE, derive_biz
            _biz = st["biz"] or derive_biz(
                lead.get("status"), need.get("blockers") or [],
                need.get("readiness") or "",
                (lead.get("wf") or {}).get("biz_status"))
            w["biz"] = _biz
            _tone, _emo = BIZ_STYLE.get(_biz, ("blue", "🔵"))
            w["emoji"], w["tone"] = _emo, _tone
            w["badge"] = BIZ_CN.get(_biz, _biz)
            if lead.get("status") == "待处理" and w["tier"] == "P1" \
                    and not (need.get("blockers") or []):
                w["tone"] = "high"
                w["badge"] = "高优 · 待回复"
            if st["overdue"]:
                w["overdue"] = True
                w["tone"] = "high"
                w["badge"] = f"{w['badge']} · 已逾期"
            if lead.get("status") == "已处理":
                w["tone"] = "green"
            if lead.get("status") == "待处理":
                _wt = wait(lead.get("created"))
                w["wait"] = _wt if _wt and _wt != "刚刚" else ""
    except Exception:
        # 极端情况下任何派生失败都退回消息级（保底不炸页面）
        w["badge"] = w["badge"] or str(need.get("intent") or "")
    communication = "ACTION_REQUIRED" if w.get("open_count", 0) > 0 else "WAITING_OR_DONE"
    quote_lifecycle = {"status": "NONE", "label": "暂无报价", "has_sent": False}
    data_quality = {"score": 0, "missing": [], "label": "资料不足"}
    deal_risks = []
    if opp:
        try:
            import db as _db
            import sales_crm as _sc
            _quotes = _db.list_quotes(opp.get("id")) or []
            _tasks = _db.list_followup_tasks(opportunity_id=opp.get("id")) or []
            _acts = _db.list_deal_activity(opp.get("id")) or []
            _types = [a.get("type") for a in _acts]
            quote_lifecycle = _sc.quote_lifecycle_summary(_quotes)
            data_quality = _sc.data_quality_score(opp, _quotes, _tasks, _types)
            deal_risks = _sc.deal_risks(opp, _quotes, _tasks, _types)
        except Exception:
            pass
    stage_values = {
        str(((opp_by_inq or {}).get(it.get("id")) or {}).get("stage")
            or (opp or {}).get("stage")
            or it.get("biz") or "").upper()
        for it in items
        if str(((opp_by_inq or {}).get(it.get("id")) or {}).get("stage")
               or (opp or {}).get("stage")
               or it.get("biz") or "").strip()
    }
    stage_conflict = len(stage_values) > 1
    amount_stale = bool(req.get("quantityChanged") and quote_lifecycle.get("has_sent"))
    needs_requote = bool(req.get("quantityChanged") and (
        quote_lifecycle.get("has_sent") or w.get("stage") in ("QUOTED", "NEGOTIATION", "PO_PENDING")
    ))
    customer_blocking = buckets.get("customerBlockingItems") or []
    optional_enhancements = buckets.get("optionalEnhancements") or []
    internal_prerequisites = buckets.get("internalPrerequisites") or []
    requested_outcome = need.get("customer_requested_outcome") or customer_requested_outcome_from_text(need.get("raw_text") or "")
    requested_set = set(requested_outcome if isinstance(requested_outcome, (list, tuple, set)) else ([requested_outcome] if requested_outcome else []))
    recommendation_requested = "REQUEST_PRODUCT_RECOMMENDATION" in requested_set
    if recommendation_requested and product_requirement_status == "KNOWN":
        customer_blocking = [
            x for x in customer_blocking
            if str(x.get("key") or x.get("field") or "").lower()
            not in {"product", "product_type", "category", "product_spec",
                    "reference_model", "reference_photo", "design_reference",
                    "lid", "lid_option", "cover", "盖子"}
        ]
        blocking_items = [
            x for x in blocking_items
            if str(x).lower()
            not in {"product", "product_type", "category", "product_spec",
                    "reference_model", "reference_photo", "design_reference",
                    "lid", "lid_option", "cover", "盖子"}
        ]
    if needs_requote or req.get("quantityChanged"):
        customer_requested_outcome = "REQUEST_UPDATED_QUOTATION"
    elif requested_set:
        customer_requested_outcome = sorted(requested_set)
    elif quotation_readiness in ("READY_FOR_QUOTE", "READY_FOR_QUOTATION", "PRELIMINARY_QUOTE_READY"):
        customer_requested_outcome = "REQUEST_QUOTATION"
    else:
        customer_requested_outcome = ""
    nba_type = str(st.get("nba_type") or "").upper()
    if customer_blocking:
        resolved_action = "CLARIFY_REQUIREMENT"
        resolved_label = "确认需求"
    elif customer_requested_outcome == "REQUEST_UPDATED_QUOTATION":
        if internal_prerequisites:
            resolved_action = "CHECK_INTERNAL_QUOTATION_PREREQUISITES"
            resolved_label = "检查报价条件"
        else:
            resolved_action = "PREPARE_UPDATED_QUOTATION"
            resolved_label = "创建更新报价"
    elif w.get("overdue") or w.get("lead_fu_state") in ("已逾期", "今日跟进"):
        resolved_action = "FOLLOW_UP"
        resolved_label = "执行跟进"
    elif w.get("stage") in ("QUOTED", "SAMPLE", "NEGOTIATION", "PO_PENDING") \
            or str(w.get("biz") or "").upper() in ("QUOTED", "NEGOTIATING"):
        resolved_action = "FOLLOW_UP"
        resolved_label = w.get("nba") or "执行跟进"
    elif blocking_items:
        resolved_action = "CLARIFY_REQUIREMENT"
        resolved_label = "确认需求"
    elif recommendation_requested and product_requirement_status == "KNOWN":
        resolved_action = "CHECK_PRODUCT_OPTIONS"
        resolved_label = "检查产品方案"
    elif product_requirement_status == "KNOWN" \
            and product_match_status in ("NO_MATCH", "UNRESOLVED",
                                         "INSUFFICIENT_INFORMATION") \
            and supplier_capability in ("UNKNOWN", "NEEDS_CHECK", ""):
        resolved_action = "CHECK_SUPPLIER_CAPABILITY"
        resolved_label = "检查供应能力"
    elif product_match_status in ("NO_MATCH", "UNRESOLVED",
                                  "INSUFFICIENT_INFORMATION"):
        resolved_action = "MATCH_PRODUCT"
        resolved_label = "匹配产品"
    elif quotation_readiness in ("READY_FOR_QUOTE", "READY_FOR_QUOTATION") \
            or str(w.get("biz") or "").upper() == "READY_FOR_QUOTE" \
            or str(w.get("stage") or "").upper() == "REQUIREMENT_CONFIRMED":
        resolved_action = "PREPARE_QUOTATION"
        resolved_label = "创建报价"
    elif nba_type in ("CREATE_QUOTE", "CREATE_QUOTATION"):
        resolved_action = "PREPARE_QUOTATION"
        resolved_label = "创建报价"
    elif nba_type in ("FOLLOW_UP_CUSTOMER", "FOLLOW_UP_QUOTE"):
        resolved_action = "FOLLOW_UP"
        resolved_label = "执行跟进"
    else:
        resolved_action = "SEND_REPLY"
        resolved_label = w.get("nba") or "查看并发送客户邮件"
    w["resolvedState"] = {
        "customerProductRequirement": customer_product,
        "requirementStatus": requirement_status,
        "productRequirementStatus": product_requirement_status,
        "productMatchStatus": product_match_status,
        "supplierCapabilityStatus": supplier_capability,
        "quotationReadiness": quotation_readiness or "UNKNOWN",
        "communicationStatus": communication,
        "pipelineStage": w.get("stage") or str(w.get("biz") or ""),
        "blockingItems": blocking_items,
        "customerBlockingItems": customer_blocking,
        "optionalEnhancements": optional_enhancements,
        "internalPrerequisites": internal_prerequisites,
        "customerRequestedOutcome": customer_requested_outcome,
        "actionState": ("NEEDS_CUSTOMER_INFO" if customer_blocking else ("WAITING_INTERNAL" if recommendation_requested or internal_prerequisites else "READY_TO_REPLY")),
        "lidOptionStatus": "RECOMMENDATION_REQUESTED" if recommendation_requested else "",
        "requirementCompleteness": req_level,
        "priority": w.get("tier"),
        "resolvedRequirement": req,
        "changedFields": req.get("changedFields") or [],
        "stageConflict": stage_conflict,
        "stageValues": sorted(stage_values),
        "amountStale": amount_stale,
        "needsRequote": needs_requote,
        "quoteLifecycle": quote_lifecycle,
        "dataQuality": data_quality,
        "dealRisks": deal_risks,
        "nextBestAction": {
            "type": resolved_action,
            "label": resolved_label,
        },
        "primaryCta": resolved_label,
    }
    w["nba"] = resolved_label
    w["nba_type"] = resolved_action
    return w


# =====================================================================
# 第十九轮 · 产品列表展示优化（Sales Home Final Cleanup A3）
# 列表层只显示「短名 + 少量规格 chips」；完整 Specification 仍保留在
# Deal Detail / Inquiry Detail / Customer Requirement，本函数纯只读，
# 不删除、不改写任何原始产品数据。
# =====================================================================
import re as _RE

# 产品短语里的规格从句起始符：见到这些就把后面切掉（" with Bluetooth…"）
_TITLE_BREAK = _RE.compile(r"[,;]|\s+with\s+|\s+featuring\s+|\s+equipped\s+with\b",
                           _RE.I)
_TITLE_STOP = {"with", "and", "&", "the", "a", "an", "of", "for", "in",
               "on", "to", "by", "custom"}
# 全大写缩写保持原样（title-case 后不破坏专有名词）
_ALLCAPS = {"ANC", "ENC", "USB", "OEM", "ODM", "TWS", "LED", "RGB", "CE",
            "IPX", "ROHS", "FDA", "TPE", "TPU", "BSCI", "REACH"}
# 数字规格：40 hours / 2000mAh / 500ml …
_SPEC_NUM = _RE.compile(
    r"\b(\d+(?:\.\d+)?)\s*(hours|hrs|hr|hour|h|mah|ml|mm|cm|w|watts|days|day)\b",
    _RE.I)
_SPEC_OEM = _RE.compile(
    r"\bOEM\b|logo printed|printed.{0,10}logo|our logo|custom(?:ized)?\s+(?:logo|packaging)",
    _RE.I)
_UNIT_SHORT = {"hours": "h", "hrs": "h", "hr": "h", "hour": "h", "h": "h",
               "days": "d", "day": "d", "watts": "w", "w": "w"}


def _norm_title_word(w: str) -> str:
    if w.upper() in _ALLCAPS:
        return w.upper()
    if w and w[0].islower():
        return w[0].upper() + w[1:]
    return w


def _norm_num(m) -> str:
    val, unit = m.group(1), m.group(2).lower()
    try:
        n = float(val)
        val = str(int(n)) if n == int(n) else val
    except Exception:
        pass
    return f"{val}{_UNIT_SHORT.get(unit, unit)}"


def product_title(need: dict, max_words: int = 4) -> str:
    """列表层产品短名（A3）。

    规则：优先用客户产品短语（product_query），在第一个规格从句（comma /
    " with " / " featuring "…）处截断，只保留前几个实词，然后按标题大小写。
    取不到产品短语时回退到产品库匹配名 / 品类。
    例："Wireless ANC earbuds with Bluetooth 5.4, ANC, 40 hours battery…"
        → "Wireless ANC Earbuds"
    """
    need = need or {}
    raw = str(need.get("product_query") or "").strip()
    if not raw:
        raw = str(need.get("product") or "").strip() or \
            str(need.get("product_cat") or "").strip()
    if not raw:
        return ""
    head = _TITLE_BREAK.split(raw)[0].strip()
    toks = [t for t in head.split() if t and t.lower() not in _TITLE_STOP]
    if not toks:
        toks = [t for t in raw.split() if t][:max_words]
    keep = []
    for t in toks:
        keep.append(t)
        if len(keep) >= max_words:
            break
    s = " ".join(_norm_title_word(t) for t in keep)
    return (s[:46] + "…") if len(s) > 46 else s


def product_chips(need: dict, max_chips: int = 4) -> list:
    """从产品短语中抽取少量规格 chips（A3，纯只读）。

    顺序按业务区分度加权：蓝牙版本 → 降噪 → 数值规格（40h/2000mAh）
    → OEM 定制 → USB-C / 无线充 / 防水 等；最多 max_chips 个。
    例："Bluetooth 5.4, ANC, 40 hours battery life, USB-C, custom logo and packaging"
        → ["Bluetooth 5.4", "ANC", "40h", "OEM"]
    """
    raw = str((need or {}).get("product_query") or "").strip()
    if not raw:
        return []
    out, seen = [], set()

    def _add(s: str):
        s = str(s).strip()
        low = s.lower()
        if s and low not in seen:
            seen.add(low)
            out.append(s)

    m = _RE.search(r"bluetooth\s*[0-9][0-9.]*", raw, _RE.I)
    if m:
        _add(" ".join(w.capitalize() for w in m.group(0).split()))
    for pat in (r"\bANC\b", r"\bactive noise cancelling\b"):
        if _RE.search(pat, raw, _RE.I):
            _add("ANC")
            break
    if _RE.search(r"\bENC\b", raw, _RE.I):
        _add("ENC")
    for mm in _SPEC_NUM.finditer(raw):
        _add(_norm_num(mm))
    if _SPEC_OEM.search(raw):
        _add("OEM")
    if _RE.search(r"\bUSB[- ]?C\b", raw, _RE.I):
        _add("USB-C")
    if _RE.search(r"wireless charging", raw, _RE.I):
        _add("无线充")
    mm = _RE.search(r"\bIPX\d+\b", raw, _RE.I)
    if mm:
        _add(mm.group(0).upper())
    return out[:max_chips]


def product_display(need: dict, max_chips: int = 4) -> dict:
    """列表层产品展示结果：title（短名）+ spec（chips 一行）。

    调用方直接展示：title 为第一行主名；spec 非空时作第二行小字。
    完整规格始终保留在 Deal / Inquiry 详情。
    """
    title = product_title(need)
    chips = product_chips(need, max_chips=max_chips)
    return {"title": title, "chips": chips,
            "spec": " · ".join(chips)}
