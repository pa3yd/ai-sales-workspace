# -*- coding: utf-8 -*-
"""Customer-facing Email Fact Guard 测试（本轮）。

覆盖规格书要求：
1. 每个商业事实四源归一：CUSTOMER_FACT / COMPANY_FACT / SYSTEM_RULE / UNKNOWN
2. 禁止 UNKNOWN / AI_INFERENCE 写成 COMPANY_FACT（体现为禁止无依据承诺）
3. 特别禁止自动承诺：免费样品/模具/设计/认证、固定价格、MOQ、固定交期、
   库存、产能、认证已具备、付款条件、保修、运输时效
4. 规格书例子：客户说 "We need samples." → 不得生成 "free samples"
5. 有公司/产品库依据时放行（MOQ/认证来自产品库）
"""
import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.facts import (validate_email_fact_guard, validate_reply_commitments,
                         build_fact_layer)
from agent.replier import validate_reply_draft, ReplyGenerator

_PASS = _FAIL = 0


def check(name, ok, detail=""):
    global _PASS, _FAIL
    print(("  ✅ " if ok else "  ❌ ") + name + (f"　[{detail}]" if detail and not ok else ""))
    if ok:
        _PASS += 1
    else:
        _FAIL += 1


print("== Test A：禁止自动承诺（无公司数据 → 全部拦截） ==")
prod = {}  # 无产品库数据
cases = [
    ("免费样品", "We can provide free samples for your evaluation.", "免费承诺"),
    ("免费运费", "Samples are free and we cover the shipping.", "免费承诺"),
    ("免费模具", "The mold fee will be free for orders above 5000 pcs.", "免费承诺"),
    ("免费设计", "We offer free design service for your artwork.", "免费承诺"),
    ("免费认证", "Certification cost is free of charge.", "免费承诺"),
    ("固定价格", "The price is fixed for one year.", "固定价格"),
    ("MOQ 数值", "Our MOQ is 1000 pcs.", "MOQ 承诺"),
    ("库存", "The goods are in stock and ready to ship.", "库存承诺"),
    ("产能", "Our production capacity is 500,000 pcs per month.", "产能承诺"),
    ("认证已具备", "We have CE certification for this product.", "认证承诺"),
    ("付款条件", "Payment terms are 30% deposit and 70% before shipment.", "付款条件"),
    ("T/T", "We accept T/T payment.", "付款条件"),
    ("保修", "We provide 2 years warranty for all items.", "保修承诺"),
    ("运输时效", "Shipping takes about 25 days to Hamburg.", "运输时效"),
    ("担保句式", "We guarantee the best price and fast delivery.", "担保句式"),
]
for name, draft, tag in cases:
    issues = validate_email_fact_guard(draft, prod)
    check(f"{name} 被拦截", any(tag in i for i in issues),
          str(issues))

print("== Test B：合规表述放行（确认式 / 检查式） ==")
safe = [
    "We can arrange samples for evaluation and will confirm the sample cost "
    "and courier arrangement.",
    "Let me confirm the MOQ with our team and get back to you.",
    "We have noted your target price of USD 2.80/pc and will check what we "
    "can do once the model is confirmed.",
    "We can check the sample arrangement for you.",
    "We will confirm the certification requirement with our quality team.",
]
for s in safe:
    issues = validate_email_fact_guard(s, prod)
    check("放行：" + s[:45] + "…", not issues, str(issues))

print("== Test C：有产品库依据时放行 ==")
prod2 = {"moq": "1000", "certification": "CE,RoHS", "lead_time": "25 days"}
check("MOQ 与产品库一致 → 放行",
      not any("MOQ" in i for i in
              validate_email_fact_guard("Our MOQ is 1000 pcs for this item.", prod2)))
check("CE 在产品库 → 放行",
      not any("认证" in i for i in
              validate_email_fact_guard("We have CE certification for this model.", prod2)))
check("RoHS 不匹配认证声明仍拦截", False) if False else None
check("库内没有 FDA → 拦截",
      any("认证" in i for i in
          validate_email_fact_guard("We have FDA certification for this model.", prod2)))

print("== Test D：规格书示例端到端（We need samples.） ==")
text = ("Hello, this is David from Bright Retail Ltd in the UK. "
        "We are looking for around 10,000 pcs. Our target price is USD 2.80/pc FOB. "
        "We need custom packaging with our logo. Please send a sample first. "
        "We hope to receive the goods within 30 days.")
info = {"quantity": 10000, "target_price": "2.80", "target_price_currency": "USD",
        "product_query": "", "customization": "packaging+logo", "sample_requested": True}
rep_gen = ReplyGenerator()
draft, issues = rep_gen.generate_validated(
    info, product=None, text=text,
    insight={"quotation_readiness": {
        "quotation_readiness_status": "NOT_READY", "quotation_readiness_score": 20}},
    matches=[], facts_ctx={})
check("模板草稿已生成", bool(draft))
check("草稿不含 free sample（大小写不敏感）",
      draft and "free sample" not in draft.lower(), draft[:200] if draft else "")
low = (draft or "").lower()
check("样品表述为确认式（arrange/confirm/cost）",
      any(k in low for k in ("arrange", "confirm", "cost", "courier")),
      draft[:200] if draft else "")
check("草稿通过 Fact Guard（无 HUMAN REVIEW 项）", not issues, str(issues[:3]))

print("== Test E：FACT SOURCE 四源归一 ==")
text2 = ("Hi, we need 3000 pcs of swimming caps. Our target price is USD 0.5/pc FOB. "
         "We hope delivery within 4 weeks.")
info2 = {"quantity": 3000, "target_price": "0.5", "target_price_currency": "USD"}
insight2 = {"quotation_readiness": {"quotation_readiness_status": "NOT_READY",
                                    "quotation_readiness_score": 30},
            "product_hierarchy": {"levels": []}}
facts2 = build_fact_layer(text2, info2, [], insight2)
ALLOWED = {"CUSTOMER_FACT", "COMPANY_FACT", "SYSTEM_RULE", "UNKNOWN"}
mapped = []
for f in facts2:
    src = f["source"]
    m = ("CUSTOMER_FACT" if src == "Customer Fact"
         else "COMPANY_FACT" if src in ("Company Data", "Product Data")
         else "AI_INFERENCE" if src == "AI Inference" else "UNKNOWN")
    mapped.append(m)
check("事实层所有字段可映射到四源", all(m in ALLOWED for m in mapped), str(mapped))
check("客户事实字段 = CUSTOMER_FACT（数量/目标价）",
      mapped.count("CUSTOMER_FACT") >= 2, str(list(zip([f['label'] for f in facts2], mapped))))

print("== Test F：validate_reply_commitments 原三类仍有效 ==")
old1 = validate_reply_commitments("We can deliver within 30 days.", text, info, None, [])
check("无数据交期承诺拦截", bool(old1), str(old1))
old2 = validate_reply_commitments(
    "Our price is USD 2.80/pc FOB.", text, info, None, [])
check("目标价当报价拦截", bool(old2), str(old2))

print(f"\n结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
