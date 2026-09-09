# -*- coding: utf-8 -*-
"""
模块⑥：首轮追问策略 (Reply Follow-up Strategy) —— 第五轮优化

解决的问题：旧草稿容易被写成"信息采集表"——一次把产品 / 定制 / 认证 / 邮箱 /
包装 / 付款方式 / 贸易术语 7 个以上字段全问一遍，客户回复成本极高。

本模块只做两件事：

  1) build_question_plan —— 把缺失信息分成 P0 / P1 / P2，只让 P0 进入首轮追问，
     并硬性限制问题数量（默认 1-2 个，绝对不超过 3 个）。
  2) validate_reply_strategy —— 草稿生成后自检 14 项，任一失败不允许直接展示，
     先带着具体错误重新生成，仍失败则降级为受控模板草稿。

分级定义
--------
P0 = 当前动作无法继续的必要信息
    判断口径：如果客户不回答这个问题，我们当前的下一步业务动作真的做不下去。
    典型：产品/型号未知（无法选型核价）、客户给了候选规格但没定、数量未知或冲突。
P1 = 后续报价 / 打样 / 订单阶段才需要确认的信息
    典型：认证标准、目的港、具体交期天数、定制细节。
P2 = 可边谈边补的信息（首轮绝不问）
    典型：付款方式、贸易术语、目标价、包装、邮箱。

设计原则：纯离线规则、零成本、断网可用；LLM 模式与规则模式共用同一套口径。
"""

import re

from agent.extractor import (
    extract_quantity_semantics, semantic_conflict_roles,
    QUANTITY_QUOTATION,
)
from agent.insight import (
    find_open_options, certify_split, normalize_status,
    STATUS_INSUFFICIENT, STATUS_PRELIM, CERT_REQUIRED,
)

def _quantity_values_in_hedge_context(text: str, vals: list) -> dict:
    """逐个数量数字判断是否在 hedge 词附近（避免 'may need certificates' 等
    非数量 hedge 误判影响 quantity 判定）。

    返回 {val: is_hedge_in_context}。窗口：数字前后 50/30 字符。
    """
    if not vals:
        return {}
    low = (text or "").lower()
    out = {}
    for v in vals:
        is_hedge = False
        # 同时匹配 "1,500" 和 "1500" 两种带/不带千分位的写法
        candidates = sorted({f"{v:,}", str(v)}, key=len, reverse=True)
        for sep in candidates:
            for m in re.finditer(re.escape(sep), low):
                ctx = low[max(0, m.start() - 50): min(len(low), m.end() + 30)]
                if _APPROX_HEDGE_RE.search(ctx):
                    is_hedge = True
                    break
            if is_hedge:
                break
        out[v] = is_hedge
    return out


def _qty_in_hedge_context(text: str, qty) -> bool:
    """主量（qty）是否在 hedge 词附近。qty=None/0 时直接 False。"""
    if not qty:
        return False
    vals = _quantity_values_in_hedge_context(text, [_to_num(qty)] if _to_num(qty) else [])
    return bool(vals.get(_to_num(qty)))


# ---------- 数量上限（第五轮优化二） ----------
MAX_QUESTIONS = 3          # 绝对上限：任何首轮回复都不能超过 3 个问题
DEFAULT_MAX = 2            # 默认控制：优先 1-2 个

# ---------- 字段分级（第五轮优化三） ----------
P0_FIELDS = ("product", "model", "specification", "quantity")
P1_FIELDS = ("certification", "destination", "delivery_time", "customization")
P2_FIELDS = ("payment_terms", "incoterm", "packaging", "target_price", "email")

# ---------- 产品匹配四态（第五轮补丁：先查库，再问人） ----------
MATCH_MATCHED = "MATCHED"                              # 库内有确认匹配 → 不问型号
MATCH_PARTIAL = "PARTIAL_MATCH"                        # 有候选产品 → 给候选让客户选
MATCH_NONE = "NO_MATCH"                                # 库内没有匹配 → 可问 reference/图片/design
MATCH_INSUFFICIENT = "INSUFFICIENT_INFORMATION"        # 规格不足 → 只问完成匹配必需的信息

MATCH_STATE_CN = {
    MATCH_MATCHED: "MATCHED · 产品库已匹配（用已有数据解决，不向客户要型号）",
    MATCH_PARTIAL: "PARTIAL_MATCH · 有候选产品（先给候选让客户选，不向客户要型号）",
    MATCH_NONE: "NO_MATCH · 产品库无匹配（可问 reference model / 图片 / design，1-2 个问题）",
    MATCH_INSUFFICIENT: "INSUFFICIENT_INFORMATION · 规格不足（只问完成匹配必需的信息）",
}

TIER_CN = {
    "P0": "P0 · 当前动作无法继续（不回答就没法往下走）",
    "P1": "P1 · 后续报价/打样/订单阶段确认",
    "P2": "P2 · 边谈边补（首轮不问）",
}

# ---------- 客户明确提出的需求（回复优先级高于追问） ----------
_REQUEST_PATTERNS = [
    ("catalog", r"\b(catalog|catalogue|product\s+range|price\s+list|"
                r"product\s+specification[s]?|spec\s+sheet)\b"),
    ("samples", r"\bsamples?\b"),
    ("certifications", r"\bcertificat\w*|\bCE\b|\bRoHS\b|\bREACH\b|\bFDA\b|\bEN\s?71\b"),
    ("quotation", r"\b(quote|quotation|best\s+price|price)\b"),
]

# 首轮禁止作为"问题"出现的主题词（P1/P2，第五轮优化六）
_NEVER_ASK_PATTERNS = {
    "payment_terms": r"(payment\s+terms?|how\s+(?:would|do)\s+you\s+(?:like|prefer)\s+to\s+pay"
                     r"|t\s?/\s?t\b|l\s?/\s?c\b|paypal|deposit)",
    "incoterm": r"(incoterm|trade\s+terms?|\bFOB\b|\bCIF\b|\bEXW\b|\bDDP\b|\bDAP\b|\bFCA\b)",
    "packaging": r"(packag\w+|poly\s?bag|blister|gift\s?box|barcode)",
    "target_price": r"(target\s+price|budget|expected\s+price|price\s+target)",
    "email": r"(e-?mail\s+address|your\s+e-?mail|contact\s+e-?mail|email\s+id)",
    "certification": r"(certificat\w+|\bCE\b|\bRoHS\b|\bREACH\b|\bFDA\b|\bEN\s?71\b)",
}

# 重复追问已知信息的句式（第五轮优化六：禁止重复询问客户已提供的信息）
_REASK_PATTERNS = [
    # 注意：确认式问法（Is approximately 3,000 pcs your expected initial order quantity?）
    # 不算重复追问，所以只抓"重新索取"的句式，不抓 "order quantity" 这个词本身。
    ("quantity", r"(how\s+many|how\s+much|what\s+quantity|which\s+quantity"
                 r"|quantity\s+(?:do|are|would|should|will)\s+you)"),
    ("customization", r"(do\s+you\s+need\s+(?:custom|logo|oem|odm|branding)"
                      r"|custom\s+logo|oem\s+branding|any\s+customization)"),
    ("color", r"(which\s+colou?r|what\s+colou?r|colou?r\s+(?:do|would)\s+you)"),
    ("packaging", r"(packag\w+|poly\s?bag|gift\s?box)"),
    ("destination", r"(which\s+country|what\s+country|destination|deliver\s+to|ship\s+to)"),
    ("deadline", r"(when\s+do\s+you\s+need|what\s+is\s+your\s+deadline|how\s+soon\s+do\s+you"
                 r"|delivery\s+time\s*\?|lead\s+time\s+requirement)"),
]

# 歧义澄清 / 确认式问法（不是重复索取，属于合理的首轮确认）
_CONFIRM_Q_RE = re.compile(
    r"(mentioned\s+both|confirm\s+which|is\s+approximately|would\s+this\s+be"
    r"|shall\s+we|just\s+to\s+confirm|could\s+you\s+confirm)", re.I)

_VAGUE_QTY_RE = re.compile(
    r"\b(about|around|approx\.?|approximately|or\s+so|more\s+or\s+less)\b", re.I)
# 第五轮补丁 02：客户用 may/might/could/potentially/trial/initial/estimated 等
# 表达数量时，数量是 unconfirmed 的（首轮草稿不得写 "your order of X pcs"）
# 第五轮补丁 03（合并）：把 about/around/approximately/or so/more or less 也并入同一
# "unconfirmed 数量"集合 —— because 这些词的本质就是"不是 confirmed quantity"，
# 数量是 about/around X → 不应触发 quantity_confirm，也不应在草稿里出现
# "your order of X pcs" 等 confirmed 措辞。
_APPROX_HEDGE_RE = re.compile(
    r"\b(about|around|approx\.?|approximately|or\s+so|more\s+or\s+less|"
    r"may|might|could|possibly|potentially|likely|probably|estimated?|"
    r"initial\s+quantity|trial\s+order|potential\s+(?:annual|monthly)?\s*volume|"
    r"subject\s+to|expected\s+quantity|target\s+quantity|planned)\b", re.I)
_QTY_UNIT_RE = re.compile(
    r"(\d[\d,]*)\s*(pcs|pieces|units|pairs|sets|cartons|ctns)\b", re.I)
_URGENT_RE = re.compile(r"\b(urgent|asap|as\s+soon\s+as\s+possible|immediate\w*|rush)\b", re.I)
_DEADLINE_RE = re.compile(
    r"\b(?:within|in|by)\s+(\d+\s+(?:days?|weeks?|months?))\b", re.I)

# 未确认报价事实（第五轮优化六 8-11）
# 说明：只抓"被写成事实"的表述（带数字 / is / : ），
#      "advise you on pricing, MOQ and lead time" 这类"后续会提供"的承诺不算虚构。
_PRICE_RE = re.compile(r"(?:USD\s*|\$)\s?(\d[\d,]*(?:\.\d+)?)", re.I)
_MOQ_AS_FACT_RE = re.compile(r"\bmoq\b[^.\n]{0,20}\d|\bmoq\s*(?:is|:|=)", re.I)
_LEADTIME_AS_FACT_RE = re.compile(
    r"\b(?:lead\s+time|production\s+time)\b[^.\n]{0,25}?\d"
    r"|\b(?:lead\s+time|production\s+time)\s*(?:is|:|=|of)"
    r"|\bdeliver\w*[^.\n]{0,20}?\bwithin\s+\d", re.I)
# 未经确认的认证：只抓"我方持有/通过某认证"的主张，客户原话里的 CE 不算
_CERT_CLAIM_RE = re.compile(
    r"\b(?:we|our|i)\b[^.]{0,50}?\b(?:are|is|have|has|hold|provide|offer|supply|obtain)"
    r"[^.]{0,40}?\b(CE|RoHS|REACH|FDA|EN\s?71|BSCI|ISO\s?9001)\b"
    r"|\b(CE|RoHS|REACH|FDA|EN\s?71|BSCI|ISO\s?9001)\b[^.]{0,15}?\bcertified\b"
    r"|\bcertified\s+(?:to|for|with|by)\s+(CE|RoHS|REACH|FDA|EN\s?71)\b", re.I)
# 公司资料中真实存在的资质（config.json SELLER.strength），可以陈述不能编造更多
_COMPANY_CERT_ALLOWED = ("BSCI", "ISO9001", "ISO 9001")

# 自行创造的时间承诺（第五轮优化九）
_TIME_COMMIT_RE = re.compile(
    r"\b(?:we|we'll|we\s+will|we\s+shall|i\s+will)\b[^.?]{0,60}?"
    r"\b(?:within|in|by)\s+(\d+\s+(?:hours?|days?|weeks?))\b", re.I)


def _to_num(s):
    try:
        return int(round(float(str(s).replace(",", ""))))
    except Exception:
        return None


def _fmt(n):
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


# ===================== 1. 提问计划 =====================

def detect_explicit_requests(text: str) -> list:
    """客户明确提出的需求：catalog / samples / certifications / quotation。

    首轮回复必须先回应这些，再谈追问（第五轮优化七：优先级 P0 阻塞 < 客户明确请求，
    但"客户明确请求"必须被回应，不能被一堆资格审查问题淹没）。
    """
    low = (text or "").lower()
    return [key for key, pat in _REQUEST_PATTERNS if re.search(pat, low)]


# ===================== 0. Inquiry Intent → Sales Action（第五轮第三次优化） =====================
# 核心原则：Customer Intent → Sales Action → Minimum Necessary Question → Next Action
# 不再对所有询盘套同一套「产品→型号→规格→数量→报价」流程。

INTENT_CATALOG = "CATALOG_REQUEST"          # 客户明确要 Catalog / Brochure / 产品范围
INTENT_PRODUCT = "PRODUCT_INQUIRY"          # 客户明确询问某个产品
INTENT_RFQ = "RFQ"                          # 客户明确要求报价
INTENT_SOURCING = "PRODUCT_SOURCING"        # 客户找某类产品，尚未确定具体产品
INTENT_SAMPLE = "SAMPLE_REQUEST"            # 客户明确要求样品
INTENT_SPEC = "SPECIFICATION_INQUIRY"       # 客户询问规格 / 参数 / 尺寸 / 材料
INTENT_PRICE = "PRICE_INQUIRY"              # 客户主要询问价格
INTENT_GENERAL = "GENERAL_INQUIRY"          # 无法明确归类的普通询盘

INTENT_CN = {
    INTENT_CATALOG: "Catalog 请求（客户要产品目录/范围）",
    INTENT_PRODUCT: "产品咨询（客户在问某个产品）",
    INTENT_RFQ: "询价（RFQ，客户要求报价）",
    INTENT_SOURCING: "采购寻源（找某类产品，未定型号）",
    INTENT_SAMPLE: "样品请求（客户要样品）",
    INTENT_SPEC: "规格咨询（问规格/参数/材料）",
    INTENT_PRICE: "价格咨询（主要问价格）",
    INTENT_GENERAL: "普通询盘（无法明确归类）",
}

# 意图 → (Current Stage, Immediate Sales Action, Next Step)
_INTENT_SALES = {
    INTENT_CATALOG: ("Product Discovery",
                     "Respond to catalog request / prepare & send catalog",
                     "Customer reviews range → identify interested product category"),
    INTENT_PRODUCT: ("Product Evaluation",
                     "Match the named product against the product library",
                     "Confirm model/specification via candidates or reference"),
    INTENT_RFQ: ("Quotation Evaluation",
                 "Check quotation readiness",
                 "Confirm blocking product/specification/quantity information"),
    INTENT_SOURCING: ("Product Discovery",
                      "Understand sourcing need & show relevant range",
                      "Narrow down to product category"),
    INTENT_SAMPLE: ("Sample Evaluation",
                    "Respond to sample request",
                    "Identify the product for sampling"),
    INTENT_SPEC: ("Specification Confirmation",
                  "Provide/verify specifications from product data",
                  "Confirm missing specification details"),
    INTENT_PRICE: ("Price Evaluation",
                   "Check what is needed for accurate pricing",
                   "Confirm product/quantity for the price tier"),
    INTENT_GENERAL: ("Initial Contact",
                     "Understand the customer's need",
                     "Identify product interest"),
}

# 意图 → 首轮问题预算（硬上限仍是 MAX_QUESTIONS=3）
# CATALOG/SAMPLE：0-1 问（客户当前请求优先，型号/数量此时都不该问）
# RFQ：1-3 问；PRODUCT/SOURCING/SPEC/PRICE/GENERAL：与旧默认一致（≤2）
_INTENT_BUDGET = {
    INTENT_CATALOG: 1, INTENT_SAMPLE: 1,
    INTENT_RFQ: 3, INTENT_PRODUCT: 2, INTENT_SOURCING: 2,
    INTENT_SPEC: 2, INTENT_PRICE: 2, INTENT_GENERAL: 2,
}

# 意图识别正则（顺序即优先级；识别口径偏保守，避免改变既有 RFQ 行为）
_CATALOG_ASK_RE = re.compile(
    r"\b(catalog|catalogue|brochure|product\s+range|price\s+list)\b", re.I)
_RFQ_ASK_RE = re.compile(
    r"\b(quote|quotation|best\s+price|please\s+quote|\bmoq\b|"
    r"\bfob\b|\bcif\b|c\.i\.f)\b", re.I)
# 注意：故意不匹配裸 "price"/"pricing"——Anna 的 "discuss quantities and pricing"
# 是 Catalog 请求的后续意向，不是 RFQ。
_SAMPLE_ASK_RE = re.compile(r"\bsamples?\b", re.I)
_SAMPLE_VERB_RE = re.compile(
    r"\b(send|provide|need|require|request|like\s+to\s+receive|arrange)\b", re.I)
_SPEC_ASK_RE = re.compile(
    r"\b(?:what|which|could|can|please)[^.?\n]{0,35}\b"
    r"(specification|spec|dimension|parameter|material)\b"
    r"|\bspecifications?\s+(?:of|for)\b", re.I)
_PRICE_ASK_RE = re.compile(
    r"\b(?:what|which|how\s+much)[^.?\n]{0,25}\bprice\b|\bunit\s+price\b", re.I)
_SOURCING_RE = re.compile(
    r"\b(sourc\w+|suppliers?|looking\s+for|looking\s+to\s+buy|where\s+to\s+buy)\b", re.I)


def classify_inquiry_intent(text: str, info: dict = None) -> dict:
    """识别询盘意图（第五轮第三次优化核心）。

    优先级：CATALOG_REQUEST → SAMPLE_REQUEST → RFQ → SPECIFICATION_INQUIRY
            → PRICE_INQUIRY → PRODUCT_INQUIRY → PRODUCT_SOURCING → GENERAL_INQUIRY

    口径说明：
      - RFQ 覆盖 Catalog：客户同封邮件既要 catalog 又要 best price/MOQ（如
        "Please send us your best price... Please also send your latest catalog"）
        本质是询价，Catalog 只是附带请求（detect_explicit_requests 仍会记录它）。
      - 纯 Catalog 请求（无任何报价/数量要求，如 Anna 的 Test 05）才是 CATALOG_REQUEST。
    返回 {intent, intent_cn, sales_stage, immediate_action, next_step}
    """
    info = info or {}
    low = (text or "").lower()
    rfq_hit = bool(_RFQ_ASK_RE.search(low))
    catalog_hit = bool(_CATALOG_ASK_RE.search(low))
    named_product = bool(customer_product_phrase(text)) or bool(
        _norm(info.get("product_query")))

    if catalog_hit and not rfq_hit:
        intent = INTENT_CATALOG
    elif _SAMPLE_ASK_RE.search(low) and _SAMPLE_VERB_RE.search(low) \
            and not rfq_hit and not catalog_hit:
        intent = INTENT_SAMPLE
    elif rfq_hit:
        intent = INTENT_RFQ
    elif _SPEC_ASK_RE.search(low):
        intent = INTENT_SPEC
    elif _PRICE_ASK_RE.search(low):
        intent = INTENT_PRICE
    elif named_product:
        intent = INTENT_PRODUCT
    elif _SOURCING_RE.search(low):
        intent = INTENT_SOURCING
    else:
        intent = INTENT_GENERAL

    stage, action, next_step = _INTENT_SALES[intent]
    return {
        "intent": intent,
        "intent_cn": INTENT_CN[intent],
        "sales_stage": stage,
        "immediate_action": action,
        "next_step": next_step,
    }


def quantity_values(text: str) -> list:
    """原文里出现的所有带单位数量（用于识别数量冲突，第五轮测试 C）。"""
    vals = []
    for m in _QTY_UNIT_RE.finditer(text or ""):
        v = _to_num(m.group(1))
        if v and v not in vals:
            vals.append(v)
    return vals


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


# ---- 客户原文里的产品信号（第五轮补丁：先查库再问人的输入） ----
_SPEC_TOKEN_RE = re.compile(r"\b\d+\s*(?:ml|l|oz|cl|cm|mm|inch|g|kg)\b", re.I)
_MATERIAL_RE = re.compile(
    r"\b(neoprene|silicone|latex|pvc|tpu|eva|stainless\s+steel|glass|aluminum|"
    r"microfiber|nylon|polyester)\b", re.I)
_OBJECT_RE = re.compile(
    r"\b(?:interested\s+in|looking\s+for|looking\s+at|need|needs|want|require|"
    r"would\s+like(?:\s+to\s+(?:buy|order|purchase))?|planning(?:\s+to\s+buy)?)\s+"
    r"([a-z0-9][a-z0-9\s\-/&]{2,60})", re.I)
# 这些"产品词"其实没有给出任何产品方向
_GENERIC_OBJECT_RE = re.compile(
    r"^(?:your|the|some|any|these|those|all|more|several|a|an|our)?\s*"
    r"(?:products?|items?|goods|things|stuff|models?|catalog|catalogue|brochure|range|"
    r"specifications?|spec\s?sheets?|details|information|quotation|quote|price|prices|"
    r"pricing|cost|offer|samples?|list)\b", re.I)


def customer_product_phrase(text: str) -> str:
    """从客户原文里提取"客户自己说的产品"（不是我们猜的）。

    例："We are interested in Stainless Steel Water Bottle, 500ml or 750ml"
        → "Stainless Steel Water Bottle"
    只回客户原话，绝不猜测、绝不补全。
    """
    for m in _OBJECT_RE.finditer(text or ""):
        raw = re.split(r"[,.;:\n]|\b(?:for|with|and|or|in|at|to|of|from)\b",
                       m.group(1), maxsplit=1)[0]
        raw = _norm(raw).strip(" ,.-")
        # 第五轮第二次补丁 03：去掉引导冠词/物主代词（与 insight.customer_product_phrase
        # 同源）——"your Stainless Steel Water Bottle" → "Stainless Steel Water Bottle"
        raw = re.sub(r"^(?:your|our|the|a|an|some|own)\s+", "", raw,
                     flags=re.I).strip(" ,.-")
        if not raw or _GENERIC_OBJECT_RE.match(raw):
            continue
        if len(raw.split()) > 6:
            raw = " ".join(raw.split()[:6])
        return raw
    return ""


def spec_tokens(text: str) -> list:
    """客户原文里出现的规格词（容量/尺寸/重量），用于"确认已理解需求"。"""
    out = []
    for m in _SPEC_TOKEN_RE.finditer(text or ""):
        v = re.sub(r"\s+", "", m.group(0)).lower()
        if v not in out:
            out.append(v)
    return out[:3]


def detect_match_state(text: str, info: dict, product, matches=None) -> dict:
    """产品匹配四态（第五轮补丁核心）。

    处理顺序：客户需求 → 产品匹配 → 产品库检索 → 候选产品 → 客户选择 → 报价准备。
    只有在产品库给不出有效匹配、或信息不足以做匹配时，才向客户要 Model / Reference / Design。

    返回 {state, state_cn, customer_product, specs, candidates, has_spec}
    """
    info = info or {}
    matches = matches or []
    # 强匹配：规则命中关键词 或 LLM 置信度 ≥ 0.5（与 main.py 的 reply_product 口径一致）
    strong = [m for m in matches
              if m.get("hit_keywords") or (m.get("match_score") or 0) >= 0.5]
    candidates = [m for m in matches
                  if m.get("hit_keywords") or (m.get("match_score") or 0) >= 0.25]
    phrase = customer_product_phrase(text) or _norm(info.get("product_query"))
    specs = spec_tokens(text)
    has_spec = bool(specs) or bool(_MATERIAL_RE.search(text or ""))

    if product or strong:
        state = MATCH_MATCHED
    elif candidates:
        state = MATCH_PARTIAL
    elif phrase or has_spec:
        # 客户说清了产品方向，但产品库检索后确实没有对应品类
        state = MATCH_NONE
    else:
        # 连产品方向都没有（"interested in your products"）→ 不足以做匹配
        state = MATCH_INSUFFICIENT

    return {
        "state": state,
        "state_cn": MATCH_STATE_CN[state],
        "customer_product": phrase,
        "specs": specs,
        "has_spec": has_spec,
        "candidates": [m.get("name") for m in (strong or candidates)[:3] if m.get("name")],
        "strong_count": len(strong),
    }


def build_question_plan(text: str, info: dict, product, insight: dict = None,
                        matches: list = None) -> dict:
    """生成首轮提问计划：P0/P1/P2 分级 + 实际要问的问题（≤3，默认 1-2）。

    返回：
      {
        "match_state": "MATCHED" | "PARTIAL_MATCH" | "NO_MATCH" | "INSUFFICIENT_INFORMATION",
        "customer_product": "客户原话里的产品", "specs": [...], "candidates": [...],
        "p0": [...], "p1": [...], "p2": [...],     # 分级明细
        "selected": [...],                          # 首轮真正要问的（已限流）
        "max_questions": int, "target_questions": int,
        "explicit_requests": [...],                 # 客户明确提出的需求
        "note": "..."                               # 给 LLM 的口径说明
      }
    每个问题项：{field, priority, tier_label, why, question}
    """
    text = text or ""
    info = info or {}
    insight = insight or {}
    low = text.lower()
    qr = insight.get("quotation_readiness") or {}
    prelim = qr.get("blockers_preliminary") or []
    partial = insight.get("partially_confirmed") or []
    cert = insight.get("certification") or certify_split(text)

    # ---- 第五轮第三次优化：先识别意图，再决定首轮销售动作 ----
    intent_info = classify_inquiry_intent(text, info)
    intent = intent_info["intent"]
    intent_budget = _INTENT_BUDGET.get(intent, DEFAULT_MAX)
    # Catalog / Sample 请求：数量与型号在当前阶段都不是必要信息，
    # 客户当前明确要求（catalog/样品）必须优先响应——不许倒过来要资格审查。
    request_first = intent in (INTENT_CATALOG, INTENT_SAMPLE)

    p0, p1, p2 = [], [], []
    mstate = detect_match_state(text, info, product, matches)
    state = mstate["state"]
    phrase = mstate["customer_product"]
    specs = mstate["specs"]

    # ---- P0-0 客户当前明确要求（第五轮第三次优化 §八：显式客户请求优先级最高） ----
    if request_first:
        p0.append({
            "field": "explicit_request_response", "priority": "P0",
            "tier_label": TIER_CN["P0"],
            "why": (f"客户意图 = {INTENT_CN[intent]}：当前明确要求必须优先响应"
                    "（这是动作，不是问题）；型号/图片/数量在此阶段都不需要"),
            "question": "",       # 动作而非问题：由草稿模板直接回应
            "action": intent_info["immediate_action"],
        })
        # §八：显式请求优先级——Catalog=P0，Product Category=P1，Quantity=P2
        # （数量在当前阶段是正常 UNKNOWN 状态，既不升 P0 也不丢进 CRM 盲区）
        p2.append({
            "field": "quantity", "priority": "P2", "tier_label": TIER_CN["P2"],
            "why": "Intent=Catalog/Sample：客户浏览产品范围之后才会谈数量，"
                   "当前阶段数量 UNKNOWN 是正常状态（不为完整询盘强制问数量）",
            "question": "（首轮禁止提问）",
        })

    # ---- P0-1 产品（第五轮补丁：先查库，再问人，绝不默认索取型号） ----
    if not request_first:
        if state == MATCH_MATCHED:
            # 情况A：库内有匹配 → 不问型号；多个候选时让客户"选"，而不是"报型号"
            if mstate["strong_count"] >= 2:
                names = " and ".join(mstate["candidates"][:2])
                p0.append({
                    "field": "model_selection", "priority": "P0",
                    "tier_label": TIER_CN["P0"],
                    "why": "产品库有多个匹配候选，让客户在候选里选即可，不必索取型号",
                    "question": (f"We can supply both {names} for this - would you like us to "
                                 f"quote both for comparison, or do you already have a "
                                 f"preferred one?"),
                })
            # 单选/唯一匹配：不给产品问题，直接用产品数据进入报价准备
        elif state == MATCH_PARTIAL:
            # 情况A（弱）：有候选 → 先抛候选让客户确认，不问型号
            names = mstate["candidates"][:2]
            if names and (product or phrase):
                joined = " and ".join(names)
                p0.append({
                    "field": "candidate_confirm", "priority": "P0",
                    "tier_label": TIER_CN["P0"],
                    "why": "产品库有接近的候选，先用已有数据给方案，再让客户确认",
                    "question": (f"Based on your description, our {joined} would be the closest "
                                 f"match - would you like us to proceed with this, or send you "
                                 f"the options for comparison first?"),
                })
            else:
                p0.append({
                    "field": "candidate_confirm", "priority": "P0",
                    "tier_label": TIER_CN["P0"],
                    "why": "有候选但不足以确认，让客户在现有方案里确认，而不是索取型号",
                    "question": ("Could you confirm which item in our range you are referring "
                                 "to - a photo or a link would help us confirm the exact "
                                 "specification?"),
                })
        elif state == MATCH_NONE:
            # 情况B：库里确实没有 → 不虚构；只问完成匹配必需的 1 个信息
            # 第五轮第二次补丁 03：此时客户品类已明确，缺的是层级里的 Model 层——
            # 问 reference model / 图片是正确的；绝不退回问"要什么产品类别"
            p0.append({
                "field": "reference_model", "priority": "P0", "tier_label": TIER_CN["P0"],
                "why": (f"客户已明确产品品类（{phrase or '含规格描述'}），但产品库没有对应品类——"
                        "按层级 Category→Type→Model→Spec→Final Selection，当前缺 Model 层，"
                        "此时才向客户索取参考型号/图片/设计"),
                "question": ("Could you share a reference model, a photo or the design you have "
                             "in mind, so we can confirm the exact specification and check the "
                             "closest options?"),
            })
        else:
            # 情况C：连产品方向都没有 → 按产品信息层级（第五轮补丁 02）：
            #      Product Category → Product → Model → Specification
            #      既然连 Product Category / Product Type 都没提供，**不应**问 model
            #      （model 是更细的层级，没有 category 时问 model 没有意义）。
            p0.append({
                "field": "product_category", "priority": "P0", "tier_label": TIER_CN["P0"],
                "why": "客户未给出任何产品方向（无 Product Category / Product Type），"
                       "应先询问产品类别，再谈具体产品/型号",
                "question": ("Could you let us know which product category or product type "
                             "you are interested in? Once we know the category we can "
                             "match the closest items from our catalog and check "
                             "applicable pricing, MOQ and lead time."),
            })
    else:
        # 第五轮第三次优化 §四：Catalog/Sample 请求的正确销售流程
        #   Customer asks for Catalog → Respond → Provide/Prepare Catalog
        #   → 客户浏览范围 → 客户指出感兴趣的品类 → Product Matching → ... → Quotation
        # 绝不反过来：不许先要 model/photo/quantity 才肯给 catalog。
        if state == MATCH_INSUFFICIENT or (state == MATCH_NONE and not phrase):
            # 连品类都不知道：唯一允许的 0-1 问，聚焦"哪个品类"，不带任何前置条件
            p1.append({
                "field": "product_category", "priority": "P1",
                "tier_label": TIER_CN["P1"],
                "why": "Intent 驱动（第五轮第三次优化）：当前动作是响应客户明确请求；"
                       "唯一有价值的下一步是把范围聚焦到品类（首轮 0-1 问）",
                "question": ("Which product category or product type are you mainly "
                             "interested in? We will then share the relevant catalog "
                             "section with specifications for your review."),
                "ask_in_first_reply": True,
            })
        elif state == MATCH_NONE and phrase:
            p1.append({
                "field": "product_category", "priority": "P1",
                "tier_label": TIER_CN["P1"],
                "why": f"客户品类已明确（{phrase}），无需再问，下一步是准备对应产品资料",
                "question": "（不作为首轮问题：品类已明确，直接准备对应 Catalog/资料）",
            })
        # MATCHED / PARTIAL_MATCH：产品库已有数据，0 问，直接响应请求

    # ---- P0-2 客户给了候选规格但没定（500ml or 750ml）→ 方案式追问，不是重复询问 ----
    open_options = find_open_options(text)
    if open_options:
        spec_item = next((p for p in partial
                          if p["field"] in ("capacity", "size", "specification")), None)
        q = (spec_item or {}).get("question") or (
            f'Regarding "{open_options[0]}" - shall we quote both options side by side so '
            f'you can compare, or do you already lean toward one?')
        p0.append({
            "field": "specification", "priority": "P0", "tier_label": TIER_CN["P0"],
            "why": f'客户给了候选「{open_options[0]}」但未最终确认，SKU 与成本无法锁定',
            "question": q,
        })

    # ---- P0-3 数量：完全没给 / 冲突 / 大概值（三种处理完全不同） ----
    # 第五轮补丁 02：当原文含 hedge 词（may/might/could/potentially/trial/
    # initial/estimated 等）时，客户表达的不是"我要订 X pcs"，而是"首单可能 X"
    # /"初期大概 X"/"trial 500 pcs"——这些都不应进入首轮 P0 数量追问。
    # 第五轮补丁 03：hedge_present 仅在数量上下文附近 50 字符内有 hedge 词时为 True，
    # 避免 "We may need certificates" 这类非数量 hedge 误触（Test 04 验证）。
    qvals = quantity_values(text)
    qty = info.get("quantity")
    hedge_per_val = _quantity_values_in_hedge_context(text, qvals) if qvals else {}
    main_hedge = _qty_in_hedge_context(text, qty)
    hedge_present = main_hedge or any(hedge_per_val.values())
    all_qty_hedge = bool(qvals) and all(hedge_per_val.values())

    # 多数量语义补丁：先给每个数量定商业语义（试单/报价/首单/潜在/年度），
    # 只有「同一语义角色出现 ≥2 个不同数值」才是真冲突。
    # 客户已说明用途的数量（500 试单 + 2,000 报价 + 5,000 潜在量）各归各位，
    # 绝不允许生成 "You mentioned both 2,000 and 500 - could you confirm..."。
    sem_list = info.get("quantity_semantics")
    if sem_list is None:
        sem_list = extract_quantity_semantics(text)
    conflict_groups = semantic_conflict_roles(sem_list)
    semantics_clear = len(qvals) >= 2 and not conflict_groups
    quotation_qty = next((s["value"] for s in sem_list
                          if s["role"] == QUANTITY_QUOTATION), None)

    # 第五轮第三次优化 §六：不要为了完整询盘而强制询问数量——
    # 只有当数量对当前业务动作确实必要时才问（RFQ/价格咨询需要；
    # Catalog/Sample 请求阶段 quantity=UNKNOWN 是正常状态，不升 P0）。
    if request_first:
        # Catalog/Sample 请求：数量在当前阶段完全不需要 → 不进 P0（正常 UNKNOWN 状态）
        pass
    elif hedge_present and (main_hedge or all_qty_hedge):
        # 主量是 hedge 或 所有 qval 都 hedge → 全部跳过：
        # 既不视为冲突，也不进 P0 quantity_confirm。
        # 草稿也不得用 "your order of X pcs" 这种 confirmed 措辞（validate_reply_strategy ⑮）
        pass
    elif semantics_clear:
        # 数量语义补丁：客户已用上下文说明各数量的用途 → 语义已清楚，
        # 绝不把能从上下文推断的问题重新丢回客户（不追问数量）
        pass
    elif conflict_groups and len(qvals) >= 2:
        # 真冲突：同一语义角色（或都无法判断用途）出现多个不同数值。
        # 只对冲突数值本身追问，不把语义已区分的数量拉进来。
        crole, cvals = next(iter(conflict_groups.items()))
        a, b = _fmt(cvals[0]), _fmt(cvals[-1])
        p0.append({
            "field": "quantity_conflict", "priority": "P0", "tier_label": TIER_CN["P0"],
            "why": (f"原文出现两个用途相同的数量（{a} / {b}），"
                    "上下文无法区分哪个适用于当前订单，单价档位无法确定"),
            "question": (f"You mentioned both {a} and {b} - could you confirm which "
                         f"quantity applies to this first order?"),
        })
    elif not qty and not qvals:
        p0.append({
            "field": "quantity", "priority": "P0", "tier_label": TIER_CN["P0"],
            "why": "客户未给出采购数量，无法核算单价档位",
            "question": "What order quantity should we quote for?",
        })
    elif qty and _VAGUE_QTY_RE.search(low) and not main_hedge:
        # 大概值（about/around/approximately 等，但无 hedge 词）：
        # 只做确认，绝不改问 "How many pieces do you need?"
        p0.append({
            "field": "quantity_confirm", "priority": "P0", "tier_label": TIER_CN["P0"],
            "why": f"客户给的是大概数量 {_fmt(qty)}，需确认是否为首单预期数量",
            "question": (f"Is approximately {_fmt(qty)} pcs your expected initial order "
                         f"quantity?"),
            "soft": True,      # 软确认：只有在问题数还有余量时才问
        })

    # ---- P1：后续阶段确认（首轮默认不问） ----
    if cert.get("certification_status") == CERT_REQUIRED:
        p1.append({
            "field": "certification", "priority": "P1", "tier_label": TIER_CN["P1"],
            "why": "客户明确提出认证要求，正式报价前需核对我方能否满足（不是首轮追问项）",
            "question": "（不作为首轮问题：先内部核对，必要时在后续邮件确认）",
        })
    if not info.get("country"):
        p1.append({
            "field": "destination", "priority": "P1", "tier_label": TIER_CN["P1"],
            "why": "目的港/国家未知，影响运费与正式报价",
            "question": "（不作为首轮问题：可在初步报价阶段一并确认）",
        })
    if not _DEADLINE_RE.search(low) and _URGENT_RE.search(low):
        p1.append({
            "field": "delivery_time", "priority": "P1", "tier_label": TIER_CN["P1"],
            "why": "客户表达紧急但无明确交期天数，报价前需与生产核排产",
            "question": "（不作为首轮问题：不要主动追问交期，避免施压）",
        })
    if not re.search(r"\b(custom\w*|logo|oem|odm|private\s+label|print\w*|emboss\w*)\b", low):
        p1.append({
            "field": "customization", "priority": "P1", "tier_label": TIER_CN["P1"],
            "why": "客户未提定制需求，打样/正式报价阶段确认即可",
            "question": "（不作为首轮问题）",
        })

    # ---- P2：边谈边补，首轮绝不问 ----
    _p2_checks = [
        ("incoterm", r"\b(fob|cif|exw|ddp|ddu|dap|fca)\b"),
        ("payment_terms", r"\b(t\s?/\s?t|l\s?/\s?c|paypal|western\s?union|deposit|payment\s+terms?)\b"),
        ("packaging", r"\b(packag\w*|poly\s?bag|blister|gift\s?box|barcode)\b"),
        ("target_price", r"\b(target\s+price|budget)\b"),
    ]
    for key, pat in _p2_checks:
        if not re.search(pat, low):
            p2.append({
                "field": key, "priority": "P2", "tier_label": TIER_CN["P2"],
                "why": "可后续确认，不阻塞当前下一步动作",
                "question": "（首轮禁止提问）",
            })
    if not (info.get("email") or re.search(r"[\w\.\-]+@[\w\-]+\.\w+", text or "")):
        p2.append({
            "field": "email", "priority": "P2", "tier_label": TIER_CN["P2"],
            "why": "询盘已通过邮件/平台渠道进入系统，回复直接走原渠道，不索要邮箱",
            "question": "（首轮禁止提问）",
        })

    # ---- 限流选取：硬 P0 优先，软确认项只在有余量时补 ----
    # 第五轮第三次优化：问题预算按意图调整（CATALOG/SAMPLE 0-1 问，RFQ ≤3）
    hard_p0 = [q for q in p0 if not q.get("soft")]
    soft_p0 = [q for q in p0 if q.get("soft")]
    if request_first:
        # 客户当前明确请求优先响应（P0 动作项不进问句）；
        # 唯一允许的首轮问题 = 聚焦品类的 P1 问题（预算 0-1 个）
        selected = [q for q in p1 if q.get("ask_in_first_reply")][:intent_budget]
    else:
        selected = hard_p0[:intent_budget]
        # 默认 1-2：只有硬 P0 真的有 3 个时才用到第 3 个名额
        if len(selected) > DEFAULT_MAX and len(hard_p0) < MAX_QUESTIONS:
            selected = selected[:DEFAULT_MAX]
        # 软确认项（如 "3,000 pcs 是否为首单数量"）：
        #   只有在没有 hard P0 时才附加 —— 否则会淹没真正的阻塞项
        #   示例 1：product 已确认 + quantity vagueness → 可附加 quantity_confirm
        #           （hard P0 缺失，软确认填补空位）
        #   示例 2：product 未知 + quantity vagueness → hard P0 = product_category
        #           已占满名额，**不**附加软确认（避免多个产品类问题淹没真阻塞）
        #   示例 3：只有一个 hard P0（如 reference_model）+ 数量 vagueness →
        #           不附加软确认 → 仍只问 1 个问题（贴合第五轮补丁 03：单 P0 时 1 问）
        if soft_p0 and not hard_p0 and len(selected) < MAX_QUESTIONS:
            selected.append(soft_p0[0])

    return {
        "match_state": state,
        "match_state_cn": mstate["state_cn"],
        "customer_product": phrase,
        "specs": specs,
        "candidates": mstate["candidates"],
        "p0": p0, "p1": p1, "p2": p2,
        "selected": selected,
        "max_questions": MAX_QUESTIONS,
        "target_questions": min(len(selected), DEFAULT_MAX) or 0,
        "explicit_requests": detect_explicit_requests(text),
        # ---- 第五轮第三次优化：Intent → Sales Action ----
        "intent": intent,
        "intent_cn": intent_info["intent_cn"],
        "sales_stage": intent_info["sales_stage"],
        "immediate_action": intent_info["immediate_action"],
        "next_step": intent_info["next_step"],
        "question_budget": intent_budget,
        "hedge_present": hedge_present,           # 第五轮补丁 02
        "quantity_semantics": sem_list,           # 多数量语义补丁
        "quotation_quantity": quotation_qty,      # 客户明确要求报价的数量（无则 None）
        "quantity_conflict_groups": conflict_groups,  # 真冲突角色组（空 = 无冲突）
        "note": ("首轮回复只问 P0；问题数默认 1-2 个，绝对不超过 "
                 f"{MAX_QUESTIONS} 个。每个问题都要能回答："
                 "「客户不回答，我们下一步是不是真的做不下去？」"
                 f"（当前意图 {intent}，预算 {intent_budget} 问）"),
    }


def question_is_necessary(q: dict) -> bool:
    """这个问题是否真的必要（P0 才算必要）。"""
    return (q or {}).get("priority") == "P0"


def plan_to_prompt(plan: dict) -> str:
    """把提问计划渲染成给 LLM 的硬指令文本。"""
    rule = {
        MATCH_MATCHED: ("PRODUCT LIBRARY HAS A MATCH - do NOT ask which model / reference / "
                        "design they want. Present the matched product data you already have "
                        "and let the buyer choose."),
        MATCH_PARTIAL: ("PRODUCT LIBRARY HAS CANDIDATES - do NOT ask which model they want. "
                        "Offer the candidates we have and ask the buyer to confirm / choose."),
        MATCH_NONE: ("NO MATCH IN PRODUCT LIBRARY - do not invent any product. You may ask "
                     "for a reference model, a photo or the design (that is the ONE thing we "
                     "cannot solve with our own data)."),
        MATCH_INSUFFICIENT: ("INSUFFICIENT INFORMATION TO MATCH - ask only what is strictly "
                             "needed to complete product matching."),
    }.get(plan.get("match_state"), "")
    lines = []
    # 第五轮第三次优化：意图 → 销售动作硬指令
    intent = plan.get("intent")
    if intent == INTENT_CATALOG:
        lines.append("[CUSTOMER INTENT: CATALOG_REQUEST] Respond to the catalog request "
                     "FIRST. Do NOT demand model/photo/design/quantity before providing "
                     "it. You may invite the buyer to indicate the product category they "
                     "are interested in (0-1 question). Never claim the catalog is "
                     "attached if no catalog file exists - say we can prepare/share it.")
    elif intent == INTENT_SAMPLE:
        lines.append("[CUSTOMER INTENT: SAMPLE_REQUEST] Respond to the sample request "
                     "FIRST. Do NOT demand model/quantity before that. Never promise "
                     "samples are ready to ship without data.")
    elif intent:
        lines.append(f"[CUSTOMER INTENT: {intent}] Immediate action: "
                     f"{plan.get('immediate_action', '')}")
    if rule:
        lines.append(f"[PRODUCT MATCHING STATE: {plan.get('match_state')}] {rule}")
    sel = plan.get("selected") or []
    if not sel:
        lines.append("(no P0 blocker - do NOT ask any question; respond to the buyer's "
                     "request and state the next step we will take)")
        return "\n".join(lines)
    for i, q in enumerate(sel, 1):
        lines.append(f"{i}. [{q['field']}] {q['question']}   (why: {q['why']})")
    lines.append(f"TOTAL ALLOWED: {len(sel)} question(s). Do not add any other question.")
    return "\n".join(lines)


def understanding_prompt(plan: dict) -> str:
    """把"我们已经理解到的客户需求"渲染成回显句素材（只用客户原话）。"""
    parts = []
    if plan.get("customer_product"):
        parts.append(f'the buyer said: "{plan["customer_product"]}"')
    if plan.get("specs"):
        parts.append("specification mentioned: " + " / ".join(plan["specs"]))
    return "; ".join(parts) or "(the buyer did not name any specific product)"


def never_ask_prompt() -> str:
    return ("Payment terms / T-T / L-C / deposit; trade terms / Incoterm / FOB / CIF / EXW; "
            "target price / budget; packaging details; the buyer's e-mail address; "
            "which certification they need (offer our own list instead). "
            "Also never re-ask what the buyer already told us (quantity, colour, logo/OEM, "
            "packaging, destination, urgency).")


# ===================== 2. 草稿自检（第五轮优化十） =====================

def _body_of(draft: str) -> str:
    """去掉 Subject 行，只取正文。"""
    d = draft or ""
    d = re.sub(r"^\s*subject\s*:?[^\n]*\n+", "", d, flags=re.I)
    return d


def question_sentences(draft: str) -> list:
    body = _body_of(draft)
    out = []
    for s in re.split(r"(?<=[?])\s+|\n", body):
        s = s.strip()
        if s.endswith("?") and len(s) > 8:
            out.append(s)
    return out


def count_questions(draft: str) -> int:
    return max(len(question_sentences(draft)), _body_of(draft).count("?"))


def _known_fields(text: str, info: dict) -> dict:
    low = (text or "").lower()
    return {
        "quantity": bool(info.get("quantity")),
        "customization": bool(re.search(
            r"\b(custom\w*|logo|oem|odm|private\s+label|print\w*|emboss\w*)\b", low)),
        "color": bool(re.search(
            r"\b(black|white|blue|red|green|yellow|pink|grey|gray|navy|purple|orange)\b", low)),
        "packaging": bool(re.search(r"\b(packag\w*|poly\s?bag|blister|gift\s?box|barcode)\b", low)),
        "destination": bool(info.get("country")) or bool(
            re.search(r"\b(deliver\w*\s+to|ship\s+to|destination)\b", low)),
        "deadline": bool(_DEADLINE_RE.search(low) or _URGENT_RE.search(low)),
    }


def validate_reply_strategy(draft: str, text: str, info: dict, product,
                            insight: dict = None, catalog_products=None,
                            previous: dict = None, plan: dict = None,
                            matches: list = None) -> list:
    """首轮回复自检 14 项 + 第五轮补丁 02 新增 5 项 = 19 项。返回问题列表，空列表 = 通过。

    previous（可选，跨询盘污染用）= {
        "product_names": [...], "quantity": int, "customer_name": str, "company": str
    }
    plan（可选）：首轮提问计划。如果未传，内部按当前参数重算（用于 _template 等
                  没有上游 plan 的场景）。
    matches（可选）：matcher.match(text) 的结果。如果未传，假设 []。
    """
    issues = []
    if not draft or not draft.strip():
        return ["草稿为空"]

    draft_low = draft.lower()
    body = _body_of(draft)
    body_low = body.lower()
    info = info or {}
    insight = insight or {}
    qs = question_sentences(draft)

    # 第五轮补丁 02：plan 参数化（caller 没传时按当前 info/product 重新算一份）
    if plan is None:
        try:
            plan = build_question_plan(text or "", info, product, insight,
                                        matches=matches or [])
        except Exception:
            plan = {"selected": [], "match_state": MATCH_INSUFFICIENT, "explicit_requests": []}

    # ① 问题数量是否超过 3 个
    n_q = count_questions(draft)
    if n_q > MAX_QUESTIONS:
        issues.append(f"首轮回复问了 {n_q} 个问题，超过上限 {MAX_QUESTIONS} 个"
                      f"（信息采集表式回复，客户回复成本过高）")

    # ② 是否询问了非 P0 信息（P1/P2 出现在问句里）
    for topic, pat in _NEVER_ASK_PATTERNS.items():
        for q in qs:
            if re.search(pat, q, re.I):
                issues.append(f"首轮问句涉及非 P0 信息「{topic}」：{_norm(q)[:70]}")
                break

    # ③ 是否重复询问客户已经提供的信息
    known = _known_fields(text, info)
    for field, pat in _REASK_PATTERNS:
        if not known.get(field):
            continue
        for q in qs:
            if _CONFIRM_Q_RE.search(q):
                continue          # 确认/澄清式问法不算重复索取
            if re.search(pat, q, re.I):
                issues.append(f"重复追问客户已提供的信息「{field}」：{_norm(q)[:70]}"
                              f"（已给信息应直接确认，不能重新问一遍）")
                break

    # ④ 是否引用了当前 Inquiry 之外的数据（无确认产品时禁止出现产品库产品名）
    if not product and not _norm(info.get("product_query")):
        for p in (catalog_products or []):
            for name in (p.get("name"), p.get("name_cn")):
                if name and len(name) >= 5 and name.lower() in draft_low:
                    issues.append(f"邮件出现当前询盘之外的产品「{name}」（客户未指明产品）")
                    break

    # ⑤⑥⑦ 跨询盘污染：上一条询盘的产品 / 客户名称 / 数量
    prev = previous or {}
    for name in (prev.get("product_names") or []):
        if name and len(name) >= 4 and name.lower() in draft_low:
            issues.append(f"邮件出现上一条询盘的产品「{name}」（跨询盘污染）")
    for key in ("customer_name", "company"):
        v = prev.get(key)
        if v and len(v) >= 4 and v.lower() in draft_low \
                and v.lower() not in (text or "").lower():
            issues.append(f"邮件出现上一条询盘的客户信息「{v}」（跨询盘污染）")
    prev_qty = prev.get("quantity")
    if prev_qty:
        pq = _to_num(prev_qty)
        cur = _to_num(info.get("quantity"))
        if pq and pq != cur and re.search(rf"{pq:,}|{pq}", body):
            issues.append(f"邮件出现上一条询盘的数量 {pq}（跨询盘污染）")

    # ⑧⑨⑩ 未经确认的价格 / MOQ / 交期
    if not product:
        if _PRICE_RE.search(draft):
            issues.append("未确认产品，邮件却出现价格（禁止虚构报价）")
        if _MOQ_AS_FACT_RE.search(draft_low):
            issues.append("未确认产品，邮件却给出具体 MOQ 数字（禁止虚构）")
        if _LEADTIME_AS_FACT_RE.search(draft_low):
            issues.append("未确认产品，邮件却给出具体交期（禁止虚构）")

    # ⑪ 未经确认的认证（只能陈述公司资料中真实存在的资质）
    for m in _CERT_CLAIM_RE.finditer(draft):
        token = next((g for g in m.groups() if g), "")
        token = re.sub(r"\s+", "", token).upper()
        allowed = [re.sub(r"\s+", "", c).upper() for c in _COMPANY_CERT_ALLOWED]
        if token and token not in allowed:
            issues.append(f"邮件出现未经确认的认证主张「{token}」"
                          f"（公司资料仅有：{'、'.join(_COMPANY_CERT_ALLOWED)}）")

    # ⑫ 是否把 Preferred Deadline 写成 Hard Deadline / 自行承诺时间
    for m in _TIME_COMMIT_RE.finditer(body):
        period = m.group(1)
        if period.lower() not in (text or "").lower():
            issues.append(f"邮件自行承诺时间「within {period}」，原文无此依据"
                          f"（禁止创造时间承诺）")

    # ⑬ Quote Readiness 与邮件内容是否一致
    status = normalize_status(
        (insight.get("quotation_readiness") or {})
        .get("quotation_readiness_status", ""))
    if status == STATUS_INSUFFICIENT:
        promise = re.search(
            r"\b(?:we|we'll|we\s+will|we\s+shall)\b[^.?]{0,40}?"
            r"\b(?:send|provide|share|submit|issue|prepare)\b[^.?]{0,30}?"
            r"\b(?:quotation|quote|price|pricing|offer)\b", body_low)
        # "we will NOT send a blind price" 这种否定式说明不算承诺，排除掉
        if promise and not re.search(r"\b(not|never|cannot|can't|won't|unable)\b",
                                     promise.group(0)):
            issues.append("报价准备度为「信息不足」，邮件却承诺发出报价"
                          f"：{_norm(promise.group(0))[:60]}")
        timed = re.search(
            r"\b(?:quotation|quote|price|pricing|offer)\b[^.?]{0,30}?\bwithin\s+\d+\s+"
            r"(?:hours?|days?)\b", body_low)
        if timed:
            issues.append("邮件承诺了带时限的报价（无公司 SLA 数据支持，禁止自行创造）")

    # ⑭ 是否真正回应了客户提出的 Catalog Request
    reqs = detect_explicit_requests(text)
    if "catalog" in reqs:
        if not re.search(r"\b(catalog|catalogue|product\s+range|price\s+list|"
                         r"product\s+information|specification[s]?)\b", body_low):
            issues.append("客户明确索取 Catalog / 产品资料，邮件却未回应这一需求")

    # ---- 第五轮补丁 02：5 项新检查（⑮ ⑯ ⑰ ⑱ ⑲） ----

    # ⑮ 是否把 hedge 数量当成 confirmed quantity（草稿不得写 "your order of X pcs" /
    #     "your confirmed quantity" 等）
    # 第五轮补丁 03：仅在"数量上下文有 hedge 词"时启用此检查；
    # 避免 "We may need certificates" 这类非数量 hedge 误触（Test 04 验证）。
    text_low = (text or "").lower()
    qvals_in_text = []
    for m in re.finditer(r"(\d[\d,]*)\s*(?:k|pcs|pieces|units|pairs|sets|cartons|ctns)\b",
                         text_low):
        try:
            n = int(re.sub(r"[,\s]", "", m.group(1)))
            if n:
                qvals_in_text.append(n)
        except Exception:
            pass
    qty_in_text = info.get("quantity") or (qvals_in_text[0] if qvals_in_text else None)
    qty_hedge_ctx = _qty_in_hedge_context(text, qty_in_text)
    qval_hedge_ctx = any(_quantity_values_in_hedge_context(text, qvals_in_text).values())
    if qty_hedge_ctx or qval_hedge_ctx:
        # 数量上下文真有 hedge → 草稿不得使用 confirmed 措辞
        confirmed_wording = re.search(
            r"\b(?:your|your\s+initial)\s+(?:confirmed|order|booking|"
            r"confirmed\s+order|confirmed\s+quantity)\s+(?:of|is|for|:)?\s*"
            r"\d[\d,]*\s*(?:pcs|pieces|units)\b",
            body_low)
        if confirmed_wording:
            issues.append(f"客户原文在数量上下文含 hedge 词（may/might/could/potentially/around/approximately 等），"
                          f"草稿却用「confirmed order of」表述数量："
                          f"{_norm(confirmed_wording.group(0))[:70]}")

    # ⑯ 在 product_category 场景下，草稿不得问 "which model do you need"
    #    （model 是更细的层级，没有 category 时问 model 没有意义）
    if plan and any(q.get("field") == "product_category" for q in (plan.get("selected") or [])):
        bad_model_q = re.search(
            r"\bwhich\s+model\s+(?:do|would|are)\s+you\b|\bwhat\s+model\s+do\s+you\s+need\b|"
            r"\bdo\s+you\s+have\s+a\s+specific\s+model\b",
            body_low)
        if bad_model_q:
            issues.append(f"客户连产品类别都未说明，草稿却问「which model do you need」（model 是更细的层级，无 category 时问 model 没有意义）："
                          f"{_norm(bad_model_q.group(0))[:70]}")

    # ⑰ 草稿不得虚构 Catalog 已经发送（"Attached is our catalog" /
    #     "We have sent you our catalog" 这种）
    fake_catalog_sent = re.search(
        r"\b(?:attached|attaching|please\s+find\s+attached|enclosed)\b[^.\n]{0,40}?"
        r"\b(?:catalog|catalogue|price\s+list|specifications?)\b"
        r"|\b(?:we|i|we've|i've)\s+(?:have\s+)?(?:sent|attached|enclosed)\b[^.\n]{0,40}?"
        r"\b(?:catalog|catalogue|price\s+list|specifications?)\b",
        body_low)
    if fake_catalog_sent and "catalog" in reqs:
        # 系统没有 catalog 附件时，这种承诺属于虚构
        issues.append("客户索取 Catalog，但草稿出现「Attached is our catalog / "
                      "We have sent you our catalog」等已发送承诺"
                      "（系统未实际生成附件，不得承诺已发）")

    # ⑱ 草稿不得虚构 Sample 已准备 / 承诺立即发出
    #    （正确：We can advise on sample availability once the relevant product is selected.）
    fake_sample = re.search(
        r"\b(?:we|i|we'll|we\s+will)\s+(?:will\s+)?(?:send|prepare|arrange|dispatch|ship|"
        r"be\s+sending)\s+(?:you\s+)?(?:the\s+|your\s+|some\s+|free\s+)?samples?\b",
        body_low)
    if fake_sample and "samples" in reqs:
        issues.append("客户索取 Samples，但草稿出现「we will send / prepare samples」"
                      "等立即承诺（无样品库存/流程依据，不得承诺发出）")

    # ⑲ 草稿不得把 "mention" 量（3,000 / 10,000）当成当前 confirmed order
    #    例如：原文 "Customer mentioned: 3,000 pcs / Potential Annual Volume: 10,000 pcs"
    #    草稿不得写 "your order of 3,000 pcs" / "your annual volume of 10,000 pcs"
    mention_qty = re.findall(
        r"(?:customer\s+mentioned|mentioned\s*:|potential\s+annual\s+volume|annual\s+volume)\s*"
        r"(\d[\d,]*)",
        text or "", re.I)
    if mention_qty:
        for q in mention_qty:
            qn = q.replace(",", "")
            pat = re.compile(rf"\b(?:your|the)\s+(?:order|annual|annual\s+volume|"
                             rf"monthly)\s+(?:of\s+)?{qn}\b", re.I)
            if pat.search(body):
                issues.append(f"原文把 {q} 标记为 mentioned/annual volume，草稿却当成"
                              f" confirmed order 写「your order of {q}」")
                break

    # ---- 第五轮第三次优化：Intent 驱动的新检查（⑳ ㉑ ㉒） ----

    # ⑳ 意图问题预算：Catalog / Sample 请求首轮最多 0-1 个问题
    intent_now = plan.get("intent") or ""
    if intent_now in (INTENT_CATALOG, INTENT_SAMPLE) and n_q > 1:
        issues.append(f"客户意图为 {intent_now}，首轮最多 0-1 个问题（实际 {n_q} 个）"
                      "——客户当前明确请求必须优先响应，不能被追问淹没")

    # ㉑ Catalog/Sample 请求下，不得把 model/photo/quantity 设为提供资料的前置条件
    if intent_now in (INTENT_CATALOG, INTENT_SAMPLE):
        precondition = re.search(
            r"(?:reference\s+model|photo|design|which\s+model|what\s+model|"
            r"quantity|how\s+many)[^.?\n]{0,60}\bbefore\b[^.?\n]{0,40}"
            r"(?:catalo|sample|we\s+(?:can|could|will|share|send|provide))",
            body_low)
        if precondition:
            issues.append(f"Intent={intent_now}：草稿把型号/图片/数量设为提供资料的"
                          f"前置条件「{_norm(precondition.group(0))[:70]}」"
                          "（应先响应客户明确请求）")
        for q in qs:
            if re.search(r"\b(reference\s+model|which\s+model|what\s+model|"
                         r"how\s+many|what\s+quantity|which\s+quantity)\b", q, re.I):
                issues.append(f"Intent={intent_now}：首轮问句索取了当前阶段不需要的信息："
                              f"{_norm(q)[:70]}")

    # ㉒ 是否把 UNKNOWN 写成 confirmed fact（无产品时不得写 "your order of X pcs"）
    if not product and re.search(r"\byour\s+order\s+of\s+\d", body_low):
        issues.append("产品尚未确认（UNKNOWN），草稿却写「your order of N pcs」"
                      "（把 UNKNOWN 写成了 confirmed fact）")

    # 去重（保持顺序）
    return list(dict.fromkeys(issues))


def report_plan(plan: dict) -> str:
    """给命令行/工作台展示的一行摘要。"""
    sel = plan.get("selected") or []
    fields = "、".join(q["field"] for q in sel) or "无（信息齐全，直接进入下一步）"
    p1 = "、".join(q["field"] for q in plan.get("p1", [])) or "无"
    p2 = "、".join(q["field"] for q in plan.get("p2", [])) or "无"
    # 第五轮第三次优化：摘要带上意图与销售阶段
    head = ""
    if plan.get("intent"):
        head = (f"意图 {plan.get('intent')}（{plan.get('sales_stage', '')}）｜"
                f"首要动作：{plan.get('immediate_action', '')}　|　")
    return (f"{head}首轮追问 {len(sel)} 个（上限 {plan['max_questions']}）：{fields}　|　"
            f"客户明确需求：{'、'.join(plan['explicit_requests']) or '无'}　|　"
            f"P1 后续确认：{p1}　|　P2 首轮不问：{p2}")
