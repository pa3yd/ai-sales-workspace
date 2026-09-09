# -*- coding: utf-8 -*-
"""
LLM 版询盘信息提取器（混合模式）：
先用 DeepSeek 理解询盘，再用规则提取器补漏；LLM 出错时自动降级为纯规则模式。

返回的字段和规则版 extractor.py 完全一致，所以后面的评分/回复模块不用改动：
  quantity / quantity_unit / target_price / country / company /
  website / email / contact_name / intent /
  新增：product_query（客户想要的产品描述）、urgency（紧急度）、
        summary（一句话摘要）、source（信息来源）、llm_note（LLM 的判断说明）
"""

import json

from agent.extractor import InquiryExtractor
from agent.llm_client import DeepSeekError

SYSTEM_PROMPT = """You are a senior B2B foreign trade sales analyst.
Your job: read an incoming customer inquiry email and extract structured lead information.

RULES:
- Extract ONLY information that is explicitly stated or can be directly inferred. Never invent facts.
- If a field is not mentioned, return null (JSON null), not an empty string and not a guess.
- "quantity" must be a plain integer (e.g. 5000, 800, 20000). If a container count is given (e.g. 2x40HQ), put the number in "quantity" and the container type in "quantity_unit".
- QUANTITY SEMANTICS (IMPORTANT): one inquiry may contain MULTIPLE quantities with different
  business meanings - trial order quantity, first/initial order quantity, quotation quantity
  (the quantity they explicitly ask you to quote), potential volume, annual volume, estimated
  quantity. Understand each quantity's role from its context. They are NOT a conflict.
  In "summary", keep the business semantics of each quantity, e.g. "客户当前希望按2,000 pcs获取报价，
  首单可能从500 pcs试单开始；若价格具有竞争力，潜在订单量可达5,000 pcs".
  NEVER write quantities with different stated purposes as a conflict (e.g. never say
  "客户需要2,000 pcs，但又提到500 pcs和5,000 pcs").
- "target_price" is a number only (no currency symbol). Put the currency in "target_price_currency".
- "country" must be an English country name (e.g. "United States", "Germany").
- "intent" must be one of: "OEM/贴牌定制", "样品询盘", "目录/报价请求", "常规采购询盘".
- "urgency" must be one of: "high", "medium", "low". Urgency is about the buyer's TIME
  pressure (urgent / ASAP / deadline), NOT about purchase intent. If no time pressure
  is expressed, return null - do not guess.
- "certification_interest" = true when the buyer ASKS what certificates you have
  (e.g. "What certificates do you have?"). 
- "certification_requirement" = true when the buyer REQUIRES a specific certification
  (e.g. "We require CE certification."). Both are false if certification is not mentioned.
- Extract ONLY information that is explicitly stated. Never invent facts. If a field is
  not mentioned (job_title, phone, whatsapp, city, customer_type, business_type...),
  return null - never guess.
- The inquiry may be written in any language (English, German, French, Spanish...). Extract regardless.
- Customer messages often contain spelling mistakes. Understand them intelligently.

JSON OUTPUT REQUIREMENTS (CRITICAL):
- Return ONLY a single JSON object. No markdown fences, no commentary.
- String values must not contain unescaped newlines. Use \\n for line breaks inside strings.
- String values must not contain unescaped quotes. Use \\" inside strings.

Return a JSON object with EXACTLY these keys:
{
  "contact_name": string or null,
  "company": string or null,
  "country": string or null,
  "city": string or null,
  "job_title": string or null,
  "email": string or null,
  "phone": string or null,
  "whatsapp": string or null,
  "website": string or null,
  "customer_type": string or null,
  "business_type": string or null,
  "product_query": string or null,
  "quantity": integer or null,
  "quantity_unit": string or null,
  "target_price": number or null,
  "target_price_currency": string or null,
  "intent": string or null,
  "urgency": string or null,
  "certification_interest": boolean,
  "certification_requirement": boolean,
  "summary": string,
  "confidence": number
}
"summary" = one Chinese sentence describing what the customer wants.
"confidence" = 0 to 1, how confident you are in the extraction."""

USER_TEMPLATE = """Analyze the following customer inquiry and return the JSON object:

----- INQUIRY START -----
{inquiry}
----- INQUIRY END -----"""


class LLMInquiryExtractor:
    """LLM 提取器（内部自带规则兜底）"""

    def __init__(self, client, fallback: InquiryExtractor = None):
        self.client = client
        self.fallback = fallback or InquiryExtractor()

    def extract(self, text: str) -> dict:
        # 先跑规则版，作为兜底和补漏的底稿
        base = self.fallback.extract(text)

        try:
            data = self.client.chat_json(
                SYSTEM_PROMPT, USER_TEMPLATE.format(inquiry=text)
            )
        except DeepSeekError as e:
            # API 出问题：明确提示，并降级为纯规则模式
            print(f"   [警告] DeepSeek 调用失败，已自动降级为规则提取：{e}")
            base["source"] = "rule（LLM 不可用）"
            base["product_query"] = None
            base["urgency"] = None
            base["summary"] = None
            base["confidence"] = None
            return base
        except Exception as e:
            print(f"   [警告] LLM 返回内容无法解析，已降级为规则提取：{e}")
            base["source"] = "rule（LLM 返回异常）"
            base["product_query"] = None
            base["urgency"] = None
            base["summary"] = None
            base["confidence"] = None
            return base

        merged = self._merge(base, data, text)
        merged["source"] = "LLM（DeepSeek）"
        return merged

    def _merge(self, base: dict, data: dict, text: str) -> dict:
        """LLM 结果为准，缺失的字段用规则结果补上"""
        result = dict(base)   # 先复制规则版全部字段
        result.update({
            "contact_name": self._clean(data.get("contact_name")) or base["contact_name"],
            "company": self._clean(data.get("company")) or base["company"],
            "country": self._clean(data.get("country")) or base["country"],
            "email": self._clean(data.get("email")) or base["email"],
            "website": self._clean(data.get("website")) or base["website"],
            "product_query": self._clean(data.get("product_query")),
            "quantity": self._to_int(data.get("quantity")) or base["quantity"],
            "quantity_unit": self._clean(data.get("quantity_unit")) or base["quantity_unit"],
            "target_price": self._to_float(data.get("target_price")) or base["target_price"],
            "target_price_currency": self._clean(data.get("target_price_currency")),
            "intent": self._clean(data.get("intent")) or base["intent"],
            "urgency": self._clean(data.get("urgency")) or base.get("urgency"),
            "summary": self._clean(data.get("summary")),
            "confidence": self._to_float(data.get("confidence")),
        })
        # ---- 业务逻辑优化新增字段（LLM 给不了就用规则版结果，再没有就是 None）----
        result.update({
            "city": self._clean(data.get("city")),
            "job_title": self._clean(data.get("job_title")) or base.get("job_title"),
            "phone": self._clean(data.get("phone")) or base.get("phone"),
            "whatsapp": self._clean(data.get("whatsapp")) or base.get("whatsapp"),
            "customer_type": self._clean(data.get("customer_type")) or base.get("customer_type"),
            "business_type": self._clean(data.get("business_type")) or base.get("business_type"),
            "certification_interest": bool(data.get("certification_interest")) or base.get("certification_interest", False),
            "certification_requirement": bool(data.get("certification_requirement")) or base.get("certification_requirement", False),
        })
        return result

    # ---------- 数据类型清洗：把 LLM 返回的脏数据收拾干净 ----------

    @staticmethod
    def _clean(value):
        """把 null / 空串 / 模型爱写的 'unknown' 'N/A' 都变成 None"""
        if value is None:
            return None
        value = str(value).strip()
        if not value or value.lower() in ("null", "none", "n/a", "na", "unknown", "-", "unknown."):
            return None
        return value

    @staticmethod
    def _to_int(value):
        """转成整数，失败返回 None（LLM 有时会把 5000 写成 '5,000' 或 '5000 pcs'）"""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        return int(digits) if digits else None

    @staticmethod
    def _to_float(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = "".join(ch for ch in str(value) if ch.isdigit() or ch == ".")
        try:
            return float(text)
        except ValueError:
            return None
