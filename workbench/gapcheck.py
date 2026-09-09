# -*- coding: utf-8 -*-
"""
缺失信息检测 + 追问建议（外贸业务版 A/B/C 三级分类）

业务规则（按外贸实际报价流程分三级）：
  🔴 A 类 · 报价前必须确认  —— 缺这些真报不了价
  🟡 B 类 · 强烈建议获取    —— 影响价格策略与方案，能问到最好
  🟢 C 类 · 可以后续再获取  —— 用来判断客户价值，别在第一封信里全问（会把客户问跑）

设计原则：
  - 默认走**离线规则**（零成本、零延迟、断网可用）
  - 有 LLM client 时，可选让 AI 结合原文润色成一封更自然的追问信
  - 每个问题带中文业务说明（reason），能引用客户原话就引用原话
  - 组内排序：智能追问（引用客户原话的定向问题）排在模板句前面
"""

import re

# 三级分类
LEVEL_A, LEVEL_B, LEVEL_C = "A", "B", "C"

LEVEL_META = {
    "A": {"icon": "⚠️", "title": "报价前需要确认", "tip": "缺这些没法报价，第一封信必须问"},
    "B": {"icon": "📌", "title": "建议确定", "tip": "影响价格方案与跟进节奏，能问到最好"},
    "C": {"icon": "💡", "title": "可选问题", "tip": "参考信息，建议第二封信再问，别一次问太多"},
}

# 关键字段定义：(字段名, 中文名, 等级, 英文追问句)
# 等级口径（业务逻辑优化后）：
#   A 类 = 缺失会直接影响产品选择 / SKU / 成本 / 正式报价 / 订单执行
#   联系方式（邮箱/公司）缺失不阻塞报价 → 归 B 类（优化九）
FIELD_DEFS = [
    # ---------- ⚠️ A 类：报价/产品决策必需 ----------
    ("product_spec", "产品型号 / 规格", "A",
     "Which model or specification are you interested in?"),
    ("quantity", "采购数量", "A",
     "What quantity do you require?"),
    ("customization", "定制要求（Logo / 颜色 / 尺寸）", "A",
     "Do you need any customization, such as logo printing, specific color or size?"),
    ("destination", "目的地（收货国家 / 港口）", "A",
     "Which country or port should we deliver to?"),
    ("certification", "关键认证要求", "A",
     "Do you need any specific certification (e.g. CE, EN71, REACH, FDA)?"),

    # ---------- 📌 B 类：建议确认 ----------
    ("email", "联系邮箱", "B",
     "Could you please share your email address so we can send the quotation?"),
    ("company", "公司信息", "B",
     "Could you share your company name and website?"),
    ("delivery", "目标交期", "B",
     "What is your target delivery date?"),
    ("packaging", "包装要求", "B",
     "Any specific packaging requirements (e.g. polybag, printed box, barcode)?"),
    ("payment", "付款方式", "B",
     "What payment terms do you usually work with (e.g. T/T, L/C at sight)?"),
    ("incoterm", "贸易术语", "B",
     "Which trade term do you prefer (e.g. FOB, CIF, EXW, DDP)?"),

    # ---------- 🟢 C 类：可选问题（参考信息，第二封信再问） ----------
    ("target_price", "目标价格", "C",
     "If you have a target price in mind, please let us know - it helps us "
     "propose the most suitable solution for you."),
    ("company_scale", "公司规模", "C",
     "Could you tell us a bit about your company scale (e.g. number of stores or employees)?"),
    ("annual_volume", "年度采购量", "C",
     "What is your estimated annual purchase volume for this item?"),
    ("sales_channel", "销售渠道", "C",
     "Which sales channel do you sell through (retail, wholesale, Amazon, etc.)?"),
    ("competitor", "现有供应商 / 竞争对手", "C",
     "Are you currently sourcing this item from another supplier?"),
    ("purchase_cycle", "采购周期", "C",
     "How often do you place orders (monthly, quarterly, or per season)?"),
]

A_TOTAL = sum(1 for f in FIELD_DEFS if f[2] == "A")
B_TOTAL = sum(1 for f in FIELD_DEFS if f[2] == "B")
C_TOTAL = sum(1 for f in FIELD_DEFS if f[2] == "C")

# 原文里出现这些词，就认为"客户已经提过了"
_PATTERNS = {
    "customization": r"\b(logo|custom\w*|oem|odm|private\s+label|color\w*|colour\w*|"
                     r"\bsize[sd]?\b|design|print\w*|emboss\w*|engrav\w*)\b",
    "certification": r"\b(ce|en\s?71|reach|fda|bsci|iso\s?\d{0,5}|sgs|oeko[-\s]?tex|"
                     r"cpsia|rohs|lfgb|astm|gots|sedex|cpsia|tuv|intertek)\b",
    "packaging": r"\b(packag\w*|packing|poly\s?bag|blister|header\s?card|hang\s?tag|"
                 r"barcode|carton|gift\s?box|display\s?box)\b",
    "payment": r"\b(t\s?/\s?t|l\s?/\s?c|d\s?/\s?p|d\s?/\s?a|paypal|western?\s?union|"
               r"deposit|balance|payment\s+terms?|credit\s+terms?|30\s?%|50\s?%)\b",
    "incoterm": r"\b(fob|cif|exw|ddp|ddu|dap|fca|cfr|c\s?&\s?f|cnf)\b",
    "company_scale": r"\b(chain|stores?|shops?|filialen|branches|outlets?|employees?|"
                     r"staff|group|subsidiar\w*|warehouse)\b",
    "annual_volume": r"\b(annual\w*|per\s+year|yearly|every\s+year|a\s+year|"
                     r"yearly\s+volume|annual\s+volume)\b",
    "sales_channel": r"\b(retail\w*|wholesale|amazon|e-?commerce|online|shopify|"
                     r"supermarket|hypermarket|distributor|dealer|reseller)\b",
    "competitor": r"\b(currently\s+(buy\w*|sourc\w*|purchas\w*|work\w*)|"
                  r"existing\s+supplier|another\s+supplier|other\s+vendor|"
                  r"competitor|switch\w*|present\s+supplier)\b",
    "purchase_cycle": r"\b(every\s+(month|quarter|season|year)|monthly|quarterly|"
                      r"seasonal|per\s+season|repeat\s+order|regular\s+basis|"
                      r"next\s+season|twice\s+a\s+year)\b",
    "delivery": r"\b(deliver\w*|lead\s*time|deadline|shipment|eta|urgent|asap|"
                r"by\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*)\b",
}

_COMPILED = {k: re.compile(v, re.I) for k, v in _PATTERNS.items()}

# 从询盘原文里找邮箱地址：很多渠道（Email / 表单 / CRM 导出 / 邮件签名）贴过来的
# 文本里本来就带着客户邮箱，看到了就不该再问一遍
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def find_email_in_text(text: str) -> str:
    """从询盘原文里提取第一个邮箱地址，没有则返回空串。"""
    m = _EMAIL_RE.search(text or "")
    return m.group(0) if m else ""


# ==================== 智能追问（引用客户自己的话，别泛泛地问） ====================
# 例：客户说 "500ml or 750ml, not sure yet."
#     ❌ 机械版：Which model or specification are you interested in?
#     ✅ 智能版：Which capacity would you prefer, 500ml or 750ml?

# 犹豫词：客户给了选项但还没定
_UNCERTAIN_RE = re.compile(
    r"\b(not\s+sure|not\s+decided|unsure|undecided|haven'?t\s+decided|"
    r"didn'?t\s+decide|not\s+certain|still\s+deciding|tbd|tbc|maybe|yet)\b", re.I)

# "X or Y" 选项结构
_OR_RE = re.compile(r"\b([\w\.\-/\s]{1,40}?)\s+or\s+([\w\.\-/\s]{1,40}?)(?=[\,\.\;\!\?]|\Z)", re.I)

# 数量含糊词：客户给了大概数，追问确认更专业
_VAGUE_QTY_RE = re.compile(r"\b(about|around|approx\.?|approximately|or\s+so|more\s+or\s+less|\d+\s*-\s*\d+)\b", re.I)

# 具体交期："within 3 weeks" / "in 20 days" / "by 2 months" / "4 weeks" / "3-week"
_DEADLINE_RE = re.compile(
    r"\b(?:within|in|by)\s+(\d+\s+(?:days?|weeks?|months?))\b"
    r"|\b(\d+)\s*[-\s]?(days?|weeks?|months?)\b", re.I)

# 各字段缺省时的中文业务说明（为什么业务员要先搞清楚这个）
REASONS = {
    "product_spec": "不知道具体型号 / 规格，报错产品的风险很高",
    "quantity": "没有数量算不出单价，也报不了运费",
    "customization": "要不要 Logo / 定制直接影响成本和起订量",
    "destination": "不知道目的地，报不了运费，FOB / CIF 差别很大",
    "certification": "认证要求决定用哪个工厂和模具，成本差异大",
    "company": "了解客户公司才能判断这是终端客户还是贸易商",
    "email": "没有邮箱报价单发不出去（系统会先查档案，查不到才问）",
    "delivery": "交期决定排产计划和报价里的运输方式",
    "packaging": "彩盒 / 吸塑卡等包装方式直接影响单价",
    "payment": "付款方式决定资金风险，小单预付、大单信用证策略不同",
    "incoterm": "贸易术语决定报价里含不含运费保险",
    "target_price": "客户有目标价的话，销售可以据此制定价格策略和推荐替代方案",
    "company_scale": "判断客户是大买家还是小买家，决定投入多少精力跟进",
    "annual_volume": "年度采购量决定给什么价格档位",
    "sales_channel": "渠道不同，包装 / 认证 / 价格体系都不一样",
    "competitor": "客户已有供应商的话，重点是比价和替换理由",
    "purchase_cycle": "采购周期决定开发信的跟进节奏",
}


def find_open_options(text: str) -> list:
    """找出客户自己给出但还没定的选项，返回片段列表。

    例："500ml or 750ml, not sure yet."  →  ["500ml or 750ml"]
    """
    out = []
    for sent in re.split(r"[.!?\n]", text or ""):
        if " or " not in sent.lower():
            continue
        if not _UNCERTAIN_RE.search(sent):
            continue
        m = _OR_RE.search(sent)
        if not m:
            continue
        left = m.group(1).strip()
        # 先取最后 1~2 个词，再去掉 "for / in / we need" 之类的前缀虚词
        words = left.split()[-2:]
        while words and words[0].lower() in (
                "for", "in", "of", "to", "on", "at", "a", "an", "the",
                "with", "need", "needs", "want", "like", "is", "are"):
            words = words[1:]
        left = " ".join(words)
        frag = f"{left} or {m.group(2).strip()}"
        frag = re.sub(r"\s+", " ", frag).strip(" ,.-")
        if frag and frag not in out:
            out.append(frag)
    return out


def _option_noun(frag: str) -> str:
    """根据选项内容猜客户在犹豫什么，让追问句更贴切。"""
    if re.search(r"\d+\s*(ml|l|cl|oz|gallon)\b", frag, re.I):
        return "capacity"
    if re.search(r"\b(black|white|blue|red|green|yellow|pink|purple|grey|gray|orange|"
                 r"navy|transparent)\b", frag, re.I):
        return "color"
    if re.search(r"\d+\s*(pcs|pieces|units|pairs|sets|k)\b", frag, re.I):
        return "quantity"
    if re.search(r"\b(x?s|m|l|xl|xxl|3xl|size[s]?)\b", frag, re.I):
        return "size"
    return "option"


def detect_missing(text: str, info: dict, matches: list, known_email: str = "") -> list:
    """检测询盘里缺哪些关键信息，按 A→B→C 排序返回。

    参数：
      text        - 询盘原文
      info        - 引擎提取出的 extracted 字典
      matches     - 产品匹配结果列表
      known_email - 系统里已知的客户邮箱（客户档案归并的，来自 Email/Alibaba/
                    WhatsApp/LinkedIn/展会/CRM 等渠道的历史询盘）。有值就不追问邮箱。
    返回：缺失项列表，每项 {"key","name","level","question"}
    """
    info = info or {}
    matches = matches or []
    low = text or ""

    # 产品型号：匹配到产品（≥0.3）就算"客户说清要什么了"
    # 阈值别定太高——"5000pcs neoprnee swim caps" 实际匹配分只有 0.4
    has_spec = any((m.get("match_score") or 0) >= 0.3 for m in matches)

    # 邮箱三来源任一命中即算"已知"：① 本次提取 ② 客户档案（历史询盘渠道带来）
    # ③ 询盘原文里直接出现（签名 / 表单字段）
    email_known = (bool((info.get("email") or "").strip())
                   or bool((known_email or "").strip())
                   or bool(find_email_in_text(low)))

    have = {
        # 从提取结果判定的
        "product_spec": has_spec,
        "quantity": bool(info.get("quantity")),
        "destination": bool(info.get("country")),
        "target_price": bool(info.get("target_price")),
        "company": bool(info.get("company")),
        "email": email_known,
        # 从原文关键词判定的
        "customization": bool(_COMPILED["customization"].search(low)),
        "certification": bool(_COMPILED["certification"].search(low)),
        "packaging": bool(_COMPILED["packaging"].search(low)),
        "payment": bool(_COMPILED["payment"].search(low)),
        "incoterm": bool(_COMPILED["incoterm"].search(low)),
        "company_scale": bool(_COMPILED["company_scale"].search(low)),
        "annual_volume": bool(_COMPILED["annual_volume"].search(low)),
        "sales_channel": bool(_COMPILED["sales_channel"].search(low)),
        "competitor": bool(_COMPILED["competitor"].search(low)),
        "purchase_cycle": bool(_COMPILED["purchase_cycle"].search(low)),
        "delivery": bool(_COMPILED["delivery"].search(low)),
    }

    # ---- 智能追问：能引用客户原话的，就不用泛泛的模板句 ----
    # targeted[key] = (英文追问句, 中文业务说明)；有 targeted 的问题算"智能追问"，
    # 排序时会排在同级的模板句前面——先解决客户话里没说死的，再补客户没提的
    targeted = {}

    # ① 客户给了选项但未定："500ml or 750ml, not sure yet."
    options = find_open_options(low)
    if options:
        frag = options[0]
        noun = _option_noun(frag)
        targeted["quantity" if noun == "quantity" else "product_spec"] = (
            f'You mentioned "{frag}" - which {noun} would you prefer?',
            f'客户在「{frag}」之间还没确定，报价前必须先锁定这个规格')

    # ② 只说 European market，没给具体国家
    if not have.get("destination") and re.search(r"\beurope\w*", low, re.I):
        targeted["destination"] = (
            "Which country in Europe should we deliver to?",
            "客户只说了欧洲市场，没给具体国家，运费和认证要求差别很大")

    # ③ 只说 certificates，没说具体哪个认证
    if (not have.get("certification")
            and re.search(r"\bcertificat\w*", low, re.I)):
        targeted["certification"] = (
            "Which certifications do you require - CE, EN71, REACH, or others?",
            "客户提到认证，但没说具体要哪个标准，不同认证成本差异大")

    # ④ 数量含糊："about 3000pcs" / "1000-2000pcs"
    if (not have.get("quantity") and _VAGUE_QTY_RE.search(low)
            and re.search(r"\d", low)):
        targeted["quantity"] = (
            "Could you confirm the quantity? I saw a rough number in your message "
            "- is that per order?",
            "客户给的是大概数量，需要确认是单次订单量还是年度总量")

    # ⑤ 客户给了具体交期："within 3 weeks" → 确认含不含运输时间
    # 注意：客户说了交期 ≠ 交期明确——半答案也要确认，这是业务上最容易踩坑的点
    dm = _DEADLINE_RE.search(low)
    if dm:
        have["delivery"] = False  # 强制进入缺失列表，用定向确认替换泛泛追问
    if not have.get("delivery") and dm:
        period = dm.group(1) or f"{dm.group(2)} {dm.group(3)}s"
        targeted["delivery"] = (
            f"Could you please confirm whether the {period} delivery "
            "requirement includes shipping time?",
            f"客户要求 {period} 内交货，需确认是否含海运时间，"
            "这直接决定能不能接这一单")

    # ⑥ 客户说明了产品品类但库内无匹配（第五轮第二次补丁 03）：
    #    客户已提出品类（如 Stainless Steel Water Bottle）≠ Unknown —— 不问"什么产品"，
    #    追问落在型号/规格层级；先在产品库检索该品类候选，匹配不上再请客户提供参考型号/图片
    _stated_phrase = ""
    try:
        from agent.reply_strategy import customer_product_phrase
        _stated_phrase = customer_product_phrase(low) or ""
    except Exception:
        _stated_phrase = ""
    if _stated_phrase and not have.get("product_spec"):
        targeted["product_spec"] = (
            f'You mentioned "{_stated_phrase}" - could you share a reference model, '
            "a photo or the exact specification (e.g. capacity / size) so we can "
            "match the closest item from our range?",
            f"客户已明确产品品类「{_stated_phrase}」→ 缺的是型号/规格层级；"
            "先在产品库检索该品类候选产品，匹配不上再请客户提供参考型号/图片")

    missing = []
    for key, name, level, question in FIELD_DEFS:
        if not have.get(key):
            if key in targeted:
                q, why = targeted[key]
                missing.append({"key": key, "name": name, "level": level,
                                "question": q, "reason": why, "smart": True})
            else:
                missing.append({"key": key, "name": name, "level": level,
                                "question": question,
                                "reason": REASONS.get(key, ""), "smart": False})

    # 排序：A → B → C；同级内智能追问（引用客户原话）排模板句前面，
    # 业务员先回复最关键的，再看补充项
    missing.sort(key=lambda x: (x["level"], not x.get("smart")))

    # 产品已匹配但客户仍有未定选项 → 单独追加一条 A 类追问（放最前）
    if options and have.get("product_spec"):
        frag = options[0]
        noun = _option_noun(frag)
        missing.insert(0, {"key": "open_option", "name": "客户未定的选项",
                           "level": "A", "smart": True,
                           "reason": f"客户在「{frag}」之间还没确定，"
                                     "产品型号是报价的第一前提",
                           "question": f'You mentioned "{frag}" - which {noun} '
                                       "would you prefer?"})
    return missing


def group_by_level(missing: list) -> dict:
    """把缺失项按 A/B/C 分组，返回 {"A": [...], "B": [...], "C": [...]}"""
    g = {"A": [], "B": [], "C": []}
    for m in missing or []:
        g.setdefault(m["level"], []).append(m)
    return g


def readiness(missing: list) -> tuple:
    """报价就绪度：(A类已有数, A类总数)。A 类齐了就可以报价。"""
    n_missing_a = sum(1 for m in missing or [] if m["level"] == "A")
    # max(0, ...) 防御异常输入：缺失数不会超过总数，就不该出现负数
    return max(0, A_TOTAL - n_missing_a), A_TOTAL


def build_followup_email(missing: list, info: dict, seller_company: str = "",
                         levels=("A", "B")) -> str:
    """用缺失项拼一封英文追问邮件（离线规则版）。

    levels: 要追问的等级，默认 A+B（C 类建议第二封信再问，否则容易把客户问跑）
    """
    picked = [m for m in (missing or []) if m["level"] in levels]
    if not picked:
        return ""
    info = info or {}
    raw_name = (info.get("contact_name") or "").strip()
    first = raw_name.split()[0] if raw_name else ""

    lines = [f"Dear {first or 'Sir/Madam'},", ""]
    lines.append("Thank you for your inquiry. To prepare an accurate quotation for you, "
                 "could you please confirm the following details:")
    lines.append("")
    for i, m in enumerate(picked, 1):
        lines.append(f"{i}. {m['question']}")
    lines.append("")
    lines.append("Once we have these details, we will send you our best offer "
                 "within 24 hours.")
    lines.append("")
    lines.append("Best regards,")
    if seller_company:
        lines.append(seller_company)
    return "\n".join(lines)


SYSTEM_FOLLOWUP = (
    "You are an experienced, friendly foreign trade salesperson. "
    "Write a short, polite, natural English follow-up email. "
    "Rules: (1) Only ONE short paragraph of greeting/reason, then a numbered list of "
    "questions. (2) Ask ONLY about the missing points given by the user. "
    "(3) Keep each question to one short sentence. "
    "(4) SMART QUESTIONS: if the buyer's original message already mentions options or "
    "half-answers (e.g. '500ml or 750ml, not sure yet', 'European market', 'about 3000pcs'), "
    "your question MUST echo the buyer's own words and narrow it down - e.g. "
    "'Which capacity would you prefer, 500ml or 750ml?', "
    "'Which country in Europe should we deliver to?'. "
    "Never ask a generic question when the buyer already gave a hint. "
    "(5) No markdown, no subject line. "
    "(6) End with a warm closing line and the seller's company name if provided."
)


def build_followup_email_llm(missing: list, text: str, info: dict,
                             client, seller_company: str = "",
                             levels=("A", "B")) -> str:
    """让 AI 结合询盘原文，生成一封更贴合的追问邮件（需要 LLM client）。

    失败时返回空字符串，调用方会自动退回规则版。
    """
    picked = [m for m in (missing or []) if m["level"] in levels]
    if not picked or client is None:
        return ""
    info = info or {}
    points = "\n".join(f"- {m['name']}: {m['question']}" for m in picked)
    user_prompt = (
        f"Buyer's original inquiry:\n\"\"\"\n{(text or '').strip()[:1500]}\n\"\"\"\n\n"
        f"Known about the buyer: name={info.get('contact_name') or 'unknown'}, "
        f"company={info.get('company') or 'unknown'}, "
        f"country={info.get('country') or 'unknown'}\n\n"
        f"Missing information to ask about:\n{points}\n\n"
        f"Seller company: {seller_company or 'our company'}\n"
        "Now write the follow-up email."
    )
    try:
        reply = client.chat(SYSTEM_FOLLOWUP, user_prompt,
                            temperature=0.4, max_tokens=700, json_mode=False)
        return (reply or "").strip()
    except Exception:
        return ""
