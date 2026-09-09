# -*- coding: utf-8 -*-
"""
模块②：产品匹配器 (ProductMatcher)
把询盘原文和产品库 (data/products.json) 比对，计算匹配度。
匹配度 = 命中的关键词数 / 该产品的关键词总数

业务逻辑优化（第十四节）：每个候选产品输出
  matched_attributes   命中了什么
  unmatched_attributes 库里有但客户没确认的属性
  uncertainty          不确定点（如数量低于 MOQ）
  recommendation       推荐语（为什么推荐 / 还需确认什么）
没有完全匹配时不只说"未匹配"，而是给出最接近的候选 + 匹配原因；
产品库确实没有相关产品时如实返回空列表（禁止编造产品）。
"""

import json
import re
import os


class ProductMatcher:

    def __init__(self, catalog_path: str):
        # 读取产品库 JSON 文件（注意 encoding="utf-8"，否则中文会乱码）
        with open(catalog_path, encoding="utf-8") as f:
            self.products = json.load(f)

    def match(self, raw_text: str) -> list:
        """返回按匹配度降序排列的产品列表（最多 Top 3），附带匹配解释。

        业务逻辑（第二轮优化七）：
          - 有关键词命中 → 正常返回 Top 3 候选
          - 无关键词命中，但询盘里的词与某产品名/品类确有重叠 → 返回"最接近候选"
            （低匹配度 + 明确说明不精确 + 需确认什么）
          - 产品库与询盘完全无关（如询瓶装品、库全是泳具）→ 返回空列表，
            由上游 insight 明确提示"当前产品库没有相关产品"并建议索取图片/链接/型号
        """
        text_lower = (raw_text or "").lower()
        results = []
        for p in self.products:
            hits = [kw for kw in p["keywords"] if kw in text_lower]
            score = round(len(hits) / len(p["keywords"]), 2)
            if score > 0:
                item = dict(p)
                item["match_score"] = score
                item["hit_keywords"] = hits
                self._annotate(item, hits, text_lower, exact=True)
                results.append(item)

        results.sort(key=lambda x: x["match_score"], reverse=True)
        if results:
            return results[:3]

        # 无关键词命中 → 尝试"最接近候选"（仅当确有词/品类重叠，否则不给假候选）
        closest = self._closest(text_lower)
        return [closest] if closest else []

    # ---------- 候选产品/最接近候选的匹配解释（八要素，第三轮优化六） ----------

    def _annotate(self, item: dict, hits: list, text_lower: str, exact: bool = True):
        """给候选产品补上八要素：
        匹配产品 / 匹配度 / 匹配依据 / 已满足条件 / 未满足条件 /
        缺失信息 / 推荐产品 / 推荐理由
        （匹配产品与匹配度由外层字段 name / match_score 承担）
        """
        item["matched_attributes"] = list(hits)
        item["unmatched_attributes"] = []
        item["uncertainty"] = []

        # ---- 匹配依据（match_basis）----
        if exact:
            item["match_basis"] = (f"询盘关键词精确命中该产品关键词 "
                                   f"{'、'.join(hits) if hits else '（间接命中）'}，"
                                   f"匹配度 = 命中关键词数 / 产品关键词总数")
        else:
            item["match_basis"] = "询盘与产品名/品类有部分词重叠，但无关键词精确命中——" \
                                  "低置信度最接近候选，需人工确认"

        # ---- 已满足条件 / 未满足条件（客户要求 vs 产品库可确认的属性） ----
        satisfied = []
        pending = []
        if hits:
            satisfied.extend(hits)
        # 客户提出、但产品库数据无法证实的属性 → 未满足/待确认，绝不假装匹配
        hay = (item.get("name", "") + " " + item.get("name_cn", "") + " "
               + " ".join(item.get("keywords", [])) + " "
               + " ".join(hits)).lower()
        for pat, label in (
                (r"black|white|blue|red|green|yellow|pink|purple|grey|gray|navy", "颜色定制"),
                (r"logo|private\s+label|print\w*|emboss\w*", "Logo 印刷"),
                (r"\boem\b|\bodm\b", "OEM/ODM"),
                (r"premium|gift\s?box|blister|header\s?card", "包装要求"),
                (r"certificat\w*|\bce\b|en\s?71|reach|fda|rohs", "认证"),
        ):
            m_feat = re.search(pat, text_lower)
            if m_feat:
                if m_feat.group(0) in hay:
                    satisfied.append(label)
                else:
                    pending.append(f"{label}：产品库未提供该属性，需人工确认")
        item["matched_conditions"] = satisfied
        item["unmatched_conditions"] = pending

        # ---- 缺失信息（missing_info）：影响匹配置信度但询盘里没有的 ----
        missing = []
        if not re.search(r"(\d[\d,]*)\s*(?:k|pcs|pieces|units|pairs|sets|cartons)\b", text_lower):
            missing.append("数量未给出，无法核对 MOQ")
        if not re.search(r"\b\d+\s*(ml|l|oz|cm|mm|g)\b", text_lower) \
                and not any(k in text_lower for k in item.get("keywords", [])):
            missing.append("关键规格未给出")
        if missing:
            item["missing_info"] = missing

        # ---- 不确定点（兼容原字段） ----
        m = re.search(r"(\d[\d,]*)\s*(?:k|pcs|pieces|units|pairs|sets|cartons)\b", text_lower)
        if m:
            qty = int(m.group(1).replace(",", "")) * (1000 if m.group(0).strip().endswith("k") else 1)
            if qty < item.get("moq", 0):
                item["uncertainty"].append(
                    f"客户数量 {qty:,} 低于该产品 MOQ {item['moq']:,}，需确认能否接小单")
        else:
            item["uncertainty"].append("客户未给出数量，无法核对 MOQ")
        if not exact:
            item["uncertainty"].insert(0, "关键词未精确命中，仅属最接近候选——需人工确认是否契合")

        # ---- 推荐理由（recommendation）----
        if exact:
            base = (f"关键词命中「{'、'.join(hits)}」，可作为主推候选"
                    if hits else "属同品类候选，可作为主推候选")
            if pending:
                item["recommendation"] = base + "；以下属性待确认：" + "；".join(pending)
            elif item["uncertainty"]:
                item["recommendation"] = base + "；需确认：" + "；".join(item["uncertainty"])
            else:
                item["recommendation"] = base
        else:
            item["recommendation"] = ("最接近候选（非精确匹配）：与询盘有部分词/品类重叠，"
                                      "需先确认客户要的具体产品与规格，再做推荐，勿当成交款报价。")

    def _closest(self, text_lower: str):
        """词/品类重叠度最高的产品，无任何重叠返回 None（=产品库与本询盘无关）。"""
        # 客户询盘里拆分出有意义的词（去掉超短/纯数量/虚词）
        stop = {"the", "and", "for", "our", "your", "are", "you", "with", "please",
                "need", "have", "this", "that", "from", "about", "will", "can",
                "please", "pcs", "pc", "pieces", "units", "sets", "delivery", "price",
                "quote", "quotation", "best", "product", "products", "quality",
                "good", "fast", "supplier", "market", "sample", "samples", "oem",
                "logo", "color", "packaging", "catalog"}
        tokens = set(re.findall(r"[a-z]{2,}", text_lower)) - stop
        best, best_overlap = None, 0
        for p in self.products:
            # 比对产品名 + 关键词 + 中文名里也可能含英文（统一转小写）
            hay = " ".join([
                p.get("name", ""), p.get("name_cn", ""),
                " ".join(p.get("keywords", []))]).lower()
            hay_tokens = set(re.findall(r"[a-z]{2,}", hay))
            overlap = len(tokens & hay_tokens)
            if overlap > best_overlap:
                best_overlap, best = overlap, p
        if best_overlap <= 0:
            return None
        item = dict(best)
        # 低置信度匹配分（0.1~0.2 区间），明确不等于可成交
        item["match_score"] = round(min(0.10 + best_overlap * 0.03, 0.2), 2)
        item["hit_keywords"] = []
        item["match_source"] = "closest_heuristic"
        self._annotate(item, [], text_lower, exact=False)
        return item

    def best_match(self, raw_text: str):
        """只取匹配度最高的那一个产品，没有匹配则返回 None"""
        results = self.match(raw_text)
        return results[0] if results else None


# 简单自测：直接运行本文件时执行
if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    matcher = ProductMatcher(os.path.join(here, "..", "data", "products.json"))
    demo = "Hi, we are looking for 5000pcs neoprnee swim caps"
    for p in matcher.match(demo):
        print(p["name"], "->", p["match_score"])
