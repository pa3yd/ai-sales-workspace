# -*- coding: utf-8 -*-
"""
LLM 版产品匹配器：让 DeepSeek 理解客户描述，从产品库里挑出最匹配的产品。

相比规则匹配（只数关键词命中），LLM 能理解：
- 拼写错误："neoprnee" -> Neoprene
- 同义表达："swimming cap" == "swim cap"
- 非英语询盘：德文、法文、西班牙文询盘也能匹配
- 语义推理："cap for winter open water" -> Neoprene（保暖款）

失败时自动降级为规则匹配（ProductMatcher）。
"""

import json

from agent.matcher import ProductMatcher
from agent.llm_client import DeepSeekError

SYSTEM_PROMPT = """You are a product matching engine for a Chinese export manufacturer.
Given a customer inquiry and a product catalog, select the product(s) the customer is asking about.

RULES:
- The inquiry may contain typos, slang, or be written in any language. Match by meaning, not by exact words.
- Only return product IDs that exist in the catalog. Never invent IDs.
- If nothing matches, return an empty best_id and an empty alternatives list.
- "confidence" is a number from 0 to 1.

Return a JSON object with EXACTLY these keys:
{
  "best_id": string or null,
  "alternatives": [string],
  "reason": string,
  "confidence": number
}
"reason" = one short Chinese sentence explaining why this product matches."""

USER_TEMPLATE = """CATALOG:
{catalog}

CUSTOMER INQUIRY:
{inquiry}

Return the JSON object."""


class LLMProductMatcher:

    def __init__(self, client, catalog_path: str):
        self.client = client
        self.rule_matcher = ProductMatcher(catalog_path)

    def _catalog_text(self) -> str:
        """把产品库压缩成给 LLM 看的简短清单，省 token"""
        lines = []
        for p in self.rule_matcher.products:
            lines.append(
                f"- id={p['id']} | {p['name']} ({p['name_cn']}) | "
                f"keywords: {', '.join(p['keywords'])} | "
                f"MOQ {p['moq']}"
            )
        return "\n".join(lines)

    def match(self, raw_text: str) -> list:
        """返回匹配到的产品列表（第一个是最匹配的），失败则降级为规则匹配"""
        prompt = USER_TEMPLATE.format(catalog=self._catalog_text(), inquiry=raw_text)

        try:
            data = self.client.chat_json(SYSTEM_PROMPT, prompt)
        except (DeepSeekError, Exception) as e:
            print(f"   [警告] LLM 产品匹配失败，已降级为规则匹配：{e}")
            return self.rule_matcher.match(raw_text)

        # 把 LLM 给的产品 id 还原成完整产品数据
        results = []
        by_id = {p["id"]: p for p in self.rule_matcher.products}
        best_id = data.get("best_id")
        ids = ([best_id] if best_id in by_id else []) + \
              [i for i in (data.get("alternatives") or []) if i in by_id and i != best_id]

        for rank, pid in enumerate(ids):
            item = dict(by_id[pid])
            confidence = float(data.get("confidence") or 0.9)
            item["match_score"] = round(confidence if rank == 0 else confidence - 0.15 * rank, 2)
            item["match_reason"] = (data.get("reason") or "") if rank == 0 else "LLM 判定的备选产品"
            item["match_source"] = "LLM"
            # 优化十四：候选产品统一带解释字段（LLM 版先给基础值）
            item["matched_attributes"] = item.get("hit_keywords", [])
            item["unmatched_attributes"] = []
            item["uncertainty"] = []
            item["recommendation"] = item["match_reason"]
            results.append(item)

        if not results:   # LLM 说没匹配上，也回退到规则再试一次
            return self.rule_matcher.match(raw_text)
        return results
