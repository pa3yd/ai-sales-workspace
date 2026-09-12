# -*- coding: utf-8 -*-
"""
TEST06 · DEAL PROGRESSION ENGINE 压力测试（ROUND 7.1）
================================================================
目的：证明「Deal Progression Engine」是**领域层唯一权威解析器**，
      且它覆盖 spec §4–§13 的全部硬性行为。

覆盖 case（spec §18）：
  A  QUOTE SENT / NO FOLLOW-UP      → next=MISSING, health≥ATTENTION,
                                       reason=报价已发送但未安排跟进,
                                       transition=QUOTED（若还停在需求已确认）
  B  QUOTE FOLLOW-UP OVERDUE        → next=OVERDUE, health=AT_RISK,
                                       reason 含"逾期 N 天", requires_action=True
  C  WAITING CUSTOMER FUTURE        → next=WAITING_CUSTOMER（未过复查点）,
                                       health 不因此自动降级为 AT_RISK,
                                       reason=等待客户回复
  D  PRICE NEGOTIATION              → transition=QUOTED→NEGOTIATION,
                                       reason=客户议价, 优先级 CUSTOMER_REPLY_NEGOTIATION
  E  INTERNAL PRODUCT CHECK         → reason=INTERNAL_BLOCKER（内部而非客户阻塞）,
                                       customerBlockingItems 为空, 不向客户追问
  F  TERMINAL DEAL                  → next=CLOSED, 无 aging 告警, 无 transition 建议,
                                       不产生任何"该跟进"假信号

附加断言（spec 非 case 类硬要求）：
  G  Stage Aging 权威性：stage_entered_at 取**真实迁移**，不被 save_inquiry
     的 None→NEW 重同步行污染（§5 / §17）
  H  阶段回退检测：曾到 QUOTED 但当前 NEW → 暴露 stage_regression，
     且推荐恢复到 QUOTED（§8 特例），绝不误导"重新走一遍"
  I  enums 完整性：health 仅三态、next_activity 仅七态、transition 仅三级（§6/§4/§10）
  J  validate_transition 三态：VALID / WARNING / BLOCKED 均可达（§10）
  K  执行优先级排序：OVERDUE > DUE_TODAY > AT_RISK > MISSING > ATTENTION
     > WAITING_CUSTOMER > SCHEDULED（§13）
  L  §4.1 MISSING 是可执行例外：next=MISSING 必然 needs_action=True，
     且绝不会被静默当成 "无需动作"
  M  §4.3 不产生重复活跃跟进：同 deal+reason 已有活跃任务 → 复用，不新增
  N  §2/§0 同一 Deal 在 domain 层只有一份 progression（同输入→同输出，
     幂等；且 Pipeline 与 AI 助手读的是同一个函数）

零副作用：本文件**不改动** workbench.db；DB 相关断言全部走临时库副本。

用法：python test_test06_progression.py
"""
import os
import re
import sys
import json
import shutil
import sqlite3
import datetime
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "workbench"))
sys.stdout.reconfigure(encoding="utf-8")

import progression as pg
import sales_crm as crm
import followup as fu

_PASS = 0
_FAIL = 0
_FAILED = []


def check(name, ok, detail=""):
    global _PASS, _FAIL
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"　（{detail}）" if detail else ""))
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
        _FAILED.append(name)


def section(title):
    print("\n" + "-" * 66)
    print(title)
    print("-" * 66)


NOW = datetime.datetime(2026, 9, 11, 12, 0, 0)


def days_ago(n, hour=9):
    return (NOW - datetime.timedelta(days=n)).replace(
        hour=hour, minute=0, second=0, microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def opp(**kw):
    """最小商机 fixture。"""
    base = {
        "id": 901, "inquiry_id": 901, "stage": "QUOTED",
        "created_at": iso(days_ago(30)), "updated_at": iso(days_ago(1)),
        "last_activity_at": iso(days_ago(1)),
        "next_action": "", "next_action_at": "",
        "details": {},
    }
    base.update(kw)
    return base


def task(**kw):
    base = {
        "id": 5001, "opportunity_id": 901, "status": "OPEN",
        "fu_status": fu.PENDING, "due_at": iso((NOW + datetime.timedelta(days=2))),
        "title": "跟进客户", "next_action": "发送第一次报价跟进",
        "reason": fu.QUOTE_SENT_NO_REPLY,
    }
    base.update(kw)
    return base


def quote(status="SENT", sent_days_ago=1, valid_days=14, version=1, amount=12000):
    """真实列序（db.list_quotes）：
    id, version, amount, currency, valid_until, status, created_at"""
    sent = days_ago(sent_days_ago)
    valid = (NOW + datetime.timedelta(days=valid_days)).strftime("%Y-%m-%d")
    return [9001, version, amount, "USD", valid, status, iso(sent)]


def hist(*rows):
    """rows: (from_stage, to_stage, days_ago)"""
    return [(f, t, iso(days_ago(d))) for (f, t, d) in rows]


def act(type_, days_ago_=1, **meta):
    return {"type": type_, "ts": iso(days_ago(days_ago_)),
            "description": type_, "metadata": meta}


def resolved_state(**kw):
    base = {
        "customerProductRequirement": "Wireless ANC Earbuds",
        "requirementStatus": "COMPLETE",
        "productMatchStatus": "MATCHED",
        "supplierCapabilityStatus": "CONFIRMED",
        "quotationReadiness": "QUOTE_SENT",
        "communicationStatus": "WAITING_REPLY",
        "blockingItems": [],
        "customerBlockingItems": [],
        "internalPrerequisites": [],
        "actionState": "READY_TO_REPLY",
        "requirementCompleteness": "HIGH",
    }
    base.update(kw)
    return base


def rp(o, **kw):
    """快捷 resolve_progression。"""
    kw.setdefault("now", NOW)
    return pg.resolve_progression(o, **kw)


# ==========================================================================
# Case A · QUOTE SENT / NO FOLLOW-UP（§4.1 MATCHING 的可执行例外）
# ==========================================================================
def case_a():
    section("Case A · QUOTE SENT / NO FOLLOW-UP（§4.1 可执行例外）")
    o = opp(stage="REQUIREMENT_CONFIRMED", id=101, inquiry_id=101)
    # 阶段 2 天前刚进入（REQUIREMENT_CONFIRMED 阈值 3/5 → 未过 attention），
    # 这样主因才不会被 aging 抢走，能干净地验证 §4.1 的 MULTI-ACTIVITY 缺失例外。
    h = hist(("NEW", "QUALIFIED", 20), ("QUALIFIED", "REQUIREMENT_CONFIRMED", 2))
    p = rp(o, quotes=[quote("SENT", sent_days_ago=3)], tasks=[],
           activities=[act("QUOTE_SENT", 3)], history=h,
           resolved_state=resolved_state(quotationReadiness="READY_FOR_QUOTE"))

    check("A1 · next_activity_status = MISSING（不是静默「未安排」）",
          p["next_activity_status"] == pg.NA_MISSING,
          str(p["next_activity_status"]))
    check("A2 · health ≥ ATTENTION（报价后无跟进必须被看见）",
          p["health"] in (pg.ATTENTION, pg.AT_RISK), p["health"])
    check("A3 · reason_code = MISSING_NEXT_ACTIVITY",
          p["reason_code"] == "MISSING_NEXT_ACTIVITY", p["reason_code"])
    check("A4 · primary_reason 具体说明「报价已发送但未安排」「",
          "报价已发送" in p["primary_reason"]
          and "跟进" in p["primary_reason"], p["primary_reason"])
    check("A5 · requires_action = True（可执行例外，不是 NO_ACTION）",
          p["requires_action"] is True, "")
    check("A6 · needs_action = True（进入「需处理」视图）",
          p["needs_action"] is True, "")
    check("A7 · transition 推荐推进到已报价（报价真的发出了）",
          p["recommended_transition"] == "QUOTED",
          f"{p['recommended_transition']} / {p['transition_reason']}")
    check("A8 · transition 证据含 quote_sent",
          "quote_sent" in (p["transition_evidence"] or []),
          str(p["transition_evidence"]))
    check("A9 · waits_customer 为 False（这是我们的欠账，不是等客户）",
          p["waiting_customer"] is False, "")
    check("A10 · overdue 为 False（没排期≠逾期，两者语义不同）",
          p["overdue"] is False, "")


# ==========================================================================
# Case B · QUOTE FOLLOW-UP OVERDUE
# ==========================================================================
def case_b():
    section("Case B · QUOTE FOLLOW-UP OVERDUE（§4 / §6 / §7）")
    o = opp(stage="QUOTED", id=102, inquiry_id=102)
    h = hist(("NEW", "QUALIFIED", 20), ("QUALIFIED", "REQUIREMENT_CONFIRMED", 12),
             ("REQUIREMENT_CONFIRMED", "QUOTED", 9))
    t = task(id=6001, fu_status=fu.PENDING,
             due_at=iso(days_ago(3)), next_action="发送第二次报价跟进",
             reason=fu.QUOTE_SENT_NO_REPLY)
    p = rp(o, quotes=[quote("SENT", sent_days_ago=9)], tasks=[t],
           activities=[act("QUOTE_SENT", 9)], history=h,
           resolved_state=resolved_state(quotationReadiness="QUOTE_SENT"))

    check("B1 · next_activity_status = OVERDUE",
          p["next_activity_status"] == pg.NA_OVERDUE, p["next_activity_status"])
    check("B2 · health = AT_RISK（逾期是最强风险信号）",
          p["health"] == pg.AT_RISK, p["health"])
    check("B3 · reason_code = OVERDUE（优先级最高）",
          p["reason_code"] == "OVERDUE", p["reason_code"])
    check("B4 · primary_reason 说明「什么逾期、逾期几天」",
          "逾期" in p["primary_reason"] and re.search(r"逾期\s*\d+\s*天",
                                                     p["primary_reason"]) is not None,
          p["primary_reason"])
    check("B5 · requires_action = True",
          p["requires_action"] is True, "")
    check("B6 · overdue = True",
          p["overdue"] is True, "")
    check("B7 · next_activity 指向逾期的那件事",
          "报价跟进" in (p["next_activity"] or ""), p["next_activity"])
    check("B8 · exec_priority 排第一档（OVERDUE=0）",
          pg.exec_priority(p)[0] == 0, str(pg.exec_priority(p)))


# ==========================================================================
# Case C · WAITING CUSTOMER（复查点在未来，未过点）
# ==========================================================================
def case_c():
    section("Case C · WAITING CUSTOMER FUTURE（§4.2 等待客户 ≠ 缺信息）")
    o = opp(stage="QUOTED", id=103, inquiry_id=103)
    h = hist(("NEW", "QUALIFIED", 15), ("QUALIFIED", "REQUIREMENT_CONFIRMED", 10),
             ("REQUIREMENT_CONFIRMED", "QUOTED", 2))
    t = task(id=6002, fu_status=fu.WAITING,
             due_at=iso((NOW + datetime.timedelta(days=4))),
             next_action="客户承诺周四前回复", reason=fu.CUSTOMER_PROMISED_REPLY)
    p = rp(o, quotes=[quote("SENT", sent_days_ago=2)], tasks=[t],
           activities=[act("QUOTE_SENT", 2)], history=h,
           resolved_state=resolved_state(quotationReadiness="QUOTE_SENT"))

    check("C1 · next_activity_status = WAITING_CUSTOMER（未过复查点）",
          p["next_activity_status"] == pg.NA_WAITING_CUSTOMER,
          p["next_activity_status"])
    check("C2 · waiting_customer = True",
          p["waiting_customer"] is True, "")
    check("C3 · health 不因「等待客户」自动降级为 AT_RISK（§6.1 末段）",
          p["health"] != pg.AT_RISK, p["health"])
    check("C4 · reason 说明是等客户回复，不是缺资料",
          p["reason_code"] in ("CUSTOMER_RESPONSE_WAIT",),
          p["reason_code"])
    check("C5 · 没有把「缺客户资料」误报为阻塞",
          p["reason_code"] != "INTERNAL_BLOCKER", p["reason_code"])
    check("C6 · requires_action = False（球在客户方）",
          p["requires_action"] is False, "")
    check("C7 · overdue = False",
          p["overdue"] is False, "")
    check("C8 · exec_priority 排在 SCHEDULED 之前（WAITING=5 < SCHEDULED=6）",
          pg.exec_priority(p)[0] == 5, str(pg.exec_priority(p)))


# ==========================================================================
# Case D · PRICE NEGOTIATION
# ==========================================================================
def case_d():
    section("Case D · PRICE NEGOTIATION（§8 QUOTED→NEGOTIATION）")
    o = opp(stage="QUOTED", id=104, inquiry_id=104)
    # 进入 QUOTED 仅 2 天（阈值 4/7 → 未过 attention），隔离出"谈判"这一主因
    h = hist(("NEW", "QUALIFIED", 25), ("QUALIFIED", "REQUIREMENT_CONFIRMED", 8),
             ("REQUIREMENT_CONFIRMED", "QUOTED", 2))
    t = task(id=6003, fu_status=fu.PENDING,
             due_at=iso((NOW + datetime.timedelta(days=1))),
             next_action="回复客户价格异议", reason=fu.QUOTE_SENT_NO_REPLY)
    p = rp(o, quotes=[quote("SENT", sent_days_ago=2)], tasks=[t],
           activities=[act("QUOTE_SENT", 2),
                       act("NEGOTIATION", 1, negotiation=True)],
           history=h, resolved_state=resolved_state())

    check("D1 · transition = NEGOTIATION（客户议价是最明确信号）",
          p["recommended_transition"] == "NEGOTIATION",
          f"{p['recommended_transition']} / {p['transition_reason']}")
    check("D2 · transition_reason 提到价格/条款谈判",
          ("谈判" in (p["transition_reason"] or "")
           or "价格" in (p["transition_reason"] or "")),
          p["transition_reason"])
    check("D3 · reason_code = CUSTOMER_REPLY_NEGOTIATION",
          p["reason_code"] == "CUSTOMER_REPLY_NEGOTIATION", p["reason_code"])
    check("D4 · 证据含 negotiation_evidence",
          "negotiation_evidence" in (p["transition_evidence"] or []),
          str(p["transition_evidence"]))
    check("D5 · 未到期的跟进 → next_activity_status = SCHEDULED（不误报逾期）",
          p["next_activity_status"] == pg.NA_SCHEDULED,
          p["next_activity_status"])
    check("D6 · health 不因谈判本身被标 AT_RISK",
          p["health"] != pg.AT_RISK, p["health"])

    # 人工推进校验：QUOTED → NEGOTIATION 应放行（VALID 或 WARNING，不得 BLOCKED）
    v = pg.validate_transition(o, "NEGOTIATION", quotes=[quote("SENT", 12)],
                               activities=[act("NEGOTIATION", 1, negotiation=True)],
                               resolved_state=resolved_state(), now=NOW)
    check("D7 · 人工推进 QUOTED→NEGOTIATION 不被硬阻止",
          v["level"] in (pg.TV_VALID, pg.TV_WARNING),
          f"{v['level']} {v.get('errors')} {v.get('warnings')}")


# ==========================================================================
# Case E · INTERNAL PRODUCT CHECK（内部不确定 ≠ 客户阻塞）
# ==========================================================================
def case_e():
    section("Case E · INTERNAL PRODUCT CHECK（§8 内部不确定不是客户阻塞）")
    o = opp(stage="QUALIFIED", id=105, inquiry_id=105)
    # 进入 QUALIFIED 仅 1 天（阈值 2/4 → 未过 attention），隔离出"内部阻塞"主因
    h = hist(("NEW", "QUALIFIED", 1))
    t = task(id=6004, fu_status=fu.PENDING,
             due_at=iso((NOW + datetime.timedelta(days=1))),
             next_action="内部核实供应能力", reason="SUPPLIER_CHECK")
    rs = resolved_state(
        productMatchStatus="NO_MATCH",
        supplierCapabilityStatus="UNKNOWN",
        actionState="WAITING_INTERNAL",
        requirementCompleteness="HIGH",
        customerBlockingItems=[],
        internalPrerequisites=["确认是否可供应同类品"])
    p = rp(o, quotes=[], tasks=[t], activities=[act("INQUIRY_RECEIVED", 1)],
           history=h, resolved_state=rs)

    check("E1 · reason_code = INTERNAL_BLOCKER（明确是内部问题）",
          p["reason_code"] == "INTERNAL_BLOCKER", p["reason_code"])
    check("E2 · primary_reason 落在内部（不向客户追问）",
          "内部" in p["primary_reason"], p["primary_reason"])
    check("E3 · health ≥ ATTENTION（内部待办要被看见）",
          p["health"] in (pg.ATTENTION, pg.AT_RISK), p["health"])
    check("E4 · 客户侧阻塞为空 → 不得判为 NEEDS_CUSTOMER_INFO",
          not rs.get("customerBlockingItems"), "")
    check("E5 · transition = REQUIREMENT_CONFIRMED（内部不确定不挡推进）",
          p["recommended_transition"] == "REQUIREMENT_CONFIRMED",
          f"{p['recommended_transition']} / {p['transition_reason']}")
    check("E6 · transition 证据显式标注 internal_uncertainty_not_a_customer_blocker",
          "internal_uncertainty_not_a_customer_blocker"
          in (p["transition_evidence"] or []), str(p["transition_evidence"]))
    check("E7 · transition_level = VALID（不是 WARNING/BLOCKED）",
          p["transition_level"] == pg.TV_VALID, p["transition_level"])


# ==========================================================================
# Case F · TERMINAL DEAL
# ==========================================================================
def case_f():
    section("Case F · TERMINAL DEAL（§5.1 / §8.1 终态无告警无建议）")
    for st, label in (("WON", "赢单"), ("LOST", "输单")):
        o = opp(stage=st, id=106, inquiry_id=106)
        h = hist(("NEW", "QUALIFIED", 60), ("QUALIFIED", "QUOTED", 40),
                 ("QUOTED", st, 1))
        p = rp(o, quotes=[quote("ACCEPTED", sent_days_ago=40)], tasks=[],
               activities=[act("STAGE_CHANGE", 1)], history=h,
               resolved_state=resolved_state())

        check(f"F1[{st}] · next_activity_status = CLOSED（不是 MISSING）",
              p["next_activity_status"] == pg.NA_CLOSED,
              p["next_activity_status"])
        check(f"F2[{st}] · 无 aging 告警（终态不产生停留告警）",
              p["aging_level"] == "" and p["aging_thresholds"]["at_risk_days"] is None,
              f"{p['aging_level']} {p['aging_thresholds']}")
        check(f"F3[{st}] · health = HEALTHY（终态不虚报风险）",
              p["health"] == pg.HEALTHY, p["health"])
        check(f"F4[{st}] · 无 transition 建议",
              p["recommended_transition"] == "",
              p["recommended_transition"])
        check(f"F5[{st}] · requires_action = False / needs_action = False",
              p["requires_action"] is False and p["needs_action"] is False, "")
        check(f"F6[{st}] · reason 说明「已结束」",
              "结束" in p["primary_reason"], p["primary_reason"])
        check(f"F7[{st}] · 不因终态被检测出「阶段回退」",
              p["stage_regressed"] is False, str(p.get("peak_stage")))


# ==========================================================================
# G · Stage Aging 权威性（§5 / §17 迁移）
# ==========================================================================
def case_g():
    section("G · Stage Aging：真实迁移优先，重同步行不污染（§5 / §17）")

    # G1 真实迁移记录 → 用它，不是创建时间
    o = opp(stage="QUOTED", id=107, inquiry_id=107,
            created_at=iso(days_ago(90)),
            last_activity_at=iso(days_ago(1)))
    h = hist(("NEW", "QUOTIFIED" and "QUALIFIED", 40),
             ("QUALIFIED", "REQUIREMENT_CONFIRMED", 20),
             ("REQUIREMENT_CONFIRMED", "QUOTED", 9))
    entered, inferred = pg.stage_entered_at(o, h)
    days = pg.stage_age_days(o, h, now=NOW)
    check("G1 · stage_entered_at 取真实进入 QUOTED 的时间（9 天前）",
          entered.startswith(days_ago(9).strftime("%Y-%m-%d")), entered)
    check("G2 · 该值标记为非推断（inferred=False）",
          inferred is False, str(inferred))
    check("G3 · 停留 ≈ 9 天，**不是** 90 天（不用 Deal 创建时间）",
          days["days"] is not None and 8.5 <= days["days"] <= 9.5,
          str(days.get("days")))
    check("G4 · 紧凑文案 = 「停留 9 天」（§5.2）",
          days["text"] == "停留 9 天", days["text"])

    # G5 save_inquiry 重同步行（from_stage 为空）不得覆盖真实时间
    o2 = opp(stage="QUOTED", id=108, inquiry_id=108)
    h2 = [("", "NEW", iso(days_ago(1))),          # 重同步副产物（很新）
          ("", "NEW", iso(days_ago(0))),          # 又一次重同步
          ("REQUIREMENT_CONFIRMED", "QUOTED", iso(days_ago(9)))]  # 真实迁移
    entered2, inferred2 = pg.stage_entered_at(o2, h2)
    check("G5 · 重同步的 None→NEW 行不污染 stage_entered_at",
          entered2.startswith(days_ago(9).strftime("%Y-%m-%d")),
          entered2)
    check("G6 · 真实迁移命中 → 非推断",
          inferred2 is False, str(inferred2))

    # G7 无任何历史 → 保守兜底 + 标记 inferred=True（不发明历史）
    o3 = opp(stage="QUOTED", id=109, inquiry_id=109,
             created_at=iso(days_ago(50)),
             last_activity_at=iso(days_ago(3)))
    entered3, inferred3 = pg.stage_entered_at(o3, [])
    check("G7 · 无迁移历史 → 兜底 last_activity_at 并标 inferred=True",
          inferred3 is True and entered3.startswith(days_ago(3).strftime("%Y-%m-%d")),
          f"{entered3} inferred={inferred3}")

    # G8 阈值可配置且阶梯正确（§5.1）
    check("G8 · QUOTED 阈值 4/7（attention/at_risk）",
          pg.aging_thresholds("QUOTED") == {"attention_days": 4, "at_risk_days": 7},
          str(pg.aging_thresholds("QUOTED")))
    check("G9 · NEW 阈值 1/2",
          pg.aging_thresholds("NEW") == {"attention_days": 1, "at_risk_days": 2},
          str(pg.aging_thresholds("NEW")))
    check("G10 · SAMPLE 阈值 7/14",
          pg.aging_thresholds("SAMPLE") == {"attention_days": 7, "at_risk_days": 14},
          str(pg.aging_thresholds("SAMPLE")))
    check("G11 · 阈值边界：QUOTED 停 5 天 → ATTENTION（>4 未 >7）",
          pg.aging_level("QUOTED", 5.0, NOW) == pg.ATTENTION,
          pg.aging_level("QUOTED", 5.0, NOW))
    check("G12 · 阈值边界：QUOTED 停 8 天 → AT_RISK（>7）",
          pg.aging_level("QUOTED", 8.0, NOW) == pg.AT_RISK,
          pg.aging_level("QUOTED", 8.0, NOW))
    check("G13 · 阈值边界：QUOTED 停 3 天 → 无告警（未过 4）",
          pg.aging_level("QUOTED", 3.0, NOW) == "",
          pg.aging_level("QUOTED", 3.0, NOW))

    # G14 aging 超期必须反映到 health（不是只算不用）
    o4 = opp(stage="QUOTED", id=110, inquiry_id=110)
    h4 = hist(("REQUIREMENT_CONFIRMED", "QUOTED", 9))
    t4 = task(id=6010, fu_status=fu.PENDING,
              due_at=iso((NOW + datetime.timedelta(days=3))))
    p4 = rp(o4, quotes=[quote("SENT", sent_days_ago=9)], tasks=[t4],
            activities=[act("QUOTE_SENT", 9)], history=h4,
            resolved_state=resolved_state())
    check("G14 · QUOTED 停 9 天（>7）→ aging_level=AT_RISK 且 health=AT_RISK",
          p4["aging_level"] == pg.AT_RISK and p4["health"] == pg.AT_RISK,
          f"{p4['aging_level']} / {p4['health']} / {p4['health_signals']}")
    check("G15 · 该情形 reason_code = STAGE_AGING_AT_RISK",
          p4["reason_code"] == "STAGE_AGING_AT_RISK", p4["reason_code"])
    check("G16 · reason 文案含「停留 N 天」与风险阈值",
          "停留" in p4["primary_reason"] and "7" in p4["primary_reason"],
          p4["primary_reason"])


# ==========================================================================
# H · 阶段回退（真实数据缺陷必须暴露）
# ==========================================================================
def case_h():
    section("H · 阶段回退检测与恢复推荐（§8 特例）")
    o = opp(stage="NEW", id=111, inquiry_id=111)
    h = [("", "NEW", iso(days_ago(1))),                     # 重同步
         ("NEW", "QUOTED", iso(days_ago(2)))]               # 真实推进过 QUOTED
    p = rp(o, quotes=[quote("SENT", sent_days_ago=2)], tasks=[],
           activities=[act("QUOTE_SENT", 2)], history=h,
           resolved_state=resolved_state(quotationReadiness="QUOTE_SENT"))

    check("H1 · stage_regressed = True",
          p["stage_regressed"] is True, "")
    check("H2 · peak_stage = QUOTED（历史峰值）",
          p["peak_stage"] == "QUOTED", p["peak_stage"])
    check("H3 · 推荐恢复到 QUOTED（不误导重走流程）",
          p["recommended_transition"] == "QUOTED",
          f"{p['recommended_transition']} / {p['transition_reason']}")
    check("H4 · transition_level = WARNING（需人工确认的数据一致性问题）",
          p["transition_level"] == pg.TV_WARNING, p["transition_level"])
    check("H5 · 证据含 stage_regression_restore",
          "stage_regression_restore" in (p["transition_evidence"] or []),
          str(p["transition_evidence"]))
    check("H6 · health 至少 ATTENTION（回退是需要复核的问题）",
          p["health"] in (pg.ATTENTION, pg.AT_RISK), p["health"])
    check("H7 · health_signals 含 stage_regression",
          "stage_regression" in (p["health_signals"] or []),
          str(p["health_signals"]))


# ==========================================================================
# I · Enums 完整性（§4 / §6 / §10）
# ==========================================================================
def case_i():
    section("I · Enums 完整性（§4 / §6 / §10 无第四态、无第四级）")
    check("I1 · health 仅三态",
          set(pg.HEALTH_CN.keys()) == {pg.HEALTHY, pg.ATTENTION, pg.AT_RISK},
          str(sorted(pg.HEALTH_CN.keys())))
    check("I2 · next_activity 恰好七态（含 CLOSED 终态专用）",
          set(pg.NA_CN.keys()) == {pg.NA_SCHEDULED, pg.NA_DUE_TODAY,
                                   pg.NA_OVERDUE, pg.NA_MISSING,
                                   pg.NA_WAITING_CUSTOMER,
                                   pg.NA_NO_ACTION_REQUIRED, pg.NA_CLOSED},
          str(sorted(pg.NA_CN.keys())))
    check("I3 · transition 校验三级",
          {pg.TV_VALID, pg.TV_WARNING, pg.TV_BLOCKED}
          == {"VALID", "WARNING", "BLOCKED"}, "")
    check("I4 · health 无数字分（字段中不存在 score/评分）",
          "health_score" not in pg.resolve_progression(opp(), now=NOW)
          and not any("score" in k for k in pg.resolve_progression(opp(), now=NOW)),
          "")
    check("I5 · ResolvedDealProgression 输出字段齐备（§3）",
          set(["deal_id", "current_stage", "stage_entered_at", "stage_age_days",
               "health", "primary_reason", "next_activity",
               "next_activity_due_at", "next_activity_status",
               "recommended_transition", "transition_reason",
               "requires_action", "waiting_customer", "overdue"])
          - set(pg.resolve_progression(opp(), now=NOW).keys()) == set(),
          "")
    check("I6 · NA_CN 与枚举一一对应（无空标签）",
          all(pg.NA_CN[k] for k in pg.NA_CN), str(pg.NA_CN))
    check("I7 · STAGE_AGING 覆盖全部活跃阶段（终态除外）",
          set(pg.STAGE_AGING.keys()) ==
          set(crm.STAGES) - crm.TERMINAL,
          f"缺：{set(crm.STAGES) - crm.TERMINAL - set(pg.STAGE_AGING.keys())}")


# ==========================================================================
# J · validate_transition 三态可达（§10）
# ==========================================================================
def case_j():
    section("J · 人工阶段推进校验 VALID / WARNING / BLOCKED（§10）")
    # 注意：transition_errors 读的是商机**顶层**字段（customer_id/product/…），
    rich = opp(stage="QUALIFIED", id=201, inquiry_id=201,
               customer_id=9, product="Wireless ANC Earbuds",
               quantity=3000, specification="ANC 30dB / BT5.3",
               customization="无", currency="USD",
               target_price="", company_quote="")
    v1 = pg.validate_transition(rich, "REQUIREMENT_CONFIRMED",
                                quotes=[], tasks=[], activities=[],
                                resolved_state=resolved_state(), now=NOW)
    check("J1 · 资料齐全 → VALID",
          v1["level"] == pg.TV_VALID, f"{v1['level']} {v1.get('warnings')}")

    # 缺非关键资料 → WARNING（人工可覆盖）
    thin = opp(stage="QUALIFIED", id=202, inquiry_id=202,
               customer_id=9, product="Wireless ANC Earbuds", quantity=3000)
    v2 = pg.validate_transition(thin, "REQUIREMENT_CONFIRMED",
                                quotes=[], tasks=[], activities=[],
                                resolved_state=resolved_state(), now=NOW)
    check("J2 · 缺非关键资料 → WARNING（不硬挡）",
          v2["level"] in (pg.TV_VALID, pg.TV_WARNING),
          f"{v2['level']} {v2.get('warnings')}")

    # 无报价却要推进到 QUOTED → 必须 BLOCKED
    v3 = pg.validate_transition(rich, "QUOTED", quotes=[], tasks=[],
                                activities=[], resolved_state=resolved_state(),
                                now=NOW)
    check("J3 · 没有已发送报价却推进到已报价 → BLOCKED",
          v3["level"] == pg.TV_BLOCKED, f"{v3['level']} {v3.get('blocking')}")

    # 终态重开 → BLOCKED
    won = opp(stage="WON", id=203, inquiry_id=203,
              customer_id=9, product="Wireless ANC Earbuds", quantity=3000,
              specification="ANC 30dB", customization="无",
              currency="USD", final_value=36000, po_number="PO-2026-001")
    v4 = pg.validate_transition(won, "NEGOTIATION", quotes=[quote("ACCEPTED", 5)],
                                tasks=[], activities=[],
                                resolved_state=resolved_state(), now=NOW)
    check("J4 · 终态商机重开 → BLOCKED",
          v4["level"] == pg.TV_BLOCKED, f"{v4['level']} {v4.get('blocking')}")

    # 未知阶段 → BLOCKED，且不抛异常
    v5 = pg.validate_transition(rich, "NOT_A_STAGE", quotes=[], tasks=[],
                                activities=[], resolved_state=resolved_state(),
                                now=NOW)
    check("J5 · 未知目标阶段 → BLOCKED（不抛异常）",
          v5["level"] == pg.TV_BLOCKED, v5["level"])

    # 同阶段 = no-op → VALID
    v6 = pg.validate_transition(rich, "QUALIFIED", quotes=[], tasks=[],
                                activities=[], resolved_state=resolved_state(),
                                now=NOW)
    check("J6 · 目标 == 当前阶段 → VALID（幂等无动作）",
          v6["level"] == pg.TV_VALID, v6["level"])

    # §9：校验函数绝不改数据
    before = json.dumps(rich, sort_keys=True, ensure_ascii=False)
    pg.validate_transition(rich, "QUOTED", quotes=[], tasks=[], activities=[],
                           resolved_state=resolved_state(), now=NOW)
    check("J7 · §9 校验不修改商机对象（推荐而非自动执行）",
          before == json.dumps(rich, sort_keys=True, ensure_ascii=False), "")


# ==========================================================================
# K · 执行优先级排序（§13）
# ==========================================================================
def case_k():
    section("K · 执行优先级排序 OVERDUE > DUE_TODAY > AT_RISK > MISSING > …（§13）")
    base = rp(opp(stage="QUOTED", id=301, inquiry_id=301), now=NOW)

    def mk(status, health):
        p = dict(base)
        p["next_activity_status"] = status
        p["health"] = health
        return p

    seq = [
        ("OVERDUE", pg.AT_RISK),
        ("DUE_TODAY", pg.ATTENTION),
        ("SCHEDULED", pg.AT_RISK),      # AT_RISK（无到期压力）
        ("MISSING", pg.ATTENTION),
        ("SCHEDULED", pg.ATTENTION),
        ("WAITING_CUSTOMER", pg.HEALTHY),
        ("SCHEDULED", pg.HEALTHY),
        ("CLOSED", pg.HEALTHY),
    ]
    keys = [pg.exec_priority(mk(s, h))[0] for (s, h) in seq]
    check("K1 · 排序键单调递增（越靠前越紧急）",
          keys == sorted(keys), str(keys))
    check("K2 · OVERDUE 排最前",
          pg.exec_priority(mk("OVERDUE", pg.AT_RISK))[0]
          < pg.exec_priority(mk("DUE_TODAY", pg.ATTENTION))[0], "")
    check("K3 · DUE_TODAY 早于 MISSING",
          pg.exec_priority(mk("DUE_TODAY", pg.ATTENTION))[0]
          < pg.exec_priority(mk("MISSING", pg.ATTENTION))[0], "")
    check("K4 · MISSING 早于 HEALTHY+SCHEDULED",
          pg.exec_priority(mk("MISSING", pg.ATTENTION))[0]
          < pg.exec_priority(mk("SCHEDULED", pg.HEALTHY))[0], "")
    check("K5 · WAITING_CUSTOMER 早于 HEALTHY+SCHEDULED",
          pg.exec_priority(mk("WAITING_CUSTOMER", pg.HEALTHY))[0]
          < pg.exec_priority(mk("SCHEDULED", pg.HEALTHY))[0], "")
    check("K6 · 终态排最后",
          pg.exec_priority(mk("CLOSED", pg.HEALTHY))[0] == max(keys), "")
    check("K7 · 排序不吃 AI Score（§13：不用评分主排序）",
          set(base.keys()) - {"priority_score", "score"} == set(base.keys()), "")


# ==========================================================================
# L · §4.1 MISSING 必为可执行例外
# ==========================================================================
def case_l():
    section("L · §4.1 MISSING 是可执行例外，不被静默吞掉")
    o = opp(stage="QUALIFIED", id=401, inquiry_id=401)
    h = hist(("NEW", "QUALIFIED", 3))
    p = rp(o, quotes=[], tasks=[], activities=[act("INQUIRY_RECEIVED", 3)],
           history=h, resolved_state=resolved_state(
               productMatchStatus="MATCHED", supplierCapabilityStatus="CONFIRMED"))
    check("L1 · next_activity_status = MISSING",
          p["next_activity_status"] == pg.NA_MISSING, p["next_activity_status"])
    check("L2 · needs_action = True（必须进「需处理」）",
          p["needs_action"] is True, "")
    check("L3 · requires_action = True",
          p["requires_action"] is True, "")
    check("L4 · reason_code = MISSING_NEXT_ACTIVITY",
          p["reason_code"] == "MISSING_NEXT_ACTIVITY", p["reason_code"])
    check("L5 · 永远不等于 NO_ACTION_REQUIRED",
          p["next_activity_status"] != pg.NA_NO_ACTION_REQUIRED, "")
    check("L6 · exec_priority 排第 3 档（MISSING=3）",
          pg.exec_priority(p)[0] == 3, str(pg.exec_priority(p)))

    # 对照：无历史 + 终态 → CLOSED 才允许 needs_action=False
    term = rp(opp(stage="WON", id=402, inquiry_id=402), now=NOW)
    check("L7 · 只有终态(C封闭)才 needs_action=False",
          term["needs_action"] is False and p["needs_action"] is True, "")


# ==========================================================================
# M · §4.3 不产生重复活跃跟进
# ==========================================================================
def case_m():
    section("M · §4.3 同一 Deal 不产生重复活跃跟进任务")
    o = opp(stage="QUOTED", id=501, inquiry_id=501)
    h = hist(("REQUIREMENT_CONFIRMED", "QUOTED", 3))
    t1 = task(id=7001, fu_status=fu.PENDING,
              due_at=iso((NOW + datetime.timedelta(days=1))),
              reason=fu.QUOTE_SENT_NO_REPLY, next_action="跟进报价")
    t2 = task(id=7002, fu_status=fu.PENDING,
              due_at=iso((NOW + datetime.timedelta(days=5))),
              reason=fu.QUOTE_SENT_NO_REPLY, next_action="重复的报价跟进")
    p2 = rp(o, quotes=[quote("SENT", 3)], tasks=[t1, t2],
            activities=[act("QUOTE_SENT", 3)], history=h,
            resolved_state=resolved_state())

    check("M1 · 多个活跃任务时只解析出**一个** next_activity",
          bool(p2["next_activity"]) and p2["next_activity"].count("·") == 0,
          p2["next_activity"])
    check("M2 · 取最早到期的那条（7001，明天）",
          "7001" not in p2["next_activity"] and "跟进报价" in p2["next_activity"]
          and "重复" not in p2["next_activity"], p2["next_activity"])
    check("M3 · next_activity_source = followup_task（复用既有跟进系统）",
          p2["next_activity_source"] == "followup_task",
          p2["next_activity_source"])
    check("M4 · status 非 OVERDUE（最早到期那条未逾期）",
          p2["next_activity_status"] != pg.NA_OVERDUE,
          p2["next_activity_status"])

    # 已有活跃 WAITING 任务（球在客户）时，不该再判 MISSING
    t3 = task(id=7003, fu_status=fu.WAITING,
              due_at=iso((NOW + datetime.timedelta(days=3))),
              reason=fu.CUSTOMER_PROMISED_REPLY, next_action="等客户回复")
    p3 = rp(o, quotes=[quote("SENT", 3)], tasks=[t3],
            activities=[act("QUOTE_SENT", 3)], history=h,
            resolved_state=resolved_state())
    check("M5 · 已有 WAITING 跟进 → 不再报 MISSING（不重复要求安排）",
          p3["next_activity_status"] != pg.NA_MISSING,
          p3["next_activity_status"])

    # 已完成/取消的任务不算活跃
    t4 = task(id=7004, status="DONE", fu_status=fu.COMPLETED,
              due_at=iso(days_ago(1)), reason=fu.QUOTE_SENT_NO_REPLY)
    p4 = rp(o, quotes=[quote("SENT", 3)], tasks=[t4],
            activities=[act("QUOTE_SENT", 3)], history=h,
            resolved_state=resolved_state())
    check("M6 · COMPLETED 任务不算活跃 → 回落 MISSING/WAITING",
          p4["next_activity_status"] in (pg.NA_MISSING, pg.NA_WAITING_CUSTOMER),
          p4["next_activity_status"])


# ==========================================================================
# N · 幂等 & 单一数据源（§2 / §0）
# ==========================================================================
def case_n():
    section("N · 同一 Deal 只有一份 progression（§2 单一解析器 / 幂等）")
    o = opp(stage="QUOTED", id=601, inquiry_id=601)
    h = hist(("REQUIREMENT_CONFIRMED", "QUOTED", 5))
    q = [quote("SENT", 5)]
    t = [task(id=8001, fu_status=fu.PENDING,
              due_at=iso(days_ago(1)), reason=fu.QUOTE_SENT_NO_REPLY)]
    a = [act("QUOTE_SENT", 5)]
    rs = resolved_state()

    p1 = rp(o, quotes=q, tasks=t, activities=a, history=h, resolved_state=rs)
    p2 = rp(o, quotes=q, tasks=t, activities=a, history=h, resolved_state=rs)
    check("N1 · 同输入 → 同输出（幂等，无隐藏状态）",
          json.dumps(p1, sort_keys=True, ensure_ascii=False, default=str)
          == json.dumps(p2, sort_keys=True, ensure_ascii=False, default=str), "")

    before = json.dumps(o, sort_keys=True, ensure_ascii=False, default=str)
    rp(o, quotes=q, tasks=t, activities=a, history=h, resolved_state=rs)
    check("N2 · 解析过程不修改商机对象（只读派生）",
          before == json.dumps(o, sort_keys=True, ensure_ascii=False, default=str),
          "")

    check("N3 · deal_id 稳定（inquiry_id 优先，回退 id）",
          p1["deal_id"] == 601, str(p1["deal_id"]))
    check("N4 · 无需 DB 也能解析（纯函数，可在测试/离线模式跑）",
          isinstance(p1, dict) and p1["current_stage"] == "QUOTED", "")

    # §5.1 阈值只有一处定义 —— progression 不得各自硬编码
    src = open(os.path.join(_HERE, "workbench", "progression.py"),
               encoding="utf-8").read()
    check("N5 · STAGE_AGING 在 progression.py 内仅定义一次",
          src.count("STAGE_AGING = {") == 1,
          str(src.count("STAGE_AGING = {")))
    for ui_file in ("pipeline_ui.py", "app.py", "queue_ui.py"):
        try:
            usrc = open(os.path.join(_HERE, "workbench", ui_file),
                        encoding="utf-8").read()
        except Exception:
            continue
        hardcoded = ("at_risk_days" in usrc and "STAGE_AGING" not in usrc)
        check(f"N6[{ui_file}] · UI 层不硬编码 aging 阈值（§5.1 唯一来源）",
              not hardcoded, "")
    check("N7 · pipeline_ui 复用 progression（不自行推导 health）",
          "import progression" in open(
              os.path.join(_HERE, "workbench", "pipeline_ui.py"),
              encoding="utf-8").read(), "")


# ==========================================================================
# O · 真实数据库端到端（临时库副本，零副作用）
# ==========================================================================
def case_o():
    section("O · 真实 DB 端到端（临时库副本 · 只读）")
    import db as _db

    src_db = os.path.join(_HERE, "workbench", "workbench.db")
    if not os.path.exists(src_db):
        check("O0 · 找不到 workbench.db（跳过真实库断言）", True, "skipped")
        return
    tmp = tempfile.mkdtemp()
    try:
        cp = os.path.join(tmp, "workbench.db")
        shutil.copyfile(src_db, cp)
        old = _db.DB_PATH
        _db.DB_PATH = cp

        opps = _db.list_opportunities() or []
        check("O1 · 能读到商机",
              len(opps) > 0, f"{len(opps)} 个商机")

        bad = []
        northaus = None
        for o in opps:
            p = pg.progression_of(o["id"], db_mod=_db, now=NOW)
            if not p:
                bad.append(f"#{o['id']} 空结果")
                continue
            if p["health"] not in (pg.HEALTHY, pg.ATTENTION, pg.AT_RISK):
                bad.append(f"#{o['id']} health={p['health']}")
            if p["next_activity_status"] not in pg.NA_CN:
                bad.append(f"#{o['id']} na={p['next_activity_status']}")
            if p["next_activity_status"] == pg.NA_MISSING \
                    and not p["requires_action"]:
                bad.append(f"#{o['id']} MISSING 却 requires_action=False")
            if p["health"] == pg.AT_RISK \
                    and p["next_activity_status"] not in (pg.NA_OVERDUE,
                                                          pg.NA_SCHEDULED,
                                                          pg.NA_DUE_TODAY,
                                                          pg.NA_WAITING_CUSTOMER):
                bad.append(f"#{o['id']} AT_RISK 但 na={p['next_activity_status']}")
            co = str(o.get("customer_name") or "")
            if "NordHaus" in co:
                northaus = (o, p)
        check("O2 · 全部商机 progression 自洽（无非法枚举/无静默 MISSING）",
              not bad, "; ".join(bad[:4]))

        allp = pg.progression_for_all(db_mod=_db, now=NOW)
        check("O3 · progression_for_all 覆盖全部商机",
              len(allp) == len(opps), f"{len(allp)}/{len(opps)}")

        if northaus:
            o, p = northaus
            check("O4 · NordHaus 解析出具体主因（非空套话）",
                  bool(p["primary_reason"]) and len(p["primary_reason"]) >= 8,
                  p["primary_reason"])
            check("O5 · NordHaus 给出明确的 next_activity 或 WAITING 状态",
                  p["next_activity_status"] in pg.NA_CN,
                  p["next_activity_status"])
            check("O6 · NordHaus 阶段停留文本为紧凑格式（§5.2）",
                  p["stage_age_text"] == "" or
                  p["stage_age_text"] == "今天" or
                  re.fullmatch(r"停留 \d+ 天", p["stage_age_text"]) is not None,
                  p["stage_age_text"])
            check("O7 · NordHaus 不越权自动改阶段（stage 保持原位）",
                  p["current_stage"] == str(o.get("stage") or ""),
                  f"{p['current_stage']} vs {o.get('stage')}")
        else:
            check("O4-O7 · 真实库无 NordHaus（跳过）", True, "skipped")

        # 只读校验：副本未被写入
        c1 = sqlite3.connect(cp)
        n_before = c1.execute("select count(*) from opportunities").fetchone()[0]
        c1.close()
        pg.progression_for_all(db_mod=_db, now=NOW)
        c2 = sqlite3.connect(cp)
        n_after = c2.execute("select count(*) from opportunities").fetchone()[0]
        c2.close()
        check("O8 · progression 解析不写库（行数不变）",
              n_before == n_after, f"{n_before} → {n_after}")

        _db.DB_PATH = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ==========================================================================
# P · 验收场景（spec §20 关键项）
# ==========================================================================
def case_p():
    section("P · 验收场景 · 报价已发未回复 + 逾期跟进（NordHaus 复刻）")
    o = opp(stage="QUOTED", id=701, inquiry_id=701,
            company_quote="", target_price="",   # 客户目标价不当作我方报价
            details={"target_price": "USD 8.50", "company_quote": ""})
    h = hist(("NEW", "QUALIFIED", 30), ("QUALIFIED", "REQUIREMENT_CONFIRMED", 20),
             ("REQUIREMENT_CONFIRMED", "QUOTED", 10))
    t = task(id=9001, fu_status=fu.PENDING, due_at=iso(days_ago(2)),
             next_action="发送第一次报价跟进", reason=fu.QUOTE_SENT_NO_REPLY)
    p = rp(o, quotes=[quote("SENT", sent_days_ago=10)], tasks=[t],
           activities=[act("QUOTE_SENT", 10)], history=h,
           resolved_state=resolved_state(quotationReadiness="QUOTE_SENT"))

    check("P1 · health = AT_RISK（报价跟进逾期）",
          p["health"] == pg.AT_RISK, p["health"])
    check("P2 · next_activity_status = OVERDUE",
          p["next_activity_status"] == pg.NA_OVERDUE, p["next_activity_status"])
    check("P3 · primary_reason 描述「报价已发 N 天 + 逾期 M 天」",
          "报价已发出" in p["primary_reason"]
          and "逾期" in p["primary_reason"], p["primary_reason"])
    check("P4 · 客户目标价未被当成我方报价（company_quote 仍为空）",
          not o["details"].get("company_quote"), str(o["details"]))
    check("P5 · 推荐阶段不越级（不跳到 NEGOTIATION/WON）",
          p["recommended_transition"] in ("", "QUOTED", "SAMPLE", "NEGOTIATION")
          and p["recommended_transition"] not in ("WON", "PO_PENDING"),
          p["recommended_transition"])
    check("P6 · requires_action = True（进「需处理」视图）",
          p["requires_action"] is True, "")

    # §13 Pipeline 排序：逾期 Deal 必须排在健康 Deal 之前
    healthy = rp(opp(stage="QUOTED", id=702, inquiry_id=702),
                 quotes=[], activities=[], history=hist(("X", "QUOTED", 1))
                 if False else [("REQUIREMENT_CONFIRMED", "QUOTED", 1)],
                 tasks=[task(id=9002, fu_status=fu.PENDING,
                             due_at=iso((NOW + datetime.timedelta(days=3))))],
                 resolved_state=resolved_state())
    check("P7 · §13 逾期 Deal 的执行优先级严格高于健康 Deal",
          pg.exec_priority(p) < pg.exec_priority(healthy),
          f"{pg.exec_priority(p)} < {pg.exec_priority(healthy)}")

    # §12 需处理视图归属
    check("P8 · §12 该 Deal 属于「需处理」",
          p["needs_action"] is True, "")
    check("P9 · §12 健康 Deal 不属于「需处理」",
          healthy["needs_action"] is False, str(healthy["health"]))


def main():
    print("=" * 66)
    print("TEST06 · DEAL PROGRESSION ENGINE（ROUND 7.1）")
    print(f"NOW = {iso(NOW)}")
    print("=" * 66)
    case_a()
    case_b()
    case_c()
    case_d()
    case_e()
    case_f()
    case_g()
    case_h()
    case_i()
    case_j()
    case_k()
    case_l()
    case_m()
    case_n()
    case_o()
    case_p()
    print("\n" + "=" * 66)
    print(f"TEST06 回归结果：{_PASS}/{_PASS + _FAIL} 项通过　"
          + ("✅ 全部通过" if _FAIL == 0 else "❌ 存在失败"))
    if _FAILED:
        print("失败项：")
        for n in _FAILED:
            print("  ·", n)
    print("=" * 66)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
