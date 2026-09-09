# -*- coding: utf-8 -*-
"""第十轮：客户级商机（crm.py）单元测试。"""
import io
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "workbench")

import crm

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def mkitem(id_=1, biz="READY_TO_REPLY", qty=None, price=None, cur="USD",
           pts=50, created="2026-09-07 08:00", action=None, status="待处理"):
    return {"id": id_, "biz": biz, "status": status, "pts": pts,
            "created": created,
            "need": {"qty_num": qty, "price_num": price,
                     "target_price_currency": cur, "intent": "inquiry",
                     "qty": "", "blockers": [], "readiness": ""},
            "action": action or {"type": "SEND_REPLY", "label": "回复客户",
                                 "priority": "P2"}}


print("== Test A：商机阶段派生 ==")
check("单条新询盘 → 新商机",
      crm.opportunity_of([mkitem()])["stage"] == crm.OPP_NEW)
check("NEEDS_INFO → 需求确认",
      crm.opportunity_of([mkitem(biz="NEEDS_INFO")])["stage"] == crm.OPP_QUALIFYING)
check("REPLIED → 产品匹配",
      crm.opportunity_of([mkitem(biz="REPLIED")])["stage"] == crm.OPP_MATCHING)
check("READY_FOR_QUOTE → 待报价",
      crm.opportunity_of([mkitem(biz="READY_FOR_QUOTE")])["stage"] == crm.OPP_QUOTE_PENDING)
check("QUOTED → 已报价",
      crm.opportunity_of([mkitem(biz="QUOTED")])["stage"] == crm.OPP_QUOTED)
check("NEGOTIATING → 谈判中",
      crm.opportunity_of([mkitem(biz="NEGOTIATING")])["stage"] == crm.OPP_NEGOTIATING)
check("WON → 已成交",
      crm.opportunity_of([mkitem(biz="WON")])["stage"] == crm.OPP_WON)
check("全 LOST → 已丢单",
      crm.opportunity_of([mkitem(biz="LOST"), mkitem(id_=2, biz="LOST")])["stage"]
      == crm.OPP_LOST)
check("无活跃但有成交 → 已成交（不是培育）",
      crm.opportunity_of([mkitem(biz="WON"), mkitem(id_=2, biz="LOST")])["stage"]
      == crm.OPP_WON)
check("无活跃无成交 → 长期培育（暂缓/挂起的客户）",
      crm.opportunity_of([mkitem(biz="ON_HOLD")])["stage"] == crm.OPP_NURTURE)

print("== Test B：阶段取最靠前的活跃询盘（spec 六：一个客户一个商机） ==")
r = crm.opportunity_of([mkitem(id_=1, biz="NEEDS_INFO"),
                        mkitem(id_=2, biz="QUOTED")])
check("新询盘+已报价 → 阶段=已报价", r["stage"] == crm.OPP_QUOTED)
check("活跃数=2", r["active_count"] == 2)
check("询盘数=2", r["inquiry_count"] == 2)
r2 = crm.opportunity_of([mkitem(id_=1, biz="REPLIED"),
                         mkitem(id_=2, biz="NEGOTIATING"),
                         mkitem(id_=3, biz="WON")])
check("活跃阶段推进到谈判中（WON 不拉高活跃阶段）",
      r2["stage"] == crm.OPP_NEGOTIATING)

print("== Test C：商机金额（有数据才算，绝不编造） ==")
r = crm.opportunity_of([mkitem(qty=10000, price=2.8)])
check("10000×2.8=28000", r["value"] == 28000)
check("金额文本 28,000", "28,000" in r["value_txt"])
check("币种提示 USD", r["currency_hint"] == "USD")
r = crm.opportunity_of([mkitem(qty=10000, price=None)])
check("缺价格 → 金额为 None", r["value"] is None)
r = crm.opportunity_of([mkitem(qty=10000, price=2.8, cur="EUR")])
check("币种跟随数据 EUR", r["currency_hint"] == "EUR")
r = crm.opportunity_of([mkitem(id_=1, biz="LOST", qty=10000, price=2.8),
                        mkitem(id_=2, biz="QUOTED", qty=100, price=5.0)])
check("丢单询盘不计入金额", r["value"] == 500)
r = crm.opportunity_of([mkitem(qty=0, price=2.8)])
check("数量为 0 → 不算金额", r["value"] is None)

print("== Test D：客户级 Next Action（P0 > P1 > P2） ==")
r = crm.opportunity_of([
    mkitem(id_=1, action={"type": "SEND_REPLY", "label": "回复客户", "priority": "P2"}),
    mkitem(id_=2, pts=30,
           action={"type": "CONFIRM_PRODUCT", "label": "确认产品", "priority": "P0"}),
])
check("跨询盘挑出 P0 动作", r["next_action"]["label"] == "确认产品")
check("动作挂到正确询盘", r["next_action"]["inquiry_id"] == 2)
r = crm.opportunity_of([
    mkitem(id_=1, pts=70, action={"type": "A", "label": "高优询盘的动作", "priority": "P1"}),
    mkitem(id_=2, pts=30, action={"type": "B", "label": "低优询盘的动作", "priority": "P1"}),
])
check("同优先级取优先分高者", r["next_action"]["label"] == "高优询盘的动作")
r = crm.opportunity_of([mkitem(action={"type": None, "label": "", "priority": ""})])
check("无有效动作 → next_action 为 None", r["next_action"] is None)

print("== Test E：计数与时间 ==")
r = crm.opportunity_of([mkitem(id_=1), mkitem(id_=2, biz="NEEDS_INFO"),
                        mkitem(id_=3, created="2026-09-01 10:00")])
check("待回复数只数 READY_TO_REPLY", r["todo_count"] == 2)
check("最近活动取最大时间", r["last_seen"] == "2026-09-07 08:00")

print("== Test F：详情页 7 段状态条定位 ==")
check("READY_TO_REPLY → 段0 新询盘", crm.funnel_position("READY_TO_REPLY") == 0)
check("NEEDS_INFO → 段1 需求确认", crm.funnel_position("NEEDS_INFO") == 1)
check("REPLIED → 段2 产品匹配", crm.funnel_position("REPLIED") == 2)
check("READY_FOR_QUOTE → 段3 待报价", crm.funnel_position("READY_FOR_QUOTE") == 3)
check("QUOTED → 段4 已报价", crm.funnel_position("QUOTED") == 4)
check("NEGOTIATING → 段5 谈判", crm.funnel_position("NEGOTIATING") == 5)
check("WON → 段6 成交", crm.funnel_position("WON") == 6)
check("LOST → 不在漏斗（-1）", crm.funnel_position("LOST") == -1)
check("ON_HOLD → 不在漏斗（-1）", crm.funnel_position("ON_HOLD") == -1)

print("== Test G：to_num 宽松解析 ==")
check("整数", crm.to_num("10,000") == 10000.0)
check("小数", crm.to_num("2.80") == 2.8)
check("原生数字", crm.to_num(5000) == 5000.0)
check("带单位字符串取数字", crm.to_num("about 10000 pcs") == 10000.0)
check("非法 → None", crm.to_num("N/A") is None)
check("None → None", crm.to_num(None) is None)

print("== Test H：空输入兜底 ==")
r = crm.opportunity_of([])
check("空列表 → 新商机不炸", r["stage"] == crm.OPP_NEW)
check("空列表金额 None", r["value"] is None)

print("=" * 50)
print(f"第十轮 crm 单元测试：通过 {PASS} · 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
