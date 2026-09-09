# -*- coding: utf-8 -*-
"""
模块④：回复草稿生成器 (ReplyGenerator)

两种模式：
1) LLM 模式（需要 API Key）：DeepSeek 结合你的公司资料 + 客户情况，
   直接写一封完整的、改改就能发的英文回复
2) 模板模式（兜底）：拼装标准外贸回复

LLM 出错时自动降级到模板模式，保证永远有草稿可用。
"""

import re

from agent.llm_client import load_config
from agent.reply_strategy import (
    build_question_plan, plan_to_prompt, never_ask_prompt,
    detect_explicit_requests, validate_reply_strategy, MAX_QUESTIONS,
    MATCH_MATCHED, MATCH_PARTIAL, MATCH_NONE, MATCH_INSUFFICIENT,
    MATCH_STATE_CN, _DEADLINE_RE, _to_num,
    INTENT_CATALOG, INTENT_SAMPLE,
)

SYSTEM_PROMPT = """You are a senior B2B foreign trade salesperson at a Chinese manufacturer.
Write a reply email to an overseas customer inquiry.

STYLE REQUIREMENTS:
- Professional, warm, concise, business-like. 120-180 words maximum.
- Short paragraphs, easy to scan on a phone.
- Natural human tone - do NOT sound like an AI, do NOT over-market.
- STRICTLY use only the facts provided in the input. Do NOT invent prices, costs, MOQ,
  lead times, stock, product specs, certificates, company qualifications, shipping cost,
  product links or model numbers. If data is not provided, say the team will confirm and
  revert - or leave it out entirely.

ANTI-HALLUCINATION RULE (ABSOLUTE):
- Only state facts that appear in SELLER PROFILE, MATCHED PRODUCT or CUSTOMER INFO.
- If the catalog has no matched product, do NOT mention any product name, price, MOQ,
  lead time or link. Ask for a photo / link / reference model instead.

CONTEXT ISOLATION RULE (ABSOLUTE - prevents cross-inquiry contamination):
- This reply is written for EXACTLY ONE inquiry: the one in CUSTOMER INFO / REPLY CONTEXT below.
- There is NO other context: no previous inquiry, no previous draft, no conversation history.
- If a fact (product, quantity, price, MOQ, lead time, certification) does not appear in
  REPLY CONTEXT, SELLER PROFILE, MATCHED PRODUCT or CUSTOMER INFO, it DOES NOT EXIST.
  Never reuse it, never "complete" it from memory.

UNKNOWN HANDLING RULE (ABSOLUTE):
- Fields marked unknown / not provided / unconfirmed must NOT be stated as facts.
- If the customer did not name a product: NEVER mention any product name, model, price,
  MOQ, lead time or certification. Ask which product/model they need instead.
- Never infer a product from the customer's industry, country or past inquiries.
- Never add a product name just to make the email look complete or professional.

FIRST-ROUND STRATEGY (ROUND 5 - ABSOLUTE, overrides everything else):
- The first reply is NOT an information-collection form. It must:
  (1) respond to what the buyer explicitly asked for (catalog / samples / certifications /
      price), (2) move the conversation forward, (3) ask ONLY the P0 questions listed in
      ASK THESE QUESTIONS.
- HARD LIMIT: at most 3 questions. When only one P0 blocker exists, ask exactly one.
  Default to 1-2. Every question must pass this test: "if the buyer does not answer it,
  can we really not take the next step?" If the answer is no, drop the question.
- NEVER ask in a first reply: payment terms, trade terms / Incoterm / FOB / CIF / EXW,
  target price / budget, packaging, the buyer's e-mail address, or which certification
  they need. These are P1/P2. If the buyer ASKS for certifications, OFFER our own list.
- NEVER re-ask what the buyer already told us (quantity, colour, logo/OEM, packaging,
  destination, urgency). If the quantity is approximate, CONFIRM it
  ("Is approximately 3,000 pcs your expected initial order quantity?") - never ask
  "How many pieces do you need?".
- Never invent time commitments ("within 24 hours", "by Friday") - no SLA data exists.
- If the buyer asked for a catalog, answer that request BEFORE any question.

QUOTE READINESS RULE:
- If QUOTE READINESS says information is insufficient, the reply must NOT read like a
  quotation. Thank the buyer, confirm exactly what they told you, and ask the key missing
  questions. No price, no MOQ, no lead time.
- If reference-quote stage, only the reference price range from MATCHED PRODUCT may be
  given, clearly marked as a reference, never as a formal quotation.

SOURCE LABEL RULE:
- Customer Fact / Company Data / Product Data may be stated directly.
- AI inference must never be written as a customer-confirmed fact.
- Unknown must never appear in the email as a fact.

FACT LAYER RULE (ROUND 9 - ABSOLUTE):
- Customer Target Price is the buyer's target, NEVER our quotation. Never write
  "our price is <customer target>" or similar.
- Approximate quantities (around / about) must keep the hedge word
  ("approximately 10,000 pcs"), never "your order of X pcs".
- Customer preferred delivery is a REQUEST: acknowledge it ("we have noted your
  preferred delivery timeline of 30 days and will check the applicable lead
  time"). NEVER write "we can deliver within 30 days" unless Product Data /
  Company Data explicitly supports that lead time.
- Use ONLY the facts in the FACT LAYER / REPLY CONTEXT. Unknown / Missing /
  AI-inferred values must NOT be stated as customer-confirmed facts.

EMAIL FACT GUARD (ROUND 11 - ABSOLUTE):
Every commercial fact in the email must come from CUSTOMER_FACT, COMPANY_FACT
(company knowledge base / product library / price list / policy) or SYSTEM_RULE.
If you cannot verify it, it is UNKNOWN - NEVER present it as a company fact.
NEVER auto-commit to any of the following unless the Company Knowledge Base
explicitly supports it: free samples, free molds/tooling, free design/artwork,
free certification, fixed price, MOQ, fixed lead time, stock availability,
production capacity, existing certification, payment terms, warranty policy,
shipping/transit time.
- Customer says "We need samples." -> write "We can arrange samples for
  evaluation and will confirm the sample cost and courier arrangement."
  NEVER "We can provide free samples."
- If a commitment cannot be verified, write a checking sentence instead:
  "Let me confirm this with our team and get back to you."

MINIMUM NECESSARY QUESTIONS RULE (MOST IMPORTANT - follow strictly):
- First-round reply asks AT MOST 2 to 3 core questions.
- Select questions DYNAMICALLY by priority based on THIS inquiry's context - never output
  a fixed question list:
  P0 (ask first, only if actually missing/open in this inquiry): product,
     specification (e.g. the open option the buyer left), quantity, certification
     requirement, destination, key deadline.
  P1 (only if P0 is fully covered AND it truly blocks quoting): payment terms,
     trade terms, target price.
  P2 (never in a first reply): other non-critical commercial info.
- NEVER ask for Payment Terms, Trade Terms, Target Price, or Email in a first reply,
  unless they truly block the current step. Do not let a missing ordinary field block
  the reply.
- NEVER ask the customer to repeat what they ALREADY told us. Acknowledge it and respond
  to it directly (quantity, color, logo, OEM, packaging, destination, deadline...).

SOLUTION-STYLE QUESTIONING RULE (upgrade from information-collecting):
- When the buyer left an option open (e.g. "Maybe 500ml or 750ml, not sure yet"), do NOT
  just ask "Which capacity would you prefer?".
- Instead HELP the buyer decide, e.g.: "We can offer both 500ml and 750ml options. Would
  you like us to quote both sizes for comparison, or do you already have a preferred
  capacity?" - echoing their OWN words, offering to quote both, and letting them choose.
- Principle: you are not only collecting information, you are helping the customer decide.

OFFER RULE:
- If the buyer ASKS what certificates we have (e.g. "what certificates do you have for
  Europe"), do NOT ask them back which certification they need as a hard question. Instead
  say you are happy to share our available certifications - then list only the ones that
  actually appear in the SELLER PROFILE or that we know we hold.
- Samples / catalog requests: offer them proactively, don't interrogate.

- No emoji.

JSON OUTPUT REQUIREMENTS (CRITICAL - non-compliance breaks the parser):
- Return ONLY a single JSON object. No markdown fences, no commentary, no leading/trailing text.
- Newlines inside string values MUST be written as \\n (two characters, not a real newline).
- Quotation marks inside string values MUST be written as \\".
- The "body" field contains the email body ONLY. Do not include the subject line inside body.
- If a value is missing, return null.

Return a JSON object with EXACTLY these keys:
{"subject": string, "body": string}"""

USER_TEMPLATE = """SELLER PROFILE:
{seller}

MATCHED PRODUCT (from our catalog):
{product}

CUSTOMER INFO EXTRACTED FROM THE INQUIRY:
{customer}

REPLY CONTEXT (the ONLY facts allowed in the email - this inquiry and nothing else):
{context}

QUOTE READINESS (from the inquiry analysis - the reply style MUST match it):
{readiness}

WHAT THE BUYER ALREADY TOLD US (never ask for these again):
{known}

ASK THESE QUESTIONS (P0 only, in this order, at most {qmax} - do NOT add any other question):
{unknown}

NEVER ASK IN THIS FIRST REPLY (P1/P2 - forbidden as questions):
{never}

CUSTOMER'S EXPLICIT REQUESTS (respond to these FIRST, before any question):
{explicit}

SOLUTION-STYLE HINTS (how to ask - help the buyer decide instead of just collecting info):
{hints}

WHAT WE CAN OFFER / SHARE WITHOUT A QUESTION (state these, do not interrogate the buyer):
{offer}

Write the reply. Return the JSON object."""


class ReplyGenerator:

    # ---------- 对外主方法 ----------

    def generate(self, info: dict, product, client=None, text: str = "",
                 insight: dict = None, catalog_products=None,
                 previous: dict = None, matches: list = None) -> str:
        """兼容旧调用：只返回草稿字符串（内部走 generate_validated）。"""
        draft, _ = self.generate_validated(info, product, client, text,
                                           insight=insight,
                                           catalog_products=catalog_products,
                                           previous=previous, matches=matches)
        return draft

    def generate_validated(self, info: dict, product, client=None, text: str = "",
                           insight: dict = None, catalog_products=None,
                           previous: dict = None, matches: list = None,
                           facts_ctx: dict = None):
        """生成回复草稿 + 双重校验，返回 (draft, issues)。

        第五轮优化十：草稿必须同时通过
          ① 事实一致性校验（validate_reply_draft：产品/数量/价格/MOQ 是否有依据）
          ② 首轮追问策略校验（validate_reply_strategy：14 项，问题数 ≤3、只问 P0、
             不重复问已知、不跨询盘污染、不虚构价格/MOQ/交期/认证、不创造时间承诺、
             Quote Readiness 与邮件一致、回应客户明确请求）
        校验失败：带着具体错误重新生成（最多 2 次，第二次收紧到 1 个问题）；
        仍失败则降级为受控模板草稿（模板同样按 P0 计划生成，不写信息采集表）。

        第五轮补丁：新增 matches 参数（来自 matcher.match），让产品库命中状态
        决定是否要 model/reference；若未传，则按 NO_MATCH 处理，绝不假定在库。

        第九轮：新增 facts_ctx 参数（agent.facts.build_reply_context 的产物）——
        邮件生成只读取事实层 allowed_facts；新增商业承诺校验
        （客户目标价≠公司报价、客户期望交期≠公司承诺）。
        """
        catalog_products = catalog_products or []
        # 第五轮补丁：把 matches 存到实例，下游 generate_with_llm / _template /
        # _known_unknown 都用 self._matches 取，签名不变。
        self._matches = matches or []
        self._facts_ctx = facts_ctx or {}
        if client is not None:
            draft = None
            issues = []
            for attempt in range(3):
                tighten = (attempt >= 1)      # 第一次失败后收紧：最多 1 个问题
                try:
                    draft = self.generate_with_llm(
                        info, product, client, text, insight=insight,
                        previous_issues=issues, tighten=tighten)
                except Exception as e:
                    print(f"   [警告] LLM 生成回复失败，已降级为模板草稿：{e}")
                    draft = None
                    break
                issues = self._validate_all(draft, text, info, product, insight,
                                            catalog_products, previous,
                                            matches=self._matches,
                                            facts_ctx=self._facts_ctx)
                if not issues:
                    return draft, []
                print(f"   [校验] 草稿有 {len(issues)} 处不合格，正在自动修正重生成"
                      f"（第 {attempt + 1} 次）…")
            if draft and not issues:
                return draft, []
        draft = self._template(info, product, text, insight=insight)
        return draft, self._validate_all(draft, text, info, product, insight,
                                         catalog_products, previous,
                                         matches=self._matches,
                                         facts_ctx=self._facts_ctx)

    @staticmethod
    def _validate_all(draft, text, info, product, insight,
                      catalog_products, previous=None,
                      matches=None, facts_ctx=None) -> list:
        """事实校验 + 追问策略校验 + 商业承诺校验（去重，保持顺序）。"""
        fact_issues = validate_reply_draft(draft, info, product, catalog_products)
        strategy_issues = validate_reply_strategy(
            draft, text, info, product, insight=insight,
            catalog_products=catalog_products, previous=previous)
        # 第九轮：商业承诺校验（交期/目标价/MOQ 承诺必须有公司或产品数据支撑）
        try:
            from agent.facts import validate_reply_commitments
            commit_issues = validate_reply_commitments(
                draft, text, info, product, matches=matches)
        except Exception:
            commit_issues = []
        # 本轮：Email Fact Guard（免费承诺/库存/产能/付款/保修/运输时效/
        # 认证已具备/MOQ/固定价格 —— 公司知识库无依据一律拦截）
        try:
            from agent.facts import validate_email_fact_guard
            guard_issues = validate_email_fact_guard(draft, product)
        except Exception:
            guard_issues = []
        return list(dict.fromkeys(
            fact_issues + strategy_issues + commit_issues + guard_issues))

    # ---------- LLM 模式 ----------

    def generate_with_llm(self, info: dict, product, client, text: str = "",
                          insight: dict = None, previous_issues=None,
                          tighten: bool = False) -> str:
        seller = load_config().get("SELLER", {})
        seller_text = (
            f"Company: {seller.get('company', 'our factory')}\n"
            f"Location: {seller.get('city', 'China')}\n"
            f"Sales representative: {seller.get('sales_name', 'Sales Team')}\n"
            f"Strength: {seller.get('strength', '')}"
        )
        if product:
            product_text = (
                f"Product: {product['name']} ({product['name_cn']})\n"
                f"Reference price: USD {product['price_range'][0]:.2f} - "
                f"{product['price_range'][1]:.2f} {product['unit']}\n"
                f"MOQ: {product['moq']:,} pcs\n"
                f"Lead time: {product['lead_time']}\n"
                f"HS Code: {product['hs_code']}"
            )
        else:
            product_text = (
                "No catalog product clearly matches this inquiry. "
                "Do NOT mention any specific product name, price, MOQ or lead time. "
                "Ask which product/model the customer needs."
            )
        qty = info.get("quantity")
        qty_str = (f"{qty:,} {info.get('quantity_unit') or 'pcs'}"
                   if qty else "quantity not specified yet")
        customer_text = (
            f"Contact: {info.get('contact_name') or 'unknown'}\n"
            f"Company: {info.get('company') or 'unknown'}\n"
            f"Country: {info.get('country') or 'unknown'}\n"
            f"Requested quantity: {qty_str}\n"
            f"Product wording used by customer: {info.get('product_query') or 'not explicit'}\n"
            f"Intent: {info.get('intent') or 'general'}\n"
            f"Urgency: {info.get('urgency') or 'unknown'}\n"
            f"Summary: {info.get('summary') or ''}"
        )
        # 已知信息 / 待确认（P0 计划，已限流）/ 方案式提示 / 可主动提供项
        plan = build_question_plan(text or "", info, product, insight,
                                    matches=self._matches)
        known, unknown, hints, offer = self._known_unknown(info, product, text,
                                                           insight, plan=plan)

        # Reply Context：草稿唯一允许引用的事实来源（第三阶段优化）
        context = self._reply_context(info, product)
        # 第九轮：事实层覆盖（Value/Source/Certainty）——客户目标价、期望交期、
        # 允许事实清单；邮件只能使用 allowed_facts，Unknown/Missing/AI推断
        # 不得写成客户已确认事实。
        facts_block = self._facts_prompt_block(getattr(self, "_facts_ctx", None))
        if facts_block:
            context = context + "\n\n" + facts_block
        readiness = self._readiness_instruction(insight)
        explicit = "、".join(plan.get("explicit_requests") or []) or "(none)"
        qmax = 1 if tighten else max(1, len(plan.get("selected") or []) or 1)
        extra_rules = ""
        if previous_issues:
            extra_rules = (
                "\nCORRECTION REQUIRED - your previous draft contained UNSUPPORTED claims:\n"
                + "\n".join(f"- {s}" for s in previous_issues)
                + "\nRewrite the email using ONLY the facts in REPLY CONTEXT / MATCHED "
                  "PRODUCT / CUSTOMER INFO. Remove every unsupported fact.")

        if tighten:
            extra_rules += (
                "\nSTRICTER MODE: your previous draft failed the first-round validation. "
                "Ask AT MOST 1 question (the single most blocking P0 item). Delete every "
                "other question, every P1/P2 topic and every invented commitment.")
        if qmax > MAX_QUESTIONS:
            qmax = MAX_QUESTIONS

        data = client.chat_json(
            SYSTEM_PROMPT,
            USER_TEMPLATE.format(seller=seller_text, product=product_text,
                                 customer=customer_text, known=known,
                                 unknown=unknown, hints=hints, offer=offer,
                                 context=context, readiness=readiness,
                                 qmax=qmax, never=never_ask_prompt(),
                                 explicit=explicit)
            + extra_rules,
            temperature=0.6,
        )
        subject = (data.get("subject") or "").strip()
        body = (data.get("body") or "").strip()
        if not body:
            raise ValueError("LLM 返回内容为空")
        if not subject.lower().startswith("subject"):
            subject = "Subject: " + subject
        # 去掉模型可能重复加上的 Subject 前缀
        subject = subject.replace("Subject: Subject:", "Subject:")
        return f"{subject}\n\n{body}"

    # ---------- Reply Context（第三阶段优化：草稿唯一事实来源） ----------

    @staticmethod
    def _facts_prompt_block(facts_ctx: dict) -> str:
        """第九轮：把事实层（Value/Source/Certainty）翻译成 Prompt 硬约束块。

        客户目标价 ≠ 公司报价；客户期望交期 ≠ 公司承诺；
        邮件只能使用 allowed_facts，Unknown/Missing/AI 推断不得当客户事实。
        """
        if not facts_ctx:
            return ""
        lines = ["FACT LAYER (Value / Source / Certainty - authoritative, "
                 "supersedes any conflicting wording):"]
        tp = facts_ctx.get("customer_target_price")
        if tp:
            lines.append(
                f"- Customer Target Price: {tp['value']} ({tp['source']}/"
                f"{tp['certainty']}) - the buyer's TARGET, never our quotation. "
                "Do NOT write it as our price / offer / quotation.")
        for pf in facts_ctx.get("preferred_facts") or []:
            lines.append(
                f"- {pf} - customer's REQUEST only. Acknowledge it (e.g. \"we have "
                "noted your preferred ...\"); NEVER turn it into a company "
                "commitment.")
        for af in facts_ctx.get("approximate_facts") or []:
            lines.append(f"- {af} - use \"approximately\"; NEVER state as a "
                         "confirmed order quantity.")
        allowed = facts_ctx.get("allowed_facts") or []
        if allowed:
            lines.append("ALLOWED FACTS (everything the email may state):\n"
                         + "\n".join(f"  * {a}" for a in allowed))
        lines.append(
            "FORBIDDEN COMMITMENTS (spec round 9): without Company Data / Product "
            "Data support you must NOT promise price, MOQ, lead time, delivery "
            "date, stock, certification, sample availability, production capacity, "
            "shipping cost, payment terms, warranty or customization capability. "
            "Customer requirement ≠ company commitment.")
        return "\n".join(lines)

    @staticmethod
    def _reply_context(info: dict, product) -> str:
        """从当前询盘构建 allowed_facts 清单：草稿只能引用这里列出的内容。"""
        lines = ["CONFIRMED FACTS (from THIS inquiry only):"]
        if info.get("quantity"):
            lines.append(f"- quantity: {info['quantity']:,} {info.get('quantity_unit') or 'pcs'}")
        else:
            lines.append("- quantity: UNKNOWN (do not state any quantity)")
        if info.get("contact_name"):
            lines.append(f"- contact name: {info['contact_name']}")
        if info.get("company"):
            lines.append(f"- company: {info['company']}")
        if info.get("country"):
            lines.append(f"- country/market: {info['country']}")
        if (info.get("product_query") or "").strip():
            lines.append(f"- product wording used by customer: {info['product_query']}")
        else:
            lines.append("- product: UNKNOWN (the customer did NOT name a product - "
                         "you must NOT mention any product name/model/price/MOQ/lead time)")
        if product:
            lines.append(f"- allowed product facts (Product Data, may be stated): "
                         f"{product['name']}, reference price USD "
                         f"{product['price_range'][0]:.2f}-{product['price_range'][1]:.2f} "
                         f"{product.get('unit', '')}, MOQ {product['moq']:,}, "
                         f"lead time {product.get('lead_time', 'n/a')}")
        else:
            lines.append("- MATCHED PRODUCT: none - any product name, price, MOQ, lead "
                         "time, certification or model number is FORBIDDEN in the email")
        lines.append("FORBIDDEN: data from any other inquiry, previous draft, previous "
                     "test, or anything the customer did not say and we cannot verify.")
        return "\n".join(lines)

    @staticmethod
    def _readiness_instruction(insight: dict) -> str:
        """把 Inquiry Analysis 的报价准备度翻译成草稿风格硬约束（第三阶段优化九）。"""
        status = ""
        try:
            from agent.insight import normalize_status
            status = normalize_status(
                (insight or {}).get("quotation_readiness", {})
                .get("quotation_readiness_status", ""))
        except Exception:
            status = (insight or {}).get("quotation_readiness", {}) \
                .get("quotation_readiness_status", "") or ""
        return {
            "insufficient_info":
                "NOT READY FOR QUOTATION. This is an information-gathering reply, NOT a "
                "quotation: thank the buyer, confirm exactly what they told you, and ask "
                "the key missing questions. Do NOT state any price, MOQ or lead time.",
            "preliminary_quote_ready":
                "REFERENCE QUOTE ONLY. You may share the reference price range from "
                "MATCHED PRODUCT, clearly marked as a reference; this is not a formal "
                "quotation.",
            "ready_for_quotation":
                "READY FOR QUOTATION. A formal quotation may be offered, using only the "
                "confirmed facts above.",
            "quoted":
                "Already quoted; this is a follow-up reply. Use only confirmed facts.",
        }.get(status,
              "Readiness unknown - stay conservative: no price, no MOQ, no lead time "
              "unless everything is confirmed above.")

    @staticmethod
    def _known_unknown(info: dict, product, text: str = "", insight: dict = None,
                       plan: dict = None):
        """从原文识别 已知/待确认/方案式提示/可主动提供 四组信息（第三轮优化四、五）。

        known  —— 客户已给的，绝不重复问
        unknown —— 第五轮：只输出 P0 提问计划里已限流的问题（≤3，默认 1-2）
        hints  —— 方案式追问写法提示（帮客户做决定，不是单纯索取信息）
        offer  —— 不用问、可直接在信里提供的（认证清单 / 样品 / 目录等）
        """
        low = (text or "").lower()
        known, unknown, hints, offer = [], [], [], []
        if info.get("quantity"):
            known.append(f"quantity: {info['quantity']:,} {info.get('quantity_unit') or 'pcs'}")
        if info.get("country"):
            known.append(f"country/market: {info['country']}")
        if info.get("company"):
            known.append(f"company: {info['company']}")
        if re.search(r"\b(custom\w*|logo|oem|odm|private\s+label)\b", low):
            known.append("customization / OEM: required")
        if re.search(r"\b(black|white|blue|red|green|pink|grey|gray|navy)\b", low):
            known.append("color preference stated (see inquiry)")
        if re.search(r"\b(packag\w*|premium|gift\s?box|blister)\b", low):
            known.append("packaging requirement stated (see inquiry)")
        if re.search(r"\b(urgent|asap|as\s+soon\s+as\s+possible|within\s+\d+\s+(day|week))\b", low):
            known.append("deadline/urgency stated (see inquiry)")

        # 客户给了候选但未定（500ml or 750ml）→ 已知信息 + 方案式追问的核心素材
        from agent.insight import find_open_options
        frags = find_open_options(text or "")
        if frags:
            known.append(f'buyer left an option open: "{frags[0]}"')
            hints.append(f'For "{frags[0]}": offer to quote BOTH options side by side for '
                         'comparison, echo the buyer\'s own words, and let them choose '
                         '(solution-style, not plain information-collecting).')

        # ---- 第五轮：待确认项只来自 P0 提问计划（已按 1-2 个限流，上限 3） ----
        # 第五轮补丁：必须传 matches，否则一律走 NO_MATCH。
        plan = plan or build_question_plan(text or "", info, product, insight,
                                           matches=getattr(self, "_matches", None))
        if plan.get("selected"):
            unknown = plan_to_prompt(plan)
        else:
            unknown = ("(no P0 blocker - do NOT ask any question; respond to the buyer's "
                       "request and state the next step we will take)")

        # 认证 requested/interested → 确认式表述（Fact Guard：认证是否具备
        # 由公司知识库决定，模板不得自动声称已具备）
        if re.search(r"\b(what|which|do\s+you\s+have|any)\s+certificat\w*", low) or \
           re.search(r"\bcertificat\w*", low):
            offer.append("buyer asked about certifications: say we can check and share the "
                         "certification documents applicable to their market once the "
                         "product/model is confirmed - do NOT claim we already hold any "
                         "certificate, and do not ask them back "
                         "which certification is mandatory")

        if re.search(r"\bsample[s]?\b", low):
            # Fact Guard：样品是否免费/费用与寄送安排由公司政策决定，禁止自动承诺
            offer.append("buyer asked about samples: say we can arrange samples for "
                         "evaluation and will confirm the sample cost and courier "
                         "arrangement - NEVER claim samples are free")
        if re.search(r"\b(catalog|catalogue|price\s+list)\b", low):
            # 第五轮第三次优化：意图为 Catalog/Sample 请求时，必须先响应请求本身，
            # 不得要求客户先给型号/图片/数量
            if (plan.get("intent") or "") in (INTENT_CATALOG, INTENT_SAMPLE):
                offer.append("buyer asked for catalog / price list: respond to the "
                             "catalog request FIRST - offer to prepare/share the catalog "
                             "and invite them to indicate the interested product "
                             "category; do NOT demand model/photo/quantity before "
                             "providing it; never claim it is attached")
            else:
                offer.append("buyer asked for catalog / price list: offer to send our catalog and "
                             "price range once the item is confirmed - do not quote blindly")

        return ("\n".join(known) or "(nothing explicit)",
                unknown,
                "\n".join(hints) or "(no open option - quote the confirmed spec directly)",
                "\n".join(offer) or "(nothing to offer)")

    # ---------- 模板模式（兜底） ----------

    def _template(self, info: dict, product, text: str = "",
                  insight: dict = None) -> str:
        """受控模板草稿（第五轮重写）：先回应客户明确提出的需求，再问 P0（≤3）。

        结构：感谢 → 回应 Catalog / 样品 / 认证等明确请求 → 已确认产品的产品库事实
              → P0 问题（默认 1-2 个）→ 下一步动作承诺 → 落款
        绝不做的事：一次性问 7 个字段、重复问已知信息、问邮箱/付款/贸易术语/包装、
                   虚构价格/MOQ/交期/认证、承诺没有 SLA 支撑的时间。

        第五轮补丁 02：进一步收紧——
          - hedge 数量（may/might/could/approximately/trial/initial 等）不得用
            "your order of X pcs" 表述
          - 样品请求不得默认承诺 "we will send samples"（无库存/流程依据）
          - 连产品类别都没有时（INSUFFICIENT），只问 Product Category / Product Type，
            不得问 model
          - Catalog 不得虚构"已发送"（系统无附件）
        """
        # 称呼回退链：联系人 > 公司 > there（第三轮：避免对已知公司客户用 "Dear there"）
        name = info.get("contact_name") or info.get("company") or "there"
        qty = info.get("quantity")
        qty_str = (f"{qty:,} {info.get('quantity_unit') or 'pcs'}"
                   if qty else "your requested quantity")
        seller = load_config().get("SELLER", {})
        low = (text or "").lower()
        # 第五轮补丁：传 matches，让产品库命中状态决定是否要 model/reference
        plan = build_question_plan(text or "", info, product, insight,
                                    matches=getattr(self, "_matches", None))
        requests = plan.get("explicit_requests") or []
        match_state = plan.get("match_state") or ""
        # 第五轮补丁 02：hedge 数量标记 → Subject 不用"X pcs"已确认表述
        hedge_present = bool(plan.get("hedge_present"))
        # 多数量语义补丁：客户明确要求报价的数量（如 "best price for 2,000 pcs"）
        quotation_qty = plan.get("quotation_quantity")
        # 公司事实防幻觉：direct manufacturer / factory 等身份表述
        # 必须来自 Company Data（config.json SELLER.company_type），未配置不得声称
        company_type = str(seller.get("company_type") or "").strip()
        # 第五轮第三次优化：意图驱动的草稿分支（CATALOG/SAMPLE 请求优先响应）
        intent = plan.get("intent") or ""

        # Subject：hedge 时用 "Initial X pcs"（避免让客户以为我们把模糊量当 confirmed）；
        # 若主量正是客户明确要求报价的数量（语义已确认），用平铺表述
        if hedge_present and qty:
            if quotation_qty and _to_num(qty) == quotation_qty:
                qty_subject = f"{qty:,} {info.get('quantity_unit') or 'pcs'}"
            else:
                qty_subject = f"Initial {qty:,} {info.get('quantity_unit') or 'pcs'}"
        else:
            qty_subject = qty_str
        if intent == INTENT_CATALOG and not product:
            # 第五轮第三次优化：Catalog 请求的 Subject 直接点题（不出现臆造数量）
            subject = (f"Re: Product Catalog & Specifications | "
                       f"{seller.get('company', 'us')}")
        elif product:
            subject = (f"Re: {product['name']} - {qty_subject} | "
                       f"Quotation from {company_type or seller.get('company', 'us')}")
        else:
            subject = (f"Re: Your inquiry - {qty_subject} | "
                       f"{seller.get('company', 'Manufacturer')}")

        blocks = []

        # 1) 开场：感谢 + 一句公司身份（不吹嘘、不编资质；身份词来自 Company Data）
        if company_type:
            blocks.append(
                f"Thank you for your inquiry. We are a {company_type} based in "
                f"{seller.get('city', 'China')}"
                f"{', and this item is within our main range' if product else ''}.")
        else:
            blocks.append(
                f"Thank you for your inquiry. This is {seller.get('company', 'us')}"
                f"{', and the item you described is of interest to us' if product else ''}.")

        # 2) 优先回应客户明确提出的需求（Catalog / 样品 / 认证 / 报价）
        if "catalog" in requests:
            # 第五轮补丁 02：不得说"已发送 / attached"，只说"愿意发"
            blocks.append("We would be happy to share our latest catalog and product "
                          "information with you.")
        if "samples" in requests:
            # 第五轮补丁 02：不得默认承诺 we will send samples
            blocks.append("Once the relevant product is selected, our team can advise "
                          "on sample availability and the quickest way to send them to "
                          "you.")
        if "certifications" in requests:
            # 公司事实防幻觉 + Fact Guard：认证清单必须来自 Company Data（config.json
            # SELLER.certifications）；有配置 → 如实列出（COMPANY_FACT）；
            # 未配置 → 只说"确认后提供文件"，不得声称已具备任何认证
            certs = seller.get("certifications") or []
            if certs:
                blocks.append("Regarding certifications, our certifications include "
                              f"{' & '.join(str(c) for c in certs)} - we can share the "
                              "certificate copies for your reference.")
            else:
                blocks.append("Regarding certifications, we will confirm which "
                              "certifications apply to your market and share the "
                              "documents accordingly.")

        # 2.5) 多数量语义补丁：确认当前报价参考数量（客户已说明语义 → 不再追问；
        #      也不重新询问试单/潜在量——那些语义已从上下文判断清楚）
        if quotation_qty and match_state != MATCH_MATCHED:
            blocks.append(f"We have noted {quotation_qty:,} pcs as the reference "
                          "quantity for this quotation.")

        # 2.6) 多数量语义补丁：客户给了明确交期 → 自然提及但不承诺满足
        #      （只复述客户原话的交期，不出现 "we will deliver within X" 类承诺）
        #      第九轮：改用事实层交期识别——"Delivery should be around 30 days."
        #      这类不带 within/in/by 的表达此前会漏识别（spec 四）
        deliv_pref = None
        try:
            from agent.facts import detect_delivery_preference
            deliv_pref = detect_delivery_preference(text or "")
        except Exception:
            deliv_pref = None
        if deliv_pref and match_state in (MATCH_INSUFFICIENT, MATCH_NONE):
            blocks.append(f"We have noted your {'preferred ' if deliv_pref['certainty'] == 'Preferred' else 'requested '}delivery timeline of "
                          f"{deliv_pref['value']} and will confirm feasibility once the "
                          "item is confirmed.")
        elif dm := _DEADLINE_RE.search(low):
            if match_state in (MATCH_INSUFFICIENT, MATCH_NONE):
                blocks.append(f"We have noted your requested delivery timeline of "
                              f"{dm.group(1)} and will confirm feasibility once the "
                              "item is confirmed.")

        # 3) 产品事实：按 match_state 分支（第五轮补丁：先查库，再问人）
        #    MATCHED          → 已有确认产品，正常展示产品库事实
        #    PARTIAL_MATCH    → 有候选产品，列候选让客户挑，不要求 reference
        #    NO_MATCH         → 产品库无匹配，如实说明 + 只问 reference（不再问"哪个型号"）
        #    INSUFFICIENT     → 连产品方向都没有，才问 product/category
        candidates = plan.get("candidates") or []
        if match_state == MATCH_MATCHED and product:
            blocks.append(
                f"Quick overview for {qty_str}:\n"
                f"- Product: {product['name']}\n"
                f"- Reference price: USD {product['price_range'][0]:.2f} - "
                f"{product['price_range'][1]:.2f} {product['unit']} "
                f"(final price depends on specs & qty)\n"
                f"- MOQ: {product['moq']:,} pcs\n"
                f"- Lead time: {product['lead_time']} after order confirmation")
        elif match_state == MATCH_PARTIAL and candidates:
            # 用候选产品代替"已确认产品"，绝不报具体价格（候选≠确认）
            # 注意：块内不含问号 —— 真正的问句由 plan.get("selected") 提供
            names = " / ".join(candidates[:3])
            blocks.append(f"Based on your description, the closest options in our "
                          f"catalog are {names}. We can prepare specifications "
                          f"and pricing for whichever you prefer - please let us "
                          f"know which one to quote.")
        elif match_state == MATCH_PARTIAL:
            blocks.append("We have a few options in our range that could match your "
                          "requirement - happy to share the closest ones once you confirm "
                          "the item.")
        elif match_state == MATCH_NONE:
            # 产品库确实没有 → 不虚构；只问 reference/photo/link
            # 第五轮第三次优化：Catalog/Sample 意图下绝不把 reference 设为前置条件
            if intent in (INTENT_CATALOG, INTENT_SAMPLE):
                blocks.append("We will prepare our latest catalog covering this range "
                              "for your review, so you can see the full options first.")
            elif "catalog" in requests:
                blocks.append("To make sure we send the right catalog, we would "
                              "appreciate it if you could share a reference model, "
                              "a photo or a link to the item. Our team will then "
                              "check whether we can supply an equivalent or "
                              "recommend the closest option for you.")
            else:
                blocks.append("Our current catalog focuses on swim gear (caps, "
                              "goggles, towels, etc.) so we do not have a direct "
                              "match for the item you described. Sharing a "
                              "reference model, a photo or a link will help us "
                              "check the closest option for you.")
        elif match_state == MATCH_INSUFFICIENT:
            # 第五轮补丁 02：连 Product Category / Product Type 都没有
            # → 块里只确认"我们需要先知道产品类别"，不要求 reference model
            #   （model 是更细的层级，无 category 时谈 model 没有意义）
            # 块内依然不含问号 —— 真问句由 plan.get("selected") 提供
            # 第五轮第三次优化：Catalog/Sample 意图下跳过该块——
            # "先确认品类才能报价"的口径与"先响应请求"矛盾，问句由 P1 类别问题承担
            if intent not in (INTENT_CATALOG, INTENT_SAMPLE):
                blocks.append("We'd like to confirm the right product category first, "
                              "so we can match the closest items from our catalog and "
                              "advise on applicable pricing, MOQ and lead time.")
        else:
            # 兜底：旧逻辑（未知 match_state 时仍按"无确认产品"处理）
            if product:
                blocks.append(
                    f"Quick overview for {qty_str}:\n"
                    f"- Product: {product['name']}\n"
                    f"- Reference price: USD {product['price_range'][0]:.2f} - "
                    f"{product['price_range'][1]:.2f} {product['unit']} "
                    f"(final price depends on specs & qty)\n"
                    f"- MOQ: {product['moq']:,} pcs\n"
                    f"- Lead time: {product['lead_time']} after order confirmation")
            else:
                blocks.append("We reviewed your request against our current range and "
                              "want to quote you accurately, so we will not send a blind "
                              "price.")

        # 4) 只问 P0（计划已限流：默认 1-2 个，绝对不超过 3 个）
        for q in plan.get("selected") or []:
            blocks.append(q["question"])

        # 5) 下一步动作承诺（条件式，不承诺没有依据的时间）
        if plan.get("selected"):
            blocks.append("Once we have this information, we can check the relevant product "
                          "options and advise you on pricing, MOQ and lead time.")
        else:
            blocks.append("We will prepare the relevant pricing and options for you and "
                          "come back to you shortly.")

        body = f"Dear {name},\n\n" + "\n\n".join(blocks) + \
               f"\n\nBest regards,\n{seller.get('sales_name', 'Sales Team')}\n" \
               f"{seller.get('company', '')}"
        return f"Subject: {subject}\n\n{body}"


# ===================== 草稿事实校验（第三阶段优化六） =====================

_QTY_RE = re.compile(r"(\d[\d,]*)\s*(?:pcs|pieces|units|pairs|sets)\b", re.I)
_PRICE_RE = re.compile(r"(?:USD\s*|\$)(\d[\d,]*(?:\.\d+)?)", re.I)


def _to_num(s: str):
    try:
        return int(round(float(str(s).replace(",", ""))))
    except Exception:
        return None


def validate_reply_draft(draft: str, info: dict, product,
                         catalog_products=None) -> list:
    """轻量校验：草稿里的事实必须来自 当前询盘 / 匹配产品 / 卖家资料。

    返回问题列表（空列表 = 通过）。检查维度：
      1. 数量：邮件中的 pcs 类数字必须是当前询盘数量或匹配产品 MOQ
      2. 产品：无确认产品时，禁止出现产品库任何产品名/关键词
      3. 价格：无匹配产品禁止出现任何价格；有匹配产品时价格必须落在其区间
      4. MOQ：无匹配产品禁止提及 MOQ
    """
    issues = []
    if not draft or not draft.strip():
        return ["草稿为空"]
    low = draft.lower()

    # 1) 数量一致性（阻止 "3,000 pcs" 这类串台数据）
    # 多数量语义补丁：当前询盘中各语义数量（试单/报价/潜在/年度）都是合法数字
    allowed_qty = set()
    if info.get("quantity"):
        allowed_qty.add(int(info["quantity"]))
    for s in (info.get("quantity_semantics") or []):
        if s.get("value"):
            allowed_qty.add(int(s["value"]))
    if product and product.get("moq"):
        allowed_qty.add(int(product["moq"]))
    if allowed_qty:
        for m in _QTY_RE.finditer(draft):
            n = _to_num(m.group(1))
            if n is not None and n not in allowed_qty:
                issues.append(f"邮件出现数量 {m.group(0)}，不是当前询盘数量也不是匹配产品 MOQ")
    # 数量是纯数字（无 pcs 后缀）时也检查一次，如 "for 3,000 pieces" 会被上面抓到，
    # 但 "quantity of 3000" 这类写法靠下面兜底
    for m in re.finditer(r"\b(\d[\d,]{2,})\b", draft):
        n = _to_num(m.group(1))
        if n is None or not info.get("quantity"):
            continue
        if n == int(info["quantity"]) or (product and n == int(product.get("moq", 0))):
            continue
        # 排除日期 / 金额 / 编号场景（带 $ 或 USD 前缀、或位于 MOQ 后）由其他规则处理
        ctx = draft[max(0, m.start() - 8): m.end() + 8].lower()
        if re.search(r"(usd|\$|20\d\d|hs\s*code)", ctx):
            continue
        if n >= 100 and re.search(r"(quantity|qty|pcs|pieces|units)", ctx):
            issues.append(f"邮件出现可疑数量 {m.group(1)}，与当前询盘数量不一致")

    # 2) 无确认产品时，禁止出现产品库产品名
    if not product and not (info.get("product_query") or "").strip():
        for p in (catalog_products or []):
            for name in (p.get("name"), p.get("name_cn")):
                if name and len(name) >= 5 and name.lower() in low:
                    issues.append(f"邮件出现未确认产品「{name}」（客户未指明产品）")
                    break

    # 3) 价格必须来自匹配产品（用浮点比较：单价低于 1 美元时不能按整数截断）
    prices = []
    for m in _PRICE_RE.finditer(draft):
        try:
            prices.append(float(str(m.group(1)).replace(",", "")))
        except Exception:
            pass
    if prices:
        if not product:
            issues.append("未匹配到产品，邮件却出现了价格（禁止虚构报价）")
        else:
            lo = float((product.get("price_range") or [0, 0])[0])
            hi = float((product.get("price_range") or [0, 0])[1])
            for pr in prices:
                if hi > 0 and not (lo * 0.8 <= pr <= hi * 1.25):
                    issues.append(f"邮件价格 {pr} 超出匹配产品价格区间（USD {lo}-{hi}）")

    # 4) MOQ 只能来自匹配产品。
    #    说明：像 "advise you on pricing, MOQ and lead time" 这种"后续会告诉你"的表述
    #    是允许的（第五轮：那是下一步动作，不是虚构事实）；只有给出具体数字才算虚构。
    if not product and re.search(r"\bmoq\b[^.\n]{0,20}\d|\bmoq\s*(?:is|:|=)", low):
        issues.append("未匹配到产品，邮件却给出了具体 MOQ 数字（禁止虚构）")

    return issues
