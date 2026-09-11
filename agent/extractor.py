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
            # ---- 竞争情报（TEST03）：竞争对手报价 ≠ 我方报价 / 客户目标价 ----
            "competitor_price": None,          # 竞品/同行报价数值
            "competitor_price_currency": None,
            "competitor_price_approx": False,  # 是否含 approximately/about 等约数词
            "competitor_price_note": "",       # 原文片段（可展示给销售，不用于对外承诺）
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
        comp = self._find_competitor_price(text)
        info["competitor_price"] = comp["value"]
        info["competitor_price_currency"] = comp["currency"]
        info["competitor_price_approx"] = comp["approx"]
        info["competitor_price_note"] = comp["note"]
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
        """提取目标价，例如 USD 0.5 / $0.8/pc / target price 1.2
        TEST03 修复：竞争对手报价（competitor / another supplier 等引出的 USD x）
        绝不当作客户目标价——先把这些句子从目标价识别中剔除。
        """
        # 竞品句剔除：从 competitor/another supplier 等线索起到最近的 USD 数字句段
        clean = re.sub(
            r"(?:competitor|another\s+(?:supplier|vendor|factory|offer)|"
            r"competitive\s+offer|their\s+offer|current\s+supplier)[^.!?\n]*"
            r"(?:USD|US\$|\$)\s*\d+(?:\.\d+)?[^.!?\n]*",
            " ", text, flags=re.IGNORECASE)
        m = re.search(r"(?:usd|us\$|\$)\s*(\d+(?:\.\d+)?)", clean, re.IGNORECASE)
        if m:
            return float(m.group(1))
        m = re.search(r"target price[:\s]*(\d+(?:\.\d+)?)", clean, re.IGNORECASE)
        if m:
            return float(m.group(1))
        return None

    @staticmethod
    def _find_competitor_price(text: str) -> dict:
        """竞争对手/同行报价（TEST03 商业情报）：competitor quote ≈ USD 4.35/pc。

        只做“保留上下文、供销售判断”的提取，绝不写进公司报价 / 客户目标价。
        支持语序一（线索在前：competitor has offered approximately USD 4.35）
        与语序二（金额在前，线索在后：USD 4.35 quoted by another supplier）。
        """
        low = text or ""
        out = {"value": None, "currency": None, "approx": False, "note": ""}
        patterns = [
            # 线索在前：competitor / another supplier ... USD 4.35
            re.compile(
                r"(?:competitor|another\s+(?:supplier|vendor|factory)|"
                r"competitive\s+offer|current\s+supplier)[^.!?\n]{0,90}?"
                r"(?:USD|US\$|\$)\s*(\d+(?:\.\d+)?)", re.I),
            # 金额在前：USD 4.35 ... competitor / another supplier
            re.compile(
                r"(?:USD|US\$|\$)\s*(\d+(?:\.\d+)?)[^.!?\n]{0,90}?"
                r"(?:competitor|another\s+(?:supplier|vendor|factory))", re.I),
        ]
        for pat in patterns:
            m = pat.search(low)
            if m:
                val = float(m.group(1))
                approx = bool(re.search(
                    r"(approximat\w*|about|around|roughly|close to|~)",
                    low[max(0, m.start() - 30): m.end() + 30], re.I))
                ctx = low[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
                out.update({"value": val, "currency": "USD", "approx": approx,
                            "note": re.sub(r"\s+", " ", ctx).strip()})
                return out
        return out

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

    # 欧洲/亚洲常见公司后缀（Rule 提取兜底：BrightPromo BV 这类无“常见英文后缀”的公司）
    _COMPANY_BV_RE = re.compile(
        r"([A-Z][\w&.,'’\- ]{2,40}?\s+(?:B\.?V\.?|N\.?V\.?|A\/?S|Oy|"
        r"Pte(?:\.)?(?: Ltd\.?)?|Pty(?:\.)?(?: Ltd\.?)?)\b)")
    _CONTACT_PARTICLE = (
        r"van der|van|von|de|den|der|ter|ten|zu|zum|du|di|da|del|la|le|bin|al")

    def _find_company(self, text: str):
        """提取公司名：匹配 Co., Ltd / Inc / GmbH / Trading / BV / NV 等公司后缀"""
        pattern = (r"([A-Z][\w&.,'’\- ]{2,40}?"
                   r"(?:Co\.,?\s?Ltd\.?|Ltd\.?|Inc\.?|LLC|GmbH|Corp\.?|"
                   r"Corporation|Company|Trading|Import|Export|Enterprises?))")
        m = re.search(pattern, text)
        if not m:
            # 公司名以 BV / NV / A/S / Oy 等结尾（荷/挪/芬/新/澳常见，无英文后缀词）
            m = self._COMPANY_BV_RE.search(text)
        if not m:
            return None
        name = m.group(1).strip().rstrip(",.")
        # 清理常见的前导杂质，如 "This is Michael Brown from AquaGear Trading"
        # 只保留 "from / sind / are" 等引导词之后的公司名部分
        for sep in (" from ", " sind ", " are ", " of ", " bei "):
            if sep in name.lower():
                idx = name.lower().rindex(sep)
                name = name[idx + len(sep):].strip()
        # BV/NV 命中的情况：把公司名与其后缀补全（capture 已含后缀，无需再处理）
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
        用 \b 词边界保证只匹配独立的 I'm / This is / My name is
        支持欧洲姓名中间小写粒子：Sophie van Dijk / Jan de Vries / Luc la Roche"""
        _name_opt = (r"[A-Z][a-zA-Z]+"
                     r"(?:\s+(?:(?:" + self._CONTACT_PARTICLE + r")\s+)?[A-Z][a-zA-Z]+)?")
        m = re.search(r"(?:\bI'?m\b|\bThis is\b|\bMy name is\b)\s+(" + _name_opt + ")", text)
        if m:
            return m.group(1)
        # 落款：邮件末尾紧跟的名字（取倒数几行里的大写开头词）
        # 跳过：国家名 / 公司后缀行 / 职位行 / 过短行，避免把 "UK"、"ABC Trading"、
        # "Purchasing" 之类当成联系人
        for line in reversed(text.strip().splitlines()):
            line = line.strip()
            if not line or len(line) <= 3:
                continue
            if re.fullmatch(_name_opt, line):
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
        Round2 TEST01 修复：客户也可能写成"CE and RoHS certification required"——
        认证名词在前、required/must/need 动词在后。两种语序都要识别为 requirement。
        """
        interest = bool(re.search(
            r"\b(what|which)\s+certificat\w*|"
            r"\bdo\s+you\s+have\s+(any\s+)?certificat\w*|"
            r"\bany\s+certificat\w*", text_lower))
        requirement = bool(
            # 语序一：动词在前（We require CE certification.）
            re.search(
                r"\b(require[sd]?|must\s+have|must\s+be|need[sed]?)\b[^.!?]{0,40}"
                r"\b(certificat\w*|ce\b|en\s?71|reach|fda|rohs|bsci|sgs)\b",
                text_lower)
            # 语序二：认证名词在前（CE and RoHS certification required / must be met）
            or re.search(
                r"\b(certificat\w*|ce\b|en\s?71|reach|fda|rohs|bsci|sgs)\b"
                r"[^.!?]{0,45}\b(required|mandatory|must\s+(?:be|have|meet)|"
                r"need[sed]?|require[sd]?|is\s+mandatory)\b",
                text_lower))
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


# =====================================================================
# Round 2 TEST01 共享原语：产品短语 / 规格信号
# （单一实现，reply_strategy / insight / gapcheck 都从这里取口径，
#   避免"客户已说明产品品类"被误判为"客户没说要什么产品"）
# =====================================================================

# 动词线索扩宽：旧版只认 interested in / looking for / need / want / require /
# would like to ...；客户更常见的写法还有
#   "looking to place an order for 5,000 pcs of Wireless ANC Earbuds"
#   "We are planning to order X" / "wish to order X" / "we buy X" 等。
_OBJECT_VERBS_RE = re.compile(
    r"\b(?:interested\s+in|looking\s+for|looking\s+at|need|needs|want|require|"
    r"wish(?:es)?\s+to\s+(?:order|buy|purchase|source)|"
    r"looking\s+to\s+(?:buy|purchase|order|source)|"
    r"looking\s+to\s+place\s+(?:an?\s+)?order\s+for|"
    r"place\s+(?:an?\s+)?order\s+for|placing\s+(?:an?\s+)?order\s+for|"
    r"planning(?:\s+to\s+(?:buy|order|purchase))?|"
    r"(?:we|i)\s+(?:want|would\s+like)\s+to\s+(?:order|buy|purchase)|"
    r"would\s+like(?:\s+to\s+(?:buy|order|purchase))?|"
    r"want\s+to\s+(?:buy|purchase|order)|need\s+to\s+(?:buy|purchase|order))\s+"
    r"([a-z0-9][a-z0-9\s\-/&]{2,60})", re.I)

# "5,000 pcs of Wireless ANC Earbuds (TWS) for Germany" —— 数量在前的常见写法。
# 注意要求紧跟单位后的 "of"，避免把裸数量当成产品。
_QTY_OF_PRODUCT_RE = re.compile(
    r"\b(\d[\d,]*)\s*(?:pcs|pieces|units|pairs|sets|cartons|ctns|k)\s+of\s+"
    r"([a-z0-9][a-z0-9\s\-/&]{2,60})", re.I)

# 产品候选里的通用占位词（与各模块旧口径一致，命中即视为"没有具体产品方向"）
_GENERIC_OBJECT_COMMON_RE = re.compile(
    r"^(?:your|the|some|any|these|those|all|more|several|a|an|our)?\s*"
    r"(?:products?|items?|goods|things|stuff|models?|catalog|catalogue|brochure|range|"
    r"specifications?|spec\s?sheets?|details|information|quotation|quote|price|prices|"
    r"pricing|cost|offer|samples?|list)\b", re.I)

# 电子/消费类产品的规格信号（旧版只认容量/尺寸/材质，识别不了
# Bluetooth 5.4 / ANC / 40h battery / USB-C / TWS 这类关键规格）
_ELECTRONICS_SPEC_RE = re.compile(
    r"\b(bluetooth\s*\d(?:\.\d+)?|anc\b|active\s+noise\s+cancell\w*|"
    r"noise\s+cancell\w*|tws\b|true\s+wireless|"
    r"wireless\s+(?:earbuds?|headphones?|headsets?|speakers?)|"
    r"usb[\s-]?c\b|usb\s+type\s*[-]?\s*c\b|type\s*[-]?\s*c\b|"
    r"charging\s+case|wireless\s+charging|battery\s+life\b|"
    r"battery\b[^.\n]{0,25}\d+\s*(?:h|hours?|hrs?)\b|"
    r"\d+\s*(?:h|hours?|hrs?)\s+(?:battery|playback|playtime|talk\s*time)|"
    r"ipx\d+\b|touch\s+control\w*|waterproof|sweatproof|led\s+indicat\w*|"
    r"earbuds?|headphones?|headsets?)\b", re.I)

_MATERIAL_COMMON_RE = re.compile(
    r"\b(neoprene|silicone|latex|pvc|tpu|eva|stainless\s+steel|glass|aluminum|"
    r"microfiber|nylon|polyester)\b", re.I)

_SPEC_UNIT_COMMON_RE = re.compile(
    r"\b\d+\s*(?:ml|l|oz|cl|cm|mm|inch|in|g|kg)\b", re.I)


def _clean_product_chunk(raw: str) -> str:
    """清洗产品候选片段：在标点/连接词处截断、去引导冠词、过滤通用词。"""
    if not raw:
        return ""
    chunk = re.split(r"[,.;:\n]|\b(?:for|with|and|or|in|at|to|of|from)\b",
                     raw, maxsplit=1)[0]
    chunk = re.sub(r"\s+", " ", chunk).strip(" ,.-")
    chunk = re.sub(r"^(?:your|our|the|a|an|some|own|their)\s+", "", chunk,
                   flags=re.I).strip(" ,.-")
    if not chunk or _GENERIC_OBJECT_COMMON_RE.match(chunk):
        return ""
    if len(chunk.split()) > 6:
        chunk = " ".join(chunk.split()[:6])
    return chunk


def extract_customer_product(text: str) -> str:
    """客户原文里"客户自己说的产品"（只用原话，绝不猜测）。

    Round 2 TEST01 修复：支持
      - "looking to place an order for 5,000 pcs of Wireless ANC Earbuds"
      - "5,000 pcs of Wireless ANC Earbuds (TWS) for the German market"
      - "interested in Wireless ANC Earbuds"
    等常见表达；旧测试（swim caps / water bottle 等）行为不变。
    """
    text = text or ""
    # ① 数量后置 of 写法最明确，先试它（capture 从单位后的 of 开始）
    for m in _QTY_OF_PRODUCT_RE.finditer(text):
        chunk = _clean_product_chunk(m.group(2))
        if chunk:
            return chunk
    # ② 动词引导写法
    for m in _OBJECT_VERBS_RE.finditer(text):
        chunk = _clean_product_chunk(m.group(1))
        if chunk:
            return chunk
    return ""


def extract_customer_specs(text: str) -> list:
    """客户原文里的规格信号清单（容量/尺寸/材质/电子规格），最多 3 条短标签。"""
    text = text or ""
    tokens = []
    for m in _SPEC_UNIT_COMMON_RE.finditer(text):
        v = re.sub(r"\s+", "", m.group(0)).lower()
        if v not in tokens:
            tokens.append(v)
    mat = _MATERIAL_COMMON_RE.search(text)
    if mat and mat.group(0).lower() not in tokens:
        tokens.append(mat.group(0).lower())
    for m in _ELECTRONICS_SPEC_RE.finditer(text):
        v = re.sub(r"\s+", " ", m.group(0)).strip().lower()
        if v not in tokens:
            tokens.append(v)
    return tokens[:3]


def has_customer_spec_signal(text: str) -> bool:
    """客户原文是否含有任何规格锚点（尺寸/容量/材质/电子规格）。"""
    return bool(_SPEC_UNIT_COMMON_RE.search(text or "")
                or _MATERIAL_COMMON_RE.search(text or "")
                or _ELECTRONICS_SPEC_RE.search(text or ""))
