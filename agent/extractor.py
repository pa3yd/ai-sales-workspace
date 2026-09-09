# -*- coding: utf-8 -*-
"""
模块①：询盘信息提取器 (InquiryExtractor)
从客户发来的英文询盘原文中，提取结构化信息：
- 数量 (quantity)
- 目标价 (target_price)
- 国家 (country)
- 公司名 (company)
- 公司网址 (website)
- 邮箱 (email)
- 联系人 (contact_name)
- 采购意图 (intent)
"""

import re


# 常见国家关键词表（可持续扩充）
COUNTRY_KEYWORDS = {
    "United States": ["usa", "u.s.a", "united states", "america", "american"],
    "United Kingdom": ["uk", "u.k", "united kingdom", "britain", "england"],
    "Germany": ["germany", "german", "deutschland"],
    "France": ["france", "french"],
    "Australia": ["australia", "australian"],
    "Canada": ["canada", "canadian"],
    "Japan": ["japan", "japanese"],
    "South Korea": ["korea", "korean"],
    "UAE": ["uae", "dubai", "emirates"],
    "Saudi Arabia": ["saudi", "ksa"],
    "Brazil": ["brazil", "brazilian"],
    "Mexico": ["mexico", "mexican"],
    "India": ["india", "indian"],
    "Netherlands": ["netherlands", "holland", "dutch"],
    "Spain": ["spain", "spanish"],
    "Italy": ["italy", "italian"],
    "Poland": ["poland", "polish"],
    "Russia": ["russia", "russian"],
}

# 邮箱后缀 -> 国家的辅助判断（企业邮箱常用国家域名后缀）
EMAIL_DOMAIN_COUNTRY = {
    ".de": "Germany", ".fr": "France", ".co.uk": "United Kingdom",
    ".jp": "Japan", ".kr": "South Korea", ".au": "Australia",
    ".ca": "Canada", ".nl": "Netherlands", ".es": "Spain",
    ".it": "Italy", ".pl": "Poland", ".ru": "Russia",
    ".com.br": "Brazil", ".com.mx": "Mexico", ".in": "India",
}

# 高购买力/主流市场国家（用于线索评分时参考）
TIER1_MARKETS = ["United States", "United Kingdom", "Germany", "Australia",
                 "Canada", "Netherlands", "France"]
TIER2_MARKETS = ["Japan", "South Korea", "UAE", "Saudi Arabia",
                 "Spain", "Italy", "Poland"]


class InquiryExtractor:
    """询盘信息提取器：输入英文询盘原文，输出结构化字典"""

    def extract(self, text: str) -> dict:
        text_lower = text.lower()
        info = {
            "raw_text": text,          # 原文，供后面的模块继续用
            "quantity": None,          # 采购数量
            "quantity_unit": None,     # 数量单位 pcs / sets / ...
            "target_price": None,      # 目标价
            "country": None,           # 客户国家
            "company": None,           # 客户公司名
            "website": None,           # 公司网址
            "email": None,             # 联系邮箱
            "contact_name": None,      # 联系人姓名
            "intent": None,            # 采购意图描述
            # ---- 业务逻辑优化新增：客户信息补全（识别不到一律 None，绝不猜测）----
            "job_title": None,         # 职位（Purchasing / Manager / CEO...）
            "phone": None,             # 电话
            "whatsapp": None,          # WhatsApp 号码
            "customer_type": None,     # 客户类型（贸易商/分销商/连锁零售...）
            "business_type": None,     # 业务类型（进口商/批发/电商...）
            # ---- 认证：兴趣 vs 要求 分开（优化十）----
            "certification_interest": False,      # "What certificates do you have?"
            "certification_requirement": False,   # "We require CE certification."
            # ---- 紧急度（优化十三：与采购意向分开）----
            "urgency": None,           # high / medium / None（未提及就不猜）
        }

        info["email"] = self._find_email(text)
        info["quantity"], info["quantity_unit"] = self._find_quantity(text_lower)
        info["target_price"] = self._find_target_price(text)
        info["country"] = self._find_country(text_lower, info["email"])
        info["company"] = self._find_company(text)
        info["website"] = self._find_website(text, info["email"])
        info["contact_name"] = self._find_contact_name(text)
        info["intent"] = self._find_intent(text_lower)
        info["job_title"] = self._find_job_title(text)
        info["phone"] = self._find_phone(text)
        info["whatsapp"] = self._find_whatsapp(text)
        info["customer_type"] = self._find_customer_type(text_lower)
        info["business_type"] = self._find_business_type(text_lower)
        cert = self._split_certification(text_lower)
        info["certification_interest"] = cert["interest"]
        info["certification_requirement"] = cert["requirement"]
        info["urgency"] = self._find_urgency(text_lower)
        # 多数量语义补丁：同一封询盘里 500 / 2,000 / 5,000 可能分别是
        # 试单数量 / 报价数量 / 潜在订单量 —— 必须识别商业语义，而不是只提数字
        info["quantity_semantics"] = extract_quantity_semantics(text)
        return info

    # ---------- 以下是一个个小的提取方法 ----------

    def _find_email(self, text: str):
        r"""用正则提取邮箱（\S 表示非空白字符）"""
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        return m.group(0) if m else None

    def _find_quantity(self, text_lower: str):
        """提取数量，例如 5000pcs / 5,000 pcs / 10K pieces / 2x40HQ"""
        # ① 普通写法：数字 + 单位
        m = re.search(r"(\d[\d,]*)\s*(pcs|pieces|sets|units|dozens?)\b", text_lower)
        if m:
            return int(m.group(1).replace(",", "")), m.group(2)
        # ② K 缩写：10k / 20K
        m = re.search(r"(\d[\d,]*)\s*k\b", text_lower)
        if m:
            return int(m.group(1).replace(",", "")) * 1000, "pcs"
        # ③ 柜量：2x40HQ / 1x20GP（整柜询盘，数量一般很大）
        m = re.search(r"(\d+)\s*x\s*(40hq|40gp|20gp)", text_lower)
        if m:
            return int(m.group(1)), m.group(2)  # 柜数先记下来
        return None, None

    def _find_target_price(self, text: str):
        """提取目标价，例如 USD 0.5 / $0.8/pc / target price 1.2"""
        m = re.search(r"(?:usd|us\$|\$)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if m:
            return float(m.group(1))
        m = re.search(r"target price[:\s]*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if m:
            return float(m.group(1))
        return None

    def _find_country(self, text_lower: str, email: str):
        """优先从正文找国家名；找不到再从邮箱后缀推断"""
        for country, keywords in COUNTRY_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    return country
        if email:
            # 按后缀长度倒序匹配，保证 .co.uk 优先于 .uk 之类的情况
            for domain in sorted(EMAIL_DOMAIN_COUNTRY, key=len, reverse=True):
                if email.lower().endswith(domain):
                    return EMAIL_DOMAIN_COUNTRY[domain]
        return None

    def _find_company(self, text: str):
        """提取公司名：匹配 Co., Ltd / Inc / GmbH / Trading 等常见公司后缀"""
        pattern = (r"([A-Z][\w&.,'’\- ]{2,40}?"
                   r"(?:Co\.,?\s?Ltd\.?|Ltd\.?|Inc\.?|LLC|GmbH|Corp\.?|"
                   r"Corporation|Company|Trading|Import|Export|Enterprises?))")
        m = re.search(pattern, text)
        if not m:
            return None
        name = m.group(1).strip().rstrip(",.")
        # 清理常见的前导杂质，如 "This is Michael Brown from AquaGear Trading"
        # 只保留 "from / sind / are" 等引导词之后的公司名部分
        for sep in (" from ", " sind ", " are ", " of ", " bei "):
            if sep in name.lower():
                idx = name.lower().rindex(sep)
                name = name[idx + len(sep):].strip()
        return name if len(name) >= 3 else None

    def _find_website(self, text: str, email: str):
        """提取网址；若正文没有，则用邮箱 @ 后面的域名推测官网"""
        m = re.search(r"(?:https?://)?(?:www\.[\w\-\.]+\.[a-z]{2,})", text, re.IGNORECASE)
        if m:
            return m.group(0)
        if email and not email.split("@")[1].lower().startswith(("gmail", "hotmail", "yahoo", "outlook", "163", "qq")):
            return "http://www." + email.split("@")[1]
        return None

    def _find_contact_name(self, text: str):
        """提取联系人：I'm Tom / This is Sarah / My name is John Smith
        注意：不能用 IGNORECASE，否则 'i?m' 会误匹配 sw*im* caps 里的 im！
        用 \b 词边界保证只匹配独立的 I'm / This is / My name is"""
        m = re.search(r"(?:\bI'?m\b|\bThis is\b|\bMy name is\b)\s+"
                      r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)", text)
        if m:
            return m.group(1)
        # 落款：邮件末尾紧跟的名字（取倒数几行里的大写开头词）
        # 跳过：国家名 / 公司后缀行 / 职位行 / 过短行，避免把 "UK"、"ABC Trading"、
        # "Purchasing" 之类当成联系人
        for line in reversed(text.strip().splitlines()):
            line = line.strip()
            if not line or len(line) <= 3:
                continue
            if re.fullmatch(r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?", line):
                low_line = line.lower()
                if any(kw in low_line for kw in
                       list(COUNTRY_KEYWORDS.keys()) +
                       [k for kws in COUNTRY_KEYWORDS.values() for k in kws]):
                    continue
                if re.search(r"\b(trading|ltd|gmbh|inc|corp|company|import|export|"
                             r"purchasing|procurement|manager|buyer|sourcing|director|"
                             r"ceo|owner|founder|department)\b", low_line):
                    continue
                return line
        return None

    def _find_intent(self, text_lower: str):
        """粗略判断采购意图类型"""
        if any(w in text_lower for w in ["oem", "logo", "custom", "print", "our brand"]):
            return "OEM/贴牌定制"
        if any(w in text_lower for w in ["sample", "samples"]):
            return "样品询盘"
        if any(w in text_lower for w in ["catalog", "catalogue", "price list", "price list", "quotation"]):
            return "目录/报价请求"
        return "常规采购询盘"

    # ---------- 业务逻辑优化新增的提取方法 ----------

    def _find_phone(self, text: str):
        """提取电话：只认 + 国际区号或 tel: 前缀，避免把数量/日期误当电话"""
        m = re.search(r"(?:\+|tel[:\s]*)\d[\d\-\s()]{7,16}\d", text)
        return re.sub(r"\s+", " ", m.group(0)).strip() if m else None

    def _find_whatsapp(self, text: str):
        """提取 WhatsApp 号码（whatsapp: +86 138 ... / WhatsApp +44...）"""
        m = re.search(r"whats\s?app[:\s]*([+\d][\d\-\s()]{6,16})", text, re.IGNORECASE)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else None

    def _find_job_title(self, text: str):
        """提取职位：签名档/正文里的明显职位词（识别不到返回 None，不猜）"""
        m = re.search(r"\b(purchasing\s+manager|procurement\s+manager|sourcing\s+manager|"
                      r"product\s+manager|general\s+manager|sales\s+manager|"
                      r"purchasing|procurement|sourcing|buyer|ceo|cfo|owner|founder|"
                      r"director)\b", text, re.IGNORECASE)
        return m.group(0).title() if m else None

    def _find_customer_type(self, text_lower: str):
        """客户类型：只按明说的词判断，没有就 None"""
        mapping = [
            ("trading", "Trading Company"), ("distributor", "Distributor"),
            ("wholesal", "Wholesaler"), ("chain", "Retail Chain"),
            ("importer", "Importer"), ("brand", "Brand Owner"),
        ]
        for kw, label in mapping:
            if kw in text_lower:
                return label
        return None

    def _find_business_type(self, text_lower: str):
        """业务类型：渠道/经营方式（amazon / online / retail / wholesale...）"""
        if "amazon" in text_lower:
            return "Amazon Seller"
        if any(w in text_lower for w in ["e-commerce", "ecommerce", "online store", "shopify"]):
            return "E-commerce"
        if "retail" in text_lower:
            return "Retailer"
        if "wholesal" in text_lower:
            return "Wholesale"
        if "import" in text_lower:
            return "Importer"
        return None

    @staticmethod
    def _split_certification(text_lower: str) -> dict:
        """认证兴趣 vs 认证要求（优化十）：
        "What certificates do you have?"  → interest（了解，不阻塞报价）
        "We require CE certification."    → requirement（影响选品和成本）
        """
        interest = bool(re.search(
            r"\b(what|which)\s+certificat\w*|"
            r"\bdo\s+you\s+have\s+(any\s+)?certificat\w*|"
            r"\bany\s+certificat\w*", text_lower))
        requirement = bool(re.search(
            r"\b(require[sd]?|must\s+have|must\s+be|need[sed]?)\b[^.!?]{0,40}"
            r"\b(certificat\w*|ce\b|en\s?71|reach|fda|rohs|bsci|sgs)\b", text_lower))
        return {"interest": interest, "requirement": requirement}

    def _find_urgency(self, text_lower: str):
        """紧急度（优化十三）：与采购意向分开，识别不到返回 None 不猜"""
        if re.search(r"\b(urgent|asap|as\s+soon\s+as\s+possible|immediate|immediately|"
                     r"rush\s+order)\b", text_lower):
            return "high"
        if re.search(r"\b(fast\s+delivery|quick\s+delivery|time\s+is\s+tight)\b", text_lower):
            return "medium"
        return None


# ==================== 数量商业语义识别（多数量语义补丁） ====================

# 七类数量商业语义
QUANTITY_TRIAL = "TRIAL_QUANTITY"                # 试单/测试数量
QUANTITY_INITIAL = "INITIAL_ORDER_QUANTITY"      # 首单/当前订单数量
QUANTITY_QUOTATION = "QUOTATION_QUANTITY"        # 客户明确要求报价的数量
QUANTITY_POTENTIAL = "POTENTIAL_VOLUME"          # 潜在订单数量
QUANTITY_ANNUAL = "ANNUAL_VOLUME"                # 年度采购量
QUANTITY_ESTIMATED = "ESTIMATED_QUANTITY"        # 估算数量
QUANTITY_UNKNOWN = "UNKNOWN_QUANTITY"            # 无法判断用途的数量

QTY_ROLE_CN = {
    QUANTITY_TRIAL: "试单数量",
    QUANTITY_INITIAL: "首单数量",
    QUANTITY_QUOTATION: "报价数量",
    QUANTITY_POTENTIAL: "潜在订单量",
    QUANTITY_ANNUAL: "年度采购量",
    QUANTITY_ESTIMATED: "估算数量",
    QUANTITY_UNKNOWN: "数量（用途未说明）",
}

# UI/摘要里的短标签
QTY_ROLE_SHORT = {
    QUANTITY_TRIAL: "试单",
    QUANTITY_INITIAL: "首单",
    QUANTITY_QUOTATION: "报价数量",
    QUANTITY_POTENTIAL: "潜在量",
    QUANTITY_ANNUAL: "年度量",
    QUANTITY_ESTIMATED: "估算",
    QUANTITY_UNKNOWN: "数量",
}

_QTY_SEM_TOKEN_RE = re.compile(
    r"(\d[\d,]*)\s*(pcs|pieces|units|pairs|sets|cartons|ctns)\b", re.I)
# 区间/二选一数量："3,000 or 5,000 pcs" / "2,000-3,000 pcs"（第一个数字无单位）
_QTY_ALT_UNIT_RE = re.compile(
    r"(\d[\d,]*)\s*(?:or|to|[-–~])\s*(\d[\d,]*)\s*"
    r"(pcs|pieces|units|pairs|sets|cartons|ctns)\b", re.I)
_SENT_BOUNDARY_RE = re.compile(r"[.!?\n]")
# 左上下文边界额外包含「逗号+空格」：防止 "500 pcs to test market, but ... reach 5,000 pcs"
# 这种单行长句把前面的角色词误配给后面的数量。
# 注意不能把裸逗号当边界——"3,000" 的千分位逗号后面是数字不是空格，不受影响。
_LEFT_BOUNDARY_RE = re.compile(r"[.!?\n]|,\s")

# 角色线索表：按优先级排列，先命中先定角色。
# 例："first order may start with 500 pcs to test market"
#     同时含 initial（first order）与 trial（test market）线索 → trial 优先，
#     因为客户表达的核心语义是"拿 500 试水"。
_QTY_ROLE_PATTERNS = [
    (QUANTITY_TRIAL, re.compile(
        r"\b(trial|sample\s+order|samples?|to\s+test|test\s+(?:the\s+)?market|testing)\b")),
    (QUANTITY_ANNUAL, re.compile(
        r"\b(annual|per\s+year|yearly|a\s+year|12\s*months?)\b")),
    (QUANTITY_POTENTIAL, re.compile(
        r"\b(could\s+reach|can\s+reach|could\s+go|up\s+to|potentially|potential|"
        r"total\s+order|in\s+total|later|in\s+the\s+future|future\s+orders?|"
        r"scal\w+|grow\w*\s+to)\b")),
    (QUANTITY_INITIAL, re.compile(
        r"\b(first\s+order|initial\s+order|initial\s+quantity|first\s+shipment|"
        r"initial\s+shipment|formal\s+order|initially)\b")),
    (QUANTITY_QUOTATION, re.compile(
        r"\b(quote|quotation|best\s+price|price\s+for|for\s+pricing|looking\s+for|"
        r"looking\s+to|need|order\s+quantity)\b")),
    (QUANTITY_ESTIMATED, re.compile(
        r"\b(about|around|approx\.?|approximately|estimated?|more\s+or\s+less|or\s+so)\b")),
]


def _to_int(s: str):
    try:
        return int(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


def extract_quantity_semantics(text: str) -> list:
    """把询盘里每个数量按商业语义分类（多数量语义补丁）。

    核心思想：500 / 2,000 / 5,000 在同一封询盘里可能分别是
    试单数量 / 报价数量 / 潜在订单量 —— 语义不同就绝不构成冲突。

    上下文取「数字前 ≤100 字符 + 数字后 ≤35 字符」，且都截断到句子边界，
    避免把上一行 "Trial Order: 500 pcs" 的角色词误配到下一行的数量。

    返回 [{value, unit, role, role_cn, role_short, context}]，识别不到数量返回 []。
    """
    low = (text or "").lower()
    out = []

    def _add(pos: int, val, unit: str):
        if not val:
            return
        # 左上下文：向前最多 100 字符，截断到最近的句子/逗号边界
        left_start = max(0, pos - 100)
        breaks = [b.end() for b in _LEFT_BOUNDARY_RE.finditer(low, left_start, pos)]
        if breaks:
            left_start = breaks[-1]
            # 标签行跨行修复："Our annual purchase volume\ncould reach 10,000 pcs"
            # 角色词在上一行。允许跨一行找角色词——但仅当上一行不含其他数量数字
            # （否则 T08 的 "Trial Order: 500 pcs" 行会被误配给下一行的数量）
            look_back = max(0, left_start - 60)
            if look_back < left_start - 1:
                b2 = [b.end() for b in _LEFT_BOUNDARY_RE.finditer(low, look_back,
                                                                  left_start - 1)
                      if b.end() < left_start]
                seg_start = b2[-1] if b2 else look_back
                prev_seg = low[seg_start:left_start]
                if (prev_seg.strip()
                        and not _QTY_SEM_TOKEN_RE.search(prev_seg)
                        and not _QTY_ALT_UNIT_RE.search(prev_seg)):
                    left_start = seg_start
        # 右上下文：向后最多 35 字符，截断到最近的句子/换行边界
        right_end = min(len(low), pos + 35)
        nb = _SENT_BOUNDARY_RE.search(low, pos, right_end)
        if nb:
            right_end = nb.start()
        ctx = low[left_start:right_end]
        role = QUANTITY_UNKNOWN
        for r, pat in _QTY_ROLE_PATTERNS:
            if pat.search(ctx):
                role = r
                break
        item = {"value": val, "unit": unit, "role": role,
                "role_cn": QTY_ROLE_CN[role], "role_short": QTY_ROLE_SHORT[role],
                "context": ctx.strip()}
        # 同值同角色去重（2,000 常在 "looking for" 和 "best price for" 各出现一次）
        if not any(x["value"] == val and x["role"] == role for x in out):
            out.append(item)

    # ① 区间/二选一数量："3,000 or 5,000 pcs" / "2,000-3,000 pcs"
    #    第一个数字后面没跟单位，单独扫出来（否则真冲突会漏检）
    for m in _QTY_ALT_UNIT_RE.finditer(low):
        unit = m.group(3).lower()
        _add(m.start(1), _to_int(m.group(1)), unit)
        _add(m.start(2), _to_int(m.group(2)), unit)
    # ② 常规数量："2,000 pcs" / "10K pieces"
    for m in _QTY_SEM_TOKEN_RE.finditer(low):
        _add(m.start(), _to_int(m.group(1)), m.group(2).lower())
    return out


def semantic_conflict_roles(semantics: list) -> dict:
    """找出真正的数量冲突：同一语义角色出现 ≥2 个不同数值。

    不同角色（试单 vs 报价数量 vs 潜在订单量）各归各位，不是冲突。
    返回 {role: [值...]}，空 dict = 无冲突。
    """
    groups = {}
    for s in semantics or []:
        groups.setdefault(s["role"], set()).add(s["value"])
    return {r: sorted(v) for r, v in groups.items() if len(v) >= 2}
