# -*- coding: utf-8 -*-
"""
模块③：线索评分器 (LeadScorer) —— 八维可解释评分模型 v4

第三轮优化七、八：
  1) 八个维度全部可解释，每维输出 分数 + 原因 + 「输入事实 → 判断 → 分数」证据链：
     采购意向 / 订单价值 / 需求明确度 / 客户信息完整度 / 紧迫度 /
     产品匹配度 / 商务成熟度 / 成交可能性
  2) 不让 AI 给出无法解释的数字：综合分 = Σ(维度分 × 权重)，权重随结果一起输出，
     任何人都能用计算器复算。
  3) 订单价值不虚构金额：没有产品库真实单价时，只按采购规模档位描述，
     输出 order_value_confidence 与 order_value_basis。

八维权重（合计 1.00）：
┌────────────────────┬──────┬────────────────────────────────────────────┐
│ 维度                │ 权重 │ 看什么                                       │
├────────────────────┼──────┼────────────────────────────────────────────┤
│ purchase_intent    │ .20  │ 采购意向：数量/找供应商/要报价/样品/兴趣/复购    │
│ order_value        │ .20  │ 订单价值：采购规模档位（不虚构金额）             │
│ requirement_clarity│ .12  │ 需求明确度：规格/定制/包装/认证/目的地/交期      │
│ customer_quality   │ .12  │ 客户信息完整度：公司/官网/邮箱/联系人/职位      │
│ urgency            │ .08  │ 紧迫度：urgent/ASAP/明确短交期                 │
│ product_match      │ .10  │ 产品匹配度：产品库匹配结果（没有匹配就低分）     │
│ business_maturity  │ .08  │ 商务成熟度：贸易术语/付款方式/目标价/样品/包装   │
│ conversion_prob.   │ .10  │ 成交可能性：有货可接+换供应商+市场契合-未定项    │
└────────────────────┴──────┴────────────────────────────────────────────┘

等级（Lead Level，80/60/40 分档）：
  ≥80  A - High Priority          → 4 小时内回复
  ≥60  B - Medium-High Priority   → 24 小时内回复
  ≥40  C - Normal Priority        → 48 小时内回复
  <40  D - Low Priority           → 先归档，定期开发信培养

向后兼容：score/grade/advice/dims/reasons/urgency 及旧六维字段名全部保留。
"""

import re

from agent.extractor import TIER1_MARKETS, TIER2_MARKETS

# ---------- 关键词正则（离线、可解释，命中即给 reason） ----------

_RE_SUPPLIER = re.compile(
    r"\b(looking\s+for|seeking|searching\s+for|find\s+(a\s+)?(supplier|manufacturer|"
    r"vendor|factory)|we\s+are\s+sourcing|we\s+buy|purchasing\s+manager|buyer)\b", re.I)
_RE_PRICE_REQ = re.compile(r"\b(quote|quotation|price\s+list|best\s+price|"
                           r"send\s+(me\s+)?(your\s+)?price|cif|fob\s+price)\b", re.I)
_RE_SAMPLE = re.compile(r"\b(sample[s]?\s+(request|required)?|free\s+sample|"
                        r"test\s+sample|ask\s+for\s+samples?)\b", re.I)
_RE_INTEREST = re.compile(r"\b(we\s+are\s+interested|interested\s+in|we\s+need|"
                          r"we\s+want|looking\s+to\s+buy)\b", re.I)
_RE_REPEAT = re.compile(r"\b(repeat\s+order|reorder|long\s*[-\s]?term|annually|"
                        r"every\s+(month|quarter|season|year)|regular\s+order)\b", re.I)
_RE_SPEC = re.compile(r"\b(model|specification|spec|material|silicone|latex|"
                      r"neoprene|vinyl|size[sd]?|color|colour|mm|cm|gsm|"
                      r"\d+\s*(ml|l|cl|oz))\b", re.I)
_RE_CUSTOM = re.compile(r"\b(custom\w*|logo|oem|odm|private\s+label|print\w*|"
                        r"emboss\w*|engrav\w*)\b", re.I)
_RE_PACK = re.compile(r"\b(packag\w*|poly\s?bag|blister|header\s?card|hang\s?tag|"
                      r"carton|gift\s?box|barcode|premium)\b", re.I)
_RE_CERT = re.compile(r"\b(ce|en\s?71|reach|fda|bsci|iso\s?\d{0,5}|sgs|oeko[-\s]?tex|"
                      r"cpsia|rohs|lfgb|astm|certificat\w*|certificate)\b", re.I)
_RE_DELIVER = re.compile(r"\b(deliver\w*|lead\s*time|deadline|shipment|eta|"
                         r"urgent|asap|within\s+\d+\s+(day|week)|by\s+(jan|feb|mar|"
                         r"apr|may|jun|jul|aug|sep|oct|nov|dec)\w*)\b", re.I)
_RE_SWITCH = re.compile(r"\b(currently\s+(buy\w*|sourc\w*|purchas\w*|work\w*)|"
                        r"existing\s+supplier|another\s+supplier|other\s+vendor|"
                        r"switch\w*)\b", re.I)
_RE_INCOTERM = re.compile(r"\b(fob|cif|exw|ddp|ddu|dap|fca|cfr|cnf)\b", re.I)
_RE_PAYMENT = re.compile(r"\b(t\s?/\s?t|l\s?/\s?c|paypal|western\s?union|deposit|"
                         r"payment\s+terms?)\b", re.I)
_RE_TARGET_PRICE = re.compile(r"\b(target\s+price|budget|price\s+range|unit\s+price)\b", re.I)

# 紧急度（独立维度）
_URG_HIGH = re.compile(
    r"\b(urgent|asap|as\s+soon\s+as\s+possible|immediate|immediately|rush\s+order)\b", re.I)
_URG_MED = re.compile(
    r"\b(fast\s+delivery|quick\s+delivery|time\s+is\s+tight)\b", re.I)

# 客户给的具体交期（第四版优化一）：within/in N days/weeks、by/before 某月/下周等
_RE_DEADLINE = re.compile(
    r"\b(within|in)\s+\d+\s*(day|days|week|weeks|month|months)\b|"
    r"\b(by|before)\s+(next\s+)?(week|month)\b|"
    r"\b(by|before)\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*",
    re.I)


def _cap(x: int) -> int:
    return max(0, min(100, int(x)))


def _ev(fact: str, judgment: str, score) -> dict:
    """一条「输入事实 → 判断 → 分数」证据（第三轮优化八）。score 可为负数。"""
    sign = f"+{score}" if isinstance(score, int) and score >= 0 else str(score)
    return {"fact": fact, "judgment": judgment, "score": sign}


class LeadScorer:
    """八维可解释线索评分器。

    用法（向后兼容）：
        result = LeadScorer().score(info, matched_products, text=inquiry_text)
    result 含 overall_score / 八维 score / 每维 detail 与 evidence 证据链 /
    order_value_basis / weights（权重表，供一致性检查复算），
    及兼容旧字段 score / grade / dims / reasons / urgency。

    第四版优化一：grade / lead_level / recommended_response_time 由
    customer_quality_total（六维客户质量分，不含接单能力）判定；
    overall_score 仍为八维综合分（含我方接单能力），仅作参考展示。
    """

    # 八维权重（和 = 1.00）
    WEIGHTS = {
        "intent": 0.20,
        "volume": 0.20,
        "clarity": 0.12,
        "quality": 0.12,
        "urgency": 0.08,
        "product_match": 0.10,
        "business_maturity": 0.08,
        "conversion": 0.10,
    }

    # 决定“等级 / 回复时限”的维度 = 客户质量（第四版优化一）：
    # product_match / conversion 度量的是“我方有没有货、能不能接”，
    # 不应拖累客户价值与跟进时限的判断，故不参与等级计算。
    QUALITY_KEYS = ("intent", "volume", "clarity", "quality",
                    "urgency", "business_maturity")

    LEVEL_NAME = {
        "A": "A - High Priority",
        "B": "B - Medium-High Priority",
        "C": "C - Normal Priority",
        "D": "D - Low Priority",
    }
    LEVEL_RESPONSE = {
        "A": "4 小时内回复",
        "B": "24 小时内回复",
        "C": "48 小时内回复",
        "D": "可先归档，定期开发信培养",
    }

    def score(self, info: dict, matched_products: list, text: str = "") -> dict:
        info = info or {}
        matched_products = matched_products or []
        low = (text or "").lower()

        # 规格是否仍未定（影响成交可能性，理由必须写实）
        spec_open = False
        try:
            from agent.insight import find_open_options
            for frag in find_open_options(text or ""):
                if re.search(r"\d+\s*(ml|l|oz)|size|colour|color", frag, re.I) \
                        or " or " in frag:
                    spec_open = True
                    break
        except Exception:
            spec_open = False

        d_intent = self._intent(info, low)
        d_volume = self._volume(info, low, matched_products)
        d_clarity = self._clarity(info, low, matched_products)
        d_quality = self._quality(info)
        d_urgency = self._urgency(info, low)
        d_match = self._product_match(matched_products)
        d_maturity = self._maturity(low)
        d_conversion = self._conversion(info, low, matched_products, spec_open)

        dims = [d_intent, d_volume, d_clarity, d_quality, d_urgency,
                d_match, d_maturity, d_conversion]
        w = self.WEIGHTS

        total = _cap(round(sum(d["score"] * w[d["key"]] for d in dims)))

        # 客户质量分（六维归一）：等级 / 回复时限只看它，接单能力维度不掺和
        q_sum = sum(w[k] for k in self.QUALITY_KEYS)
        quality_total = _cap(round(
            sum(d["score"] * w[d["key"]] for d in dims if d["key"] in self.QUALITY_KEYS) / q_sum))
        grade, advice = self._grade(quality_total, low)

        # 可解释：逐条打分依据（带中文名 + 分数）
        reasons = [f"[{d['name']} {d['score']}分] {r}" for d in dims for r in d["detail"]]
        reasons.append(
            "综合 = " + " + ".join(
                f"{d['name']}{d['score']}×{w[d['key']]:.2f}" for d in dims)
            + f" = {total} 分（权重和 = 1.00，可复算）")
        reasons.append(
            f"等级按「客户质量分 {quality_total}」（六维归一，不含产品匹配/成交概率）判定"
            f" → {grade} 级；8维综合 {total} 分含我方接单能力，仅作参考")

        # 紧急度等级（独立字段，供界面/跟进用）
        urgency = info.get("urgency")
        if urgency is None:
            urgency = self._urgency_label(low)

        return {
            # ---- 兼容旧字段 ----
            "score": total,
            "grade": grade,
            "advice": advice,
            "dims": dims,             # 八维明细（含 evidence 证据链），界面自动渲染
            "reasons": reasons,
            "urgency": urgency,
            # ---- 规范字段名 ----
            "overall_score": total,
            "lead_level": self.LEVEL_NAME[grade],
            "recommended_response_time": self.LEVEL_RESPONSE[grade],
            "customer_quality_total": quality_total,   # 客户质量分（六维归一）→ 等级/时限依据
            "weights": w,             # 权重表（一致性检查用它复算综合分）
            "purchase_intent_score": d_intent["score"],
            "order_volume_score": d_volume["score"],
            "order_value_score": d_volume["score"],   # 别名，兼容第一轮命名
            "requirement_clarity_score": d_clarity["score"],
            "customer_quality_score": d_quality["score"],
            "urgency_score": d_urgency["score"],
            "product_match_score": d_match["score"],
            "business_maturity_score": d_maturity["score"],
            "conversion_probability_score": d_conversion["score"],
            # ---- 订单价值：不虚构金额 ----
            "order_value_confidence": d_volume["confidence"],
            "order_value_basis": d_volume["basis"],
        }

    # ================= 八个维度 =================

    def _intent(self, info, low) -> dict:
        s, detail, ev = 0, [], []
        if info.get("quantity"):
            s += 30
            # 第五轮第二次补丁 03：约数（about/around/approximately/initial/trial）
            # 也是客户明确给出的采购量 → 照常计满分，不降档、不降质量评分
            approx = bool(re.search(
                r"\b(about|around|approx\.?|approximately|or\s+so|more\s+or\s+less|"
                r"initial|trial)\b", low))
            detail.append("明确给出采购数量（约数也按有效采购量计，不降档） (+30)"
                          if approx else "明确给出采购数量 (+30)")
            ev.append(_ev(f"数量 {info['quantity']:,}" + ("（约数）" if approx else ""),
                          "有具体采购量，不是泛询盘"
                          + ("；约数不降低意向/质量评分" if approx else ""), 30))
        if _RE_SUPPLIER.search(low):
            s += 25
            detail.append("正在寻找供应商（looking for supplier 等） (+25)")
            ev.append(_ev("出现寻找供应商的表述", "处于选供应商阶段，意向真实", 25))
        if _RE_PRICE_REQ.search(low):
            s += 20
            detail.append("主动要报价 (+20)")
            ev.append(_ev("主动要求报价", "推进到询价环节", 20))
        if _RE_SAMPLE.search(low):
            s += 15
            detail.append("索取样品 (+15)")
            ev.append(_ev("提到样品", "愿意进入打样验证环节", 15))
        if _RE_INTEREST.search(low):
            s += 10
            detail.append("表达采购兴趣 (+10)")
            ev.append(_ev("出现 interested / we need 等表述", "有采购兴趣表达", 10))
        if _RE_REPEAT.search(low):
            s += 15
            detail.append("有复购 / 长期合作信号 (+15)")
            ev.append(_ev("出现长期/复购信号", "潜在长期客户", 15))
        if not ev:
            ev.append(_ev("询盘无采购信号词", "仅为泛泛了解", 0))
        return {"key": "intent", "name": "采购意向", "score": _cap(s),
                "detail": detail, "evidence": ev}

    def _volume(self, info, low, matched_products) -> dict:
        """订单价值：只基于"采购规模档位"，不虚构金额。"""
        s, detail, ev = 0, [], []
        qty_raw = info.get("quantity")
        qty = None
        if isinstance(qty_raw, int):
            qty = qty_raw
        elif isinstance(qty_raw, str) and qty_raw.strip().isdigit():
            qty = int(qty_raw)

        if qty:
            if qty >= 5000:
                s += 55
                detail.append(f"采购规模 {qty:,} 件，属大单档位 (+55)")
                judgment = "大单档位"
            elif qty >= 1000:
                s += 45
                detail.append(f"采购规模 {qty:,} 件，中大批量档位 (+45)")
                judgment = "中大批量档位"
            elif qty >= 300:
                s += 35
                detail.append(f"采购规模 {qty:,} 件，中等批量档位 (+35)")
                judgment = "中等批量档位"
            else:
                s += 20
                detail.append(f"采购规模 {qty:,} 件，属小批量档位 (+20)")
                judgment = "小批量档位"
            ev.append(_ev(f"采购数量 {qty:,} 件", judgment, s))
        else:
            s += 10
            detail.append("未给出具体数量，只能按保守档位估 (+10)")
            ev.append(_ev("未给出数量", "按保守档位估", 10))

        if _RE_REPEAT.search(low):
            s += 20
            detail.append("有年度量 / 长期采购信号 (+20)")
            ev.append(_ev("出现年度/长期采购表述", "按长期量加成", 20))

        country = info.get("country")
        if country in TIER1_MARKETS:
            s += 15
            detail.append(f"{country} 属欧美主流市场，客单价 / 利润空间较好 (+15)")
            ev.append(_ev(f"目的地 {country}", "欧美主流市场", 15))
        elif country in TIER2_MARKETS or country:
            s += 8
            detail.append(f"{country} 市场 (+8)")
            ev.append(_ev(f"目的地 {country}", "一般市场", 8))

        # ---- 置信度与依据：不假装知道订单金额 ----
        has_price = any(m.get("price_range") for m in matched_products)
        confidence = "low"
        if qty and has_price:
            confidence = "high"
        elif qty and not has_price:
            confidence = "medium"
        basis = []
        basis.append(f"采购规模明确：{'%s pcs' % qty if qty else '未给出'}")
        if has_price:
            top = matched_products[0]
            basis.append(f"产品库参考单价：USD {top['price_range'][0]:.2f}-"
                         f"{top['price_range'][1]:.2f} / {top.get('unit', 'pc')}")
        else:
            basis.append("产品单价：未知（产品库未匹配 / 未给出真实价格）")
            basis.append("预计订单金额：无法计算 —— 订单价值评分只基于采购规模档位，"
                         "不基于虚构的订单金额。")

        return {"key": "volume", "name": "订单价值", "score": _cap(s),
                "detail": detail, "evidence": ev,
                "confidence": confidence, "basis": basis}

    def _clarity(self, info, low, matched_products) -> dict:
        s, detail, ev = 0, [], []
        if any((m.get("match_score") or 0) >= 0.3 for m in matched_products):
            s += 25
            detail.append(f"说清了要什么产品：{matched_products[0].get('name', '')} (+25)")
            ev.append(_ev(f"产品库匹配「{matched_products[0].get('name', '')}」",
                          "产品类型明确", 25))
        if _RE_SPEC.search(low):
            s += 20
            detail.append("有具体型号 / 规格 / 容量 / 颜色等描述 (+20)")
            ev.append(_ev("出现规格/容量/颜色描述", "需求有细节支撑", 20))
        if _RE_CUSTOM.search(low):
            s += 15
            detail.append("有定制要求（logo / OEM / 颜色） (+15)")
            ev.append(_ev("出现定制/logo/OEM", "定制要求明确", 15))
        if _RE_PACK.search(low):
            s += 10
            detail.append("有包装要求 (+10)")
            ev.append(_ev("出现包装要求", "包装明确", 10))
        if _RE_CERT.search(low):
            s += 10
            detail.append("提及认证 (+10)")
            ev.append(_ev("提及认证", "合规要求有表达", 10))
        if info.get("country"):
            s += 10
            detail.append(f"明确了目的地 {info['country']} (+10)")
            ev.append(_ev(f"目的地 {info['country']}", "市场明确", 10))
        if _RE_DELIVER.search(low):
            s += 10
            detail.append("提及交期 / 时效要求 (+10)")
            ev.append(_ev("提及交期/时效", "交付要求有表达", 10))
        if not ev:
            ev.append(_ev("询盘只有泛泛需求", "明确度低", 0))
        return {"key": "clarity", "name": "需求明确度", "score": _cap(s),
                "detail": detail, "evidence": ev}

    def _quality(self, info) -> dict:
        """客户信息完整度：资料是否齐全、像不像真买家。"""
        s, detail, ev = 0, [], []
        if info.get("company"):
            s += 30
            detail.append(f"有公司名「{info['company']}」，公司级采购 (+30)")
            ev.append(_ev(f"公司 {info['company']}", "公司级采购", 30))
        if info.get("website"):
            s += 20
            detail.append(f"有官网 {info['website']}，可验证 (+20)")
            ev.append(_ev(f"官网 {info['website']}", "背景可验证", 20))
        if info.get("email"):
            s += 20
            detail.append("有联系邮箱 (+20)")
            ev.append(_ev("有邮箱", "可直接联系", 20))
        if info.get("contact_name"):
            s += 15
            detail.append(f"有联系人 {info['contact_name']} (+15)")
            ev.append(_ev(f"联系人 {info['contact_name']}", "实名询盘", 15))
        if info.get("job_title"):
            s += 15
            detail.append(f"有职位 {info['job_title']}（采购负责人） (+15)")
            ev.append(_ev(f"职位 {info['job_title']}", "采购决策相关", 15))
        if not (info.get("company") or info.get("website") or info.get("email")):
            detail.append("无公司 / 官网 / 邮箱信息，需先核实客户背景")
            ev.append(_ev("无公司/官网/邮箱", "客户背景未核实", 0))
        return {"key": "quality", "name": "客户信息完整度", "score": _cap(s),
                "detail": detail, "evidence": ev}

    def _urgency(self, info, low) -> dict:
        """紧迫度独立维度：影响回复速度，不混入采购意向。"""
        label = info.get("urgency")
        if label is None:
            label = self._urgency_label(low)
        score = {"high": 90, "medium": 55, "low": 15}.get(label, 5)
        detail = {
            "high": "客户表达明确紧急（urgent / ASAP / 明确短交期），需优先处理",
            "medium": "客户表达希望尽快（fast delivery / time is tight），正常从速",
            "low": "客户未显式强调时间，按常规节奏跟进",
        }.get(label, "未识别到紧急表达")
        ev = [_ev("紧急度信号词扫描",
                  {"high": "有 urgent/ASAP/明确短交期",
                   "medium": "有希望尽快的表述",
                   "low": "无紧急表达"}.get(label, "无"), score)]
        return {"key": "urgency", "name": "紧迫度", "score": score,
                "detail": [detail], "evidence": ev}

    def _product_match(self, matched_products) -> dict:
        """产品匹配度（第三轮新增维度）：产品库能不能接住这单，不编造。"""
        top = matched_products[0] if matched_products else None
        if top and (top.get("match_score") or 0) >= 0.3:
            score = _cap(int((top["match_score"] or 0) * 100))
            detail = [f"产品库匹配「{top.get('name', '')}」，"
                      f"匹配度 {int(top['match_score'] * 100)}%"]
            ev = [_ev(f"匹配度 {int(top['match_score'] * 100)}%",
                      "产品库有对应产品", score)]
        elif top:
            score = 15
            detail = [f"仅有最接近候选「{top.get('name', '')}」"
                      f"（匹配度 {int(top['match_score'] * 100)}%，非精确匹配），"
                      "需人工确认是否契合"]
            ev = [_ev(f"最接近候选匹配度仅 {int(top['match_score'] * 100)}%",
                      "证据不足，不能当精确匹配", 15)]
        else:
            score = 5
            detail = ["当前产品库没有足够证据找到匹配产品（如实提示，不编造）——"
                      "需先向客户索取产品图片 / 链接 / 型号"]
            ev = [_ev("产品库无匹配", "无法确认能供应", 5)]
        return {"key": "product_match", "name": "产品匹配度", "score": score,
                "detail": detail, "evidence": ev}

    def _maturity(self, low) -> dict:
        """商务成熟度（第三轮新增维度）：客户像不像做过外贸的人。"""
        s, detail, ev = 0, [], []
        if _RE_INCOTERM.search(low):
            s += 25
            detail.append("主动提及贸易术语（FOB/CIF 等） (+25)")
            ev.append(_ev("出现贸易术语", "懂外贸流程", 25))
        if _RE_PAYMENT.search(low):
            s += 25
            detail.append("主动提及付款方式 (+25)")
            ev.append(_ev("出现付款方式", "商务条件有概念", 25))
        if _RE_TARGET_PRICE.search(low):
            s += 20
            detail.append("给出目标价 / 预算 (+20)")
            ev.append(_ev("出现 target price/budget", "有价格预期", 20))
        if _RE_SAMPLE.search(low):
            s += 15
            detail.append("走样品流程 (+15)")
            ev.append(_ev("提到样品", "按正规采购流程走", 15))
        if _RE_PACK.search(low):
            s += 15
            detail.append("对包装有具体要求 (+15)")
            ev.append(_ev("出现包装要求", "细节要求成熟", 15))
        if not ev:
            ev.append(_ev("无商务条件表述", "商务成熟度未知", 0))
        return {"key": "business_maturity", "name": "商务成熟度", "score": _cap(s),
                "detail": detail, "evidence": ev}

    def _conversion(self, info, low, matched_products, spec_open: bool) -> dict:
        """成交可能性：正向信号加分，未定项按事实写明扣分——低分必须说得清为什么。"""
        s, detail, ev = 0, [], []
        if any((m.get("match_score") or 0) >= 0.3 for m in matched_products):
            s += 35
            detail.append("需求与自有产品匹配，有货可接 (+35)")
            ev.append(_ev("产品库有匹配产品", "有货可接", 35))
        else:
            detail.append("产品库当前未匹配到该产品，成交需先确认能否供应（不虚报）")
            ev.append(_ev("产品库无匹配", "供应能力未确认", 0))

        if _RE_SWITCH.search(low):
            s += 25
            detail.append("正在找新供应商（替换现有供应商的机会） (+25)")
            ev.append(_ev("出现换供应商信号", "有切入机会", 25))
        if _RE_REPEAT.search(low):
            s += 15
            detail.append("有长期采购信号，复购可能性高 (+15)")
            ev.append(_ev("出现复购信号", "长期价值", 15))
        country = info.get("country")
        if country in TIER1_MARKETS:
            s += 15
            detail.append(f"{country} 主流市场，我方供货契合度高 (+15)")
            ev.append(_ev(f"目的地 {country}", "市场契合", 15))
        elif country:
            s += 8
            detail.append(f"目的地 {country} (+8)")
            ev.append(_ev(f"目的地 {country}", "可供货", 8))
        if _URG_HIGH.search(low):
            s += 10
            detail.append("订单表达紧急，推进意愿强 (+10)")
            ev.append(_ev("出现紧急表述", "推进意愿强", 10))

        # ---- 未定项扣分（每条扣分都有事实依据，写进证据链） ----
        if spec_open:
            s -= 15
            detail.append("但产品规格尚未最终确认（客户给了候选值） (-15)")
            ev.append(_ev("规格仍为二选一/候选状态", "规格未定影响成单确定性", -15))
        if not info.get("email"):
            s -= 10
            detail.append("缺少有效邮箱，只能靠原渠道联系 (-10)")
            ev.append(_ev("无有效邮箱", "触达受限", -10))
        if not info.get("target_price") and not _RE_TARGET_PRICE.search(low):
            s -= 5
            detail.append("未知预算/目标价，价格预期不明 (-5)")
            ev.append(_ev("未提及预算", "价格预期未知", -5))
        if _RE_CERT.search(low):
            s -= 5
            detail.append("认证要求未最终确认，合规成本待核 (-5)")
            ev.append(_ev("提及认证但未定标准", "合规成本待核", -5))

        return {"key": "conversion", "name": "成交可能性", "score": _cap(s),
                "detail": detail, "evidence": ev}

    @staticmethod
    def _urgency_label(low: str):
        if _URG_HIGH.search(low):
            return "high"
        if _URG_MED.search(low) or re.search(r"\bwithin\s+\d+\s+(day|week)s?\b", low, re.I):
            return "medium"
        return "low"

    @staticmethod
    def _detect_urgency(low: str):
        """兼容旧接口：只返回 high/medium/None（紧急判断）"""
        if _URG_HIGH.search(low):
            return "high"
        if _URG_MED.search(low):
            return "medium"
        return None

    # ================= 等级 =================

    @staticmethod
    def _grade(quality_total: int, low: str = "") -> tuple:
        """按“客户质量分”判等级 / 回复时限（第四版优化一）。

        - 只依赖客户侧质量维度（意向/量/明确度/信息完整/紧迫/成熟度）；
          我方能否接单（product_match / conversion）不拖累客户价值判断；
        - 客户有明确时间要求（ASAP/urgent，或给了交期如 within 3 weeks）
          的普通质量线索兜底收紧为 24 小时，避免“客户在催却排 48h 档”。
        """
        if quality_total >= 80:
            return "A", "🔥 A - High Priority 高优先级询盘，建议 4 小时内回复"
        if quality_total >= 60:
            return "B", "🟠 B - Medium-High Priority 较高价值询盘，建议 24 小时内回复"
        if quality_total >= 40:
            if low and (_URG_HIGH.search(low) or _RE_DEADLINE.search(low)):
                return ("B",
                        "🟠 B - 客户有明确时间要求（紧急/指定交期），"
                        "由 48 小时收紧为 24 小时内回复")
            return "C", "🟢 C - Normal Priority 普通询盘，建议 48 小时内回复"
        return "D", "⚪ D - Low Priority 低意向询盘，可先归档，定期发开发信培养"
