# -*- coding: utf-8 -*-
"""
AI 外贸询盘分析 Agent - 主入口（LLM 版）
流程：询盘原文 -> ①信息提取 -> ②产品匹配 -> ③线索评分 -> ④回复草稿

用法：
  python main.py                       # 自动模式：有 API Key 就用 LLM，没有就用规则
  python main.py --mode llm            # 强制用 LLM（没 Key 会给出提示）
  python main.py --mode rule           # 强制用规则（离线可用）
  python main.py --check-key           # 只检测 API Key 是否配置正确
  python main.py 询盘.txt              # 分析你自己的询盘文本文件
  python main.py 询盘.txt --mode llm   # 组合使用
"""

import os
import sys
import json
import argparse
import re

sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文不乱码

from agent.extractor import InquiryExtractor
from agent.matcher import ProductMatcher
from agent.lead_score import LeadScorer
from agent.replier import ReplyGenerator
from agent.llm_client import DeepSeekClient, load_api_key, load_config, DeepSeekError
from agent.llm_extractor import LLMInquiryExtractor
from agent.llm_matcher import LLMProductMatcher

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(BASE_DIR, "data", "products.json")

DEMO_INQUIRIES = [
    # 演示 1：信息齐全的优质客户（含拼写错误 "neoprnee"）
    """Hi, we are looking for 5000pcs neoprnee swim caps.

This is Michael Brown from AquaGear Trading Ltd, a distributor in the United States.
You can check our website: www.aquagear-us.com
Please quote your best price. Email: michael@aquagear-us.com

Do you support custom logo printing? We need them for the next season.

Best regards,
Michael""",
    # 演示 2：信息很少的普通询盘
    """Hello,
I want 800 pcs swimming goggles.
Please send me your catalog and price list.
Thanks,
Sarah""",
    # 演示 3：德文整柜大客户（规则模式基本抓瞎，只有 LLM 能看懂）
    """Guten Tag!

Wir sind die Sport GmbH aus Germany, eine Handelskette mit 40 Filialen.
Wir benoetigen 20K pcs silicone swim caps mit OEM Logo, Lieferung nach Hamburg.
Unsere Website: www.sport-gmbh.de

Mit freundlichen Gruessen,
Thomas Mueller
E-Mail: t.mueller@sport-gmbh.de""",
]


def build_engine(mode: str):
    """
    根据模式构建分析引擎，返回 (extractor, matcher, client)
    mode: auto / llm / rule
    """
    api_key, key_source = load_api_key()

    if mode == "rule":
        print("运行模式：规则模式（离线，不调用 API）\n")
        return InquiryExtractor(), ProductMatcher(CATALOG_PATH), None

    if mode == "llm" and not api_key:
        print("× 强制 LLM 模式但未找到 API Key")
        print("  解决方法二选一：")
        print("    1) 编辑 config.json，把 Key 填进 DEEPSEEK_API_KEY 字段")
        print("    2) 命令行执行：setx DEEPSEEK_API_KEY sk-你的密钥（重开终端生效）")
        sys.exit(1)

    if not api_key:
        print("运行模式：规则模式（未检测到 API Key，自动降级）")
        print(f"  提示：{key_source}\n")
        return InquiryExtractor(), ProductMatcher(CATALOG_PATH), None

    model = load_config().get("MODEL", "deepseek-chat")
    client = DeepSeekClient(api_key, model=model)
    print(f"运行模式：LLM 模式（DeepSeek {model}，Key 来源：{key_source}）\n")
    return (LLMInquiryExtractor(client),
            LLMProductMatcher(client, CATALOG_PATH),
            client)


def analyze(text: str, extractor, matcher, client, previous: dict = None) -> dict:
    """执行完整分析流水线，返回一份完整报告

    业务逻辑优化：在旧的 提取→匹配→评分→草稿 之上，新增业务洞察层：
      quotation_readiness（报价准备度）/ blocking_reasons（阻塞原因）/
      partially_confirmed（候选未定）/ risks（风险）/ next_actions（下一步动作）
    全部为新增字段，旧字段原样保留，向后兼容。
    """
    info = extractor.extract(text)              # ① 提取
    # Runtime fact evolution：客户把旧数量改成新数量时，必须在所有下游
    # 决策前先形成当前事实。否则 UI / 邮件会把 "3,000 instead of 5,000"
    # 误判成数量冲突，或继续把旧报价数量当当前数量。
    _rev = _extract_quantity_revision(text)
    if _rev:
        info["quantity"] = _rev["current"]
        info["quantity_unit"] = _rev["unit"]
        info["quantity_revision"] = _rev
    # TEST03：规则模式下也把"客户原话的产品短语"补进 info.product_query——
    # 客户明确说了产品 ≠ 产品库匹配成功；product_query 只承担“客户说了什么”，
    # 产品库是否匹配由 matcher/matches 独立表达，两者不再混为一谈。
    if not (info.get("product_query") or "").strip():
        try:
            from agent.extractor import extract_customer_product
            _phrase = extract_customer_product(text)
            if _phrase:
                info["product_query"] = _phrase
        except Exception:
            pass
    if not (info.get("product_query") or "").strip():
        _phrase = (_extract_labeled_product_phrase(text) or _extract_product_is_phrase(text)
                   or _extract_need_product_phrase(text))
        if _phrase:
            info["product_query"] = _phrase
    info.update({k: v for k, v in _extract_labeled_requirement_fields(text).items() if v and not info.get(k)})
    matches = matcher.match(text)               # ② 产品匹配
    lead = LeadScorer().score(info, matches, text=text)  # ③ 六维可解释线索评分

    # ③·5 缺失信息检测（与工作台同一套 gapcheck，口径一致）
    gaps = []
    try:
        from workbench.gapcheck import detect_missing
        gaps = detect_missing(text, info, matches, known_email=info.get("email") or "")
    except Exception:
        pass

    # ③·6 业务洞察层
    insight = {}
    try:
        from agent.insight import build_insight
        insight = build_insight(text, info, matches, lead, gaps)
    except Exception as e:
        print(f"   [警告] 业务洞察生成失败（不影响主流程）：{e}")

    # ④ 回复草稿：只绑定"已确认"的产品（第三阶段优化：防止弱匹配产品串进草稿）
    #    判定标准：规则匹配需有精确关键词命中；LLM 匹配置信度需 >= 0.5。
    #    closest_heuristic（最接近候选）与低置信 LLM 匹配一律不作为已确认产品，
    #    草稿按"客户未指明产品"处理，只追问不报价。
    top = matches[0] if matches else None
    if top and (top.get("hit_keywords")
                or (top.get("match_source") == "LLM"
                    and (top.get("match_score") or 0) >= 0.5)):
        reply_product = top
    else:
        reply_product = None

    # 产品库清单供草稿校验使用（校验"无确认产品时禁止出现产品名"）
    catalog_products = getattr(matcher, "products", None)
    if catalog_products is None:
        catalog_products = getattr(getattr(matcher, "rule_matcher", None),
                                   "products", []) or []

    # ③·7 业务事实层（第九轮）：Value / Source / Certainty 统一三元组
    #      客户目标价≠公司报价、约数量≠确认量、客户期望交期≠公司承诺。
    #      纯新增字段，旧字段与旧流程原样保留。
    facts_layer, reply_ctx = [], {}
    try:
        from agent.facts import build_fact_layer, build_reply_context
        facts_layer = build_fact_layer(text, info, matches, insight)
        reply_ctx = build_reply_context(text, info, matches, insight,
                                        product=reply_product)
    except Exception as e:
        print(f"   [警告] 事实层生成失败（不影响主流程）：{e}")

    # ④·前 首轮追问策略（第五轮优化）：P0/P1/P2 分级 + 问题数限流（默认 1-2，上限 3）
    # 第五轮补丁：必须把 matches 传进去——产品库命中状态决定是否要 model，
    # 缺了 matches 会一律走 NO_MATCH 分支，对在库产品也会问"哪个型号"。
    reply_plan = {}
    try:
        from agent.reply_strategy import build_question_plan
        reply_plan = build_question_plan(text, info, reply_product, insight,
                                         matches=matches)
    except Exception as e:
        print(f"   [警告] 追问策略生成失败（不影响主流程）：{e}")

    draft, draft_issues = ReplyGenerator().generate_validated(
        info, reply_product, client, text=text, insight=insight,
        catalog_products=catalog_products, previous=previous,
        matches=matches, facts_ctx=reply_ctx)
    if info.get("quantity_revision"):
        try:
            from workbench import email_unified as _email_runtime
        except Exception:
            try:
                import email_unified as _email_runtime
            except Exception:
                _email_runtime = None
        if _email_runtime:
            _mail = _email_runtime.generate_customer_email({
                "text": text, "info": info, "matches": matches, "gaps": gaps,
                "stored_draft": "", "seller_company": "",
                "customer_product": info.get("product_query") or "",
                "quotation_ready": False, "known": [k for k, v in info.items() if v],
            }, intent="AUTO")
            if (_mail.get("body") or "").strip():
                draft = _mail["body"]
                draft_issues = _mail.get("issues") or []
    if draft_issues:
        print(f"   [校验警告] 回复草稿仍有 {len(draft_issues)} 处疑似无依据内容，"
              f"请人工复核：{'；'.join(draft_issues[:3])}")

    # ⑤ 系统一致性检查（第三轮优化十二）：分析完成后自动跑 6 项检查，冲突优先提示
    consistency = []
    try:
        from agent.insight import check_consistency
        consistency = check_consistency(text, info, matches, lead, insight, draft, gaps)
        bad = [c for c in consistency if not c["ok"]]
        if bad:
            print(f"   [一致性警告] 发现 {len(bad)} 处逻辑冲突，请人工复核！")
            for c in bad:
                print(f"      ✗ {c['check']}：{c['detail']}")
    except Exception as e:
        print(f"   [警告] 一致性检查失败（不影响主流程）：{e}")

    return {"extracted": info, "matches": matches, "lead": lead,
            "draft": draft, "draft_issues": draft_issues, "gaps": gaps,
            "insight": insight, "consistency": consistency,
            "reply_plan": reply_plan,
            # Fact Guard：草稿含未经公司知识库验证的商业承诺 → 禁止自动发送
            "human_review_required": bool(draft_issues),
            "facts": facts_layer, "reply_context": reply_ctx}


def _extract_labeled_requirement_fields(text: str) -> dict:
    """Parse common RFQ label lines without changing the AI decision model."""
    labels = {
        "material": ("material", "材质"),
        "capacity": ("capacity", "容量"),
        "colors": ("colors", "color", "colours", "colour", "颜色"),
        "customization": ("customization", "customisation", "定制"),
        "incoterm": ("incoterm", "trade term", "贸易条款"),
        "destination": ("destination", "ship to", "目的地"),
        "lead_time": ("lead time", "delivery time", "timeline", "交期"),
        "samples": ("samples", "sample", "样品"),
    }
    out = {}
    lines = str(text or "").splitlines()
    for key, names in labels.items():
        for name in names:
            pat = rf"^\s*{re.escape(name)}\s*[:：-]\s*(.+?)\s*$"
            m = next((re.search(pat, line, re.I) for line in lines if re.search(pat, line, re.I)), None)
            if m:
                out[key] = " ".join(m.group(1).split()).strip(" ,.;")[:120]
                break
    return out


def _extract_quantity_revision(text: str) -> dict:
    """Resolve revised quantity before runtime decisions.

    "revise the first order quantity to 3,000 pcs instead of 5,000 pcs"
    means current=3000 and previous=5000. It is a revision, not a conflict.
    """
    m = re.search(
        r"\b(?:revise|revised|update|updated|change|changed|adjust|adjusted)"
        r"[^.\n]{0,80}?\b(?:quantity|order)[^.\n]{0,40}?\bto\s+"
        r"(\d[\d,]*)\s*(pcs|pieces|sets|units|pairs)?"
        r"[^.\n]{0,60}?\binstead\s+of\s+"
        r"(\d[\d,]*)\s*(pcs|pieces|sets|units|pairs)?",
        text or "", re.I)
    if not m:
        return {}
    current = int(m.group(1).replace(",", ""))
    previous = int(m.group(3).replace(",", ""))
    unit = (m.group(2) or m.group(4) or "pcs").lower()
    return {"current": current, "previous": previous, "unit": unit,
            "evolution": "REVISION", "conflict": False,
            "source": "quantity_revision_phrase"}


def _extract_labeled_product_phrase(text: str) -> str:
    """Fallback for labeled RFQ lines such as: Product: Insulated Food Container."""
    m = re.search(r"(?im)^\s*(?:product|item|project)\s*[:：-]\s*(.+?)\s*$", text or "")
    if not m:
        return ""
    raw = re.split(r"[,.;:\n]|\s+with\s+|\s+for\s+", m.group(1), maxsplit=1, flags=re.I)[0]
    return " ".join(raw.split()).strip(" ,.-")[:90]


def _extract_product_is_phrase(text: str) -> str:
    """Fallback for explicit wording such as: The product is Wireless ANC Earbuds."""
    m = re.search(
        r"\b(?:the\s+)?product\s+is\s+([A-Za-z0-9][A-Za-z0-9 /&+-]{2,80})",
        text or "", re.I)
    if not m:
        return ""
    raw = re.split(r"[,.;:\n]|\s+with\s+|\s+for\s+", m.group(1),
                   maxsplit=1, flags=re.I)[0]
    return " ".join(raw.split()).strip(" ,.-")


def _extract_need_product_phrase(text: str) -> str:
    """Fallback for wording such as: We need 1,000 pcs USB-C Travel Charger."""
    patterns = [
        r"\b(?:we\s+)?(?:need|want|require|looking\s+for)\s+\d[\d,]*\s*(?:pcs|pieces|sets|units|pairs)\s+(?:of\s+)?([A-Za-z0-9][A-Za-z0-9 /&+-]{2,80})",
        r"\b(?:we\s+)?(?:need|want|require|looking\s+for)\s+([A-Za-z0-9][A-Za-z0-9 /&+-]{2,80})\s*,?\s*\d[\d,]*\s*(?:pcs|pieces|sets|units|pairs)",
    ]
    for pat in patterns:
        m = re.search(pat, text or "", re.I)
        if not m:
            continue
        raw = re.split(r"[,.;:\n]|\s+with\s+|\s+for\s+|\s+please\s+",
                       m.group(1), maxsplit=1, flags=re.I)[0]
        phrase = " ".join(raw.split()).strip(" ,.-")
        phrase = re.sub(r"^(?:of|for)\s+", "", phrase, flags=re.I).strip()
        if phrase and not re.fullmatch(r"(?:quote|quotation|price|catalog|sample)s?", phrase, re.I):
            return phrase
    return ""


def print_report(index: int, text: str, report: dict):
    info, matches, lead = report["extracted"], report["matches"], report["lead"]

    print("=" * 66)
    print(f"【询盘 #{index}】")
    print("-" * 66)
    print(text.strip())
    print("-" * 66)

    print("① 提取到的客户信息：")
    qty = f"{info['quantity']:,} {info['quantity_unit']}" if info.get("quantity") else "未识别"
    price = info.get("target_price")
    urgency_cn = {"high": "高", "medium": "中", "low": "低"}.get(info.get("urgency"), "未判断")
    fields = [
        ("国家/地区", info.get("country") or "未识别"),
        ("公司名", info.get("company") or "未识别"),
        ("公司网址", info.get("website") or "未识别"),
        ("联系邮箱", info.get("email") or "未识别"),
        ("联系人", info.get("contact_name") or "未识别"),
        ("采购数量", qty),
        ("目标价", f"{info.get('target_price_currency') or 'USD'} {price}" if price else "未提及"),
        ("采购意图", info.get("intent") or "常规采购询盘"),
        ("紧急度", urgency_cn),
    ]
    for k, v in fields:
        print(f"   {k:<8}: {v}")
    if info.get("summary"):
        conf = info.get("confidence")
        extra = f"（置信度 {conf}）" if conf else ""
        print(f"   AI 摘要   : {info['summary']}{extra}")

    print("\n② 产品匹配结果：")
    if matches:
        for p in matches[:3]:
            reason = p.get("match_reason", "关键词命中")
            print(f"   {p['name']}（{p['name_cn']}） 匹配度 {int(p['match_score']*100)}%  "
                  f"${p['price_range'][0]:.2f}-{p['price_range'][1]:.2f}  MOQ {p['moq']:,}")
            print(f"      匹配依据：{p.get('match_basis', reason)}")
            if p.get("matched_conditions"):
                print(f"      ✅ 已满足：{', '.join(p['matched_conditions'])}")
            if p.get("unmatched_conditions"):
                print(f"      ❔ 未满足/待确认：{'；'.join(p['unmatched_conditions'])}")
            if p.get("missing_info"):
                print(f"      ⚠ 缺失信息：{'；'.join(p['missing_info'])}")
            print(f"      💡 推荐理由：{p.get('recommendation', '')}")
            if p.get("uncertainty"):
                for u in p["uncertainty"]:
                    print(f"      ❓ {u}")
    else:
        print("   当前产品库没有足够证据找到匹配产品（如实提示，不编造产品）。")
        print("   下一步建议：向客户索取产品图片、产品链接或参考型号，再判断产品库有无可替代品类。")

    print(f"\n③ AI 询盘评分：综合 {lead['score']} 分 -> {lead.get('lead_level') or lead['grade']}")
    print(f"   {lead.get('advice', '')}")
    if lead.get("urgency"):
        u_cn = {"high": "高", "medium": "中", "low": "低"}.get(lead["urgency"], "")
        print(f"   紧急度（独立字段）：{u_cn}")
    for d in lead.get("dims", []):
        filled = d["score"] // 10
        bar = "█" * filled + "░" * (10 - filled)
        print(f"   {d['name']:<8} {d['score']:>3} 分  {bar}")
        # 证据链：输入事实 → 判断 → 分数（第三轮优化八）
        for e in d.get("evidence", []):
            print(f"      · 事实[{e['fact']}] → 判断[{e['judgment']}] → 分数[{e['score']}]")
    if lead.get("order_value_basis"):
        print("   订单价值依据：")
        for line in lead["order_value_basis"]:
            print(f"      · {line}")
        print(f"   订单价值置信度：{lead.get('order_value_confidence', 'low')}")
    print(f"   权重复算：{lead.get('reasons', [''])[-1]}")

    # ③·6 业务洞察：报价准备度 + 阻塞原因 + 下一步动作
    insight = report.get("insight") or {}
    if insight:
        qr = insight.get("quotation_readiness") or {}
        status = qr.get("quotation_readiness_status", "")
        from agent.insight import (STATUS_CN, CERT_STATUS_CN, normalize_status,
                                   REQ_STATE_CN)
        status = normalize_status(status)
        print(f"\n③·6 报价准备度：{qr.get('quotation_readiness_score', '-')} 分 -> "
              f"{STATUS_CN.get(status, status)}")
        # 第五轮第二次补丁 03：产品需求层级 + 需求确认五态（旧记录无此字段时跳过）
        _hier = insight.get("product_hierarchy") or {}
        if _hier.get("levels"):
            print("   产品需求层级（Category→Type→Model→Spec→Final Selection）：")
            for _l in _hier["levels"]:
                _v = f" = {_l['value']}" if _l.get("value") else ""
                print(f"      · {_l['label']}: "
                      f"{REQ_STATE_CN.get(_l['state'], _l['state'])}{_v}")
        _sem = insight.get("requirement_semantics") or []
        if _sem:
            print("   需求确认状态（客户已提出 ≠ 已确认 ≠ 最终商业确认）：")
            for _it in _sem:
                _v = f"（{_it['value']}）" if _it.get("value") else ""
                print(f"      · {_it['label']}: {_it['state_cn']}{_v}")
        if qr.get("quote_summary"):
            print(f"   ▸ {qr['quote_summary']}")
        # 十项因素评分依据（输入事实 → 判断 → 分数）
        for line in qr.get("readiness_basis", []):
            print(f"   ▸ 依据：{line}")
        # A/B/C 确认项分级
        ci = qr.get("confirmation_items") or {}
        tier_icons = {"A": "🔴 A·必须确认", "B": "🟡 B·建议确认", "C": "🟢 C·可后续确认"}
        for tier in ("A", "B", "C"):
            if ci.get(tier):
                print(f"   {tier_icons[tier]}（{len(ci[tier])} 项）：")
                for it in ci[tier]:
                    st_txt = "未确认" if it["state"] == "unknown" else "部分确认"
                    print(f"      · {it['label']}（{st_txt}）：{it['reason']}")
        cert = insight.get("certification") or {}
        cstatus = cert.get("certification_status", "unknown")
        print(f"   认证判定：{CERT_STATUS_CN.get(cstatus, cstatus)}")
        # 部分确认字段：客户当前意向 + 销售建议
        for p in insight.get("partially_confirmed", []):
            print(f"   🟡 部分确认[{p['field']}] 客户当前意向：{' / '.join(p.get('customer_options', []))}"
                  f"（{p.get('status_text', '尚未最终确认')}）")
            if p.get("sales_advice"):
                print(f"      ↳ 销售建议：{p['sales_advice']}")
        # 硬阻塞（连初步报价都做不了）
        for b in qr.get("blockers_preliminary", []):
            print(f"   🔴 需先解决[{b['severity']}] {b['field']}: {b['reason']}")
        # 仅正式报价前需确认
        for b in qr.get("blockers_formal", []):
            print(f"   🟠 正式报价前确认[{b['severity']}] {b['field']}: {b['reason']}")
        if insight.get("product_matching_note"):
            print(f"   🔍 {insight['product_matching_note']}")
        for r in insight.get("risks", []):
            print(f"   ⚠ 风险[{r['severity']}] {r['reason']}")
        print("   下一步业务动作（结构化，按 P0 优先执行）：")
        for a in insight.get("next_actions", []):
            fld = f" 关联字段:{a['related_field']}" if a.get("related_field") else ""
            dl = f" 时限:{a['deadline']}" if a.get("deadline") else ""
            print(f"   {a.get('priority')} {a.get('action_cn') or a['action']} - {a['reason']}{fld}{dl}")

    # ⑤ 系统一致性检查结果（第三轮优化十二）
    consistency = report.get("consistency") or []
    if consistency:
        print("\n⑤ 系统一致性检查：")
        for c in consistency:
            mark = "✅" if c["ok"] else "❌"
            print(f"   {mark} {c['check']}：{c['detail']}")

    print("\n④ 首轮追问策略（第五轮优化）：")
    plan = report.get("reply_plan") or {}
    if plan:
        try:
            from agent.reply_strategy import report_plan
            print(f"   {report_plan(plan)}")
        except Exception:
            pass
        for q in plan.get("selected", []):
            print(f"   ▸ [{q['priority']}·{q['field']}] {q['question']}")

    print("\n④·2 回复草稿：")
    print("-" * 66)
    print(report["draft"] if report["draft"] else "（未匹配到产品，无法生成草稿）")
    if report.get("draft_issues"):
        print(f"   ⚠ 草稿校验警告（{len(report['draft_issues'])} 处，发送前请人工复核）：")
        for it in report["draft_issues"]:
            print(f"      - {it}")
    print()


def check_key():
    """检测 API Key 是否可用"""
    api_key, source = load_api_key()
    if not api_key:
        print(f"× 未找到 API Key（{source}）")
        print("  请把 Key 填进 config.json 的 DEEPSEEK_API_KEY，或设置环境变量后重试。")
        return 1
    print(f"已找到 Key（来源：{source}），正在连接 DeepSeek 测试...")
    try:
        model = load_config().get("MODEL", "deepseek-chat")
        reply = DeepSeekClient(api_key, model=model).check_key()
        print(f"√ 连接成功！模型 {model} 回复：{reply}")
        return 0
    except DeepSeekError as e:
        print(f"× 连接失败：{e}")
        return 1


def main():
    parser = argparse.ArgumentParser(description="AI 外贸询盘分析 Agent")
    parser.add_argument("inquiry_file", nargs="?",
                        help="待分析的询盘文本文件（不填则跑演示数据）")
    parser.add_argument("--mode", choices=["auto", "llm", "rule"], default="auto",
                        help="运行模式：auto=有Key就用LLM / llm=强制LLM / rule=离线规则")
    parser.add_argument("--check-key", action="store_true", help="只检测 API Key 是否可用")
    args = parser.parse_args()

    if args.check_key:
        sys.exit(check_key())

    extractor, matcher, client = build_engine(args.mode)

    if args.inquiry_file:
        with open(args.inquiry_file, encoding="utf-8") as f:
            inquiries = [f.read()]
    else:
        inquiries = DEMO_INQUIRIES

    all_reports = []
    for i, text in enumerate(inquiries, 1):
        report = analyze(text, extractor, matcher, client)
        print_report(i, text, report)
        all_reports.append(report)

    out_path = os.path.join(BASE_DIR, "output", "analysis_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, ensure_ascii=False, indent=2)
    print(f"完整分析结果已保存到: {out_path}")


if __name__ == "__main__":
    main()
